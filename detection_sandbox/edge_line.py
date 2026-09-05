"""Automatic detection of the solid white edge line at the side of the road.

Replaces the manual four-corner click in sidewalk_geometry.py. The camera finds
the painted boundary itself, and a vehicle is judged by how much of its
footprint sits past that line - a ratio of the vehicle to itself, so no tape
measure and no homography are needed.

THE HARD PART IS NOT FINDING WHITE PIXELS, IT IS REJECTING THEM. A concrete
wall, an overcast sky, a white jeepney and a bright roofline are all "white and
straight". Four filters, applied in order, separate paint from all of that:

  1. TOP-HAT, not brightness. Road paint is a THIN BRIGHT STRIPE lying on a
     DARKER surface. A morphological top-hat keeps exactly that and erases
     anything bright but broad - which is what a wall and the sky are. This is
     the filter that does most of the work; plain HSV thresholding on this
     project's own sample photos returned 26-51 "lines" that were all roofline
     and sky.

  2. PERSISTENCE. The camera is fixed and the paint never moves, so a pixel that
     is only white in some frames is not paint. Averaging the mask over a warm-up
     and keeping pixels present in most frames removes white vehicles, plastic
     bags, puddle glare and moving shadows.

  3. COLLINEAR SUPPORT. Real paint yields many short segments lying on ONE line.
     Clutter yields segments at unrelated angles. Segments are clustered in
     (angle, offset) space and only the best-supported cluster survives.

  4. FLANK CONTEXT. What finally settles it is what lies on either SIDE of the
     stripe. Paint sits on a road: both sides similar to each other, and both
     MUCH darker than the paint. A roofline has sky on one side. A crack in
     concrete is barely brighter than the concrete. See `flanks_ok`.

THE LINE IS THEN LOCKED. Recomputing it every frame is self-defeating: a vehicle
parked on the line hides the paint, the "edge" migrates around the vehicle, and
the violation quietly redefines the boundary it is supposed to be breaking. Once
locked, paint that goes missing under a vehicle is evidence, not a correction.

KNOWN LIMITATION, measured not guessed. On the 2560x1440 night clip
(Aug14_17.mkv) the paint IS found - it survives the mask and the persistence
filter at 15 of 15 sampled points along its true path - but the fit still does
not lock. The cause is `_clusters`: it is a GREEDY first-match grouper, so in a
scene with many competing diagonals (kerb, shadow edges, fence, wall) a longer
neighbouring structure absorbs the paint's segments before they can form their
own cluster, and the true line never becomes a candidate. Loosening thresholds
does not fix this and risks hallucinating edges on unmarked roads, which is the
worse failure. The real fix is to replace greedy clustering with RANSAC or a
Hough-space accumulator that lets every candidate compete on its own support.

No Django here - pure CV, same rule as core/vision/recognition.py.
"""

import math

import cv2
import numpy as np

# --- paint appearance --------------------------------------------------------

# EVERYTHING BELOW IS IN CANONICAL-FRAME PIXELS. Frames are resized to
# PROCESS_WIDTH before detection and the fitted line is scaled back afterwards.
# Without this the detector is resolution-dependent in the worst way: the same
# painted line is 8px wide on a 640px stream and 32px on a 2560px one, so a
# kernel tuned on one silently fails on the other. Measured on this project's
# 2560x1440 night clip, the edge line is ~30px wide - four times the default -
# and the detector found nothing until the frame was normalized.
PROCESS_WIDTH = 960

# Expected painted-line thickness at PROCESS_WIDTH. The top-hat kernel must be a
# few times this: big enough to span the stripe, small enough that a wall is
# treated as background rather than as a very fat stripe.
LINE_WIDTH_PX = 11
TOPHAT_SCALE = 4

# Paint is white: bright, and nearly colourless. Saturation is the useful half
# of this test - it rejects the yellow of a warning sign and the ochre of dirt,
# both of which can be bright.
MIN_VALUE = 150
MAX_SATURATION = 60

