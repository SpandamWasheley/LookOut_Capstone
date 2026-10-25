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
ZOOM_POSE = True                    # v6.2.6: far people get keypoints from a pose pass on their own (upscaled) crop
ZOOM_POSE_BELOW_FRAC = 0.4          # ... when the person box is shorter than this x the frame height (or the whole-frame pass missed them)

CLOSE_SHOULDER_FRAC = 0.5           # "close" = within this x shoulder width of an anchor
CLOSE_HEIGHT_FRAC_NO_SHOULDERS = 0.15   # ... or this x person box height when a shoulder is missing

JUMP_HEIGHT_FRAC = 0.25             # a hit that moved more than this x person box height is a jump
JUMP_MEMORY_SECONDS = 2.0           # the last hit only counts as a reference for this long (then re-lock)
CONF_FULL_AT = 0.6                  # momentum increment x min(1, conf / CONF_FULL_AT)
CLASS_FLIP_FRAMES = 5               # same spot seen as a different class within this many frames
CLASS_FLIP_IOU = 0.3

# smoking puff gate (v6.2.1: a puff = the wrist reaches the mouth AND a cigarette on the same person is
# seen at that wrist or at the mouth within +/- PUFF_PAIR_WINDOW_SECONDS, in either order)
PUFF_PAIR_WINDOW_SECONDS = 5.0      # was 3 s one-way in v6.2
PUFF_CLOSE_SHOULDER_FRAC = 0.7      # "wrist at the mouth" / "cigarette at the mouth" radius x shoulder width (the momentum gate keeps 0.5)
ELBOW_FALLBACK = True               # v6.2.6: a missing wrist keypoint falls back to its elbow for the wrist-at-mouth test
PUFF_ELBOW_SHOULDER_FRAC = 1.1      # elbow-to-mouth radius x shoulder width (measured: ~0.9-1.1 with the hand at the mouth, median 1.5 hanging)
PUFF_MIN_GAP_SECONDS = 1.0         # two wrist-to-mouth reaches closer than this are one
# far-range puff from the cigarette's own movement (v6.2.8). When pose cannot resolve the wrist / mouth (the person is small, or the
# nose / wrists are under KP_CONF_MIN), a puff is counted from where the CIGARETTE is inside the person box: seen in the head zone and,
# within FAR_PUFF_PAIR_SECONDS before or after, seen lower in the chest / hand zone. It feeds the same puff gate and nothing else.
FAR_PUFF_FROM_OBJECT = True
FAR_PUFF_BELOW_FRAC = 0.25          # person box shorter than this x frame height -> use the fallback
FAR_PUFF_HEAD_ZONE = 0.22           # head zone = top this fraction of the person box (0 = top, 1 = bottom)
FAR_PUFF_BODY_ZONE = (0.30, 0.70)   # chest / hand zone
FAR_PUFF_PAIR_SECONDS = 4.0         # head and body sightings must be this close in time (either order)
FAR_PUFF_MIN_GAP_SECONDS = 2.0      # at most one object puff per track per this long
# near_mouth is "held for cue_hold seconds after it last fired", but it was only evaluated while the object cue was ON, so a cigarette
# seen at the mouth BEFORE the cue finished confirming (ON for object_confirm_seconds) was never remembered, and the status came up
# without it (Monitoring 0.40 instead of Possible 0.55) just as the cigarette left view. True = evaluate it every frame so the hold works.
NEAR_MOUTH_HELD_BEFORE_CONFIRM = True
POSSIBLE_PUFF_WINDOW_SECONDS = 10.0 # Possible needs >= POSSIBLE_MIN_PUFFS puff in this window (else capped at Monitoring)
POSSIBLE_MIN_PUFFS = 1
LIKELY_PUFF_WINDOW_SECONDS = 30.0   # Likely also needs >= LIKELY_MIN_PUFFS puffs in this window
LIKELY_MIN_PUFFS = 2
CAP_NO_PUFF_TEXT = "capped: no puff"
CAP_FEW_PUFFS_TEXT = "capped: needs 2 puffs in 30 s for Likely"

