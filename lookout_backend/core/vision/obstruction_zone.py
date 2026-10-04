"""
LookOut - single-polygon obstruction zone.

Draw ONE polygon (python manage.py draw_zone). Any vehicle whose ground point
(bottom-centre of its box = where the wheels touch the road) is inside the
polygon accrues an obstruction score in seconds. When the score reaches
alert_score, the vehicle becomes a candidate violator and ONE alert fires.

Time source: pass VIDEO time for file sources (cv2.CAP_PROP_POS_MSEC / 1000),
wall time (time.time()) for live RTSP. Using wall time on files makes scores
grow slower than the video plays.
"""
import json
import math
from collections import deque
from pathlib import Path

import cv2
import numpy as np

# COCO names from the pretrained YOLOv8n model
VEHICLE_CLASSES = {"car", "motorcycle", "bus", "truck"}

# ---- Tunables (camera-specific; tune against Tetuan footage) ----
ALERT_SCORE = 60.0           # seconds of "inside zone" before alert
# Share of the vehicle's own ground footprint that must lie inside the polygon
# for it to count as being in the area: "at least half the vehicle inside".
#
# A SHARE OF ITSELF, so it needs no tape measure and no per-camera
# calibration — 50% means the same thing on a motorcycle at the kerb and a
# truck down the block. Overridden per camera by Camera.obstruction_pct, which
# is the "Inside the road %" box in the dashboard's zone editor.
#
# This replaces a single-point test (is the bottom-CENTRE of the box inside?),
# which was binary and flipped frame to frame for anything straddling the
# boundary — a motorcycle parked half on the kerb read as fully out, then
# fully in, then out again, and banked nothing. The older edge rule in
# obstruction.py always measured the fraction; the zone module had dropped it.
ENTER_FRACTION = 0.50
# Hysteresis: engage at ENTER, disengage only this far below it, so box jitter
# around the threshold cannot chop the dwell into fragments.
FRACTION_HYSTERESIS = 0.10
# Score rate while the vehicle is still MOVING, as a fraction of the stopped
# rate. 0 = only stopped time counts, which is the default because a moving
# vehicle is not obstructing anything — it is traffic.
#
# It was 0.25, and on real footage that inverted the whole rule: a car driving
# up the road accrued (it was over the carriageway, which is inside the zone)
# while a motorcycle actually parked at the kerb accrued nothing. The live view
# showed the passing car as "Possible" and the parked bike as "Monitoring" —
# precisely backwards. Through-traffic on a busy road would also reach any
# threshold eventually, given enough of it.
#
# Raise it only if you want a crawling/stop-start vehicle to earn partial
# credit; the stationary test already tolerates ordinary detector jitter.
MOVING_WEIGHT = 0.0
STATIONARY_DIST = 0.15       # max centre drift (in box-widths) over the window to count as stopped
STATIONARY_WINDOW = 3.0      # seconds of history for the stopped check
DECAY_PER_SEC = 2.0          # score lost per second while outside the zone / missing
PRESENCE_GRACE = 2.0         # seconds a vehicle can go undetected before decay starts
MAX_DT = 2.0                 # clamp per-frame time step (slow FPS / stalls)
FORGET_AFTER = 10.0          # drop a vehicle's state after this long unseen
ADOPT_DIST = 1.2             # box-widths; a new track ID this close MAY inherit a lost vehicle's score (ID churn fix)
# ...but only if it also sits ON the lost box by this much. Centre distance
# alone is far too loose a test: ADOPT_DIST is over a whole car-width, which a
# vehicle driving past a parked one satisfies easily. See _absorb_lost.
ADOPT_MIN_IOU = 0.55
COOLDOWN_SECONDS = 120.0     # no repeat alert at the same spot within this window
COOLDOWN_CENTER_DIST = 1.5   # box-widths; spatial dedup, same idea as smoking/drinking