# Share of the below-horizon frame allowed through as candidate paint, and the
# floor the response must clear regardless. Paint is a small fraction of any
# frame, so a high percentile is the right shape of test.
# The percentile is CLAMPED at both ends. Measured on the night clip: the raw
# 99th percentile lands at 148 because of the lit shopfront, a white shirt and
# the burnt-in timestamp, while the painted line's own top-hat response is only
# 52-83 - so an unclamped percentile prices the paint out of its own mask. The
# ceiling stops that; the floor stops the opposite failure, where a frame with
# no paint at all yields "the brightest noise available".
TOPHAT_PERCENTILE = 99.0
MIN_TOPHAT_RESPONSE = 25.0
MAX_TOPHAT_RESPONSE = 45.0

# Ignore the top of the frame outright. Sky, rooflines and power cables live
# there and no road surface does, for a camera mounted looking down at a street.
HORIZON_FRACTION = 0.35

# --- persistence (filter 2) --------------------------------------------------

WARMUP_FRAMES = 90          # frames blended before a lock is attempted
PERSISTENCE_MIN = 0.60      # a pixel must read as paint in this share of them

# --- line fitting (filter 3) -------------------------------------------------

MIN_SEGMENT_FRACTION = 0.08   # shortest Hough segment, as a share of frame width
ANGLE_TOLERANCE_DEG = 8.0     # segments within this angle may share a cluster
OFFSET_TOLERANCE_PX = 22.0    # ...and within this perpendicular distance
MIN_CLUSTER_SUPPORT = 2       # a lone segment is not a painted line
MIN_INLIER_FRACTION = 0.10    # cluster length must cover this much of the frame

# --- flank context (filter 4) ------------------------------------------------

# SCALE GOTCHA: OpenCV's Lab L channel is 0-255, NOT the 0-100 of the textbook
# CIE definition. Every threshold below is on the 0-255 scale, so a "lightness
# difference of 45" here is about 18 in CIE terms.
FLANK_SAMPLES = 24            # points tested along the candidate line
FLANK_OFFSET_SCALE = 2.5      # how far off the stripe to sample, in line widths

# THE DECISIVE TEST. Measured on this project's own footage: genuine painted
# stripe scores 122, while every false positive found on the unmarked barangay
# photos - hairline cracks, a wall's top edge, a gutter lip - scored 12 to 27.
# Real paint is not subtly brighter than asphalt, it is dramatically brighter,
# and that gap is what separates it from ordinary bright clutter.
MIN_PAINT_CONTRAST = 45.0

# Rejects a stripe with sky on one side and structure on the other, i.e. a
# roofline. Set well above the ~45 that a legitimate road/shoulder pair shows,
# and well below the ~170 of sky against a dark roof.
MAX_FLANK_L_DIFF = 80.0
MAX_FLANK_AB_DIFF = 30.0      # ...and different materials differ in colour too

# How far a candidate may be nudged along its own normal to sit on the stripe.
# The Hough fit is centred on the THRESHOLDED mask, and a threshold clips one
# flank of the stripe harder than the other, so the fit lands a few pixels off
# the true centre - enough for the flank probes to straddle the paint instead of
# measuring across it. On the night clip snapping raised the real line's
# contrast from below zero to 46.5, which is the difference between finding it
# and not.
SNAP_RANGE_PX = 25

# Geometry lives with the rule that consumes it, so the management command and
# this experiment share one definition of "which side of the edge is a point on".
from obstruction import ABOVE, BELOW, EdgeLine, PolyEdge  # noqa: F401


