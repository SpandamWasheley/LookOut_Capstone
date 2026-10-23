"""v6.2 location-aware object momentum + the smoking puff gate (docs/specs/V6_2_LOCATION_MOMENTUM.md).

Object momentum (momentum.py) used to rise whenever a Cigarette / Bottle was detected *anywhere* on a
person's box, so a lamp post, a phone or a pen at the hip fed the cue exactly like a cigarette in a
hand. Here a detection only adds momentum when it is where a held object can be: at a wrist or at the
mouth. Everything else (chest, legs, a sudden jump, a class flip, no keypoints) adds nothing and the
slot decays normally.

Pure CV plumbing: no Django models, no wall clock. Every time value is the frame/video clock the
callers already pass in. All tuning values live in the one constants block below.
"""
import math
from collections import deque
from statistics import median

from core.vision import recognition

# --- v6.2 location-aware momentum --------------------------------------------------------------
LOCATION_AWARE_MOMENTUM = True      # master switch; False = behaviour exactly as before v6.2
GATED_CLASSES = ("cigarette", "bottle")   # lower-case class names that need a location (knife is untouched)

KP_CONF_MIN = 0.3                   # a keypoint below this confidence is "missing"
KP_MAX_AGE_SECONDS = 0.05           # keypoints must belong to THIS frame (pose runs every processed frame)
POSE_MATCH_IOU = 0.3                # pose box <-> person track box

CLOSE_SHOULDER_FRAC = 0.5           # "close" = within this x shoulder width of an anchor
CLOSE_HEIGHT_FRAC_NO_SHOULDERS = 0.15   # ... or this x person box height when a shoulder is missing

JUMP_HEIGHT_FRAC = 0.25             # a hit that moved more than this x person box height is a jump
JUMP_MEMORY_SECONDS = 2.0           # the last hit only counts as a reference for this long (then re-lock)
CONF_FULL_AT = 0.6                  # momentum increment x min(1, conf / CONF_FULL_AT)
CLASS_FLIP_FRAMES = 5               # same spot seen as a different class within this many frames
CLASS_FLIP_IOU = 0.3

# smoking puff gate
PUFF_ARM_HOLD_SECONDS = 3.0         # cigarette seen at a wrist -> the wrist has this long to reach the mouth
PUFF_MIN_GAP_SECONDS = 1.0          # two puffs closer than this are one
POSSIBLE_PUFF_WINDOW_SECONDS = 10.0 # Possible needs >= POSSIBLE_MIN_PUFFS puff in this window (else capped at Monitoring)
POSSIBLE_MIN_PUFFS = 1
LIKELY_PUFF_WINDOW_SECONDS = 30.0   # Likely also needs >= LIKELY_MIN_PUFFS puffs in this window
LIKELY_MIN_PUFFS = 2
CAP_NO_PUFF_TEXT = "capped: no puff"
CAP_FEW_PUFFS_TEXT = "capped: needs 2 puffs in 30 s for Likely"

# drinking Path B (inuman cluster)
GROUP_AREA_EXPAND = 1.2             # group area = cluster box scaled by this (20% larger)
STATIONARY_HEIGHT_FRAC = 0.10       # stationary = moved less than this x median member box height since its last hit
STATIONARY_MEMORY_SECONDS = 2.0     # how long a bottle's previous hit stays a reference
# ------------------------------------------------------------------------------------------------

REASON_OFF_ANCHOR = "off-anchor"
REASON_JUMP = "jump"
REASON_NO_KP = "no-kp"
REASON_FLIP = "flip"

LEFT_WRIST, RIGHT_WRIST = "lw", "rw"
WRISTS = (LEFT_WRIST, RIGHT_WRIST)
MOUTH = "mouth"


# ---- small geometry ---------------------------------------------------------------------------

def _center(box):
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def weighted_confidence(conf):
    """Momentum increment for a hit: confidence, scaled down when it is below CONF_FULL_AT."""
    conf = float(conf)
    return conf * min(1.0, conf / CONF_FULL_AT)


# ---- frame clock + class-flip memory -----------------------------------------------------------
# Both engines (smoking, drinking) feed their raw detections in here, so a spot that one engine sees
# as a Cigarette and the other as a Bottle can be recognised as a flip.

_SEQ = {"now": None, "n": 0}
_RECENT = deque(maxlen=400)          # (frame_number, box, class)


def frame_number(now):
    """A running frame counter derived from the video clock (one step per distinct timestamp)."""
    if now != _SEQ["now"]:
        _SEQ["now"] = now
        _SEQ["n"] += 1
    return _SEQ["n"]