# holdup (v6.2.1, reworked in v6.2.5). True = knife stability rules, E12 as "stayed close" with pass-through rejection, the
# "knife + E12" gate (capped at Monitoring without it) and the sticky holdup status. False = the pre-v6.2 (v6) holdup rules.
HOLDUP_V621_RULES = True
KNIFE_CLASS = "knife"
KNIFE_CONF_FULL_AT = 0.6            # knife momentum increment x min(1, conf / this)
KNIFE_JUMP_HEIGHT_FRAC = 0.25       # same jump rule as the cigarette (no anchors: any move this big is a jump)
KNIFE_JUMP_MEMORY_SECONDS = 2.0
KNIFE_CLASS_FLIP_FRAMES = 5
KNIFE_PRESENT_SECONDS = 2.0         # a knife counts as present for E14 if an accepted hit is at most this old (v6.2.4: 1.0)
# E12 = "stayed close" (v6.2.5): the two people within the close distance for CLOSE_STAY_SECONDS of video time. No speed
# requirement, and overlapping boxes count as close. A walk-past is rejected (see PASS_*).
CLOSE_DISTANCE = 1.0                # "close" for E12, in person heights, NOT widened in a crowd (the legacy E23 gate widening made
                                    # two scooters / walkers 1.0-1.3 heights apart count as close in the Kabilang clip)
CO_WALKERS_REJECTED = True          # two people who BOTH keep walking (median speed > PASS_MOVING_SPEED over the last CLOSE_STAY_SECONDS) are
                                    # walking together, not a standoff: no E12. One of them must be slow. (Kabilang: two pedestrians
                                    # walking side by side got E12 + a knife hallucination = a false Possible.)
CLOSE_STAY_SECONDS = 2.0
CLOSE_GAP_TOLERANCE_SECONDS = 0.3   # a break shorter than this (tracker jitter on the close-distance line) does not end the stay
PASS_MOVING_SPEED = 0.3             # normalised speed above which a person is "moving" (same number as theft.FREEZE_SPEED)
PASS_BEFORE_WINDOW_SECONDS = 1.0    # speed is judged over this long before the closest approach, and from it to now
PASS_SEPARATE_SECONDS = 2.0         # pass-through = someone moving before AND after the closest approach, and the pair is back
                                    # outside the close distance within this long of it. E12 waits this long to be sure.
CAP_NO_FROZEN_PAIR_TEXT = "capped: no frozen pair"

# holdup pairs: shadow filter (v6.2.4; the skeleton / keypoint requirement was removed in v6.2.5 because it blocked real holdups).
# A pair is ignored when it is a person and their own shadow.
FAKE_PERSON_FILTER = True
SHADOW_FEET_TOUCH_FRAC = 0.15       # bottom edges within this x the taller box height, and the boxes touching sideways
SHADOW_BRIGHTNESS_RATIO = 0.6       # person + own shadow: one box's mean brightness below this x the other's
SHADOW_HEIGHT_RATIO = 0.65          # ... OR (v6.2.5 addition, 0 = off): the shorter box is below this x the taller one's height. On the
                                    # ALT-0152 footage the shadow was only 58-90 % as bright (never < 60 %) but 50-63 % as tall.

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
ELBOWS = {LEFT_WRIST: 7, RIGHT_WRIST: 8}            # COCO keypoint index of the elbow on the same arm
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


def _class_flipped_within(now, box, cls, frames):
    n = frame_number(now)
    for n2, b2, c2 in _RECENT:
        if c2 != cls and 0 <= n - n2 <= frames and _iou(box, b2) > CLASS_FLIP_IOU:
            return True
    return False