def paint_mask(frame):
    """Pixels that look like painted road marking in ONE frame (filter 1).

    Top-hat supplies the "thin bright stripe on a darker surface" test; the HSV
    pair supplies "white rather than coloured". Both must agree.
    """
    h = frame.shape[0]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    ksize = (LINE_WIDTH_PX * TOPHAT_SCALE) | 1        # force odd
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (ksize, ksize))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    # Otsu on the top-hat response adapts to overcast vs harsh sun without a
    # hand-set brightness threshold, which would not survive both.
    # NOT Otsu. Otsu is global, so one bright object - a white shirt, a lit
    # shopfront, a streetlight - drags the threshold above the paint and the
    # line vanishes from the mask. That is exactly what happened on the night
    # clip: Otsu chose 86 while the line's top-hat response was 20-65. A high
    # percentile of the response below the horizon keeps the brightest thin
    # structures whatever the overall exposure, and the absolute floor stops it
    # from finding "the brightest noise" on a frame with no paint at all.
    roi = tophat[int(h * HORIZON_FRACTION):, :]
    cut = float(np.clip(np.percentile(roi, TOPHAT_PERCENTILE),
                        MIN_TOPHAT_RESPONSE, MAX_TOPHAT_RESPONSE))
    _, thin = cv2.threshold(tophat, cut, 255, cv2.THRESH_BINARY)

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, (0, 0, MIN_VALUE), (180, MAX_SATURATION, 255))

    mask = cv2.bitwise_and(thin, white)
    mask[:int(h * HORIZON_FRACTION), :] = 0          # no road above the horizon
    return mask


def _segments(mask, width):
    min_len = max(int(width * MIN_SEGMENT_FRACTION), 10)
    return cv2.HoughLinesP(mask, 1, np.pi / 180, threshold=40,
                           minLineLength=min_len, maxLineGap=int(min_len * 0.5))


def _clusters(segments, shape):
    """Groups collinear segments (filter 3); returns groups, best-supported first.

    Clustering is in (angle, perpendicular offset) space rather than on
    endpoints, because one painted line broken by wear or by a passing wheel
    arrives as several disjoint segments that must still be recognised as one.
    """
    w = shape[1]
    groups = []
    for seg in segments[:, 0]:
        x1, y1, x2, y2 = (float(v) for v in seg)
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1)) % 180.0
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        rad = np.radians(angle)
        offset = -mx * np.sin(rad) + my * np.cos(rad)
        length = float(np.hypot(x2 - x1, y2 - y1))
        for g in groups:
            d_ang = abs(g["angle"] - angle)
            d_ang = min(d_ang, 180.0 - d_ang)        # 179 deg and 1 deg are close
            if (d_ang <= ANGLE_TOLERANCE_DEG
                    and abs(g["offset"] - offset) <= OFFSET_TOLERANCE_PX):
                n = g["n"]
                g["angle"] = (g["angle"] * n + angle) / (n + 1)
                g["offset"] = (g["offset"] * n + offset) / (n + 1)
                g["n"] = n + 1
                g["length"] += length
                g["pts"].extend([(x1, y1), (x2, y2)])
                break
        else:
            groups.append({"angle": angle, "offset": offset, "n": 1,
                           "length": length, "pts": [(x1, y1), (x2, y2)]})

    groups = [g for g in groups
              if g["n"] >= MIN_CLUSTER_SUPPORT and g["length"] >= w * MIN_INLIER_FRACTION]
    return sorted(groups, key=lambda g: -g["length"])


def _line_from_group(group, shape):
    h, w = shape[:2]
    pts = np.array(group["pts"], dtype=np.float32)
    vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_L2, 0, 0.01, 0.01).ravel()
    # Extend the fit past the frame edges so the line spans the whole image and
    # a vehicle anywhere along it can be tested.
    scale = float(w + h)
    p0 = (x0 - vx * scale, y0 - vy * scale)
    p1 = (x0 + vx * scale, y0 + vy * scale)
    return EdgeLine(p0, p1, support=min(group["length"] / max(w, 1), 1.0))


