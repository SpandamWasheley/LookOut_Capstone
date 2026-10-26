"""Trace parking's no-parking polygon from where traffic actually drives.

The operator's job today is to click a dozen points around the carriageway in
the Edge Zones editor. This module proposes that polygon instead, from footage,
so the click-through becomes "check this and save" rather than "trace this from
scratch".

Why traffic and not pixels
--------------------------
A still frame does not say where the road is. Kerbs are hidden behind parked
cars, shadows read as edges, and an elevated oblique CCTV view is nothing like
the dashcam framing road-segmentation models are trained on. What this system
already knows, because watch_parking computes it every frame anyway, is where
VEHICLES ARE -- and a vehicle that drove across a patch of ground has proved
that patch is drivable road far more directly than any edge detector could.

So the road is traced from the GROUND CONTACT of vehicles that were seen to
MOVE -- the bottom edge of the box, where the wheels meet the road -- hulled
into a polygon.

The bottom EDGE, not its centre, because that is what the rule being fed tests:
ObstructionZone samples FOOTPRINT_SAMPLES points across a vehicle's bottom edge
and asks what fraction of them are inside. Tracing from centres would describe
the line traffic drives ALONG; tracing from footprints describes the width it
sweeps, which is the pathway itself. The difference is roughly a vehicle's
width of road -- and it is the difference between one lane of traffic reading
as "a straight line with no area" and reading as a lane.

Two filters, each with one job
------------------------------
A track must be seen `MIN_TRACK_SAMPLES` times before it counts, which throws
out single-frame false positives: a bush or a bin that one frame called a car
never forms a track.

A track must then have MOVED `MOVED_MIN_WIDTHS` of its own box width, which
throws out PARKED vehicles. That one is counter-intuitive -- a car parked on
the carriageway is exactly what this detector exists to catch, so its ground
point really is inside the zone -- but the rule cannot tell that car from one
parked legitimately in a driveway, a forecourt or a yard, and a convex hull is
maximally sensitive to its outliers: one vehicle sitting off-road swallows the
whole frame. Only moving vehicles are allowed to define the shape; stationary
ones are what the finished zone is then used to judge.

Displacement is measured in BOX WIDTHS, not pixels, so the rule means the same
thing for a motorcycle at the kerb and a truck down the block -- the same
near/far invariance obstruction_zone.py's _centre_dist uses.

No Django and no model
----------------------
Everything here takes boxes in and gives points out, so the rule is unit
testable with synthetic rectangles -- no torch, no weights, no camera, no
database. The caller supplies detections (core/vision/recognition.py's
detect_vehicles) and reads back a polygon. Association between consecutive
samples is done here, deliberately, rather than by ultralytics' tracker:
load_yolo() returns ONE shared model and its tracker keeps `persist=True`
state between calls, so two concurrent callers would interleave into a single
tracker conversation. Short-horizon nearest-centre matching is all "did this
thing move?" needs, and it costs no state that can be corrupted.
"""

import math

import cv2
import numpy as np

# The ONE thing imported from the rule this feeds: how a vehicle's ground
# contact is sampled. ObstructionZone tests a fraction of those points against
# the polygon, so tracing from the same points is what makes the traced shape
# mean the same thing as the shape being tested -- a private name, taken
# deliberately, because a local copy is exactly the drift this must not have.
from core.vision.obstruction_zone import _footprint_points

# --- sampling (the caller's budget, not used here) --------------------------
# A clip is sampled rather than decoded frame by frame: the shape of a road
# does not need 30 fps to establish, and every sample costs a model pass.
SAMPLE_STRIDE_SECONDS = 0.4
# Hard cap on model passes, so an hour of footage costs the same as a minute.
# At ~0.4s stride this is roughly the first 48 seconds of traffic, which on a
# street with any traffic at all is plenty and on a dead one would not be
# rescued by more.
MAX_SAMPLES = 120
# Inference resolution for the TRACE, well below recognition.VEHICLE_IMGSZ
# (1280) which the detection run itself uses.
#
# Detection needs a precise box on one vehicle at one moment. Tracing needs
# roughly where a hundred vehicles were, and the hull is decided by the
# EXTREMES of that cloud -- per-box imprecision averages out across sightings
# in a way it never does for a single judgement. Measured on the sample street
# footage, the same 75 samples at 640 produced a polygon 96% identical (IoU) to
# the one at 1280, in 27s instead of 64s. Paying 2.4x for 4% of a shape the
# operator reviews anyway is not a trade worth making.
TRACE_IMGSZ = 640
# What a REQUEST can afford, as opposed to the CLI's thoroughness. A sample
# costs about a third of a second of inference, so 120 is around 40 seconds of
# a held-open HTTP connection -- fine for a terminal that prints progress,
# miserable behind a spinner. The dashboard trades shape quality for a wait an
# operator will actually sit through; trace_zone on the command line is the
# way to spend longer on a difficult camera.
WEB_MAX_SAMPLES = 60
VEHICLE_CONF = 0.4

