"""Layer E — theft pattern rules (E1-E29).

Layers A-D (detect / associate / accrue / decide) are the shared chain every
violation class uses; they live in recognition.py, tracking.py and each watch_*
command. Layer E is the class-specific PATTERN layer for theft: it consumes
tracks and detections and produces a weighted evidence vector that Layer D turns
into a candidate violation.

    A (detect) -> B (associate) -> C (accrue) -> E (pattern) -> D (decide)

Four sub-patterns, each with its own motion signature:

  * snatch      (E6-E9)   — brief contact, then an asymmetric velocity burst.
  * holdup      (E10-E14) — the inverse: loiter, rapid close, then a freeze.
  * carnapping  (E15-E20) — state change at a registered parked-vehicle anchor.
  * property    (E21-E22) — custody change over an unattended carriable.

Plus suppression (E23-E27), which ABSTAINS rather than rejects — consistent with
B4 in the existing chain — and weighted scoring with a three-band decision
(E28-E29). Scoring, not conjunctive gating: requiring six cues to co-fire would
mean the module never fires in the field, so cues accumulate weight instead and
a single threshold is what gets calibrated.

E1-E3 (the normalized primitives) and the track-level guards for E25-E27 live in
tracking.py, because they are properties of a track rather than of theft.

No Django access here — pure CV plumbing, same rule as recognition.py.

Two places where this file deliberately diverges from the written spec, both
flagged rather than silently resolved:

  1. E14's weight (0.45) sits below the E29 alert band (0.55), so a weapon on
     its own reaches Observe, not Candidate — while E14's own rationale says
     weapon presence is "sufficient alone to reach the alert band". The
     constants are implemented exactly as written; WEAPON_ALONE_ALERTS lets the
     operator opt into the rationale's reading instead.
  2. E14's rule text requires the weapon to be near a track satisfying E11,
     but the E.A pseudocode scores the weapon with no such condition. Both are
     honoured: a weapon near an E11 track joins the holdup evidence, a weapon
     anywhere else emits standalone weapon evidence.
"""

import math
from collections import deque

from .recognition import _iou
from .tracking import SceneBaseline, norm_distance

# --- E.8 constants summary --------------------------------------------------
# Every value here is a reasoned default. None has been calibrated against
# footage from the mounted camera; this table is an initialization, not a result.

REACH_NORM = 0.8              # E6 contact distance, in person heights
CONTACT_DWELL_MIN = 0.3       # E6 contact duration bounds
CONTACT_DWELL_MAX = 2.0
BURST_RATIO = 2.5             # E7 normalized velocity threshold
STATIC_RATIO = 0.4            # E7 victim stationarity threshold
DIVERGENCE_DEG = 90           # E8 heading separation
EFF_LOITER = 0.30             # E3 / E10 path-efficiency ceiling
LOITER_SECONDS = 20           # E10 minimum presence
FREEZE_SECONDS = 3.0          # E12 confrontation hold
ANCHOR_STATIC_SECONDS = 60    # E5 parked-vehicle registration
ABSENCE_SECONDS = 120         # E15 unattended timer
INTERACT_DWELL = 15.0         # E16 vehicle manipulation
INTERACT_DWELL_NIGHT = 10.0   # E16 under E20
CROUCH_ASPECT = 0.90          # E17 width-to-height ratio
PUSH_SECONDS = 5.0            # E18 sustained push
OBJ_STATIC_SECONDS = 30       # E21 unattended property
CROWD_SOFT = 6                # E23 density guard
CROWD_HARD = 10
SCORE_OBSERVE = 0.35          # E29 decision bands
SCORE_ALERT = 0.55

# --- constants stated inside individual rules rather than in the E.8 table ---

APPROACH_FRAMES = 3           # E6 consecutive strictly-decreasing frames
BURST_AFTER = 1.5             # E7 burst must start within this of d_min
BURST_HOLD = 1.0              # E7 asymmetry must hold this long
OWNER_REACH = 1.2             # E4 max person-heights for a custody candidate
CO_MOVE_SECONDS = 1.0         # E9 object must co-move with the new owner
STILL_TOLERANCE = 0.1         # E5 displacement ceiling, in vehicle heights
UNATTENDED_REACH = 1.5        # E15 / E21 "someone is with it" radius
INTERACT_REACH = 1.0          # E16 person-to-vehicle distance
INTERACT_IOU = 0.15           # E16 person/vehicle overlap
PUSH_REACH = 0.9              # E18 walker-beside-vehicle distance
PUSH_SPEED_MAX = 1.0          # E18 vehicle v_norm ceiling (walking pace)
DEPART_DISPLACE = 1.0         # E19 anchor displacement, in vehicle heights
PROPERTY_IOU = 0.2            # E22 person/object overlap
CLOSE_FROM = 3.0              # E11 starts beyond this
CLOSE_TO = 1.0                # E11 ends inside this
CLOSE_SECONDS = 3.0           # E11 time budget for the approach
FREEZE_REACH = 1.0            # E12 arm's-length
FREEZE_SPEED = 0.2            # E12 v_norm ceiling for "stationary"
CONVERGE_WINDOW = 5.0         # E13 window for counting converging tracks
CONVERGE_MIN = 2              # E13 tracks needed for the multiplier
WEAPON_REACH = 0.5            # E14 weapon-to-person distance, in person heights
CROWD_GATE_RELAX = 1.3        # E23 soft-guard widening of every d_norm gate
GREETING_WINDOW = 3.0         # E24 grace before a snatch is committed
GREETING_SPEED_LO = 0.7       # E24 "resumed normal gait" band
GREETING_SPEED_HI = 1.3
OCCLUSION_ABSTAIN = 0.4       # E27 occluded share that voids a cue
EVIDENCE_WINDOW = 20.0        # window E25-E27 are evaluated over
EVIDENCE_REEMIT_SECONDS = 30.0  # same incident is not re-scored faster than this
ANCHOR_LOST_SECONDS = 10.0    # an anchor survives this long unseen
ANCHOR_MATCH_IOU = 0.3
# Fallback association radius, in the anchor's own heights. Generous on purpose:
# a snatched handbag is YANKED more than its own width between frames, and a
# one-height radius loses the anchor at precisely the moment custody changes —
# the single moment E9 exists to observe. A parked vehicle never moves far
# enough per frame for this to mis-associate it, and IoU is always tried first.
ANCHOR_MATCH_REACH = 2.0