def flanks_ok(frame, line, samples=FLANK_SAMPLES):
    """Filter 4 - is this stripe lying ON A ROAD, or is it a roofline?

    THIS IS THE FILTER THAT MATTERS ON REAL FOOTAGE. Persistence (filter 2)
    cannot help here, because a roofline is exactly as static as paint is; on
    this project's own sample photos the first three filters still locked onto
    the top edge of a wall in all six images.

    What separates them is what lies on either SIDE. Road paint has road on both
    sides: two surfaces of similar brightness and colour, both darker than the
    paint. A roofline, a wall top or a gutter edge has SKY on one side and
    structure on the other - a large brightness difference. So the test is:

        both flanks darker than the stripe, AND similar to each other.
    """
    h, w = frame.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(frame, (5, 5), 0), cv2.COLOR_BGR2LAB)
    off = LINE_WIDTH_PX * FLANK_OFFSET_SCALE

    # Sample only the stretch of the line actually inside the frame. The fitted
    # endpoints are deliberately extended far beyond the image so that any
    # vehicle can be tested against the line, but sampling between THOSE would
    # put most probes outside the picture and the test would abstain every time.
    inside, q0, q1 = cv2.clipLine((0, 0, w - 1, h - 1),
                                  (int(line.p0[0]), int(line.p0[1])),
                                  (int(line.p1[0]), int(line.p1[1])))
    if not inside:
        return False

    on, left, right = [], [], []
    for t in np.linspace(0.0, 1.0, samples):
        x = q0[0] + (q1[0] - q0[0]) * t
        y = q0[1] + (q1[1] - q0[1]) * t
        if not (0 <= x < w and 0 <= y < h):
            continue
        pa = (x + line.a * off, y + line.b * off)
        pb = (x - line.a * off, y - line.b * off)
        if not all(0 <= p[0] < w and 0 <= p[1] < h for p in (pa, pb)):
            continue
        on.append(lab[int(y), int(x)])
        left.append(lab[int(pa[1]), int(pa[0])])
        right.append(lab[int(pb[1]), int(pb[0])])

    if len(on) < samples * 0.4:
        return False        # too little of the line is inside the frame to judge
    on, left, right = (np.median(np.array(v, float), axis=0) for v in (on, left, right))

    if on[0] - max(left[0], right[0]) < MIN_PAINT_CONTRAST:
        return False        # the "stripe" is not brighter than what surrounds it
    if abs(left[0] - right[0]) > MAX_FLANK_L_DIFF:
        return False        # sky on one side, structure on the other
    if np.hypot(left[1] - right[1], left[2] - right[2]) > MAX_FLANK_AB_DIFF:
        return False        # different materials, not one road surface
    return True


# --- RANSAC line search (replaces greedy clustering) -------------------------

HOUGH_THRESHOLD = 180         # votes a line needs to become a candidate
# Peaks are scanned in full and DEDUPED AS WE GO, stopping once this many
# distinct lines have been accepted. Slicing the raw peak list first does not
# work: one strong structure produces dozens of near-identical peaks, which fill
# the whole slice and hide every weaker line behind them. On the night clip that
# alone kept the true 57-degree line out of the candidate set entirely.
HOUGH_MAX_LINES = 14
INLIER_TOL = 3.0              # inlier band around a candidate, in pixels
MIN_LINE_POINTS = 220         # mask pixels a candidate must actually own
DEDUPE_RHO = 25.0             # peaks closer than this in (rho, theta)...
DEDUPE_THETA_DEG = 6.0        # ...are the same line reported twice
COVERAGE_BINS = 20            # extent is split into this many bins...
COVERAGE_MIN = 0.55           # ...and this share of them must contain inliers