# --- the two filters --------------------------------------------------------
MIN_TRACK_SAMPLES = 3       # sightings before a track counts at all
MOVED_MIN_WIDTHS = 1.0      # own-box-widths of travel to count as traffic
# Association radius between consecutive samples, also in box widths. Wide,
# because samples are 0.4s apart and a vehicle covers ground in that time;
# over-merging two vehicles into one track is harmless here (the result is
# still "something drove along this line"), while splitting one vehicle into
# stationary fragments is not -- each fragment would fail the motion test.
ASSOC_MAX_WIDTHS = 2.5

# --- turning the point cloud into a polygon ---------------------------------
# Sightings of qualifying vehicles, NOT points: a sighting contributes a whole
# footprint's worth of points, so counting points would measure the sampling
# resolution rather than the traffic. 12 sightings is a couple of vehicles
# crossing the frame.
MIN_TRAFFIC_POINTS = 12     # below this there is not enough evidence to trace
# approxPolyDP tolerance, as a fraction of the frame's longest edge. The editor
# expects a shape an operator can nudge, not a 200-vertex blob traced around
# every wobble in the point cloud.
SIMPLIFY_EPSILON_FRAC = 0.015
MIN_POLYGON_POINTS = 3      # what ObstructionZone / the editor require
# A small residual grow-outward, for detector box slop rather than for geometry.
#
# This was 0.06 when the hull was built from box CENTRES, which really did sit
# a half-vehicle inboard of the kerb and needed compensating. Tracing the whole
# footprint already reaches the outer wheels, so most of that justification is
# gone and 6% then over-reached -- measurably: on the sample street footage it
# pushed the right-hand boundary off the carriageway and onto the concrete
# gutter. At 2% the boundary tracks the kerb.
PAD_FRACTION = 0.02


def ground_point(box):
    """Bottom-CENTRE of a box.

    One point per sighting, so this is what a track's path and its travelled
    distance are measured on. The hull is built from footprint() instead --
    see the module docstring.
    """
    x1, _y1, x2, y2 = box[:4]
    return ((x1 + x2) / 2.0, float(y2))


def footprint(box):
    """Points across a box's ground contact, exactly as the rule samples them.

    Delegated to ObstructionZone's own sampler so the traced shape and the
    tested shape cannot come to disagree about what a vehicle's footprint is.
    """
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    return [(float(px), float(py)) for px, py in _footprint_points((x1, y1, x2, y2))]


def box_width(box):
    x1, _y1, x2, _y2 = box[:4]
    return max(1.0, float(x2 - x1))


class Track:
    """One vehicle followed across consecutive samples.

    Not an identity in any lasting sense -- it exists only to answer "did this
    move?", and it is thrown away with the clip.
    """

    def __init__(self, box):
        self.points = [ground_point(box)]
        self.widths = [box_width(box)]
        self.boxes = [tuple(box[:4])]
        self.box = box

    def add(self, box):
        self.points.append(ground_point(box))
        self.widths.append(box_width(box))
        self.boxes.append(tuple(box[:4]))
        self.box = box

    def footprints(self):
        """Every ground-contact point this track ever stood on."""
        out = []
        for box in self.boxes:
            out.extend(footprint(box))
        return out

    @property
    def samples(self):
        return len(self.points)

    @property
    def span(self):
        """Greatest distance between any two of its ground points, in box widths.

        The EXTREME, not first-to-last: a vehicle that pulls out, drives up the
        road and parks again near where it started has still proved the road
        between, and first-to-last would read it as stationary. The scale is the
        mean observed width, so a box that grows as it approaches the camera
        does not inflate its own travel.
        """
        if len(self.points) < 2:
            return 0.0
        scale = sum(self.widths) / len(self.widths)
        best = 0.0
        for i, (ax, ay) in enumerate(self.points):
            for bx, by in self.points[i + 1:]:
                best = max(best, math.hypot(ax - bx, ay - by))
        return best / max(scale, 1.0)

    def is_traffic(self, min_samples=MIN_TRACK_SAMPLES,
                   min_widths=MOVED_MIN_WIDTHS):
        """Seen enough times to be real, and moved enough to be traffic."""
        return self.samples >= min_samples and self.span >= min_widths