# When True, a weapon on its own is promoted to the Candidate band regardless of
# arithmetic — E14's stated rationale ("sufficient alone"). Left False so the
# written constants govern by default; watch_thief exposes it as a flag.
WEAPON_ALONE_ALERTS = False

# --- E28 recommended initial weights ----------------------------------------
# Reasoned defaults pending field calibration. The ablation harness supports
# per-cue removal so each weight can be revised against measured precision.
WEIGHTS = {
    "E14": 0.45,   # weapon presence   — escalation is intentional
    "E9": 0.35,    # custody transfer  — highest specificity of any non-weapon cue
    "E22": 0.35,   # custody at anchor — same cue, unattended-property variant
    "E18": 0.35,   # push-away         — near-unique motion signature
    "E12": 0.25,   # confrontation freeze
    "E19": 0.25,   # identity mismatch — conditional on a re-ID embedding
    "E7": 0.20,    # separation burst
    "E10": 0.20,   # loiter            — context, never sufficient alone
    "E16": 0.20,   # interaction dwell — primary carnapping cue
    "E17": 0.15,   # tamper posture    — coarse aspect-ratio proxy
    "E8": 0.10,    # heading divergence — corroborating only
}
MULTIPLIERS = {
    "E13": 1.5,    # group convergence
    "E20": 1.3,    # nocturnal
}

DISCARD, OBSERVE, CANDIDATE = "discard", "observe", "candidate"


def band_of(score):
    """E29 — map a score to one of three outcomes."""
    if score > SCORE_ALERT:
        return CANDIDATE
    if score >= SCORE_OBSERVE:
        return OBSERVE
    return DISCARD


def _person_norm_distance(person_box, other_box):
    """Centre distance in units of the PERSON's height.

    E1 divides by the mean height of two people. For a person-to-object pair
    that is wrong: the quantity being modelled is human arm's reach, which is a
    property of the person alone — normalizing by the mean of a person and a
    parked motorcycle would make the same gap read differently for a scooter
    than for a van.
    """
    px1, py1, px2, py2 = person_box
    h = max(py2 - py1, 1)
    pcx, pcy = (px1 + px2) / 2, (py1 + py2) / 2
    ocx = (other_box[0] + other_box[2]) / 2
    ocy = (other_box[1] + other_box[3]) / 2
    return math.hypot(pcx - ocx, pcy - ocy) / h


def _angle_between(u, v):
    """Angle in degrees between two unit vectors."""
    dot = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
    return math.degrees(math.acos(dot))


def _seat_region(box):
    """The part of a two-wheeler a rider would occupy: upper half, centre 60%.

    E18 distinguishes pushing from riding. A rider's box overlaps the seat; a
    person walking a motorcycle beside them does not.
    """
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    return (x1 + 0.2 * w, y1, x2 - 0.2 * w, y1 + 0.5 * h)


class Evidence:
    """One scored incident (E28) with its band (E29) and full cue vector.

    The cue vector is kept intact even for Observe-band events: that is the
    mechanism by which the threshold gets calibrated against real footage after
    the field shoot, instead of against reasoned defaults.
    """

    def __init__(self, kind, box, cues, multipliers, abstained, tracks, detail,
                 weapon_alone_alerts=WEAPON_ALONE_ALERTS):
        self.kind = kind
        self.box = tuple(int(v) for v in box)
        self.cues = dict(cues)
        self.multipliers = dict(multipliers)
        self.abstained = set(abstained)
        self.tracks = list(tracks)
        self.detail = detail
        self.score = sum(self.cues.values())
        for factor in self.multipliers.values():
            self.score *= factor
        self.band = band_of(self.score)
        if weapon_alone_alerts and "E14" in self.cues and self.band != CANDIDATE:
            self.band = CANDIDATE

    @property
    def rules(self):
        return sorted(self.cues) + sorted(self.multipliers)

    def summary(self):
        cues = ", ".join(f"{r}={w:.2f}" for r, w in sorted(self.cues.items()))
        mults = "".join(f" x{f}" for f in self.multipliers.values())
        abst = f" [abstained: {', '.join(sorted(self.abstained))}]" if self.abstained else ""
        return f"{self.detail} | {cues}{mults} -> {self.score:.2f} ({self.band}){abst}"

    def __repr__(self):
        return f"<Evidence {self.kind} {self.score:.2f} {self.band}>"


