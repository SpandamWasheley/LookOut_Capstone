"""Per-person tracking for the violation watchers (smoking, thief).

Association is greedy: each frame's person boxes (from recognition.detect_persons)
are matched to existing tracks by highest IoU, falling back to center proximity
when IoU fails. Unmatched boxes start new tracks, which first try to inherit the
state of a recently-lost track nearby (see Track.adopt). Tracks unseen for
TRACK_MAX_GAP seconds become tombstones, then expire.

This is deliberately simple — no Kalman filter or appearance embedding — because
the watchers only need identity to hold *between adjacent frames* so that dwell
timers and vote windows follow a person instead of the whole scene. When you do
need real motion modelling (two people crossing paths swap identities under
greedy matching), pass `ids` from recognition.detect_persons_tracked and this
module defers association to ultralytics' ByteTrack/BoT-SORT instead.

Each Track carries its own temporal-confirmation state (vote window, dwell
start, alert cooldown), so two people in frame are timed and alerted
independently. Detections that no person box claims (person detector missed, or
the object isn't on a person) are grouped into position-keyed *scene* tracks so
they are still confirmed and alerted — separately per location, not pooled.

No Django model access here — same rule as recognition.py, pure CV plumbing.
"""

from collections import deque

from .recognition import _iou

TRACK_MATCH_IOU = 0.3    # min IoU for a person box to continue an existing track
TRACK_MATCH_DIST = 0.8   # fallback match radius, as a fraction of mean box size
TRACK_MAX_GAP = 2.0      # seconds a track survives without being re-detected

# A lost track is remembered this long so a person who was occluded (walked
# behind a parked jeepney, or the person detector simply dropped them) can pick
# their dwell timer back up instead of restarting from zero. Without it someone
# who flickers in and out can loiter indefinitely and never complete a dwell.
TOMBSTONE_SECONDS = 10.0
TOMBSTONE_MATCH_DIST = 1.2   # more generous than live matching: they may have moved

# Reach used when deciding which person a detection belongs to, as a fraction of
# the person box's WIDTH — applied to both axes. Scaling each axis by its own
# extent (the obvious approach) is wrong here: a person box is tall and thin, so
# 15% gave ~9px of horizontal slack against ~30px of vertical, while people
# extend their arms *sideways*. A cigarette or knife held out to the side fell
# outside the box and dropped to the scene tracks.
ASSIGN_REACH = 0.45

SCENE_MATCH_DIST = 1.5   # clustering radius for unattributed detections
SCENE_MAX_GAP = 2.0      # seconds a scene track survives unseen

# N-of-M temporal voting, measured in SECONDS rather than frames. A frame-count
# window means wildly different things per mode: near mode runs ~10-15 FPS
# (15 frames ~ 1s) while far mode's tiling + person-crop cascade can drop to
# ~1 FPS on CPU (15 frames ~ 15s), so a fixed frame count silently becomes a
# 15-second confirmation delay exactly where detections are hardest to get.
# A time window behaves the same at any frame rate.
# Window widened from 3s to 5s and the frame floor lowered from 3 to 2 so that
# far mode — which runs ~0.5-0.7 FPS because of tiling — can still gather enough
# positive frames to confirm. At 0.6 FPS a 5s window holds ~3 frames, so a
# 2-frame floor is reachable; a 3s/3-frame rule was mathematically impossible
# there (only ~2 frames ever fit the window). At near-mode frame rates the ratio
# (0.4) is still the real guard, so this doesn't make close detection trigger-happy.
VOTE_WINDOW_SECONDS = 5.0
VOTE_MIN_RATIO = 0.4    # fraction of the window's frames that must be positive
VOTE_MIN_FRAMES = 2     # absolute floor, so a single stray frame at low FPS can't confirm

# Puff-cycle heuristic (smoking): the human tell for smoking is the hand-to-mouth
# RHYTHM — the cigarette raised to the lips, lowered, raised again — not just a
# cigarette parked near a face. These thresholds are in FACE-WIDTHS (so they are
# distance-invariant, like the mouth-proximity rule). The gap between them is
# hysteresis: the object must clearly rise AND clearly fall to count, so small
# jitter near one threshold can't fake a puff.
PUFF_RAISED_RATIO = 1.5    # within this many face-widths of the mouth = "raised"
PUFF_LOWERED_RATIO = 3.0   # beyond this = "lowered"
PUFF_WINDOW_SECONDS = 20.0  # count completed puffs within this rolling window