def associate(tracks, boxes, max_widths=ASSOC_MAX_WIDTHS):
    """Extend `tracks` with this sample's `boxes`; return the updated list.

    Greedy nearest-centre matching, closest pair first, each track and each box
    used at most once -- the same shape of rule as disposal._match and
    obstruction_zone._absorb_lost. Unmatched boxes start new tracks; unmatched
    tracks are simply not extended (a track is a list of sightings, not a live
    object that has to be retired).

    Greedy rather than optimal on purpose: the cost of a wrong pairing here is
    one slightly odd path through a point cloud that is about to be hulled, and
    an assignment solver would be machinery bought for nothing.
    """
    pairs = []
    for ti, track in enumerate(tracks):
        tx, ty = track.points[-1]
        reach = max_widths * box_width(track.box)
        for bi, box in enumerate(boxes):
            bx, by = ground_point(box)
            dist = math.hypot(tx - bx, ty - by)
            if dist <= reach:
                pairs.append((dist, ti, bi))
    pairs.sort()

    used_tracks, used_boxes = set(), set()
    for _dist, ti, bi in pairs:
        if ti in used_tracks or bi in used_boxes:
            continue
        tracks[ti].add(boxes[bi])
        used_tracks.add(ti)
        used_boxes.add(bi)

    for bi, box in enumerate(boxes):
        if bi not in used_boxes:
            tracks.append(Track(box))
    return tracks


def traffic_points(tracks, min_samples=MIN_TRACK_SAMPLES,
                   min_widths=MOVED_MIN_WIDTHS):
    """Every ground-CONTACT point belonging to a track that passed both filters.

    The swept width of the traffic, which is the pathway. Many points per
    sighting; count sightings with traffic_sightings() instead when the
    question is "how much evidence is there", or the floor becomes meaningless.
    """
    out = []
    for track in tracks:
        if track.is_traffic(min_samples, min_widths):
            out.extend(track.footprints())
    return out


def traffic_sightings(tracks, min_samples=MIN_TRACK_SAMPLES,
                      min_widths=MOVED_MIN_WIDTHS):
    """How many times a qualifying vehicle was actually SEEN.

    The honest measure of how much the trace rests on. Footprint points are a
    fixed multiple of this, so counting them would inflate the evidence by the
    sampling resolution rather than by anything observed.
    """
    return sum(t.samples for t in tracks
               if t.is_traffic(min_samples, min_widths))


def hull(points):
    """Convex hull of a point cloud, as a list of (x, y). [] if degenerate."""
    if len(points) < MIN_POLYGON_POINTS:
        return []
    cloud = np.array(points, dtype=np.float32).reshape(-1, 1, 2)
    return [tuple(float(v) for v in p[0]) for p in cv2.convexHull(cloud)]


def simplify(points, frame_shape, epsilon_frac=SIMPLIFY_EPSILON_FRAC):
    """Drop vertices the shape does not need, via approxPolyDP.

    Never below MIN_POLYGON_POINTS: a tolerance that collapsed the road to two
    points would hand the editor something it cannot save, so an over-simplified
    result falls back to the hull it came from.
    """
    if len(points) <= MIN_POLYGON_POINTS:
        return list(points)
    height, width = frame_shape[:2]
    eps = epsilon_frac * max(width, height)
    poly = np.array(points, dtype=np.float32).reshape(-1, 1, 2)
    out = [tuple(float(v) for v in p[0]) for p in cv2.approxPolyDP(poly, eps, True)]
    return out if len(out) >= MIN_POLYGON_POINTS else list(points)