# --- E4, E5, E15-E22: the anchor store ---------------------------------------


class ObjectAnchor:
    """A tracked non-person object plus the Layer E state kept against it.

    Converts a frame-wise object detector into something stateful: carnapping
    and property theft are defined by a CHANGE OF STATE at a fixed asset, not by
    anything visible in a single frame.
    """

    def __init__(self, aid, box, label, kind, now):
        self.id = aid
        self.box = tuple(int(v) for v in box)
        self.label = label
        self.kind = kind                  # 'vehicle' | 'carriable'
        self.first_seen = now
        self.last_seen = now
        self.trail = deque()              # (t, cx, cy)
        self.still_anchor = self.center
        self.still_since = now
        self.origin = self.center         # position at registration, for E19
        self.anchor_since = None          # E5: registered as a ParkedAnchor
        self.unattended_since = None      # E15 / E21
        self.person_near_at = now
        self.owner_id = None              # E4 current owner
        self.owner_since = None
        self.owner_start_pos = None
        self.retained_owner = None        # E21 owner held across the absence
        self.transfers = deque()          # (t, from_id, to_id) — E9 / E22
        self.interact_since = {}          # person id -> first E16 frame
        self.push_since = None
        self.departed_at = None           # E19: first frame it left its origin
        self.emitted = {}                 # cue id -> last emission time
        # Cues fire at different moments of the same incident — a thief crouches
        # at the lock (E16/E17) and only then pushes the bike away (E18) — so a
        # cue stays live for the evidence window instead of having to co-occur
        # in one frame. E.A scores a WINDOW (`a.pushed_for`, `window.dwell_at`),
        # not an instant; requiring simultaneity would cap carnapping at whichever
        # single cue happened to be true, and it could never reach the alert band.
        self.latches = {}                 # rule -> (timestamp, reason, actor)

    # ---- geometry ----

    @property
    def center(self):
        x1, y1, x2, y2 = self.box
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @property
    def height(self):
        return max(self.box[3] - self.box[1], 1)

    @property
    def unattended(self):
        return self.unattended_since is not None

    def speed_px(self, window=1.0):
        """Centroid speed in pixels per second over the window."""
        if len(self.trail) < 2:
            return 0.0
        t1, x1, y1 = self.trail[-1]
        ref = None
        for sample in self.trail:
            if t1 - sample[0] <= window:
                ref = sample
                break
        if ref is None or ref[0] >= t1:
            ref = self.trail[-2]
        dt = t1 - ref[0]
        if dt <= 0:
            return 0.0
        return math.hypot(x1 - ref[1], y1 - ref[2]) / dt

    def displacement(self):
        """Distance from where this anchor was registered, in its own heights."""
        cx, cy = self.center
        return math.hypot(cx - self.origin[0], cy - self.origin[1]) / self.height

    # ---- per-frame state ----

    def observe(self, box, now):
        self.box = tuple(int(v) for v in box)
        self.last_seen = now
        cx, cy = self.center
        self.trail.append((now, cx, cy))
        while self.trail and now - self.trail[0][0] > EVIDENCE_WINDOW:
            self.trail.popleft()
        ax, ay = self.still_anchor
        if math.hypot(cx - ax, cy - ay) > STILL_TOLERANCE * self.height:
            self.still_anchor = (cx, cy)
            self.still_since = now       # stillness clock restarts
            if self.anchor_since is not None and self.displacement() > DEPART_DISPLACE:
                # Departure is EVIDENCE (E19), not a reason to forget the anchor.
                # Deregistering here would delete the unattended state exactly
                # when it matters most: a motorcycle being pushed away clears
                # DEPART_DISPLACE in about a second and a half, well inside the
                # 5s E18 needs to confirm the push.
                self.departed_at = self.departed_at or now

    def register(self, now):
        """E5 / E21 — promote a persistently still object to an anchor."""
        if self.anchor_since is None:
            self.anchor_since = now
            self.origin = self.center

    def latch(self, rule, now, reason, actor):
        self.latches[rule] = (now, reason, actor)

    def live_cues(self, now):
        """{rule: (reason, actor)} for cues that fired inside the window."""
        self.latches = {
            r: v for r, v in self.latches.items() if now - v[0] <= EVIDENCE_WINDOW
        }
        return {r: (v[1], v[2]) for r, v in self.latches.items()}

    def should_emit(self, key, rules, now):
        """Rate-limit re-scoring, but never sit on genuinely new evidence.

        An incident escalates as cues accumulate: the crouch at the lock scores
        0.35 (Observe), and the push five seconds later takes the same incident
        to 0.70 (Candidate). A plain time-based cooldown would swallow that
        second, higher-value emission — so a strictly larger cue set re-emits
        immediately, and only a repeat of what was already reported waits out
        the cooldown.
        """
        last = self.emitted.get(key)
        if last is None:
            return True
        last_ts, last_sig = last
        if not frozenset(rules) <= last_sig:
            return True
        return now - last_ts >= EVIDENCE_REEMIT_SECONDS

    def mark_emitted(self, key, rules, now):
        self.emitted[key] = (now, frozenset(rules))