# BGR colours (Dark Ops amber zone)
COLOR_ZONE = (0, 191, 255)
COLOR_OUTSIDE = (160, 160, 160)
COLOR_ACCRUING = (0, 200, 0)
COLOR_ALERT = (0, 140, 255)


def _centre(box):
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _width(box):
    return max(1.0, box[2] - box[0])


def _centre_dist(a, b):
    """Centre distance in units of the larger box width (near/far invariant)."""
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    return math.hypot(ax - bx, ay - by) / max(_width(a), _width(b))


def _ground_point(box):
    x1, _, x2, y2 = box
    return (x1 + x2) / 2.0, y2


# Points sampled across the vehicle's ground contact when measuring how much of
# it is inside the area. Same count the edge rule in obstruction.py uses.
FOOTPRINT_SAMPLES = 21


def _footprint_points(box, samples=FOOTPRINT_SAMPLES):
    """Points along the vehicle's ground contact — the bottom edge of its box.

    The bottom edge rather than the whole box because only the ground contact
    is relevant to whether the way is blocked; the roof of a truck overhanging
    the line obstructs nobody.
    """
    x1, _, x2, y2 = box
    if samples < 2:
        return [_ground_point(box)]
    step = (x2 - x1) / (samples - 1)
    return [(x1 + step * i, y2) for i in range(samples)]


