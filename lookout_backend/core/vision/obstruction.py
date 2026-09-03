"""Road-edge obstruction: geometry, plus the "how much, for how long" rule.

Canonical implementation. detection_sandbox/obstruction.py executes THIS file
into its own namespace, so the browser tester and the management command run
identical code and cannot drift apart.

A vehicle is judged by the share of its ground footprint lying past a marked
edge, held for a minimum time:

    "at least half the vehicle past the line, for at least five minutes"

The share is a ratio of the vehicle to ITSELF, so it needs no tape measure, no
homography and no per-camera calibration - the same threshold means the same
thing on every camera and at any distance.

The edge is supplied by the operator (two clicks per side at install, more where
the footpath bends). Automatic detection of the painted line was built and
measured, and is deliberately not used: an edge line is defined by function
rather than appearance, so a stripe on a road and one on a court are identical
at pixel level. On this project's night footage the real line scored a
brightness contrast of 46.5 while the kerb beside it scored 35 - too close to
separate once weather and exposure move each by more than that gap. The
experiment lives in detection_sandbox/edge_line.py.

No Django imports here, same rule as recognition.py - this stays pure CV so it
can be unit-tested without a database.
"""

import math

import cv2
import numpy as np

ABOVE, BELOW = "above", "below"


# --- geometry ----------------------------------------------------------------


def build_edge(spec):
    """Builds an oriented edge from {"points": [[x, y], ...], "side": 1|-1}.

    Two points give a straight EdgeLine; three or more give a PolyEdge that
    follows a bend. `side` names which way the protected footpath lies, as the
    drawing tool shaded it, and the edge is oriented by probing a point just off
    its MIDDLE segment - so a bent edge is judged against a part of itself that
    the probe is actually beside.

    Shared by the browser tester and the watch_parking command so an edge drawn
    once means the same thing in both.
    """
    pts = [(float(x), float(y)) for x, y in spec["points"]]
    if len(pts) < 2:
        raise ValueError("an edge needs at least two points")
    edge = EdgeLine(pts[0], pts[1]) if len(pts) == 2 else PolyEdge(pts)

    mid = len(pts) // 2
    (ax, ay), (bx, by) = pts[max(mid - 1, 0)], pts[min(mid, len(pts) - 1)]
    if (ax, ay) == (bx, by):
        (ax, ay), (bx, by) = pts[0], pts[-1]
    dx, dy = bx - ax, by - ay
    length = (dx * dx + dy * dy) ** 0.5 or 1.0
    sign = 1 if spec.get("side", 1) >= 0 else -1
    probe = ((ax + bx) / 2 - dy / length * 60 * sign,
             (ay + by) / 2 + dx / length * 60 * sign)
    edge.protected_side = ABOVE
    if not edge.is_protected(probe):
        edge.protected_side = BELOW
    return edge


class EdgeLine:
    """A fitted line plus which side of it is the protected way.

    Stored as two endpoints spanning the frame, and as the implicit form
    a*x + b*y + c = 0 with (a, b) unit, so `signed_distance` is a true pixel
    distance and its sign names the side.
    """

    def __init__(self, p0, p1, protected_side=ABOVE, support=0.0):
        self.p0 = (float(p0[0]), float(p0[1]))
        self.p1 = (float(p1[0]), float(p1[1]))
        self.protected_side = protected_side
        self.support = support
        dx, dy = self.p1[0] - self.p0[0], self.p1[1] - self.p0[1]
        norm = max((dx * dx + dy * dy) ** 0.5, 1e-6)
        self.a, self.b = -dy / norm, dx / norm
        self.c = -(self.a * self.p0[0] + self.b * self.p0[1])

    def signed_distance(self, point):
        return self.a * point[0] + self.b * point[1] + self.c

    def is_protected(self, point):
        """True when the point lies on the protected (non-road) side."""
        d = self.signed_distance(point)
        return d < 0 if self.protected_side == ABOVE else d > 0

    def draw(self, frame, color=(0, 0, 255), thickness=3):
        cv2.line(frame, (int(self.p0[0]), int(self.p0[1])),
                 (int(self.p1[0]), int(self.p1[1])), color, thickness)

    def __repr__(self):
        return (f"EdgeLine({self.p0}->{self.p1}, "
                f"protected={self.protected_side}, support={self.support:.2f})")