class AnchorStore:
    """Tracks vehicles and carriables across frames and holds their Layer E state.

    Implements E4 (ownership), E5 (parked-vehicle registration), E15-E19
    (carnapping) and E21-E22 (unattended property). Keyed by anchor id, so state
    survives the frames where the detector misses the object.
    """

    def __init__(self):
        self._next_id = 1
        self.anchors = []

    # ---- association ----

    def update(self, vehicles, carriables, persons, now):
        """Matches this frame's object boxes to anchors and advances their state.

        `persons` are the live, usable person tracks (already filtered by the
        E25/E26 guards).
        """
        self._associate(vehicles, "vehicle", now)
        self._associate(carriables, "carriable", now)
        self._expire(now)
        for a in self.anchors:
            self._update_presence(a, persons, now)
            if a.kind == "carriable":
                self._update_ownership(a, persons, now)
                self._update_property_state(a, now)
            else:
                self._update_parked_state(a, now)

    def _associate(self, detections, kind, now):
        claimed = set()
        for det in detections:
            box, label = det[:4], det[5]
            best, best_score = None, 0.0
            for a in self.anchors:
                if a.kind != kind or id(a) in claimed:
                    continue
                score = _iou(box, a.box)
                if score < ANCHOR_MATCH_IOU:
                    # An object the detector re-localises slightly, or one seen
                    # again after a gap, can overlap by nothing; fall back to
                    # centre proximity in units of the anchor's own size. Ranked
                    # below any real IoU match so proximity only breaks ties IoU
                    # could not make.
                    d = _person_norm_distance(a.box, box)
                    score = 0.29 if d < ANCHOR_MATCH_REACH else 0.0
                if score > best_score:
                    best, best_score = a, score
            if best is None:
                best = ObjectAnchor(self._next_id, box, label, kind, now)
                self._next_id += 1
                self.anchors.append(best)
            claimed.add(id(best))
            best.observe(box, now)

    def _expire(self, now):
        self.anchors = [
            a for a in self.anchors if now - a.last_seen <= ANCHOR_LOST_SECONDS
        ]

    # ---- state machines ----

    def _update_presence(self, anchor, persons, now):
        """E15 / E21 — is anybody with this object, and for how long has nobody been.

        UNATTENDED is latched: it records that the owner walked away, and a
        stranger arriving does not undo that. Clearing it on proximity looks
        right ("someone is with it again") but makes E16 unsatisfiable — E16
        tests a person interacting with an UNATTENDED anchor, so the approach
        that triggers the rule would be the same approach that cancels its
        precondition, and carnapping could never fire at all.
        """
        for t in persons:
            if _person_norm_distance(t.box, anchor.box) < UNATTENDED_REACH:
                anchor.person_near_at = now
                return

    def _update_parked_state(self, anchor, now):
        """E5 then E15 — registration, then the unattended timer."""
        if now - anchor.still_since >= ANCHOR_STATIC_SECONDS:
            anchor.register(now)
        if anchor.anchor_since is None:
            return
        if (anchor.unattended_since is None
                and now - anchor.person_near_at >= ABSENCE_SECONDS):
            anchor.unattended_since = now

    def _update_ownership(self, anchor, persons, now):
        """E4 — owner = argmax IoU among persons within OWNER_REACH."""
        best, best_iou = None, 0.0
        for t in persons:
            if _person_norm_distance(t.box, anchor.box) >= OWNER_REACH:
                continue
            overlap = _iou(t.box, anchor.box)
            if overlap > best_iou:
                best, best_iou = t, overlap
        owner = best.id if best is not None else None
        if owner != anchor.owner_id:
            if owner is not None:
                anchor.transfers.append((now, anchor.owner_id, owner))
                while anchor.transfers and now - anchor.transfers[0][0] > EVIDENCE_WINDOW:
                    anchor.transfers.popleft()
            anchor.owner_id = owner
            anchor.owner_since = now if owner is not None else None
            anchor.owner_start_pos = anchor.center if owner is not None else None

    def _update_property_state(self, anchor, now):
        """E21 — a carriable left still and alone becomes an unattended anchor."""
        still_for = now - anchor.still_since
        if (still_for >= OBJ_STATIC_SECONDS and anchor.owner_id is None
                and now - anchor.person_near_at >= OBJ_STATIC_SECONDS):
            if anchor.unattended_since is None:
                anchor.unattended_since = now
                anchor.register(now)
                # The owner is retained across the absence so a change of custody
                # can be recognised when someone finally picks it up.
                if anchor.retained_owner is None:
                    anchor.retained_owner = self._last_known_owner(anchor)

    @staticmethod
    def _last_known_owner(anchor):
        for _, prev, new in reversed(anchor.transfers):
            if prev is not None:
                return prev
            if new is not None:
                return new
        return None

    # ---- cue extraction ----

    def carnapping_cues(self, persons, baseline, now, is_night):
        """E16-E19 against every UNATTENDED vehicle anchor.

        Yields (anchor, person_track, {cue: reason}) for anchors showing
        interaction. Only two-wheelers are evaluated — four-wheeled carnapping
        is out of scope at the planned mounting geometry and is not claimed.
        """
        dwell_needed = INTERACT_DWELL_NIGHT if is_night else INTERACT_DWELL
        for a in self.anchors:
            if a.kind != "vehicle" or not a.unattended:
                continue
            if a.label not in ("motorcycle", "bicycle"):
                continue
            for t in persons:
                d = _person_norm_distance(t.box, a.box)
                overlap = _iou(t.box, a.box)
                if d < INTERACT_REACH and overlap > INTERACT_IOU:
                    a.interact_since.setdefault(t.id, now)
                    held = now - a.interact_since[t.id]
                    if held >= dwell_needed:
                        a.latch("E16", now, f"manipulated for {held:.0f}s", t)
                        if t.aspect > CROUCH_ASPECT:
                            a.latch("E17", now,
                                    f"crouched (w/h {t.aspect:.2f})", t)
                else:
                    a.interact_since.pop(t.id, None)

            push = self._push_cue(a, persons, baseline, now)
            if push is not None:
                pusher, reason = push
                a.latch("E18", now, reason, pusher)

            live = a.live_cues(now)
            if not live:
                continue
            # Attribute to whichever actor triggered the most recent cue.
            actor = max(a.latches.values(), key=lambda v: v[0])[2]
            yield a, actor, {r: reason for r, (reason, _) in live.items()}

    def _push_cue(self, anchor, persons, baseline, now):
        """E18 — the vehicle moving at walking pace beside a non-mounted person.

        Offenders push a motorcycle out of earshot rather than start it, and
        almost nothing in ordinary scene activity produces a vehicle rolling at
        walking pace next to somebody who is not on it.
        """
        seat = _seat_region(anchor.box)
        for t in persons:
            if _person_norm_distance(t.box, anchor.box) >= PUSH_REACH:
                continue
            if _iou(t.box, seat) > 0.05:
                continue                      # they are on it — that is riding
            # Normalize the vehicle's pixel speed by the PERSON's height so the
            # comparison against the scene baseline (which is in person-heights
            # per second) is dimensionally consistent.
            v_norm = anchor.speed_px() / t.height / baseline.reference
            if 0 < v_norm < PUSH_SPEED_MAX:
                if anchor.push_since is None:
                    anchor.push_since = now
                held = now - anchor.push_since
                if held >= PUSH_SECONDS:
                    return t, f"pushed {held:.0f}s beside a non-riding person"
                return None
        anchor.push_since = None
        return None

    def property_cues(self, persons, now):
        """E22 — custody transfer at an unattended carriable anchor."""
        for a in self.anchors:
            if a.kind != "carriable" or not a.unattended:
                continue
            for t in persons:
                if _iou(t.box, a.box) <= PROPERTY_IOU:
                    continue
                if a.owner_id != t.id or a.owner_since is None:
                    continue
                if now - a.owner_since < CO_MOVE_SECONDS:
                    continue
                if a.owner_start_pos is not None:
                    moved = math.hypot(a.center[0] - a.owner_start_pos[0],
                                       a.center[1] - a.owner_start_pos[1])
                    if moved < STILL_TOLERANCE * a.height:
                        continue      # picked up but not yet carried off
                if a.retained_owner is not None and a.retained_owner == t.id:
                    continue          # the owner came back for their own bag
                yield a, t, {"E22": f"{a.label} taken by a different person"}

    def custody_flip(self, a_id, b_id, start, end, now):
        """E9 — did a carriable change hands from a to b inside the window?

        The transfer alone is not enough: the object must then CO-MOVE with its
        new owner for CO_MOVE_SECONDS. Without that, one frame of a bag being
        mis-assigned as two people pass each other reads as a completed theft.
        """
        for anchor in self.anchors:
            if anchor.kind != "carriable":
                continue
            for ts, prev, new in anchor.transfers:
                if prev == a_id and new == b_id and start <= ts <= end:
                    if (anchor.owner_id == b_id and anchor.owner_since is not None
                            and now - anchor.owner_since >= CO_MOVE_SECONDS):
                        return anchor
        return None