def _extent_and_coverage(pts, origin, direction):
    """How far the inliers reach along the line, and how continuously.

    A painted line is LONG and CONTINUOUS. A bright blob - a white shirt, a lit
    sign - can also put many pixels inside a narrow band, but only over a short
    stretch. Extent rejects the short ones; coverage rejects a line supported by
    two distant clumps with nothing in between, which is how unrelated clutter
    at opposite ends of a frame can conspire into a false line.
    """
    proj = (pts - origin) @ direction
    lo, hi = float(proj.min()), float(proj.max())
    extent = hi - lo
    if extent <= 1e-6:
        return 0.0, 0.0
    bins = np.clip(((proj - lo) / extent * COVERAGE_BINS).astype(int),
                   0, COVERAGE_BINS - 1)
    return extent, len(np.unique(bins)) / COVERAGE_BINS


def _candidate_lines(mask, shape):
    """Candidate lines from a paint mask, strongest support first.

    A HOUGH ACCUMULATOR, after two earlier approaches failed on real footage:

      * Greedy angle/offset grouping of Hough SEGMENTS assigned each segment to
        the first group it was near, so in a scene full of diagonals - kerb,
        shadow edges, fences - a longer neighbouring structure absorbed the
        paint's segments and the real line never became a candidate.

      * RANSAC scored every hypothesis on its own inliers, which fixed that, but
        it draws its hypotheses at random: on the night clip the true line owned
        1973 of 70159 mask pixels, so the chance of drawing two points both on
        it was under 0.1% per trial and it was usually never proposed at all.

    A Hough transform has neither weakness. Every mask pixel votes for every
    line through it, so a line is found if its pixels exist, regardless of how
    much brighter clutter shares the frame. Peaks are then re-scored against the
    actual mask - inlier count, extent and continuity - because the accumulator
    alone cannot tell a long line from two distant clumps that happen to align.
    """
    h, w = shape[:2]
    peaks = cv2.HoughLines(mask, 1, np.pi / 180, HOUGH_THRESHOLD)
    if peaks is None:
        return []
    ys, xs = np.nonzero(mask)
    if len(xs) < MIN_LINE_POINTS:
        return []
    pts = np.stack([xs, ys], axis=1).astype(np.float32)

    min_extent = w * MIN_INLIER_FRACTION
    out, taken = [], []
    for rho, theta in peaks[:, 0]:
        if len(out) >= HOUGH_MAX_LINES:
            break
        if any(abs(rho - r) < DEDUPE_RHO
               and abs(np.degrees(theta - t)) < DEDUPE_THETA_DEG
               for r, t in taken):
            continue
        ct, st = float(np.cos(theta)), float(np.sin(theta))
        normal = np.array([ct, st], dtype=np.float32)
        direction = np.array([-st, ct], dtype=np.float32)
        origin = np.array([rho * ct, rho * st], dtype=np.float32)

        inliers = np.abs(pts @ normal - rho) <= INLIER_TOL
        n = int(inliers.sum())
        if n < MIN_LINE_POINTS:
            continue
        sel = pts[inliers]
        extent, coverage = _extent_and_coverage(sel, origin, direction)
        if extent < min_extent or coverage < COVERAGE_MIN:
            continue        # too short, or too gappy, to be a painted line
        taken.append((rho, theta))

        # Total-least-squares refit on the inliers, then extend past the frame
        # so a vehicle anywhere along the line can be tested against it.
        vx, vy, x0, y0 = cv2.fitLine(sel, cv2.DIST_L2, 0, 0.01, 0.01).ravel()
        scale = float(w + h)
        out.append((n, EdgeLine((x0 - vx * scale, y0 - vy * scale),
                                (x0 + vx * scale, y0 + vy * scale),
                                support=min(extent / max(w, 1), 1.0))))
    return [ln for _, ln in sorted(out, key=lambda t: -t[0])]