def class_flipped(now, box, cls):
    return _class_flipped_within(now, box, cls, CLASS_FLIP_FRAMES)


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
        self.kp_source = None         # "full" (whole-frame pose) or "zoom" (pose on the person's crop)
        self.last_hit = {}            # class -> (rel_x, rel_y, kinds, t)
        self.puffs = deque()          # video times of confirmed puffs
        self.cig_at_wrist = {}        # wrist id -> last time a cigarette was seen at that wrist
        self.cig_at_mouth = None      # last time a cigarette was seen at the mouth
        self.in_zone = {}             # wrist id -> was the wrist at the mouth last frame (absent = unknown)
        self.reaches = {}             # wrist id -> time of a wrist-to-mouth reach still waiting for a cigarette sighting
        self.last_reach = None
        self.head_seen = None         # last time a cigarette was in the head zone (far-range object puff)
        self.body_seen = None         # last time a cigarette was in the chest / hand zone
        self.last_object_puff = None
        self.n_object_puffs = 0
        self.n_pose_puffs = 0


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
        loc.kp_source = "full"
    else:
        best = None
    # v6.2.6 zoomed pose: a far person is only ~35 px across the shoulders in the whole frame, where pose puts the hands
    # in the wrong place (or loses the person). Re-run pose on that person's crop, scaled up, and use it instead.
    if ZOOM_POSE and (best is None or frame.shape[0] * ZOOM_POSE_BELOW_FRAC > track.box[3] - track.box[1]):
        try:
            zoomed = recognition.pose_on_box(frame, track.box)
        except Exception:                               # noqa: BLE001 - pose is optional evidence
            zoomed = None
        if zoomed is not None:
            best, loc.kp_source = zoomed, "zoom"
    if best is None:
        return None
    loc.kp, loc.kp_at = best, now
    return best


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
    out["has_shoulders"] = shoulder_w >= 1
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
        if cls in GATED_CLASSES or (cls == KNIFE_CLASS and holdup_rules_on()):
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
        if cls == KNIFE_CLASS:
            conf_k, reason_k = _evaluate_knife(loc, track, ds, now)
            out[cls] = conf_k
            if reason_k:
                why = why or reason_k
                _count(cmd, reason_k)
            continue
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


def _evaluate_knife(loc, track, ds, now):
    """Holdup (v6.2.1): the knife keeps its near-hand rule (applied before this) and gets confidence
    weighting, the class-flip check and the jump check, but NO wrist/mouth anchoring. Returns
    (weighted confidence or 0.0, rejection reason)."""
    bx1, by1, bx2, by2 = track.box
    bw, bh = max(bx2 - bx1, 1), max(by2 - by1, 1)
    best, best_reason, best_conf = None, "", -1.0
    for d in sorted(ds, key=lambda x: -x[4]):
        box = tuple(d[:4])
        c = _center(box)
        reason = ""
        if _class_flipped_within(now, box, KNIFE_CLASS, KNIFE_CLASS_FLIP_FRAMES):
            reason = REASON_FLIP
        else:
            last = loc.last_hit.get(KNIFE_CLASS)
            if last is not None and now - last[3] <= KNIFE_JUMP_MEMORY_SECONDS:
                moved = math.hypot(((c[0] - bx1) / bw - last[0]) * bw, ((c[1] - by1) / bh - last[1]) * bh)
                if moved > KNIFE_JUMP_HEIGHT_FRAC * bh:
                    reason = REASON_JUMP
        if reason:
            if best is None and d[4] > best_conf:
                best_reason, best_conf = reason, d[4]
            continue
        if best is None:
            best = (d, c)
    if best is None:
        return 0.0, best_reason
    d, c = best
    loc.last_hit[KNIFE_CLASS] = ((c[0] - bx1) / bw, (c[1] - by1) / bh, frozenset(), now)
    return min(1.0, float(d[4]) / KNIFE_CONF_FULL_AT) * float(d[4]), ""


# ---- holdup pairs: fake-person filter ----------------------------------------------------------

CURRENT_FRAME = {"frame": None}      # set by the thief engine's per-frame hook; the shadow test reads brightness from it


def fake_person_filter_on():
    return LOCATION_AWARE_MOMENTUM and FAKE_PERSON_FILTER


def _mean_brightness(frame, box):
    x1, y1, x2, y2 = (max(int(v), 0) for v in box)
    roi = frame[y1:max(y2, y1 + 1), x1:max(x2, x1 + 1)]
    if roi.size == 0:
        return None
    import cv2
    return float(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).mean()) if roi.ndim == 3 else float(roi.mean())


def is_shadow_pair(ta_box, tb_box, frame=None):
    """Person + own shadow: the two boxes touch at the feet (bottom edges within SHADOW_FEET_TOUCH_FRAC of the taller
    box, boxes touching sideways) AND either one box is much darker than the other (mean brightness <
    SHADOW_BRIGHTNESS_RATIO x) or one is much shorter (height < SHADOW_HEIGHT_RATIO x)."""
    hmax = max(ta_box[3] - ta_box[1], tb_box[3] - tb_box[1], 1)
    feet = abs(ta_box[3] - tb_box[3]) <= SHADOW_FEET_TOUCH_FRAC * hmax
    side = max(ta_box[0], tb_box[0]) <= min(ta_box[2], tb_box[2])
    if not (feet and side):
        return False
    hmin = min(ta_box[3] - ta_box[1], tb_box[3] - tb_box[1])
    if SHADOW_HEIGHT_RATIO and hmin < SHADOW_HEIGHT_RATIO * hmax:
        return True                                    # same foot line, much shorter: a shadow on the wall, not a person
    frame = CURRENT_FRAME["frame"] if frame is None else frame
    if frame is None:
        return False
    ba, bb = _mean_brightness(frame, ta_box), _mean_brightness(frame, tb_box)
    if not ba or not bb:
        return False
    return min(ba, bb) < SHADOW_BRIGHTNESS_RATIO * max(ba, bb)