def pad(points, frame_shape, fraction=PAD_FRACTION):
    """Grow the polygon outward about its centroid, clamped to the frame.

    The hull runs through wheel-contact points, which sit inboard of the kerb
    by about half a vehicle's width, so an unpadded polygon systematically
    under-covers the road it traced.

    Scaling about the centroid, rather than offsetting each edge by a fixed
    distance: it pads a wide near kerb more than a narrow far one, which is the
    right way round for a carriageway that recedes to a vanishing point, and it
    cannot self-intersect the way a naive per-edge offset can on a concave
    shape. (The hull is convex anyway; simplify can only drop vertices.)
    """
    if not points or fraction <= 0:
        return list(points)
    height, width = frame_shape[:2]
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    scale = 1.0 + fraction
    return [(min(max(cx + (x - cx) * scale, 0.0), float(width - 1)),
             min(max(cy + (y - cy) * scale, 0.0), float(height - 1)))
            for x, y in points]


def trace(samples, frame_shape, min_samples=MIN_TRACK_SAMPLES,
          min_widths=MOVED_MIN_WIDTHS, min_points=MIN_TRAFFIC_POINTS,
          epsilon_frac=SIMPLIFY_EPSILON_FRAC, pad_fraction=PAD_FRACTION):
    """(points, report) for a sequence of per-sample vehicle box lists.

    `samples` is one list of boxes per sampled frame, in capture order --
    exactly what repeated detect_vehicles calls produce. `points` is the
    proposed polygon in frame pixels, or None when the footage did not support
    one; `report` always says what was seen, because "no zone" needs a reason
    an operator can act on ("no vehicles at all" and "vehicles, but all parked"
    call for completely different next steps).

    `report["cloud"]` is the ground points the polygon was traced FROM, which
    is the fastest way to see why a zone came out the shape it did. It is a
    diagnostic for the CLI's preview, not payload: it is as long as the traffic
    was busy, so a caller serialising the report to a browser should drop it.
    """
    tracks = []
    for boxes in samples:
        associate(tracks, list(boxes), ASSOC_MAX_WIDTHS)

    moving = [t for t in tracks if t.is_traffic(min_samples, min_widths)]
    cloud = traffic_points(tracks, min_samples, min_widths)
    sightings = traffic_sightings(tracks, min_samples, min_widths)
    report = {
        "samples": len(samples),
        "detections": sum(len(b) for b in samples),
        "tracks": len(tracks),
        "moving_tracks": len(moving),
        # How often a qualifying vehicle was SEEN. `cloud` is this times the
        # footprint sampling resolution, which measures nothing observed.
        "ground_points": sightings,
        "cloud": cloud,
    }

    if not report["detections"]:
        report["reason"] = ("No vehicles were detected in this footage, so "
                            "there is nothing to trace the road from.")
        return None, report
    if not moving:
        report["reason"] = ("Vehicles were detected but none of them moved, so "
                            "the road cannot be told from the kerb. Use footage "
                            "with traffic passing through.")
        return None, report
    if sightings < min_points:
        report["reason"] = (f"Only {sightings} sighting(s) of moving vehicles "
                            "— too little traffic to trace a road. Use a longer "
                            "clip or a busier stretch.")
        return None, report

    shape = hull(cloud)
    shape = simplify(shape, frame_shape, epsilon_frac)
    shape = pad(shape, frame_shape, pad_fraction)
    if len(shape) < MIN_POLYGON_POINTS:
        # Much rarer now the hull is built from footprints: a single lane of
        # traffic sweeps a vehicle's width and so has real area. What still
        # reaches here is a degenerate cloud -- one vehicle, never moving
        # across the frame at all.
        report["reason"] = ("The traffic seen covered no width of road. Use "
                            "footage with vehicles driving through the area.")
        return None, report
    report["polygon_points"] = len(shape)
    return shape, report