# --- E6-E13, E24: person-pair patterns ---------------------------------------


class PairState:
    """Snatch (E6-E9) and holdup (E10-E13) state for one pair of person tracks."""

    def __init__(self, key, now):
        self.key = key
        self.created = now
        self.last_update = now
        self.last_d = None
        self.decreasing = 0
        self.d_min = None
        self.d_min_at = None
        self.contact_start = None
        self.contact_end = None
        self.contact_dwell = None
        self.approach_ok = False
        self.burst_since = None
        self.burst_ok = False
        self.runner = None
        self.divergence = None
        self.far_at = None
        self.close_at = None
        self.freeze_since = None
        self.freeze_ok = False
        self.post_sep = deque()
        self.pending = None       # snatch awaiting the E24 grace period
        self.emitted = {}

    def update(self, ta, tb, d, va, vb, now, reach):
        self.last_update = now

        # E6 — monotonic approach and contact.
        if self.last_d is not None:
            if d < self.last_d - 1e-6:
                self.decreasing += 1
            elif d > self.last_d + 1e-6:
                self.decreasing = 0
        self.last_d = d

        if d < reach:
            if self.contact_start is None:
                self.contact_start = now
                # Evaluated on entry: "strictly decreasing for >= 3 frames,
                # reaching d_min < REACH".
                self.approach_ok = self.decreasing >= APPROACH_FRAMES
                self.d_min, self.d_min_at = d, now
            elif self.d_min is None or d < self.d_min:
                self.d_min, self.d_min_at = d, now
        elif self.contact_start is not None:
            self.contact_end = now
            self.contact_dwell = now - self.contact_start
            self.contact_start = None
            self.post_sep.clear()

        # E7 — separation burst: asymmetric velocity within 1.5s of d_min.
        if self.d_min_at is not None and not self.burst_ok:
            since = now - self.d_min_at
            fast_a = va > BURST_RATIO and vb < STATIC_RATIO
            fast_b = vb > BURST_RATIO and va < STATIC_RATIO
            if (fast_a or fast_b) and since <= BURST_AFTER + BURST_HOLD:
                if self.burst_since is None and since <= BURST_AFTER:
                    self.burst_since = now
                    self.runner = ta if fast_a else tb
                if (self.burst_since is not None
                        and now - self.burst_since >= BURST_HOLD):
                    self.burst_ok = True
            elif self.burst_since is not None:
                self.burst_since = None    # the asymmetry broke before it held

        # E8 — heading divergence after separation.
        ha, hb = ta.heading(now), tb.heading(now)
        if ha is not None and hb is not None:
            self.divergence = _angle_between(ha, hb)

        # E11 — rapid closing, > 3.0 to < 1.0 in under 3s.
        if d > CLOSE_FROM:
            self.far_at = now
            self.close_at = None
        elif (d < CLOSE_TO and self.far_at is not None
                and now - self.far_at <= CLOSE_SECONDS and self.close_at is None):
            self.close_at = now

        # E12 — confrontation freeze.
        if d < FREEZE_REACH * (reach / REACH_NORM) and va < FREEZE_SPEED and vb < FREEZE_SPEED:
            if self.freeze_since is None:
                self.freeze_since = now
            self.freeze_ok = now - self.freeze_since >= FREEZE_SECONDS
        else:
            self.freeze_since = None
            self.freeze_ok = False

        # E24 — sample the post-separation window so a greeting can be told
        # apart from a snatch after the fact.
        if self.contact_end is not None and now - self.contact_end <= GREETING_WINDOW:
            self.post_sep.append((now, va, vb, self.divergence))

        # A completed contact plus a burst is a snatch candidate — held for the
        # greeting grace period before it is committed.
        if (self.burst_ok and self.pending is None and self.approach_ok
                and self.contact_dwell is not None
                and CONTACT_DWELL_MIN <= self.contact_dwell <= CONTACT_DWELL_MAX):
            self.pending = {
                "at": now,
                "decide_at": (self.contact_end or now) + GREETING_WINDOW,
                "dwell": self.contact_dwell,
                "window": ((self.contact_end or now) - self.contact_dwell,
                           self.contact_end or now),
            }

    def is_greeting(self):
        """E24 — both parties resumed ordinary gait on similar headings.

        Handshakes, hand-offs and near-passes reproduce the contact-and-separate
        signature for a few hundred milliseconds; what distinguishes them is what
        happens next.
        """
        for _, va, vb, div in self.post_sep:
            in_band = (GREETING_SPEED_LO <= va <= GREETING_SPEED_HI
                       and GREETING_SPEED_LO <= vb <= GREETING_SPEED_HI)
            if in_band and (div is None or div < DIVERGENCE_DEG):
                return True
        return False

    def can_emit(self, kind, now):
        last = self.emitted.get(kind)
        return last is None or now - last >= EVIDENCE_REEMIT_SECONDS

    def mark_emitted(self, kind, now):
        self.emitted[kind] = now
        if kind == "snatch":
            self.burst_ok = False
            self.pending = None
            self.contact_dwell = None