def holdup_rules_on():
    """True when the v6.2.1 holdup rules apply (needs both switches)."""
    return LOCATION_AWARE_MOMENTUM and HOLDUP_V621_RULES


def knife_recent(track, now):
    """True when an ACCEPTED knife hit on this person is at most KNIFE_PRESENT_SECONDS old."""
    loc = getattr(track, "loc", None)
    last = None if loc is None else loc.last_hit.get(KNIFE_CLASS)
    return last is not None and 0 <= now - last[3] <= KNIFE_PRESENT_SECONDS


def _count(cmd, reason):
    stats = getattr(cmd, "stats", None)
    if stats is not None and reason:
        stats[f"location gate: {reason}"] += 1


# ---- smoking: the puff ---------------------------------------------------------------------------

def update_puffs(cmd, track, dets, now):
    """Advance the puff detector for one person this frame and return the puff count so far.

    v6.2.1 strict puff: the wrist reaches the mouth anchor (within PUFF_CLOSE_SHOULDER_FRAC x shoulder
    width) AND a cigarette on the same person is seen at that wrist, or at the mouth, within
    +/- PUFF_PAIR_WINDOW_SECONDS of the reach -- either order. A wrist already at the mouth when the
    person is first seen is not a reach (it must have been seen away from the mouth first)."""
    loc = loc_of(track)
    cutoff = now - max(LIKELY_PUFF_WINDOW_SECONDS, POSSIBLE_PUFF_WINDOW_SECONDS)
    while loc.puffs and loc.puffs[0] < cutoff:
        loc.puffs.popleft()
    kp = ensure_keypoints(cmd, track, now)
    # v6.2.8: far / unresolvable person -> count a puff from where the cigarette sits inside the person box
    if FAR_PUFF_FROM_OBJECT and _object_puff_applies(cmd, track, kp):
        _update_object_puff(loc, track, dets, now)
    anchors = anchors_from_kpts(kp, track.box) if kp is not None else None
    if anchors is None:
        _expire_reaches(loc, now)
        return len(loc.puffs)
    close_wrist = anchors["close"]                      # 0.5 x shoulder width (the momentum rule)
    close_mouth = (anchors["close"] * (PUFF_CLOSE_SHOULDER_FRAC / CLOSE_SHOULDER_FRAC)
                   if anchors.get("has_shoulders") else anchors["close"])
    mouth = anchors.get(MOUTH)
    cigs = [tuple(d[:4]) for d in dets if str(d[5]).lower() == "cigarette"
            and not class_flipped(now, tuple(d[:4]), "cigarette")]
    for w in WRISTS:
        pos = anchors.get(w)
        if pos is not None and any(_dist(_center(b), pos) <= close_wrist for b in cigs):
            loc.cig_at_wrist[w] = now
    if mouth is not None and any(_dist(_center(b), mouth) <= close_mouth for b in cigs):
        loc.cig_at_mouth = now
    shoulder_w = anchors["close"] / CLOSE_SHOULDER_FRAC if anchors.get("has_shoulders") else 0.0
    for w in WRISTS:
        pos = anchors.get(w)
        if mouth is None:
            continue
        if pos is not None:
            in_zone = _dist(pos, mouth) <= close_mouth
        else:
            # v6.2.6 elbow fallback: this wrist's keypoint is missing, but its elbow is visible -- an elbow within
            # PUFF_ELBOW_SHOULDER_FRAC x shoulder width of the mouth means the forearm is folded up to the face.
            elbow = kp[ELBOWS[w]] if (ELBOW_FALLBACK and shoulder_w >= 1) else None
            if elbow is None or elbow[2] < KP_CONF_MIN:
                continue                                # unknown this frame: keep the previous zone state
            in_zone = _dist((float(elbow[0]), float(elbow[1])), mouth) <= PUFF_ELBOW_SHOULDER_FRAC * shoulder_w
        was = loc.in_zone.get(w)
        loc.in_zone[w] = in_zone
        if in_zone and was is False and (loc.last_reach is None or now - loc.last_reach >= PUFF_MIN_GAP_SECONDS):
            loc.reaches[w] = now
            loc.last_reach = now
    for w, t_reach in list(loc.reaches.items()):
        seen = [t for t in (loc.cig_at_wrist.get(w), loc.cig_at_mouth) if t is not None]
        if any(abs(t - t_reach) <= PUFF_PAIR_WINDOW_SECONDS for t in seen):
            loc.puffs.append(now)
            loc.n_pose_puffs += 1
            del loc.reaches[w]
    _expire_reaches(loc, now)
    return len(loc.puffs)


