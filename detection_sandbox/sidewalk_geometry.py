"""Sidewalk obstruction geometry: is a vehicle leaving enough room to walk past?

Shared by sidewalk_demo.py (the annotated worked example) and parking_web.py
(the browser tester), so the two can never drift apart.

The method in three steps:

  1. The sidewalk is clicked once as four corners, plus ONE tape-measure reading
     of its real width. That defines a homography from camera pixels to a
     top-down strip in metres.

  2. Each vehicle is reduced to a GROUND POINT — the bottom-centre of its box,
     which is very nearly the ground directly beneath it. Its extent across the
     walk comes from a per-class width prior, NOT from the box.

  3. On the top-down strip the sidewalk is a plain rectangle, so "how much room
     is left for a pedestrian" is the largest gap no vehicle covers.

WHY NOT THE WHOLE BOTTOM EDGE. The obvious idea is to project the box's entire
bottom edge and call that the footprint. It does not work. An axis-aligned box
takes its left/right extremes from whichever corners are widest in the image —
often FAR corners, or corners at roof height — while its bottom edge sits at the
depth of the NEAREST corner. Projecting those widths at that depth smears the
footprint sideways; on a test scene it drags a car parked cleanly on the road
0.49m onto the sidewalk. The bottom-CENTRE point does not suffer from this,
because the smearing is symmetric about it and cancels. See sidewalk_demo.py,
which prints both and measures the error against ground truth.
"""

import cv2
import numpy as np

# Extent across the walk, per class, in metres. A prior beats measuring it from
# the box: the box's width is corrupted by perspective, this is not. Assumes the
# vehicle is parked roughly parallel to the kerb, which is the common case; a
# motorcycle parked nose-in presents its length across the walk instead.
ACROSS_PRIOR_M = {
    "bicycle": 0.7,
    "motorcycle": 1.0,   # includes PH tricycles
    "car": 1.8,
    "truck": 2.4,
    "bus": 2.5,
}
DEFAULT_ACROSS_M = 1.8

DEFAULT_MIN_PASSABLE_M = 0.75   # roughly one adult
DEFAULT_ENCROACH_M = 0.10       # ignore a wheel a hand's breadth over the line
DEFAULT_PX_PER_M = 40           # top-down resolution; affects precision only

CLEAR, ENCROACHING, BLOCKED = "CLEAR", "ENCROACHING", "BLOCKED"


def ground_point(box):
    """Bottom-centre of a detection box — the ground beneath the vehicle.

    A bounding box is not a footprint: a jeepney's box is mostly air above the
    road. The bottom edge is the only part touching the ground, and its centre
    is the only part of that edge whose depth and lateral position agree.
    """
    x1, _, x2, y2 = box
    return ((x1 + x2) / 2.0, float(y2))