def _iou(a, b):
    """Overlap of two boxes. A vehicle re-identified where it was parked sits
    almost exactly on its old box; one merely driving past it does not."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    union = ((ax2 - ax1) * (ay2 - ay1)) + ((bx2 - bx1) * (by2 - by1)) - inter
    return inter / union if union > 0 else 0.0


class VehicleState:
    def __init__(self, track_id, t):
        self.track_id = track_id
        self.score = 0.0
        self.last_t = t          # last time the score was updated
        self.last_seen = t       # last time the vehicle was detected
        self.box = None
        self.label = ""
        self.conf = 0.0
        self.inside = False
        self.fraction = 0.0      # share of the footprint inside the polygon
        self.stationary = False
        self.alerted = False
        self.adopted = False     # has already absorbed a lost track's score
        self.history = deque()   # (t, cx, cy)

    def push(self, t, box):
        """Record position; return True if the vehicle is stopped."""
        cx, cy = _centre(box)
        self.history.append((t, cx, cy))
        while self.history and t - self.history[0][0] > STATIONARY_WINDOW:
            self.history.popleft()
        if len(self.history) < 2 or t - self.history[0][0] < 1.0:
            return False  # not enough history yet
        _, x0, y0 = self.history[0]
        drift = max(math.hypot(x - x0, y - y0) for _, x, y in self.history)
        return drift / _width(box) <= STATIONARY_DIST


class ObstructionZone:
    def __init__(self, points_norm, alert_score=ALERT_SCORE, moving_weight=MOVING_WEIGHT,
                 enter_fraction=ENTER_FRACTION):
        if len(points_norm) < 3:
            raise ValueError("Obstruction zone needs at least 3 points.")
        self.points_norm = [tuple(p) for p in points_norm]
        self.alert_score = float(alert_score)
        self.moving_weight = float(moving_weight)
        self.enter_fraction = min(max(float(enter_fraction), 0.05), 1.0)
        self.exit_fraction = max(self.enter_fraction - FRACTION_HYSTERESIS, 0.02)
        self.states = {}
        self._alert_log = []     # (t, box)
        self._poly = None
        self._shape = None
        self._now = None

    @classmethod
    def load(cls, path, **kwargs):
        data = json.loads(Path(path).read_text())
        return cls(data["points"], **kwargs)

    # ---- geometry ----
    def _polygon(self, frame_shape):
        h, w = frame_shape[:2]
        if self._shape != (h, w):
            self._poly = np.array(
                [[px * w, py * h] for px, py in self.points_norm], dtype=np.float32
            )
            self._shape = (h, w)
        return self._poly

    def contains(self, point, frame_shape):
        poly = self._polygon(frame_shape)
        return cv2.pointPolygonTest(poly, (float(point[0]), float(point[1])), False) >= 0

    def fraction_inside(self, box, frame_shape):
        """Share of the vehicle's ground footprint inside the polygon, 0.0-1.0."""
        poly = self._polygon(frame_shape)
        pts = _footprint_points(box)
        n = sum(1 for p in pts
                if cv2.pointPolygonTest(poly, (float(p[0]), float(p[1])), False) >= 0)
        return n / len(pts)

    def _engaged(self, fraction, was_inside):
        """Hysteretic 'is it in the area': cross the high mark to engage, fall
        under the low one to leave. Between the two, the previous state holds."""
        return (fraction >= self.exit_fraction if was_inside
                else fraction >= self.enter_fraction)

    # ---- main entry ----
    def update(self, detections, t, frame_shape):
        """
        detections: iterable of (track_id, (x1, y1, x2, y2), label, conf)
        t:          current time in seconds (video time for files)
        Returns a list of alert dicts that fired THIS frame.
        """
        self._now = t
        vehicles = [
            (tid, tuple(float(v) for v in box), label, float(conf))
            for tid, box, label, conf in detections
            if tid is not None and label in VEHICLE_CLASSES
        ]
        current_ids = {tid for tid, *_ in vehicles}
        fired = []

        for tid, box, label, conf in vehicles:
            fraction = self.fraction_inside(box, frame_shape)
            st = self.states.get(tid)
            inside = self._engaged(fraction, st.inside if st else False)
            if st is None:
                # The polygon decides WHAT IS WATCHED, not merely what scores.
                # A vehicle that has never had its wheels inside the area is
                # not this rule's business: it gets no state, so it cannot
                # accrue, cannot be published to the live view, and cannot be
                # confused for the thing being judged. Traffic on the road
                # behind a marked kerb used to appear in the view as tracked
                # subjects at score 0, which read as "the detector is watching
                # the wrong vehicles" — because it was.
                #
                # A vehicle ALREADY being watched keeps its state when it
                # leaves (below): that is the existing decay path, and it is
                # what lets a parked car survive a frame where the detector
                # shifts its box a few pixels over the line.
                if not inside:
                    continue
                st = VehicleState(tid, t)
                self.states[tid] = st

            dt = min(max(0.0, t - st.last_t), MAX_DT)
            st.last_t = t
            st.last_seen = t
            st.box, st.label, st.conf = box, label, conf
            st.stationary = st.push(t, box)
            st.inside = inside
            st.fraction = fraction
            # Claim a lost track's banked seconds — AFTER `stationary` is known,
            # because standing still is the test a passing vehicle fails.
            self._absorb_lost(st, t, current_ids)

            if st.inside:
                weight = 1.0 if st.stationary else self.moving_weight
                st.score += dt * weight
            else:
                st.score = max(0.0, st.score - dt * DECAY_PER_SEC)

            if st.inside and not st.alerted and st.score >= self.alert_score:
                st.alerted = True  # one alert per stay, even if cooldown blocks it
                if not self._cooldown_blocks(box, t):
                    self._alert_log.append((t, box))
                    fired.append({
                        "track_id": tid,
                        "box": box,
                        "label": label,
                        "conf": conf,
                        "score": round(st.score, 1),
                        "ground_point": _ground_point(box),
                        "t": t,
                    })

        # vehicles not seen this frame: grace, then decay, then forget
        for tid in list(self.states):
            if tid in current_ids:
                continue
            st = self.states[tid]
            if t - st.last_seen > FORGET_AFTER:
                del self.states[tid]
                continue
            if t - st.last_seen > PRESENCE_GRACE:
                st.score = max(0.0, st.score - (t - st.last_t) * DECAY_PER_SEC)
            st.last_t = t

        self._alert_log = [(at, b) for at, b in self._alert_log if t - at < COOLDOWN_SECONDS]
        return fired

    def _absorb_lost(self, st, t, current_ids):
        """A parked vehicle that comes back under a new tracker id keeps the
        seconds it had already banked.

        The transfer has to be EARNED, which is the whole difficulty. The
        obvious version — hand the score to whichever new id turns up nearest
        the lost one — gives it to the first detection in the list, and on a
        street the first detection to turn up next to a parked car is usually
        the vehicle driving past it. Measured: a car parked for 39s was
        occluded for one frame by a passer, the passer inherited all 39s, and
        the real obstruction came back under a new id and restarted at zero.
        The timer could then never finish on a busy road, which is exactly the
        road the rule exists for.

        Three conditions, each ruling out that case:

          STATIONARY  the claimant must have held still for a second first, so
                      it is asked to prove it parked rather than asked where it
                      happens to be this instant. A passer never qualifies.
          OVERLAP     it must sit ON the lost box, not merely near its centre.
                      ADOPT_DIST alone is over a whole car-width of slack.
          ONCE        a track absorbs at most one lost score, so a single
                      long-parked vehicle cannot collect several.

        The wait costs the returning vehicle about a second of credit, against
        PRESENCE_GRACE seconds before a lost score even starts to decay — so in
        practice nothing is lost. Scores are taken, not added: two ids are two
        views of one vehicle, and summing them would invent time.
        """
        if st.adopted or not st.stationary or st.box is None:
            return
        best, best_key = None, None
        for oid, lost in self.states.items():
            if oid == st.track_id or oid in current_ids or lost.box is None:
                continue
            if lost.score <= 0 or t - lost.last_seen > FORGET_AFTER:
                continue
            if _centre_dist(st.box, lost.box) > ADOPT_DIST:
                continue
            overlap = _iou(st.box, lost.box)
            if overlap < ADOPT_MIN_IOU:
                continue
            if best is None or overlap > best:
                best, best_key = overlap, oid
        if best_key is None:
            return
        lost = self.states.pop(best_key)
        st.score = max(st.score, lost.score)
        # Carried so a vehicle that already alerted under its old id cannot be
        # reported a second time for the same stay just by changing identity.
        st.alerted = st.alerted or lost.alerted
        st.adopted = True

    def _cooldown_blocks(self, box, t):
        return any(
            t - at < COOLDOWN_SECONDS and _centre_dist(box, b) <= COOLDOWN_CENTER_DIST
            for at, b in self._alert_log
        )

    # ---- overlay ----
    def draw(self, frame):
        h, w = frame.shape[:2]
        poly = self._polygon(frame.shape).astype(np.int32)
        overlay = frame.copy()
        cv2.fillPoly(overlay, [poly], COLOR_ZONE)
        cv2.addWeighted(overlay, 0.18, frame, 0.82, 0, frame)
        cv2.polylines(frame, [poly], True, COLOR_ZONE, max(2, w // 800))

        fs = max(0.5, w / 1800)
        thick = max(1, w // 900)
        for st in self.states.values():
            if st.box is None or st.last_seen != self._now:
                continue
            x1, y1, x2, y2 = (int(v) for v in st.box)
            if st.alerted:
                color, bt = COLOR_ALERT, thick + 1
            elif st.inside:
                color, bt = COLOR_ACCRUING, thick
            else:
                color, bt = COLOR_OUTSIDE, 1
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, bt)
            gx, gy = (int(v) for v in _ground_point(st.box))
            cv2.circle(frame, (gx, gy), max(3, w // 500), color, -1)
            if st.inside or st.alerted:
                tag = f"#{st.track_id} {st.score:.0f}/{self.alert_score:.0f}s"
                if st.stationary:
                    tag += " STOP"
                cv2.putText(frame, tag, (x1, max(15, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, fs, color, thick, cv2.LINE_AA)
        return frame