class PolyEdge:
    """A multi-point edge, for a footpath that bends.

    Duck-typed with EdgeLine - it exposes `is_protected`, `signed_distance`,
    `draw` and `protected_side` - so the obstruction rule consumes either
    without knowing which it has.

    A single straight line cannot follow a curving barangay road: forced to fit
    a bend it cuts the corner, putting real footpath on the road side at one end
    and real road on the footpath side at the other, which is wrong in BOTH
    directions at once. Here the boundary is a chain of segments and a point is
    judged against the segment nearest to it.

    Sign consistency comes from treating the chain as a DIRECTED path: every
    segment shares the path's direction, so "left of the path" means the same
    thing along its whole length even as it turns.
    """

    def __init__(self, points, protected_side=ABOVE):
        if len(points) < 2:
            raise ValueError("a PolyEdge needs at least two points")
        self.points = [(float(x), float(y)) for x, y in points]
        self.protected_side = protected_side
        self.support = 1.0

    @property
    def p0(self):
        return self.points[0]

    @property
    def p1(self):
        return self.points[-1]

    def _segments(self):
        return zip(self.points, self.points[1:])

    def signed_distance(self, point):
        """Perpendicular distance to the NEAREST segment, signed by which side
        of the directed path the point falls on."""
        px, py = point
        best = None
        for (ax, ay), (bx, by) in self._segments():
            dx, dy = bx - ax, by - ay
            length2 = dx * dx + dy * dy
            if length2 <= 1e-9:
                continue
            # Clamped projection: distance to the SEGMENT, not its infinite line,
            # so a point beyond a bend is measured against the segment it is
            # actually beside rather than a far-away one extended to reach it.
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
            cx, cy = ax + dx * t, ay + dy * t
            dist = math.hypot(px - cx, py - cy)
            if best is None or dist < best[0]:
                norm = math.sqrt(length2)
                # Negated to match EdgeLine's convention exactly: its normal is
                # (-dy, dx), so the bare cross product carries the opposite
                # sign. Without this a PolyEdge and an EdgeLine drawn along the
                # same path would protect OPPOSITE sides, and the two would
                # silently disagree wherever the rule accepts either.
                cross = -((px - ax) * dy - (py - ay) * dx) / norm
                best = (dist, cross)
        return 0.0 if best is None else best[1]

    def is_protected(self, point):
        d = self.signed_distance(point)
        return d < 0 if self.protected_side == ABOVE else d > 0

    def draw(self, frame, color=(0, 0, 255), thickness=3):
        pts = np.array(self.points, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(frame, [pts], False, color, thickness)

    def __repr__(self):
        return (f"PolyEdge({len(self.points)} pts, "
                f"protected={self.protected_side})")


# --- the rule ----------------------------------------------------------------

# Hysteresis band around the "half the vehicle" rule. Enter high, leave low, so
# box jitter around the threshold cannot chop the dwell into fragments.
ENTER_FRACTION = 0.55
EXIT_FRACTION = 0.45

# How long the vehicle must hold that state to be an obstruction.
OBSTRUCTION_SECONDS = 300.0      # five minutes

# Anything over the line for less than this is traffic, not parking. Reported
# separately so a review can see the detector noticed and dismissed it.
PASSING_SECONDS = 5.0

# A vehicle counts as stationary while its ground point stays inside this many
# of its own box-widths. Scaling by the box rather than using fixed pixels keeps
# it valid at any distance from the camera.
MOVE_TOLERANCE_FRAC = 0.15

# Accrued dwell survives this long without a sighting before it is written off
# as the vehicle having left. Generously longer than the 2s used elsewhere,
# because a five-minute rule has to outlast a passing jeepney.
OCCLUSION_GRACE_SECONDS = 12.0

# Most time a single sighting may add to the accrual. Dwell SURVIVES an
# occlusion but must not GROW through it, or a five-minute alert could be built
# largely out of seconds when the vehicle was hidden and unverified. Sized to
# span an ordinary frame interval even in far mode (~1 FPS) with margin.
MAX_ACCRUAL_STEP = 2.0

# Once a spot has been reported, the same spot is not reported again for this
# long. Keyed to the REGION OF FRAME, not to the track id, and that is the whole
# point: when a parked vehicle is occluded past the grace period its track dies
# and it comes back with a new id, a cleared `alerted` flag and a fresh timer.
# Identity-based suppression cannot see that those are the same car; position
# can. Same reasoning as the spatial cooldown in watch_thief.
ALERT_COOLDOWN_SECONDS = 600.0
COOLDOWN_IOU = 0.3            # overlap at which two boxes are "the same spot"

# Points sampled across the footprint when measuring the fraction past the line.
FOOTPRINT_SAMPLES = 21

# Per-class width correction applied to a BOX footprint, as a multiple of the
# box width. 1.0 everywhere by default, and that is a deliberate choice rather
# than an oversight: for PH tricycles the aspect-ratio test in
# vehicle_detection identifies the case where the detector ALREADY boxed the
# sidecar, so no correction is due. The opposite case - the detector seeing only
# the motorbike and missing the sidecar entirely - cannot be corrected here
# either, because nothing in the box says which side the sidecar is on.
# Widening symmetrically would be a guess, and it would inflate solo
# motorcycles too. The real fix is a model trained on tricycles; this hook
# exists so a measured prior can be dropped in without touching the rule.
FOOTPRINT_WIDTH_FACTOR = {}

# A box within this many pixels of a frame edge is truncated: part of the
# vehicle is outside the picture, so its footprint - and therefore the whole
# percentage - is wrong. Same guard as E25 in core/vision/tracking.py.
EDGE_MARGIN_PX = 3

# Traffic-jam guard. When this many vehicles are stationary at once the street
# is congested, not being parked on, and flagging all of them would be wrong.
# Mirrors the crowd guard (E23) in the theft layer: abstain, do not reject.
JAM_MIN_VEHICLES = 5
JAM_STATIONARY_FRACTION = 0.8

# Pedestrian corroboration: a person whose feet are on the ROAD side of the
# line, within this many vehicle-widths of a flagged vehicle, is very likely
# walking around it. This does not gate the verdict - it is recorded as
# supporting evidence, because "three pedestrians had to step into the road"
# is far stronger than a percentage to anyone reading the report.
DETOUR_RADIUS_WIDTHS = 2.0

CLEAR, PASSING, WATCHING, OBSTRUCTION = "clear", "passing", "watching", "obstruction"


def is_truncated(box, frame_shape):
    """True when the box touches a frame edge, so its footprint is unreliable."""
    if not frame_shape:
        return False
    h, w = frame_shape[:2]
    x1, y1, x2, y2 = box
    return (x1 <= EDGE_MARGIN_PX or y1 <= EDGE_MARGIN_PX
            or x2 >= w - EDGE_MARGIN_PX or y2 >= h - EDGE_MARGIN_PX)


def footprint_points(box, samples=FOOTPRINT_SAMPLES, width_factor=1.0):
    """Points along the vehicle's ground contact - the bottom edge of the box.

    The bottom edge is used rather than the whole box because only the ground
    contact is relevant to whether the walkway is blocked; the roof of a truck
    overhanging the line obstructs nobody.

    CAVEAT, inherited from sidewalk_geometry.py: an axis-aligned box takes its
    left and right extremes from whichever corners are widest in the image,
    which can be far corners at roof height, so the bottom edge is slightly
    wider than the true footprint. The error is roughly symmetric about the
    centre, so a FRACTION past the line is far less affected by it than an
    absolute width would be. Pass a segmentation mask to remove it entirely.
    """
    x1, y1, x2, y2 = box
    if width_factor != 1.0:
        mid, half = (x1 + x2) / 2.0, (x2 - x1) / 2.0 * width_factor
        x1, x2 = mid - half, mid + half
    xs = np.linspace(x1, x2, samples)
    return [(float(x), float(y2)) for x in xs]


def mask_footprint_points(mask, box, samples=FOOTPRINT_SAMPLES, band=0.15):
    """Ground-contact points taken from a segmentation mask instead of a box.

    For each sampled column inside the vehicle, the lowest set pixel is its
    ground contact. Columns where the mask is empty are skipped, so a vehicle
    whose true shape is narrower than its box is measured correctly.
    """
    x1, y1, x2, y2 = box
    h = max(y2 - y1, 1)
    lo = int(max(y1, y2 - h * band))
    pts = []
    for x in np.linspace(x1, x2, samples):
        col = mask[lo:y2, int(np.clip(x, 0, mask.shape[1] - 1))]
        nz = np.nonzero(col)[0]
        if nz.size:
            pts.append((float(x), float(lo + nz.max())))
    return pts


def fraction_past(line, box, mask=None, label=None):
    """Share of the vehicle's footprint lying on the protected side, 0.0 - 1.0.

    Uses the segmentation mask when one is supplied - the vehicle's real ground
    contact - and falls back to the bottom edge of the box otherwise.
    """
    if mask is not None:
        pts = mask_footprint_points(mask, box)
    else:
        pts = footprint_points(box,
                               width_factor=FOOTPRINT_WIDTH_FACTOR.get(label, 1.0))
    if not pts:
        return 0.0
    return sum(1 for p in pts if line.is_protected(p)) / len(pts)


class VehicleState:
    """Per-vehicle dwell accrual with hysteresis and occlusion tolerance."""

    def __init__(self, vid, box, now):
        self.id = vid
        self.box = box
        self.anchor = self._ground(box)
        self.first_seen = now
        self.last_seen = now
        self.over = False           # currently past the line (hysteresis state)
        self.held = 0.0             # accrued seconds over the line AND stationary
        self.last_accrued_at = None
        self.fraction = 0.0
        self.alerted = False      # this track has already been reported
        self.fresh_alert = False  # ...and it became an alert on THIS frame
        self.truncated = False    # box is clipped by a frame edge -> unreliable
        self.detours = 0          # pedestrians seen stepping into the road

    @staticmethod
    def _ground(box):
        x1, y1, x2, y2 = box
        return ((x1 + x2) / 2.0, float(y2))

    def _moved(self, box):
        """True if the vehicle shifted more than a fraction of its own width."""
        gx, gy = self._ground(box)
        tol = max((box[2] - box[0]) * MOVE_TOLERANCE_FRAC, 3.0)
        return np.hypot(gx - self.anchor[0], gy - self.anchor[1]) > tol

    def update(self, box, fraction, now, truncated=False, frozen=False):
        """Folds one sighting into the accrual and returns the current state.

        `truncated` (box clipped by a frame edge) and `frozen` (traffic jam)
        both ABSTAIN: the accrued time is held rather than reset, because in
        both cases the system has not learned that the vehicle left - only that
        it cannot currently judge. Resetting would punish a vehicle for the
        camera's framing or for the traffic around it.
        """
        gap = now - self.last_seen
        if gap > OCCLUSION_GRACE_SECONDS:
            # Gone long enough to be a different parking event entirely, so the
            # reported-already latch is cleared with the timer. A new stay
            # deserves a new report; whether it actually gets one is then the
            # spatial cooldown's call, not this flag's. Leaving `alerted` set
            # here would silently mute this spot for the rest of the run.
            self.held = 0.0
            self.over = False
            self.alerted = False
            self.anchor = self._ground(box)
            self.first_seen = now

        moving = self._moved(box)
        if moving:
            # Movement resets both the accrual and the reference position: a
            # vehicle that shuffles forward has started a new stay.
            self.held = 0.0
            self.anchor = self._ground(box)

        self.box = box
        self.fraction = fraction
        self.last_seen = now

        # Hysteresis: cross the high mark to engage, fall under the low one to
        # disengage. Between the two the previous state simply persists.
        if self.over:
            if fraction < EXIT_FRACTION:
                self.over = False
                self.held = 0.0
        elif fraction >= ENTER_FRACTION:
            self.over = True
            self.last_accrued_at = now

        self.truncated = truncated
        if truncated or frozen:
            # Hold the timer where it is and take no new evidence this frame.
            self.last_accrued_at = now if self.over else None
            return self.verdict()

        if self.over and not moving:
            # Accrue only the time actually observed. Time lost to an occlusion
            # is NOT credited - the dwell survives the gap, it does not grow
            # through it - so the five minutes remains five observed minutes.
            if self.last_accrued_at is not None:
                self.held += min(now - self.last_accrued_at, MAX_ACCRUAL_STEP)
            self.last_accrued_at = now
        else:
            self.last_accrued_at = now if self.over else None

        return self.verdict()

    def verdict(self):
        if not self.over:
            return CLEAR
        if self.held >= OBSTRUCTION_SECONDS:
            return OBSTRUCTION
        if self.held < PASSING_SECONDS:
            return PASSING
        return WATCHING

    def summary(self):
        return (f"vehicle #{self.id}: {self.fraction * 100:.0f}% past the line, "
                f"held {self.held:.0f}s -> {self.verdict().upper()}")


class ObstructionMonitor:
    """Tracks every vehicle against a locked edge line and reports verdicts.

    Association is by IoU against the previous frame, which is enough for parked
    vehicles: the thing being tracked is by definition not moving. Pass external
    track ids instead when the caller already runs a tracker.
    """

    def __init__(self, line, obstruction_seconds=OBSTRUCTION_SECONDS,
                 cooldown=ALERT_COOLDOWN_SECONDS):
        self.line = line
        self.obstruction_seconds = obstruction_seconds
        self.states = {}
        self.cooldown = cooldown
        # (box, when) for every alert raised, surviving track churn.
        self._alert_log = []
        self._next_id = 0

    def _detours(self, box, pedestrians):
        """Pedestrians standing on the ROAD side of the line, close to this
        vehicle - i.e. people who had to leave the footpath to get past it."""
        x1, _, x2, y2 = box
        width = max(x2 - x1, 1)
        cx = (x1 + x2) / 2.0
        n = 0
        for px1, _, px2, py2 in pedestrians:
            foot = ((px1 + px2) / 2.0, float(py2))
            if self.line.is_protected(foot):
                continue                     # still on the footpath - fine
            if abs(foot[0] - cx) <= width * DETOUR_RADIUS_WIDTHS and foot[1] >= y2 - width:
                n += 1
        return n

    def _cooldown_blocks(self, box, now):
        """True if this part of the frame was already reported recently."""
        self._alert_log = [(b, t) for b, t in self._alert_log
                           if now - t <= self.cooldown]
        return any(_iou(box, b) >= COOLDOWN_IOU for b, _ in self._alert_log)

    def _match(self, box):
        best, best_iou = None, 0.3
        for vid, st in self.states.items():
            iou = _iou(box, st.box)
            if iou > best_iou:
                best, best_iou = vid, iou
        return best

    def _jammed(self, vehicles, now):
        """True when the street is congested rather than being parked on.

        Distinguishing a jam from mass illegal parking is not possible from one
        frame, so the test is deliberately blunt: a lot of vehicles, nearly all
        of them stationary, at the same moment. Flagging every car in a traffic
        queue would discredit the whole detector, and abstaining costs only a
        delayed alert - the timer is held, not lost.
        """
        if len(vehicles) < JAM_MIN_VEHICLES:
            return False
        still = 0
        for box in vehicles:
            vid = self._match(box)
            st = self.states.get(vid)
            if st is not None and not st._moved(box):
                still += 1
        return still >= len(vehicles) * JAM_STATIONARY_FRACTION

    def update(self, vehicles, now, masks=None, labels=None,
               frame_shape=None, pedestrians=None):
        """vehicles: list of (x1, y1, x2, y2) - returns [(VehicleState, verdict)].

        `masks` gives exact footprints, `labels` selects any per-class width
        prior, `frame_shape` enables the edge-truncation guard, and
        `pedestrians` supplies person boxes used as corroborating evidence.
        """
        out = []
        seen = set()
        jam = self._jammed(vehicles, now)
        for i, box in enumerate(vehicles):
            vid = self._match(box)
            if vid is None:
                vid = self._next_id
                self._next_id += 1
                self.states[vid] = VehicleState(vid, box, now)
            seen.add(vid)
            mask = masks[i] if masks is not None else None
            label = labels[i] if labels is not None else None
            frac = fraction_past(self.line, box, mask, label)
            state = self.states[vid]
            state.obstruction_seconds = self.obstruction_seconds
            verdict = state.update(box, frac, now,
                                   truncated=is_truncated(box, frame_shape),
                                   frozen=jam)
            if pedestrians:
                state.detours = max(state.detours,
                                    self._detours(box, pedestrians))

            # Raise each obstruction ONCE. `alerted` stops the same track
            # re-reporting every frame; the spatial cooldown stops a track that
            # died and was reacquired under a new id from reporting the same
            # parked vehicle a second time.
            state.fresh_alert = False
            if verdict == OBSTRUCTION and not state.alerted:
                state.alerted = True
                if not self._cooldown_blocks(box, now):
                    state.fresh_alert = True
                    self._alert_log.append((tuple(box), now))
            out.append((state, verdict))

        for vid in [v for v, st in self.states.items()
                    if v not in seen and now - st.last_seen > OCCLUSION_GRACE_SECONDS]:
            del self.states[vid]
        return out


def _iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    return inter / ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)