# --- the engine --------------------------------------------------------------


class TheftEngine:
    """Drives Layer E for one camera feed.

    One instance per stream. Feed it the frame's person tracks, object boxes and
    thief.pt detections; it returns scored Evidence for whatever fired.
    """

    def __init__(self, ablate=(), baseline=None, stats=None,
                 weapon_alone_alerts=WEAPON_ALONE_ALERTS):
        self.ablate = {r.lower() for r in ablate}
        self.baseline = baseline or SceneBaseline()
        self.anchors = AnchorStore()
        self.pairs = {}
        self.stats = stats if stats is not None else {}
        self.weapon_alone_alerts = weapon_alone_alerts
        self._converge_log = deque()   # (t, target_id, other_id) for E13
        self._weapon_emitted = {}      # track id -> last E14 emission

    # ---- helpers ----

    def _bump(self, key):
        self.stats[key] = self.stats.get(key, 0) + 1

    def _off(self, rule):
        """True if this rule is ablated (measurement runs, not production)."""
        return rule.lower() in self.ablate

    def _add_cue(self, cues, abstained, rule, tracks, now, reason=None):
        """Adds a weighted cue unless it is ablated or E27-abstained.

        E27: a cue whose participating track was occluded for most of the window
        is excluded from the sum WITHOUT penalty — absence of evidence is not
        evidence of absence, the same convention B4 uses.
        """
        if self._off(rule):
            return
        for t in tracks:
            if t.occluded_fraction(now, EVIDENCE_WINDOW) > OCCLUSION_ABSTAIN:
                abstained.add(rule)
                self._bump(f"abstained (E27):{rule}")
                return
        cues[rule] = WEIGHTS[rule]
        if reason:
            self._bump(f"cue:{rule}")

    def _usable(self, track, now):
        """E25 and E26 — discard evidence from a track we cannot trust."""
        if not self._off("e25") and track.truncated:
            self._bump("discarded (E25): edge-truncated track")
            return False
        if not self._off("e26") and track.id_switched_within(now, EVIDENCE_WINDOW):
            self._bump("discarded (E26): identity switch in window")
            return False
        return True

    # ---- per-frame entry point ----

    def update(self, tracks, carriables, vehicles, threats, now,
               is_night=False):
        """Runs Layer E for one frame and returns a list of Evidence.

        `tracks`    live person tracks from tracking.PersonTracker
        `carriables`/`vehicles`  COCO boxes from recognition.detect_scene
        `threats`   thief.pt detections, for E14
        """
        people = [t for t in tracks if not t.is_scene]
        self.baseline.update(people, now)

        # E23 — crowd density guard. Proximity carries little information in a
        # dense scene, and abstaining beats emitting alerts the dispatcher will
        # uniformly dismiss.
        density = len(people)
        if not self._off("e23") and density > CROWD_HARD:
            self._bump("SUPPRESSED_CROWD")
            return []
        reach = REACH_NORM
        if not self._off("e23") and density > CROWD_SOFT:
            reach *= CROWD_GATE_RELAX

        usable = [t for t in people if self._usable(t, now)]
        self.anchors.update(vehicles, carriables, usable, now)

        evidence = []
        evidence += self._pair_evidence(usable, now, reach, threats, is_night)
        evidence += self._carnapping_evidence(usable, now, is_night)
        evidence += self._property_evidence(usable, now, is_night)
        evidence += self._weapon_evidence(usable, threats, now, is_night)
        self._prune(now)
        for ev in evidence:
            self._bump(f"evidence:{ev.kind}:{ev.band}")
        return evidence

    # ---- E6-E13 ----

    def _pair_evidence(self, tracks, now, reach, threats, is_night):
        out = []
        for i, ta in enumerate(tracks):
            for tb in tracks[i + 1:]:
                key = (min(ta.id, tb.id), max(ta.id, tb.id))
                state = self.pairs.get(key)
                if state is None:
                    state = self.pairs[key] = PairState(key, now)
                d = norm_distance(ta.box, tb.box)
                va = self.baseline.norm(ta, now)
                vb = self.baseline.norm(tb, now)
                was_closing = state.close_at
                state.update(ta, tb, d, va, vb, now, reach)
                if state.close_at is not None and state.close_at != was_closing:
                    self._converge_log.append((now, ta.id, tb.id))
                    self._converge_log.append((now, tb.id, ta.id))

                snatch = self._snatch(state, ta, tb, now, is_night)
                if snatch is not None:
                    out.append(snatch)
                holdup = self._holdup(state, ta, tb, now, threats, is_night)
                if holdup is not None:
                    out.append(holdup)
        return out

    def _snatch(self, state, ta, tb, now, is_night):
        """E6-E9 committed after the E24 grace period."""
        pending = state.pending
        if pending is None or now < pending["decide_at"]:
            return None
        if not state.can_emit("snatch", now):
            state.pending = None
            return None
        if not self._off("e24") and state.is_greeting():
            self._bump("SUPPRESSED_GREETING")
            state.mark_emitted("snatch", now)
            return None

        cues, abstained, mult = {}, set(), {}
        self._add_cue(cues, abstained, "E7", [ta, tb], now, "separation burst")
        if state.divergence is not None and state.divergence > DIVERGENCE_DEG:
            self._add_cue(cues, abstained, "E8", [ta, tb], now, "heading divergence")
        start, end = pending["window"]
        flip = self.anchors.custody_flip(ta.id, tb.id, start, end, now)
        if flip is None:
            flip = self.anchors.custody_flip(tb.id, ta.id, start, end, now)
        if flip is not None:
            self._add_cue(cues, abstained, "E9", [ta, tb], now, "custody transfer")
        self._apply_multipliers(mult, [ta.id, tb.id], now, is_night)

        state.mark_emitted("snatch", now)
        if not cues:
            return None
        runner = state.runner or ta
        detail = (f"snatch pattern: contact {pending['dwell']:.1f}s then "
                  f"asymmetric separation (person #{runner.id} away)")
        if flip is not None:
            detail += f", {flip.label} changed hands"
        return self._evidence("snatch", runner.box, cues, mult, abstained,
                              [ta.id, tb.id], detail)

    def _holdup(self, state, ta, tb, now, threats, is_night):
        """E10-E14 — loiter, rapid close, freeze, optionally armed."""
        if not state.freeze_ok or self._off("e12"):
            return None
        if not state.can_emit("holdup", now):
            return None

        cues, abstained, mult = {}, set(), {}
        self._add_cue(cues, abstained, "E12", [ta, tb], now, "confrontation freeze")

        loiterer = None
        for t in (ta, tb):
            eff = t.path_efficiency(now)
            if (eff is not None and eff < EFF_LOITER
                    and t.age(now) >= LOITER_SECONDS):
                loiterer = t
                break
        if loiterer is not None:
            self._add_cue(cues, abstained, "E10", [loiterer], now, "loiter")

        armed = self._weapon_on(ta, threats) or self._weapon_on(tb, threats)
        if armed is not None:
            self._add_cue(cues, abstained, "E14", [ta, tb], now, "weapon")

        self._apply_multipliers(mult, [ta.id, tb.id], now, is_night)
        if not cues:
            return None
        state.mark_emitted("holdup", now)
        detail = (f"holdup pattern: person #{ta.id} and #{tb.id} stationary at "
                  f"arm's length for {FREEZE_SECONDS:.0f}s+")
        if loiterer is not None:
            detail += f" after person #{loiterer.id} loitered"
        if armed is not None:
            detail += f", {armed} visible"
        return self._evidence("holdup", ta.box, cues, mult, abstained,
                              [ta.id, tb.id], detail)

    # ---- E15-E20 ----

    def _carnapping_evidence(self, tracks, now, is_night):
        out = []
        for anchor, track, found in self.anchors.carnapping_cues(
                tracks, self.baseline, now, is_night):
            cues, abstained, mult = {}, set(), {}
            for rule in found:
                self._add_cue(cues, abstained, rule, [track], now, found[rule])
            # E19 — requires an appearance re-identification embedding that is
            # not present in the pipeline. The module is designed to remain
            # functional with this cue abstaining; the plumbing is here so a
            # re-ID model can be dropped in without touching the scoring.
            if anchor.displacement() > DEPART_DISPLACE and not self._off("e19"):
                abstained.add("E19")
                self._bump("abstained (E19): no re-ID embedding available")
            self._apply_multipliers(mult, [track.id], now, is_night)
            if not cues or not anchor.should_emit("carnapping", cues, now):
                continue
            anchor.mark_emitted("carnapping", cues, now)
            reasons = "; ".join(found[r] for r in sorted(found))
            detail = (f"carnapping pattern: unattended {anchor.label} "
                      f"(anchor #{anchor.id}) — {reasons}")
            out.append(self._evidence("carnapping", anchor.box, cues, mult,
                                      abstained, [track.id], detail))
        return out

    # ---- E21-E22 ----

    def _property_evidence(self, tracks, now, is_night):
        out = []
        for anchor, track, found in self.anchors.property_cues(tracks, now):
            cues, abstained, mult = {}, set(), {}
            for rule in found:
                self._add_cue(cues, abstained, rule, [track], now, found[rule])
            self._apply_multipliers(mult, [track.id], now, is_night)
            if not cues or not anchor.should_emit("property", cues, now):
                continue
            anchor.mark_emitted("property", cues, now)
            detail = (f"unattended property taken: {anchor.label} "
                      f"(anchor #{anchor.id}) by person #{track.id}")
            out.append(self._evidence("property", anchor.box, cues, mult,
                                      abstained, [track.id], detail))
        return out

    # ---- E14 standalone ----

    def _weapon_on(self, track, threats):
        """The weapon label on or within WEAPON_REACH of this track, if any."""
        if self._off("e14"):
            return None
        for det in threats:
            label = det[5]
            if label not in ("gun", "knife"):
                continue
            if _person_norm_distance(track.box, det[:4]) <= WEAPON_REACH:
                return label
        return None

    def _weapon_evidence(self, tracks, threats, now, is_night):
        """E14 on its own — a weapon that is not already part of a holdup.

        The dwell requirement is waived: latency matters more than precision
        here, and the cost of a false positive is absorbed by the human
        verification step.
        """
        out = []
        for t in tracks:
            label = self._weapon_on(t, threats)
            if label is None:
                continue
            last = self._weapon_emitted.get(t.id)
            if last is not None and now - last < EVIDENCE_REEMIT_SECONDS:
                continue
            cues, abstained, mult = {}, set(), {}
            self._add_cue(cues, abstained, "E14", [t], now, "weapon")
            self._apply_multipliers(mult, [t.id], now, is_night)
            if not cues:
                continue
            self._weapon_emitted[t.id] = now
            out.append(self._evidence(
                "weapon", t.box, cues, mult, abstained, [t.id],
                f"weapon visible: {label} on person #{t.id}",
            ))
        return out

    def _evidence(self, *args):
        """Builds Evidence with this engine's band policy applied."""
        return Evidence(*args, weapon_alone_alerts=self.weapon_alone_alerts)

    # ---- E13 / E20 multipliers ----

    def _apply_multipliers(self, mult, track_ids, now, is_night):
        if not self._off("e13"):
            for tid in track_ids:
                others = {o for ts, target, o in self._converge_log
                          if target == tid and now - ts <= CONVERGE_WINDOW}
                if len(others) >= CONVERGE_MIN:
                    mult["E13"] = MULTIPLIERS["E13"]
                    self._bump("multiplier:E13 group convergence")
                    break
        if is_night and not self._off("e20"):
            mult["E20"] = MULTIPLIERS["E20"]

    def _prune(self, now):
        while self._converge_log and now - self._converge_log[0][0] > CONVERGE_WINDOW:
            self._converge_log.popleft()
        stale = [k for k, s in self.pairs.items()
                 if now - s.last_update > EVIDENCE_WINDOW]
        for k in stale:
            del self.pairs[k]