class Sidewalk:
    """A calibrated sidewalk: four clicked corners plus one tape measure.

    `quad` is four (x, y) image points in order:
        near-building, far-building, far-kerb, near-kerb
    ("near" = closest to the camera). `width_m` is the real width, measured.
    `length_m` only scales the along-axis, so a rough estimate is fine — it
    changes where a pinch point is reported, never how wide the gap is.
    """

    def __init__(self, quad, width_m, length_m=None,
                 px_per_m=DEFAULT_PX_PER_M,
                 min_passable_m=DEFAULT_MIN_PASSABLE_M,
                 encroach_m=DEFAULT_ENCROACH_M):
        self.quad = np.float32(quad)
        self.width_m = float(width_m)
        self.length_m = float(length_m) if length_m else self.width_m * 8
        self.px_per_m = float(px_per_m)
        self.min_passable_m = float(min_passable_m)
        self.encroach_m = float(encroach_m)

        self.length_px = self.length_m * self.px_per_m
        self.width_px = self.width_m * self.px_per_m
        dst = np.float32([(0, 0), (self.length_px, 0),
                          (self.length_px, self.width_px), (0, self.width_px)])
        self.H = cv2.getPerspectiveTransform(self.quad, dst)

    # ---- projection --------------------------------------------------------

    def to_strip(self, points):
        """Camera pixels -> top-down sidewalk pixels."""
        pts = np.float32(points).reshape(1, -1, 2)
        return cv2.perspectiveTransform(pts, self.H)[0]

    def footprint_span(self, box, label):
        """(along_lo, along_hi, across_lo, across_hi) in strip pixels, or None if
        the vehicle sits entirely off the sidewalk."""
        gx, gy = self.to_strip([ground_point(box)])[0]
        half = ACROSS_PRIOR_M.get(label, DEFAULT_ACROSS_M) / 2 * self.px_per_m
        along_lo, along_hi = max(gx - half, 0), min(gx + half, self.length_px)
        across_lo, across_hi = max(gy - half, 0), min(gy + half, self.width_px)
        if along_hi <= along_lo or across_hi <= across_lo:
            return None
        return along_lo, along_hi, across_lo, across_hi

    def blocked_m(self, span):
        return 0.0 if span is None else float(span[3] - span[2]) / self.px_per_m

    # ---- the measurement that matters --------------------------------------

    def free_width(self, spans, samples=240):
        """Narrowest gap left for a pedestrian (metres) and where along it is.

        Walks the sidewalk and at each step measures the largest run of width no
        vehicle covers. The verdict is the WORST point: a footpath is only as
        passable as its tightest pinch.
        """
        worst_free, worst_at = self.width_px, 0.0
        for i in range(samples + 1):
            x = self.length_px * i / samples
            blocked = [(lo, hi) for a_lo, a_hi, lo, hi in spans if a_lo <= x <= a_hi]
            gap, cursor = 0.0, 0.0
            for lo, hi in sorted(blocked):
                gap = max(gap, lo - cursor)
                cursor = max(cursor, hi)
            gap = max(gap, self.width_px - cursor)
            if gap < worst_free:
                worst_free, worst_at = gap, x
        # float(), not numpy float32: these cross a JSON boundary in parking_web.
        return float(worst_free) / self.px_per_m, float(worst_at) / self.px_per_m

    def verdict(self, free_m, encroaching):
        if not encroaching:
            return CLEAR
        return BLOCKED if free_m < self.min_passable_m else ENCROACHING

    # ---- drawing -----------------------------------------------------------

    def draw_zone(self, frame, color=(90, 200, 250)):
        cv2.polylines(frame, [self.quad.astype(np.int32)], True, color, 2)

    def draw_strip(self, frame, spans, free_m, at_m, origin=(None, 14),
                   scale=None, height=64):
        """Draws the top-down strip as an inset, so the verdict is legible."""
        h, w = frame.shape[:2]
        scale = scale or min((w - 40) / max(self.length_px, 1), 2.0)
        sw, sh = int(self.length_px * scale), max(int(self.width_px * scale), 18)
        x0 = origin[0] if origin[0] is not None else w - sw - 14
        y0 = origin[1]
        if x0 < 0 or y0 + sh + 26 > h:
            return
        cv2.rectangle(frame, (x0 - 6, y0 - 6), (x0 + sw + 6, y0 + sh + 24),
                      (24, 24, 28), -1)
        cv2.rectangle(frame, (x0, y0), (x0 + sw, y0 + sh), (86, 86, 94), -1)
        for a_lo, a_hi, c_lo, c_hi in spans:
            cv2.rectangle(frame,
                          (x0 + int(a_lo * scale), y0 + int(c_lo * scale)),
                          (x0 + int(a_hi * scale), y0 + int(c_hi * scale)),
                          (60, 60, 220), -1)
        cv2.rectangle(frame, (x0, y0), (x0 + sw, y0 + sh), (90, 200, 250), 1)
        px = x0 + int(at_m * self.px_per_m * scale)
        cv2.line(frame, (px, y0), (px, y0 + sh), (0, 235, 255), 2)
        cv2.putText(frame, f"top-down  {free_m:.2f}m free of {self.width_m:.2f}m",
                    (x0, y0 + sh + 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (200, 200, 200), 1)