def to_spec(points):
    """The camera `edges` shape EdgeCanvas.toSpec writes and ObstructionZone reads.

    Built here as well as in the browser so an auto-trace lands in storage
    identically to a hand-drawn zone -- including "type": "zone", which
    obstruction.build_edge treats a MISSING value as an open path for, turning
    a polygon into a line along its own outline.
    """
    if len(points) < MIN_POLYGON_POINTS:
        return {}
    return {"road": {"type": "zone",
                     "points": [[int(round(x)), int(round(y))] for x, y in points]}}


def overlay(frame, points, cloud=()):
    """A copy of `frame` with the proposed polygon (and optionally the point
    cloud it came from) drawn on, for the preview the operator is shown.

    Copied, not drawn in place: the caller may still want the clean frame as
    the canvas background behind an editable shape.
    """
    out = frame.copy()
    for x, y in cloud:
        cv2.circle(out, (int(x), int(y)), 2, (0, 191, 255), -1)
    if len(points) >= MIN_POLYGON_POINTS:
        poly = np.array([[int(x), int(y)] for x, y in points], dtype=np.int32)
        shade = out.copy()
        cv2.fillPoly(shade, [poly], (86, 95, 255))
        cv2.addWeighted(shade, 0.25, out, 0.75, 0, out)
        cv2.polylines(out, [poly], True, (86, 95, 255), 3)
    return out


# --- clip glue --------------------------------------------------------------
# Kept at the bottom and importing nothing new: everything above is pure
# geometry over boxes somebody else detected, and that is what the tests
# exercise. These two touch a video file and the vehicle model, so they are
# the part that cannot run without weights.

def sample_clip(path, stride_seconds=SAMPLE_STRIDE_SECONDS,
                max_samples=MAX_SAMPLES):
    """Yield (position_seconds, frame) at roughly `stride_seconds` apart.

    Reads SEQUENTIALLY and skips with grab(), rather than seeking to each
    sample. Seeking looks like the obvious way to do this and is a trap: on a
    long-GOP container (the H.264/H.265 MKV a CCTV recorder writes)
    CAP_PROP_POS_FRAMES re-decodes from the nearest keyframe on EVERY call, so
    the cost is paid again per sample. Measured on a 30s 2560x1440 clip,
    seeking cost 1.65s a sample against 0.33s for the model pass itself -- the
    sampling was five times the price of the inference it existed to feed.

    grab() decodes a frame but skips the colour conversion retrieve() does, so
    skipped frames cost a fraction of a wanted one and nothing is re-decoded.

    One path for a FILE and a LIVE stream, which is the other thing this buys:
    an RTSP capture cannot seek at all, so the two used to need separate
    branches that could drift. Reading forward is simply what both do.

    Only the first `max_samples * stride_seconds` of a file is covered, and
    that was already true when this seeked -- the cap bounds the span either
    way. An unreadable source yields nothing rather than raising; the caller
    turns an empty sample list into an explained refusal.
    """
    cap = cv2.VideoCapture(int(path) if str(path).isdigit() else str(path))
    try:
        if not cap.isOpened():
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        if fps <= 0:
            fps = 25.0            # a plausible default beats refusing outright
        step = max(1, int(round(fps * stride_seconds)))
        read, taken = 0, 0
        while taken < max_samples:
            if read % step:
                if not cap.grab():
                    break
            else:
                ok, frame = cap.read()
                if not ok:
                    break
                yield read / fps, frame
                taken += 1
            read += 1
    finally:
        cap.release()


def trace_clip(path, detect, stride_seconds=SAMPLE_STRIDE_SECONDS,
               max_samples=MAX_SAMPLES, **kwargs):
    """(points, report, preview_frame) for a clip on disk.

    `detect` is injected rather than imported so this stays callable with a
    stub: it takes a frame and returns vehicle boxes, i.e.
    recognition.detect_vehicles. The preview frame is the FIRST sample, which
    is also the frame whose resolution the polygon's coordinates are in and so
    the one the editor must draw it over.
    """
    samples, first = [], None
    for _pos, frame in sample_clip(path, stride_seconds, max_samples):
        if first is None:
            first = frame
        samples.append(detect(frame))

    if first is None:
        return None, {"samples": 0, "detections": 0, "tracks": 0,
                      "moving_tracks": 0, "ground_points": 0, "cloud": [],
                      "reason": "Could not read any frames from that video."}, None

    points, report = trace(samples, first.shape, **kwargs)
    return points, report, first