def _line_contrast(lab_l, line, shift=0.0):
    """Median lightness on the stripe minus the brighter flank, or None."""
    h, w = lab_l.shape[:2]
    ox, oy = line.a * shift, line.b * shift
    ok, q0, q1 = cv2.clipLine(
        (0, 0, w - 1, h - 1),
        (int(line.p0[0] + ox), int(line.p0[1] + oy)),
        (int(line.p1[0] + ox), int(line.p1[1] + oy)))
    if not ok:
        return None
    off = LINE_WIDTH_PX * FLANK_OFFSET_SCALE
    on, left, right = [], [], []
    for t in np.linspace(0.0, 1.0, FLANK_SAMPLES):
        x = q0[0] + (q1[0] - q0[0]) * t
        y = q0[1] + (q1[1] - q0[1]) * t
        pa = (x + line.a * off, y + line.b * off)
        pb = (x - line.a * off, y - line.b * off)
        if not all(0 <= p[0] < w and 0 <= p[1] < h for p in (pa, pb)):
            continue
        on.append(lab_l[int(y), int(x)])
        left.append(lab_l[int(pa[1]), int(pa[0])])
        right.append(lab_l[int(pb[1]), int(pb[0])])
    if len(on) < FLANK_SAMPLES * 0.4:
        return None
    return float(np.median(on) - max(np.median(left), np.median(right)))


def snap_to_ridge(frame, line, max_shift=SNAP_RANGE_PX):
    """Slides a candidate along its normal onto the brightest ridge it can find.

    Returns the shifted line. A no-op when nothing nearby is brighter, so a
    candidate that is not on paint is not dragged onto something that is.
    """
    lab_l = cv2.cvtColor(cv2.GaussianBlur(frame, (5, 5), 0),
                         cv2.COLOR_BGR2LAB)[:, :, 0].astype(float)
    best, best_shift = None, 0.0
    for shift in range(-max_shift, max_shift + 1):
        c = _line_contrast(lab_l, line, shift)
        if c is not None and (best is None or c > best):
            best, best_shift = c, float(shift)
    if best is None or best_shift == 0.0:
        return line
    return EdgeLine((line.p0[0] + line.a * best_shift, line.p0[1] + line.b * best_shift),
                    (line.p1[0] + line.a * best_shift, line.p1[1] + line.b * best_shift),
                    protected_side=line.protected_side, support=line.support)


def fit_line(mask, shape, frame=None):
    """Fits one straight edge line to a paint mask, or returns None.

    When `frame` is supplied the candidates are additionally required to pass
    the flank test, and the best-supported PASSING candidate wins - so a long
    roofline does not shadow a shorter stretch of genuine paint.
    """
    for line in _candidate_lines(mask, shape):
        if frame is None:
            return line
        # Ridge snapping (snap_to_ridge) is deliberately NOT applied here. It
        # raises a true line's contrast, but it also drags a near-miss candidate
        # onto whatever bright thing is within reach, and on the unmarked
        # barangay photos that was enough to manufacture an edge line where
        # there is no paint at all. Inventing a boundary is far worse than
        # failing to find one: every vehicle would then be judged against it.
        # It is kept as a tool for calibration, not used in the decision path.
        if flanks_ok(frame, line):
            return line
    return None