def _frame_height(cmd):
    frame = getattr(cmd, "_gate_frame", None)
    if frame is not None:
        return frame.shape[0]
    return getattr(cmd, "_frame_height", None)


def _object_puff_applies(cmd, track, kp):
    """The object-based puff is used only when pose cannot be trusted for this person: the box is shorter than
    FAR_PUFF_BELOW_FRAC of the frame, or the nose / both wrists are under KP_CONF_MIN (or there are no keypoints)."""
    h = _frame_height(cmd)
    if h and track.box is not None and (track.box[3] - track.box[1]) < FAR_PUFF_BELOW_FRAC * h:
        return True
    if kp is None:
        return True
    return kp[recognition.KP_NOSE][2] < KP_CONF_MIN or all(
        kp[i][2] < KP_CONF_MIN for i in (recognition.KP_LWRIST, recognition.KP_RWRIST))


def _update_object_puff(loc, track, dets, now):
    """One puff = the cigarette on this track seen in the head zone and, within FAR_PUFF_PAIR_SECONDS before or after, seen in the
    chest / hand zone. Both sightings are consumed by a puff; at most one per FAR_PUFF_MIN_GAP_SECONDS."""
    bx1, by1, bx2, by2 = track.box
    bh = max(by2 - by1, 1)
    for d in dets:
        if str(d[5]).lower() != "cigarette" or class_flipped(now, tuple(d[:4]), "cigarette"):
            continue
        rel_y = (_center(tuple(d[:4]))[1] - by1) / bh
        if rel_y <= FAR_PUFF_HEAD_ZONE:
            loc.head_seen = now
        elif FAR_PUFF_BODY_ZONE[0] <= rel_y <= FAR_PUFF_BODY_ZONE[1]:
            loc.body_seen = now
    if loc.head_seen is None or loc.body_seen is None:
        return
    if abs(loc.head_seen - loc.body_seen) > FAR_PUFF_PAIR_SECONDS or now - max(loc.head_seen, loc.body_seen) > FAR_PUFF_PAIR_SECONDS:
        return
    if loc.last_object_puff is not None and now - loc.last_object_puff < FAR_PUFF_MIN_GAP_SECONDS:
        return
    loc.puffs.append(now)
    loc.n_object_puffs += 1
    loc.last_object_puff = now
    loc.head_seen = loc.body_seen = None                 # consumed: the next puff needs fresh sightings of both


def _expire_reaches(loc, now):
    for w, t_reach in list(loc.reaches.items()):
        if now - t_reach > PUFF_PAIR_WINDOW_SECONDS:
            del loc.reaches[w]


def puffs_within(track, now, window):
    loc = getattr(track, "loc", None)
    return 0 if loc is None else sum(1 for t in loc.puffs if now - t <= window)


def puff_cap(track, now, previous_level=None):
    """(max level name or None, reason text) for a smoking incident WITH an object. Level names are
    scoring's stored values ('monitoring' / 'warning').

    Sticky (v6.2.1): the caps only block the FIRST rise. An incident that already reached Possible is
    never pushed back to Monitoring because the 10 s window ran out, and one that reached Likely keeps it."""
    if puffs_within(track, now, POSSIBLE_PUFF_WINDOW_SECONDS) < POSSIBLE_MIN_PUFFS:
        if previous_level in ("warning", "violation"):
            return None, ""
        return "monitoring", CAP_NO_PUFF_TEXT
    if puffs_within(track, now, LIKELY_PUFF_WINDOW_SECONDS) < LIKELY_MIN_PUFFS:
        if previous_level == "violation":
            return None, ""
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