def note_detections(now, dets):
    n = frame_number(now)
    for d in dets:
        _RECENT.append((n, tuple(d[:4]), str(d[5]).lower()))


def class_flipped(now, box, cls):
    n = frame_number(now)
    for n2, b2, c2 in _RECENT:
        if c2 != cls and 0 <= n - n2 <= CLASS_FLIP_FRAMES and _iou(box, b2) > CLASS_FLIP_IOU:
            return True
    return False


def reset():
    """Forget all module-level memory (tests, or between clips)."""
    _SEQ["now"], _SEQ["n"] = None, 0
    _RECENT.clear()
    _POSE["now"], _POSE["poses"], _POSE["fails"] = None, None, 0


# ---- shared pose pass --------------------------------------------------------------------------
# One whole-frame pose inference per processed frame, shared by the smoking engine's hand-to-mouth
# counter and by both engines' location gate.

_POSE = {"now": None, "poses": None, "fails": 0}
POSE_MAX_FAILS = 3


def shared_poses(frame, now):
    """[(box, keypoints 17x3)] for this frame, computed once per timestamp. Raises like
    recognition.detect_pose does when the pose model fails (callers already handle that)."""
    if _POSE["now"] == now and _POSE["poses"] is not None:
        return _POSE["poses"]
    poses = recognition.detect_pose(frame)
    _POSE["now"], _POSE["poses"] = now, poses
    return poses


# ---- per-track state ---------------------------------------------------------------------------

class TrackLoc:
    """Location-gate memory of one person track (in memory only, dies with the track)."""

    def __init__(self):
        self.kp = None
        self.kp_at = None
        self.last_hit = {}            # class -> (rel_x, rel_y, kinds, t)
        self.puffs = deque()          # video times of completed puffs
        self.armed = {}               # wrist id -> time a cigarette was last seen at it (outside the mouth zone)


def loc_of(track):
    loc = getattr(track, "loc", None)
    if loc is None:
        loc = track.loc = TrackLoc()
    return loc


def ensure_keypoints(cmd, track, now):
    """Keypoints of `track` for THIS frame, or None. Uses the shared pose pass (one inference per frame)."""
    loc = loc_of(track)
    if loc.kp is not None and loc.kp_at is not None and abs(now - loc.kp_at) <= KP_MAX_AGE_SECONDS:
        return loc.kp
    frame = getattr(cmd, "_gate_frame", None)
    if frame is None or track.box is None or _POSE["fails"] >= POSE_MAX_FAILS:
        return None
    try:
        poses = shared_poses(frame, now)
    except Exception:                                   # noqa: BLE001 - pose is optional evidence
        _POSE["fails"] += 1
        return None
    best, best_iou = None, 0.0
    for box, kpts in poses:
        iou = _iou(track.box, box)
        if iou > best_iou:
            best, best_iou = kpts, iou
    if best is not None and best_iou >= POSE_MATCH_IOU:
        loc.kp, loc.kp_at = best, now
        return best
    return None


def anchors_from_kpts(kpts, person_box):
    """{'lw','rw','mouth': (x, y), 'close': pixels} from whole-frame keypoints, or None when no anchor
    is confidently visible. 'close' = CLOSE_SHOULDER_FRAC x shoulder width (CLOSE_HEIGHT_FRAC_NO_SHOULDERS
    x person height when a shoulder is missing)."""
    if kpts is None:
        return None
    out = {}
    for name, idx in ((LEFT_WRIST, recognition.KP_LWRIST), (RIGHT_WRIST, recognition.KP_RWRIST)):
        k = kpts[idx]
        if k[2] >= KP_CONF_MIN:
            out[name] = (float(k[0]), float(k[1]))
    nose = kpts[recognition.KP_NOSE]
    if nose[2] >= KP_CONF_MIN:
        try:
            face_w, _ = recognition.pose_face_width(kpts)
        except Exception:                               # noqa: BLE001
            face_w = None
        if face_w:
            out[MOUTH] = (float(nose[0]), float(nose[1]) + recognition.POSE_MOUTH_DY * face_w)
    if not out:
        return None
    ls, rs = kpts[recognition.KP_LSHOULDER], kpts[recognition.KP_RSHOULDER]
    height = max(person_box[3] - person_box[1], 1)
    shoulder_w = _dist(ls[:2], rs[:2]) if (ls[2] >= KP_CONF_MIN and rs[2] >= KP_CONF_MIN) else 0.0
    out["close"] = (CLOSE_SHOULDER_FRAC * shoulder_w if shoulder_w >= 1
                    else CLOSE_HEIGHT_FRAC_NO_SHOULDERS * height)
    return out