class EdgeFinder:
    """Accumulates paint evidence over a warm-up, then LOCKS the edge line.

    Feed every frame to `update`. While unlocked it blends the per-frame paint
    masks; once enough frames have been seen it fits the persistent paint and
    locks. After the lock the result never changes, so a vehicle covering the
    paint cannot move the boundary it is being judged against.
    """

    def __init__(self, warmup=WARMUP_FRAMES, persistence=PERSISTENCE_MIN):
        self.warmup = warmup
        self.persistence = persistence
        self.frames = 0
        self.retry_at = 0
        self.scale = 1.0
        self.accumulator = None
        # Running mean of the frames themselves. The flank test needs to know
        # what the scene looks like either side of a candidate stripe, and the
        # mean frame is the static scene with the traffic averaged out of it.
        self.mean_frame = None
        self.line = None
        self.locked = False
        self.failed_locks = 0
        self.road_votes = {ABOVE: 0, BELOW: 0}
        self._pending = []

    def update(self, frame):
        """Returns the locked EdgeLine, or None while still warming up.

        The frame is resized to PROCESS_WIDTH before detection so that every
        threshold in this module means the same thing on a 640px stream and a
        2560px one; the returned line is scaled back to the caller's pixels.
        """
        if self.locked:
            return self.line
        if frame.shape[1] != PROCESS_WIDTH:
            self.scale = frame.shape[1] / PROCESS_WIDTH
            frame = cv2.resize(
                frame, (PROCESS_WIDTH, int(frame.shape[0] / self.scale)))
        else:
            self.scale = 1.0
        mask = paint_mask(frame)
        if self.accumulator is None:
            self.accumulator = np.zeros(mask.shape, dtype=np.float32)
            self.mean_frame = np.zeros(frame.shape, dtype=np.float32)
        self.accumulator += (mask > 0)
        self.mean_frame += frame
        self.frames += 1
        if self.frames >= max(self.warmup, self.retry_at):
            self._lock(frame.shape)
        return self.line

    def persistent_mask(self):
        """The warm-up's static-paint mask - useful for debugging a failed lock."""
        if self.accumulator is None:
            return None
        keep = (self.accumulator / max(self.frames, 1)) >= self.persistence
        return (keep * 255).astype(np.uint8)

    def _lock(self, shape):
        mask = self.persistent_mask()
        # Bridge wear gaps along the stripe before fitting.
        k = cv2.getStructuringElement(cv2.MORPH_RECT,
                                      ((LINE_WIDTH_PX * 2) | 1,) * 2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        reference = (self.mean_frame / max(self.frames, 1)).astype(np.uint8)
        line = fit_line(mask, shape, frame=reference)
        if line is None:
            # No persistent paint yet. Keep accumulating and try again later.
            #
            # Do NOT rewind self.frames to force the retry: the vote accumulator
            # keeps every frame it has seen, so lowering the divisor inflates
            # every pixel's persistence and the next attempt judges against a
            # mask that cannot occur. That bug made a failed lock look like a
            # successful one when re-checked by hand.
            self.failed_locks += 1
            self.retry_at = self.frames + max(int(self.warmup * 0.5), 1)
            return
        line.protected_side = self._decide_side(line, shape)
        if self.scale != 1.0:
            line = EdgeLine((line.p0[0] * self.scale, line.p0[1] * self.scale),
                            (line.p1[0] * self.scale, line.p1[1] * self.scale),
                            protected_side=line.protected_side,
                            support=line.support)
        self.line = line
        self.locked = True

    def observe_traffic(self, vehicle_ground_points):
        """Votes on which side is the ROAD, from where vehicles actually drive.

        Called during warm-up with the ground point of every MOVING vehicle. The
        road is the side they occupy; the protected way is the other one. This
        is what makes the module orientation-agnostic - it works whether the
        footpath is at the top of frame or the bottom, without being told.
        """
        if self.line is None:
            self._pending.extend(vehicle_ground_points)
            return
        for pt in vehicle_ground_points:
            side = ABOVE if self.line.signed_distance(pt) < 0 else BELOW
            self.road_votes[side] += 1

    def _decide_side(self, line, shape):
        """The protected side is whichever one vehicles do NOT drive on."""
        for pt in [(x / self.scale, y / self.scale) for x, y in self._pending]:
            side = ABOVE if line.signed_distance(pt) < 0 else BELOW
            self.road_votes[side] += 1
        above, below = self.road_votes[ABOVE], self.road_votes[BELOW]
        if above + below >= 5:
            return BELOW if above > below else ABOVE
        # No traffic evidence. Fall back to image geometry: for a camera looking
        # down a street the road runs toward the BOTTOM of the frame, so the
        # side of the line further from the bottom edge is the protected way.
        h = shape[0]
        probe_road = (shape[1] / 2, h - 1)
        return ABOVE if line.signed_distance(probe_road) > 0 else BELOW