# Pose-based hand-to-mouth gesture (long-range smoking, when the cigarette is too
# small to see). Distance of a wrist to the nose, in SHOULDER-WIDTHS, so it is
# distance-invariant. A gesture = the hand clearly reaches the face then clearly
# drops; the hysteresis gap between the two keeps ordinary small movements from
# counting.
HAND_RAISED_RATIO = 0.7    # wrist within this many shoulder-widths of the nose = at face
HAND_LOWERED_RATIO = 1.3   # beyond this = hand down
GESTURE_WINDOW_SECONDS = 20.0

# Dwell only accrues while a detection is this recent. The vote ratio alone has
# inertia — it stays true for over a second after the last detection, because
# older positive frames are still inside the window — so without this bound the
# window's lag and the grace period would stack and a "3 second dwell" could be
# satisfied by ~1.3s of actual detection.
ACCRUAL_STALE_SECONDS = 0.5


def _center_proximity(a, b, max_frac=TRACK_MATCH_DIST):
    """1.0 when two boxes' centers coincide, falling linearly to 0 at
    `max_frac` x their mean box size.

    IoU alone breaks down at the low frame rates far mode runs at: a walking
    person can clear their own bounding box between frames, so two consecutive
    detections of the SAME person overlap by nothing, every frame starts a new
    track, and the vote window and dwell timer reset forever — the watcher
    would never alert on anyone who moves. Center proximity still recognises
    them. Scaling the radius by box size keeps it distance-aware: a person far
    down the street has a small box and so a correspondingly tight radius.
    """
    acx, acy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bcx, bcy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    dist = ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5
    size = ((a[2] - a[0]) + (b[2] - b[0]) + (a[3] - a[1]) + (b[3] - b[1])) / 4
    limit = max_frac * size
    if limit <= 0 or dist >= limit:
        return 0.0
    return 1.0 - dist / limit