def anchor_kinds(center, anchors):
    """The set of anchors ('lw','rw','mouth') the point is close to."""
    close = anchors["close"]
    return frozenset(k for k in (LEFT_WRIST, RIGHT_WRIST, MOUTH) if k in anchors and _dist(center, anchors[k]) <= close)


def _is_wrist_mouth_move(prev, new):
    return ((prev & set(WRISTS) and MOUTH in new) or (MOUTH in prev and new & set(WRISTS)))


# ---- Path A: one person, one class per frame ---------------------------------------------------

def evaluate_track(cmd, track, dets, now):
    """Location gate for one person's detections this frame.

    Returns ({class: momentum confidence}, why) for the gated classes present in `dets`: the confidence
    is the confidence-weighted best ACCEPTED hit, or 0.0 when every hit of that class was rejected.
    `why` is the short reason tag of a rejected class ('' when something was accepted or nothing gated
    was detected). Classes outside GATED_CLASSES are not touched."""
    per_class = {}
    for d in dets:
        cls = str(d[5]).lower()
        if cls in GATED_CLASSES:
            per_class.setdefault(cls, []).append(d)
    if not per_class:
        return {}, ""
    note_detections(now, [d for ds in per_class.values() for d in ds])
    loc = loc_of(track)
    kp = ensure_keypoints(cmd, track, now)
    anchors = anchors_from_kpts(kp, track.box) if kp is not None else None
    bx1, by1, bx2, by2 = track.box
    bw, bh = max(bx2 - bx1, 1), max(by2 - by1, 1)

    out, why = {}, ""
    for cls, ds in per_class.items():
        best, best_reason, best_conf = None, "", -1.0
        for d in sorted(ds, key=lambda x: -x[4]):
            box = tuple(d[:4])
            c = _center(box)
            reason, kinds = "", frozenset()
            if class_flipped(now, box, cls):
                reason = REASON_FLIP
            elif anchors is None:
                reason = REASON_NO_KP
            else:
                kinds = anchor_kinds(c, anchors)
                if not kinds:
                    reason = REASON_OFF_ANCHOR
                else:
                    last = loc.last_hit.get(cls)
                    if last is not None and now - last[3] <= JUMP_MEMORY_SECONDS:
                        moved = math.hypot(((c[0] - bx1) / bw - last[0]) * bw, ((c[1] - by1) / bh - last[1]) * bh)
                        if moved > JUMP_HEIGHT_FRAC * bh and not _is_wrist_mouth_move(last[2], kinds):
                            reason = REASON_JUMP
            if reason:
                if best is None and d[4] > best_conf:
                    best_reason, best_conf = reason, d[4]
                continue
            if best is None:
                best = (d, c, kinds)
        if best is None:
            out[cls] = 0.0
            why = why or best_reason
            _count(cmd, best_reason)
        else:
            d, c, kinds = best
            out[cls] = weighted_confidence(d[4])
            loc.last_hit[cls] = ((c[0] - bx1) / bw, (c[1] - by1) / bh, kinds, now)
    return out, why


def _count(cmd, reason):
    stats = getattr(cmd, "stats", None)
    if stats is not None and reason:
        stats[f"location gate: {reason}"] += 1


# ---- smoking: the puff ---------------------------------------------------------------------------

def update_puffs(cmd, track, dets, now):
    """Advance the puff detector for one person this frame and return the puff count so far.

    A puff = a cigarette seen at a wrist, then THAT wrist comes within mouth distance. A cigarette held
    still at the mouth is not a puff (the wrist must have been outside the mouth zone when the cigarette
    was seen at it)."""
    loc = loc_of(track)
    cutoff = now - max(LIKELY_PUFF_WINDOW_SECONDS, POSSIBLE_PUFF_WINDOW_SECONDS)
    while loc.puffs and loc.puffs[0] < cutoff:
        loc.puffs.popleft()
    kp = ensure_keypoints(cmd, track, now)
    anchors = anchors_from_kpts(kp, track.box) if kp is not None else None
    if anchors is None:
        return len(loc.puffs)
    cigs = [tuple(d[:4]) for d in dets if str(d[5]).lower() == "cigarette"
            and not class_flipped(now, tuple(d[:4]), "cigarette")]
    close, mouth = anchors["close"], anchors.get(MOUTH)
    for w in WRISTS:
        pos = anchors.get(w)
        if pos is None:
            continue
        in_mouth = mouth is not None and _dist(pos, mouth) <= close
        if not in_mouth and any(_dist(_center(b), pos) <= close for b in cigs):
            loc.armed[w] = now
        elif in_mouth and w in loc.armed and now - loc.armed[w] <= PUFF_ARM_HOLD_SECONDS:
            if not loc.puffs or now - loc.puffs[-1] >= PUFF_MIN_GAP_SECONDS:
                loc.puffs.append(now)
            del loc.armed[w]
        if w in loc.armed and now - loc.armed[w] > PUFF_ARM_HOLD_SECONDS:
            del loc.armed[w]
    return len(loc.puffs)