class Track:
    """One tracked person (or a scene track) and its temporal-confirmation state."""

    def __init__(self, track_id, box, now, window_seconds=VOTE_WINDOW_SECONDS,
                 is_scene=False, ext_id=None):
        self.id = track_id
        self.box = box                    # (x1,y1,x2,y2)
        self.last_seen = now              # last frame this track was matched
        self.window_seconds = window_seconds
        self.is_scene = is_scene          # True = unattributed-detection cluster
        self.ext_id = ext_id              # ByteTrack/BoT-SORT id, when in use
        self.votes = deque()              # (timestamp, dets) per frame in the window
        self.dets = []                    # last non-empty detections, for evidence
        # Dwell is ACCUMULATED confirmed time, not elapsed wall-clock since the
        # first hit. Measuring elapsed time credited gaps as dwell: between the
        # rolling vote window's inertia and the grace period, a 3s dwell could be
        # satisfied by ~1.3s of real detection plus tolerated silence. Summing
        # only the frames where the violation was actually confirmed makes
        # "present for 3s" mean three seconds of it.
        self.dwell_held = 0.0
        self._last_tick = None
        self.last_threat_seen = 0.0       # for the grace-period reset
        # Cached face anchor for the mouth-proximity rule, stored RELATIVE to the
        # person box (fractions of its width/height) so it can be re-projected
        # onto the box as they move, instead of re-running a ~400ms face pass
        # every frame. Format: (rel_x, rel_y, rel_w, timestamp).
        self.face_anchor = None
        # None = never alerted. Not 0.0: that only reads as "long ago" because
        # the watchers happen to pass epoch timestamps, so any caller using a
        # relative clock would silently be inside the cooldown from frame one.
        self.last_alerted_at = None       # per-person alert cooldown
        # Puff-cycle state (smoking): which zone the cigarette is in relative to
        # the mouth, and the timestamps of completed raise-then-lower puffs.
        self.puff_zone = None             # 'raised' | 'lowered' | None
        self.puffs = deque()              # timestamps of completed puffs
        # Pose hand-to-mouth gesture state (long-range smoking).
        self.hand_zone = None             # 'atface' | 'down' | None
        self.gestures = deque()           # timestamps of completed hand-to-mouth cycles

    def update_puff(self, dist_ratio, now, window=PUFF_WINDOW_SECONDS):
        """Feeds the cigarette->mouth distance (in FACE-WIDTHS) into the puff
        state machine and returns the count of completed puffs in the window.

        A puff = the object clearly rises to the mouth (ratio <= RAISED) and then
        clearly falls away (ratio >= LOWERED). The hysteresis gap between the two
        thresholds means jitter around one line can't fake a puff. This is the
        hand-to-mouth *rhythm* of real smoking, not just a cigarette parked near
        a face.
        """
        if dist_ratio <= PUFF_RAISED_RATIO:
            self.puff_zone = "raised"
        elif dist_ratio >= PUFF_LOWERED_RATIO:
            if self.puff_zone == "raised":
                self.puffs.append(now)   # raised then lowered = one completed puff
            self.puff_zone = "lowered"
        # else: in the hysteresis band — hold the current zone.
        cutoff = now - window
        while self.puffs and self.puffs[0] < cutoff:
            self.puffs.popleft()
        return len(self.puffs)

    def puff_count(self, now, window=PUFF_WINDOW_SECONDS):
        cutoff = now - window
        while self.puffs and self.puffs[0] < cutoff:
            self.puffs.popleft()
        return len(self.puffs)

    def update_gesture(self, ratio, now, window=GESTURE_WINDOW_SECONDS):
        """Feeds the wrist->nose distance (in SHOULDER-WIDTHS) into the pose
        hand-to-mouth state machine. Returns the count of completed hand-to-mouth
        cycles in the window. A cycle = hand clearly at the face then clearly
        down; hysteresis prevents jitter from counting."""
        if ratio <= HAND_RAISED_RATIO:
            self.hand_zone = "atface"
        elif ratio >= HAND_LOWERED_RATIO:
            if self.hand_zone == "atface":
                self.gestures.append(now)   # reached the face then dropped = one cycle
            self.hand_zone = "down"
        cutoff = now - window
        while self.gestures and self.gestures[0] < cutoff:
            self.gestures.popleft()
        return len(self.gestures)

    def gesture_count(self, now, window=GESTURE_WINDOW_SECONDS):
        cutoff = now - window
        while self.gestures and self.gestures[0] < cutoff:
            self.gestures.popleft()
        return len(self.gestures)

    @property
    def display(self):
        """How this track should be named in an alert description."""
        return f"unattributed detection #{self.id}" if self.is_scene else f"person #{self.id}"

    def adopt(self, other, now):
        """Inherits a recently-lost track's identity and confirmation state."""
        self.id = other.id
        self.votes = other.votes
        self.dets = other.dets
        self.last_alerted_at = other.last_alerted_at
        if other.dwell_held > 0:
            # Carry the dwell progress across the occlusion. The occlusion itself
            # costs nothing extra, because dwell only accrues on confirmed frames.
            self.dwell_held = other.dwell_held
            self._last_tick = now
            # Their vote window is stale and prunes to empty on the next vote,
            # which would trip the grace-period reset on the very first frame
            # back and throw the progress away. They were occluded, not
            # innocent — give them the full grace period to re-confirm.
            self.last_threat_seen = now

    def vote(self, dets, now):
        """Records this frame's detections in the rolling window and drops
        anything older than `window_seconds`."""
        self.votes.append((now, tuple(dets)))
        cutoff = now - self.window_seconds
        while self.votes and self.votes[0][0] < cutoff:
            self.votes.popleft()
        if dets:
            self.dets = dets
            self.last_threat_seen = now

    def present(self):
        """True when enough of the window's frames were positive.

        A ratio rather than a raw count, so the bar is the same whether the
        window holds 45 near-mode frames or 3 far-mode ones; VOTE_MIN_FRAMES
        stops a freshly created track from confirming off its first hit.
        """
        hits = sum(1 for _, dets in self.votes if dets)
        if hits < VOTE_MIN_FRAMES:
            return False
        return hits >= len(self.votes) * VOTE_MIN_RATIO

    def accruing(self, now):
        """True when the vote window says present AND a detection is current, so
        the dwell clock should be running."""
        if not self.dets or now - self.last_threat_seen > ACCRUAL_STALE_SECONDS:
            return False
        return self.present()

    def seen_at(self, now):
        """True if this track was matched to a box in the frame at `now`.

        Distinguishes "the person is standing right there and has stopped" from
        "the person is out of view": the first should time out and reset, the
        second is an occlusion and must not be punished for it.
        """
        return now - self.last_seen < 1e-6

    def tick(self, now, active):
        """Advances the dwell clock, crediting only confirmed time. Returns the
        total confirmed seconds held so far."""
        if self._last_tick is not None and active:
            self.dwell_held += now - self._last_tick
        self._last_tick = now
        return self.dwell_held

    def reset_dwell(self):
        self.dwell_held = 0.0

    def in_cooldown(self, now, cooldown):
        """True if this track alerted less than `cooldown` seconds ago."""
        return self.last_alerted_at is not None and now - self.last_alerted_at < cooldown

    def label_votes(self):
        """{label: number of frames in the window it appeared in}."""
        counts = {}
        for _, dets in self.votes:
            for label in {d[5] for d in dets}:
                counts[label] = counts.get(label, 0) + 1
        return counts

    def best_detection(self):
        """The detection an alert should be built from: the highest-confidence
        box *of the class seen in the most frames* across the window.

        Taking the single highest-confidence box instead would let one frame of
        a spurious class outrank the class that was actually present for the
        whole dwell — which is how a sustained `stealing` gets reported as a
        one-frame `gun`. The box is searched across the whole window, not just
        the latest frame, because the winning class may not be in the latest
        frame at all.
        """
        counts = self.label_votes()
        if not counts:
            return None
        top = max(counts.values())
        tied = {label for label, n in counts.items() if n == top}
        candidates = [d for _, dets in self.votes for d in dets if d[5] in tied]
        return max(candidates, key=lambda d: d[4]) if candidates else None


class PersonTracker:
    def __init__(self, window_seconds=VOTE_WINDOW_SECONDS,
                 iou_match=TRACK_MATCH_IOU, max_gap=TRACK_MAX_GAP):
        self._next_id = 1
        self._next_scene_id = 1
        self.tracks = []
        self.scene_tracks = []
        self.tombstones = []      # recently lost tracks, for re-identification
        self.window_seconds = window_seconds
        self.iou_match = iou_match
        self.max_gap = max_gap

    # ---- person tracks -----------------------------------------------------

    def update(self, person_boxes, now, ids=None):
        """Matches person boxes to tracks, starts tracks for unmatched boxes,
        expires stale ones. Returns live tracks.

        `ids` are external track ids from recognition.detect_persons_tracked
        (ByteTrack/BoT-SORT). When given, association is theirs and this just
        keeps the per-track confirmation state keyed to them; otherwise the
        greedy IoU + proximity matcher below runs.
        """
        if ids is not None:
            self._update_by_ext_id(person_boxes, ids, now)
        else:
            self._update_greedy(person_boxes, now)
        self._expire(now)
        return self.tracks

    def _update_greedy(self, person_boxes, now):
        """Greedy association, best score first.

        An IoU match always outranks a proximity match (its score is offset by
        +1.0), so proximity only decides pairings IoU couldn't make.
        """
        pairs = []
        for t in self.tracks:
            for i, pb in enumerate(person_boxes):
                iou = _iou(t.box, pb)
                if iou >= self.iou_match:
                    pairs.append((1.0 + iou, t, i))
                    continue
                prox = _center_proximity(t.box, pb)
                if prox > 0:
                    pairs.append((prox, t, i))
        pairs.sort(key=lambda p: p[0], reverse=True)

        matched_tracks, matched_boxes = set(), set()
        for _, t, i in pairs:
            if id(t) in matched_tracks or i in matched_boxes:
                continue
            t.box = tuple(int(v) for v in person_boxes[i][:4])
            t.last_seen = now
            matched_tracks.add(id(t))
            matched_boxes.add(i)

        for i, pb in enumerate(person_boxes):
            if i not in matched_boxes:
                self.tracks.append(self._spawn(tuple(int(v) for v in pb[:4]), now))

    def _update_by_ext_id(self, person_boxes, ids, now):
        """Keys tracks off an external tracker's ids instead of associating."""
        live = {t.ext_id: t for t in self.tracks if t.ext_id is not None}
        for pb, ext in zip(person_boxes, ids):
            box = tuple(int(v) for v in pb[:4])
            if ext is None:
                # The external tracker declined to id this box (usually a
                # low-confidence detection it won't commit to yet). Fall back to
                # proximity against the untracked ones so it still gets counted.
                self._match_or_spawn(box, now)
                continue
            t = live.get(ext)
            if t is None:
                t = self._spawn(box, now, ext_id=ext)
                self.tracks.append(t)
                live[ext] = t
            t.box = box
            t.last_seen = now

    def _match_or_spawn(self, box, now):
        best, best_score = None, 0.0
        for t in self.tracks:
            score = max(_iou(t.box, box), _center_proximity(t.box, box))
            if score > best_score:
                best, best_score = t, score
        if best is None:
            self.tracks.append(self._spawn(box, now))
        else:
            best.box, best.last_seen = box, now

    def _spawn(self, box, now, ext_id=None):
        """Creates a track, reviving a nearby recently-lost one if there is one."""
        t = Track(self._next_id, box, now, self.window_seconds, ext_id=ext_id)
        ghost = self._claim_tombstone(box, now)
        if ghost is not None:
            t.adopt(ghost, now)
        else:
            self._next_id += 1
        return t

    def _claim_tombstone(self, box, now):
        """Pops the best-matching recently-lost track for this box, if any."""
        best, best_score = None, 0.0
        for t in self.tombstones:
            score = max(_iou(t.box, box),
                        _center_proximity(t.box, box, TOMBSTONE_MATCH_DIST))
            if score > best_score:
                best, best_score = t, score
        if best is not None:
            self.tombstones.remove(best)
        return best

    def _expire(self, now):
        live = []
        for t in self.tracks:
            if now - t.last_seen <= self.max_gap:
                live.append(t)
            else:
                self.tombstones.append(t)
        self.tracks = live
        self.tombstones = [
            t for t in self.tombstones if now - t.last_seen <= TOMBSTONE_SECONDS
        ]

    # ---- detection attribution --------------------------------------------

    def assign(self, detections, now):
        """Splits (x1,y1,x2,y2,conf,label) detections among tracks and returns
        {track: [dets]} covering every live person track AND every scene track.

        A detection goes to the person whose box (expanded by ASSIGN_REACH)
        contains its center; when several people contain it, the smallest box
        wins — the person it is most tightly on. Anything unclaimed is grouped
        by location into scene tracks.
        """
        per_track = {t: [] for t in self.tracks}
        leftovers = []
        for det in detections:
            x1, y1, x2, y2 = det[:4]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            best, best_area = None, None
            for t in self.tracks:
                bx1, by1, bx2, by2 = t.box
                reach = (bx2 - bx1) * ASSIGN_REACH
                if bx1 - reach <= cx <= bx2 + reach and by1 - reach <= cy <= by2 + reach:
                    area = (bx2 - bx1) * (by2 - by1)
                    if best is None or area < best_area:
                        best, best_area = t, area
            if best is None:
                leftovers.append(det)
            else:
                per_track[best].append(det)

        per_track.update(self._update_scene(leftovers, now))
        return per_track

    def _update_scene(self, leftovers, now):
        """Groups unattributed detections into position-keyed pseudo-tracks.

        A single global scene bucket (the previous design) merged a knife in one
        corner of the frame with a gun in the other: shared vote window, shared
        dwell timer and — worst — a shared alert cooldown, so whichever fired
        first silenced the other completely.
        """
        per_scene = {t: [] for t in self.scene_tracks}
        for det in leftovers:
            box = tuple(int(v) for v in det[:4])
            best, best_score = None, 0.0
            for t in self.scene_tracks:
                score = max(_iou(t.box, box),
                            _center_proximity(t.box, box, SCENE_MATCH_DIST))
                if score > best_score:
                    best, best_score = t, score
            if best is None:
                best = Track(self._next_scene_id, box, now, self.window_seconds,
                             is_scene=True)
                self._next_scene_id += 1
                self.scene_tracks.append(best)
                per_scene[best] = []
            best.box, best.last_seen = box, now
            per_scene[best].append(det)

        # Scene tracks are dropped outright rather than tombstoned: they track a
        # place, not a person, so there is no identity worth reviving.
        stale = [t for t in self.scene_tracks if now - t.last_seen > SCENE_MAX_GAP]
        for t in stale:
            self.scene_tracks.remove(t)
            per_scene.pop(t, None)
        return per_scene