def puffs_within(track, now, window):
    loc = getattr(track, "loc", None)
    return 0 if loc is None else sum(1 for t in loc.puffs if now - t <= window)


def puff_cap(track, now):
    """(max level name or None, reason text) for a smoking incident WITH an object. Level names are
    scoring's stored values ('monitoring' / 'warning')."""
    if puffs_within(track, now, POSSIBLE_PUFF_WINDOW_SECONDS) < POSSIBLE_MIN_PUFFS:
        return "monitoring", CAP_NO_PUFF_TEXT
    if puffs_within(track, now, LIKELY_PUFF_WINDOW_SECONDS) < LIKELY_MIN_PUFFS:
        return "warning", CAP_FEW_PUFFS_TEXT
    return None, ""


# ---- Path B: a gathering --------------------------------------------------------------------------

def _expanded(box, factor):
    cx, cy = _center(box)
    hw, hh = (box[2] - box[0]) / 2.0 * factor, (box[3] - box[1]) / 2.0 * factor
    return cx - hw, cy - hh, cx + hw, cy + hh


def cluster_confidence(cmd, cluster, member_dets, scene_dets, now):
    """Bottle momentum input for a gathering this frame: (weighted confidence of the best accepted
    bottle or 0.0, reason tag of the rejection or '').

    member_dets  [(track, det)] bottles on group members' boxes
    scene_dets   [det] bottles outside every box (the table) near the group
    A bottle is accepted when it is (a) close to a wrist/mouth of a member -- same rule as Path A --
    or (b) stationary (moved < STATIONARY_HEIGHT_FRAC x the members' median box height since its last
    hit) inside the group area expanded by GROUP_AREA_EXPAND. Going from one to the other (picked up,
    set down, passed) is never a jump."""
    allr = [(t, d) for t, d in member_dets if str(d[5]).lower() in GATED_CLASSES]
    allr += [(None, d) for d in scene_dets if str(d[5]).lower() in GATED_CLASSES]
    note_detections(now, [d for _, d in allr])
    area = _expanded(cluster.bbox, GROUP_AREA_EXPAND)
    heights = [max(t.box[3] - t.box[1], 1) for t, _ in member_dets if t is not None and t.box is not None]
    med_h = median(heights) if heights else max(cluster.bbox[3] - cluster.bbox[1], 1)
    prev = [h for h in getattr(cluster, "loc_hits", []) if 0 < now - h[2] <= STATIONARY_MEMORY_SECONDS]
    kept = [h for h in getattr(cluster, "loc_hits", []) if now - h[2] <= STATIONARY_MEMORY_SECONDS]
    anchors_of = {}
    best_conf, reason = 0.0, ""
    for t, d in allr:
        box = tuple(d[:4])
        c = _center(box)
        inside = area[0] <= c[0] <= area[2] and area[1] <= c[1] <= area[3]
        if inside:
            kept.append((c[0], c[1], now))
        if class_flipped(now, box, str(d[5]).lower()):
            reason = reason or REASON_FLIP
            continue
        accepted = False
        if t is not None:
            if t.id not in anchors_of:
                kp = ensure_keypoints(cmd, t, now)
                anchors_of[t.id] = anchors_from_kpts(kp, t.box) if kp is not None else None
            an = anchors_of[t.id]
            if an is not None and anchor_kinds(c, an):
                accepted = True
        if not accepted and inside and any(_dist(c, (h[0], h[1])) < STATIONARY_HEIGHT_FRAC * med_h for h in prev):
            accepted = True
        if accepted:
            best_conf = max(best_conf, weighted_confidence(d[4]))
        else:
            reason = reason or (REASON_NO_KP if (t is not None and anchors_of.get(t.id) is None) else REASON_OFF_ANCHOR)
    cluster.loc_hits = kept
    if best_conf <= 0.0 and allr:
        _count(cmd, reason)
    return best_conf, ("" if best_conf > 0 else reason)
