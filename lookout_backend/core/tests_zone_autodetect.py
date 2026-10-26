"""Automatic parking-zone tracing — the geometry, on synthetic boxes.

Everything under test here takes vehicle boxes in and gives a polygon out, so
none of it needs torch, weights, a video file or a database. A drawn rectangle
driving across a drawn frame exercises the actual rule: does traffic define the
road, and does a parked vehicle stay out of it?
"""

import ast
import inspect
import json
import types

from django.test import TestCase

from core.models import Camera
from core.vision import zone_autodetect as auto

FRAME = (720, 1280, 3)        # h, w, c — the shape cv2 reports


def box(cx, cy, w=80, h=50):
    """A vehicle box centred on (cx, cy), as (x1, y1, x2, y2)."""
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def drive(start_x, end_x, y, steps=10, w=80):
    """One vehicle's boxes as it drives along y, one per sample."""
    if steps == 1:
        return [box(start_x, y, w)]
    step = (end_x - start_x) / (steps - 1)
    return [box(start_x + step * i, y, w) for i in range(steps)]


def samples_from(*vehicle_tracks):
    """Per-sample box lists from one list of boxes per vehicle.

    Each argument is a vehicle's own sequence; they are zipped into frames, so
    `samples_from(a, b)` is two vehicles seen together over len(a) samples.
    """
    length = max(len(t) for t in vehicle_tracks)
    return [[t[i] for t in vehicle_tracks if i < len(t)] for i in range(length)]


class GroundPointTests(TestCase):
    def test_the_ground_point_is_the_bottom_centre(self):
        self.assertEqual(auto.ground_point((100, 50, 200, 150)), (150.0, 150.0))

    def test_extra_tuple_fields_are_ignored(self):
        """detect_vehicles returns (x1, y1, x2, y2, conf, label), so the box
        arrives with a confidence and a class name attached."""
        self.assertEqual(auto.ground_point((100, 50, 200, 150, 0.9, "car")),
                         (150.0, 150.0))

    def test_a_zero_width_box_does_not_divide_by_zero(self):
        self.assertEqual(auto.box_width((100, 50, 100, 150)), 1.0)


class TrackTests(TestCase):
    def test_a_single_sighting_has_no_span(self):
        self.assertEqual(auto.Track(box(100, 400)).span, 0.0)

    def test_span_is_measured_in_box_widths(self):
        """So the rule means the same thing for a motorcycle at the kerb and a
        truck down the block."""
        track = auto.Track(box(100, 400, w=80))
        track.add(box(260, 400, w=80))
        self.assertAlmostEqual(track.span, 2.0, places=5)

    def test_span_is_the_extreme_pair_not_first_to_last(self):
        """A vehicle that pulls out, drives up the road and parks again near
        where it started has still proved the road in between."""
        track = auto.Track(box(100, 400))
        track.add(box(500, 400))
        track.add(box(104, 400))
        self.assertGreater(track.span, 4.0)

    def test_a_growing_box_does_not_inflate_its_own_travel(self):
        """The scale is the mean observed width, so a vehicle approaching the
        camera is not credited with distance it did not cover."""
        near = auto.Track(box(100, 400, w=80))
        near.add(box(180, 400, w=120))
        self.assertLess(near.span, 1.0)

    def test_a_parked_vehicle_is_not_traffic(self):
        track = auto.Track(box(300, 500))
        for _ in range(20):
            track.add(box(301, 500))
        self.assertEqual(track.samples, 21)
        self.assertFalse(track.is_traffic())

    def test_a_flicker_is_not_traffic_however_far_it_jumped(self):
        """Two sightings a long way apart are more likely two false positives
        than one vehicle, so the sample floor applies first."""
        track = auto.Track(box(100, 400))
        track.add(box(900, 400))
        self.assertFalse(track.is_traffic())

    def test_a_moving_vehicle_seen_enough_times_is_traffic(self):
        track = auto.Track(box(100, 400))
        for x in (200, 300, 400):
            track.add(box(x, 400))
        self.assertTrue(track.is_traffic())


class AssociateTests(TestCase):
    def test_the_first_sample_starts_a_track_per_box(self):
        tracks = auto.associate([], [box(100, 400), box(600, 400)])
        self.assertEqual(len(tracks), 2)

    def test_a_nearby_box_extends_the_existing_track(self):
        tracks = auto.associate([], [box(100, 400)])
        auto.associate(tracks, [box(160, 400)])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].samples, 2)

    def test_a_box_beyond_the_reach_starts_its_own_track(self):
        tracks = auto.associate([], [box(100, 400)])
        auto.associate(tracks, [box(900, 400)])
        self.assertEqual(len(tracks), 2)

    def test_two_vehicles_keep_their_own_tracks(self):
        tracks = auto.associate([], [box(100, 400), box(700, 400)])
        auto.associate(tracks, [box(140, 400), box(740, 400)])
        self.assertEqual(len(tracks), 2)
        self.assertEqual([t.samples for t in tracks], [2, 2])

    def test_one_box_cannot_extend_two_tracks(self):
        """Each track and each box is used at most once, so two vehicles
        crossing do not both claim the same detection."""
        tracks = auto.associate([], [box(300, 400), box(340, 400)])
        auto.associate(tracks, [box(320, 400)])
        self.assertEqual(sorted(t.samples for t in tracks), [1, 2])

    def test_the_closest_pair_wins(self):
        tracks = auto.associate([], [box(300, 400)])
        auto.associate(tracks, [box(800, 400), box(310, 400)])
        extended = [t for t in tracks if t.samples == 2]
        self.assertEqual(len(extended), 1)
        self.assertAlmostEqual(extended[0].points[-1][0], 310.0, places=5)

    def test_an_empty_sample_extends_nothing_and_adds_nothing(self):
        tracks = auto.associate([], [box(100, 400)])
        auto.associate(tracks, [])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].samples, 1)

    def test_the_reach_scales_with_the_box(self):
        """A truck covers more ground per sample than a motorcycle, and the
        association radius is in box widths for exactly that reason."""
        small = auto.associate([], [box(300, 400, w=20)])
        auto.associate(small, [box(400, 400, w=20)])
        self.assertEqual(len(small), 2, "100px is far for a 20px-wide vehicle")

        large = auto.associate([], [box(300, 400, w=200)])
        auto.associate(large, [box(400, 400, w=200)])
        self.assertEqual(len(large), 1, "100px is nothing for a 200px-wide one")


class HullAndShapeTests(TestCase):
    def test_a_hull_needs_three_points(self):
        self.assertEqual(auto.hull([(0, 0), (10, 10)]), [])

    def test_interior_points_are_dropped_from_the_hull(self):
        shape = auto.hull([(0, 0), (100, 0), (100, 100), (0, 100), (50, 50)])
        self.assertNotIn((50.0, 50.0), shape)
        self.assertEqual(len(shape), 4)

    def test_simplify_never_goes_below_three_points(self):
        """A tolerance that collapsed the road to a line would hand the editor
        something it cannot save."""
        square = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
        self.assertGreaterEqual(len(auto.simplify(square, FRAME, 10.0)), 3)

    def test_simplify_keeps_a_shape_the_operator_can_nudge(self):
        """A hull traced around a wobbly point cloud has a vertex per wobble;
        the editor wants a handful."""
        import math as _m
        circle = [(640 + 300 * _m.cos(i / 120 * 2 * _m.pi),
                   360 + 200 * _m.sin(i / 120 * 2 * _m.pi)) for i in range(120)]
        self.assertLess(len(auto.simplify(auto.hull(circle), FRAME)), 20)

    def test_padding_grows_the_shape(self):
        """Ground points run through wheel contacts, which sit inboard of the
        kerb, so an unpadded polygon under-covers the road it traced."""
        square = [(500.0, 300.0), (700.0, 300.0), (700.0, 400.0), (500.0, 400.0)]
        grown = auto.pad(square, FRAME, 0.10)
        self.assertLess(min(p[0] for p in grown), 500.0)
        self.assertGreater(max(p[0] for p in grown), 700.0)

    def test_padding_keeps_the_centroid(self):
        square = [(500.0, 300.0), (700.0, 300.0), (700.0, 400.0), (500.0, 400.0)]
        grown = auto.pad(square, FRAME, 0.10)
        self.assertAlmostEqual(sum(p[0] for p in grown) / 4, 600.0, places=3)
        self.assertAlmostEqual(sum(p[1] for p in grown) / 4, 350.0, places=3)

    def test_padding_is_clamped_to_the_frame(self):
        """A road that already runs to the edge of shot cannot be padded off it
        — the editor would draw vertices nobody can reach."""
        wide = [(0.0, 0.0), (1279.0, 0.0), (1279.0, 719.0), (0.0, 719.0)]
        for x, y in auto.pad(wide, FRAME, 0.25):
            self.assertGreaterEqual(x, 0.0)
            self.assertGreaterEqual(y, 0.0)
            self.assertLessEqual(x, 1279.0)
            self.assertLessEqual(y, 719.0)

    def test_padding_zero_is_a_no_op(self):
        square = [(500.0, 300.0), (700.0, 300.0), (700.0, 400.0)]
        self.assertEqual(auto.pad(square, FRAME, 0.0), square)


class TraceTests(TestCase):
    """The whole chain, on footage described as boxes."""

    def test_traffic_across_the_frame_traces_a_road(self):
        near = drive(100, 1100, 600, steps=12)
        far = drive(1100, 100, 420, steps=12)
        points, report = auto.trace(samples_from(near, far), FRAME)
        self.assertIsNotNone(points, report.get("reason"))
        self.assertGreaterEqual(len(points), 3)
        self.assertEqual(report["moving_tracks"], 2)

    def test_the_traced_road_covers_where_the_traffic_drove(self):
        import cv2
        import numpy as np
        # Two lanes. The cloud is WHEEL CONTACTS, so the traced band runs
        # between the two lanes' ground lines (625 and 475), not between their
        # box centres.
        near = drive(200, 1000, 600, steps=12)
        far = drive(1000, 200, 450, steps=12)
        points, _report = auto.trace(samples_from(near, far), FRAME)
        poly = np.array([[int(x), int(y)] for x, y in points], dtype=np.int32)
        for probe in ((600, 550), (300, 500), (900, 600), (600, 625)):
            self.assertGreaterEqual(
                cv2.pointPolygonTest(poly, probe, False), 0,
                f"{probe} is where traffic drove and must be inside the zone")

    def test_a_vehicle_parked_off_the_road_does_not_balloon_the_zone(self):
        """The failure this rule exists to stop: a convex hull is maximally
        sensitive to its outliers, so one vehicle sitting in a yard would
        swallow the frame."""
        import cv2
        import numpy as np
        near = drive(300, 900, 620, steps=12)
        far = drive(900, 300, 500, steps=12)
        yard = [box(1200, 120)] * 12          # parked, top-right, never moves
        points, report = auto.trace(samples_from(near, far, yard), FRAME)
        self.assertEqual(report["moving_tracks"], 2)
        poly = np.array([[int(x), int(y)] for x, y in points], dtype=np.int32)
        self.assertLess(cv2.pointPolygonTest(poly, (1200, 120), False), 0,
                        "the parked vehicle's spot must not be inside the zone")

    def test_a_single_frame_false_positive_is_ignored(self):
        near = drive(300, 900, 620, steps=12)
        far = drive(900, 300, 500, steps=12)
        blip = [box(1200, 120)]               # seen once, then gone
        _points, report = auto.trace(samples_from(near, far, blip), FRAME)
        self.assertEqual(report["moving_tracks"], 2)

    def test_no_vehicles_at_all_is_refused_with_a_reason(self):
        points, report = auto.trace([[], [], []], FRAME)
        self.assertIsNone(points)
        self.assertIn("No vehicles", report["reason"])

    def test_vehicles_that_never_moved_are_refused_with_their_own_reason(self):
        """"Nothing was detected" and "everything was parked" call for
        completely different next steps from the operator."""
        parked = [box(400, 500)] * 15
        points, report = auto.trace(samples_from(parked), FRAME)
        self.assertIsNone(points)
        self.assertIn("none of them moved", report["reason"])
        self.assertEqual(report["tracks"], 1)

    def test_too_little_traffic_is_refused_with_its_own_reason(self):
        """Counted in SIGHTINGS of moving vehicles, not in the points they
        contribute — one sighting is a whole footprint's worth of points, so
        counting points would measure the sampling resolution instead."""
        points, report = auto.trace(
            samples_from(drive(300, 600, 620, steps=4)), FRAME,
            min_points=12)
        self.assertIsNone(points)
        self.assertEqual(report["ground_points"], 4, "sightings, not points")
        self.assertIn("too little traffic", report["reason"])

    def test_traffic_that_covers_no_width_is_refused(self):
        """A degenerate hull. Saying so beats offering a zone with no width.

        Footprint points spread along the box's own x-axis, so traffic running
        purely left-to-right at a fixed distance stays collinear however many
        points each sighting contributes — tracing the footprint widens the
        hull only where the direction of travel differs from that axis, i.e.
        traffic receding up a street, which is the real case.
        """
        points, report = auto.trace(
            samples_from(drive(100, 1200, 600, steps=20)), FRAME, min_points=4)
        self.assertIsNone(points)
        self.assertIn("no width", report["reason"])

    def test_traffic_receding_up_the_street_traces_a_lane(self):
        """The real framing: a vehicle driving away shrinks and its ground
        contact climbs the frame, so one lane does sweep an area."""
        lane = [box(300 + i * 40, 620 - i * 14, w=150 - i * 9, h=90 - i * 5)
                for i in range(14)]
        points, report = auto.trace(samples_from(lane), FRAME, min_points=4)
        self.assertIsNotNone(points, report.get("reason"))
        self.assertGreaterEqual(len(points), 3)

    def test_the_report_always_says_what_was_seen(self):
        """So a refusal is diagnosable without re-running anything."""
        _points, report = auto.trace(samples_from(drive(100, 900, 600)), FRAME)
        for key in ("samples", "detections", "tracks", "moving_tracks",
                    "ground_points"):
            self.assertIn(key, report)


class SpecTests(TestCase):
    def test_the_spec_matches_what_the_editor_writes(self):
        spec = auto.to_spec([(10.4, 20.6), (300.0, 20.0), (300.0, 200.0)])
        self.assertEqual(set(spec), {"road"})
        self.assertEqual(spec["road"]["points"], [[10, 21], [300, 20], [300, 200]])

    def test_the_type_is_written_explicitly(self):
        """obstruction.build_edge defaults a MISSING type to an open path, so
        dropping it would silently turn the polygon into a line along its own
        outline."""
        self.assertEqual(
            auto.to_spec([(0, 0), (10, 0), (10, 10)])["road"]["type"], "zone")

    def test_too_few_points_is_an_empty_spec_not_a_broken_one(self):
        self.assertEqual(auto.to_spec([(0, 0), (10, 10)]), {})

    def test_the_browser_and_the_backend_agree_on_the_shape(self):
        """EdgeCanvas.toSpec builds the same payload in JS. If the two drift,
        an auto-traced zone is stored differently from a hand-drawn one."""
        from pathlib import Path
        from django.conf import settings
        source = (Path(settings.BASE_DIR).parent / "lookout" / "src"
                  / "EdgeCanvas.jsx").read_text(encoding="utf-8")
        self.assertIn('type: "zone"', source)
        self.assertIn("road:", source)


class PurityTests(TestCase):
    """What this module is allowed to depend on.

    Asserted against the AST rather than the source text, because the module
    docstring discusses django, torch and ultralytics by name to explain why it
    does NOT use them — and a string search cannot tell an explanation from an
    import.
    """

    def _imported(self):
        tree = ast.parse(inspect.getsource(auto))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_it_imports_nothing_but_geometry(self):
        """Same discipline as recognition.py and disposal.py: the rule stays
        importable and testable without a database, weights or a camera.

        obstruction_zone is the one allowed import, and only for
        _footprint_points: the traced shape and the tested shape must sample a
        vehicle's ground contact identically, and a local copy is exactly the
        drift that would break. It is itself pure CV, so it costs nothing.
        """
        self.assertEqual(
            self._imported(),
            {"math", "cv2", "numpy", "core.vision.obstruction_zone"})

    def test_the_model_is_injected_rather_than_imported(self):
        """trace_clip takes `detect`, so the chain is runnable with a stub and
        importing this module never costs torch."""
        self.assertIn("detect", inspect.signature(auto.trace_clip).parameters)
        for heavy in ("torch", "ultralytics", "core.vision.recognition"):
            self.assertNotIn(heavy, self._imported())


class TraceClipTests(TestCase):
    """trace_clip's wiring, with both the video and the model stubbed."""

    def test_an_unreadable_video_is_explained_not_raised(self):
        points, report, frame = auto.trace_clip(
            "does-not-exist.mp4", detect=lambda f: [])
        self.assertIsNone(points)
        self.assertIsNone(frame)
        self.assertIn("Could not read", report["reason"])

    def test_the_preview_frame_is_the_first_sample(self):
        """It is also the frame whose resolution the polygon's coordinates are
        in, so it is the one the editor has to draw the shape over."""
        import numpy as np
        # Each stub frame is filled with its own index, so the returned preview
        # identifies itself.
        frames = [(i * 0.4, np.full((720, 1280, 3), i, dtype=np.uint8))
                  for i in range(12)]
        near = drive(100, 1100, 600, steps=12)
        far = drive(1100, 100, 420, steps=12)
        seen = []

        def detect(frame):
            i = len(seen)
            seen.append(i)
            return [near[i], far[i]]

        original = auto.sample_clip
        auto.sample_clip = lambda *_a, **_k: iter(frames)
        try:
            points, report, preview = auto.trace_clip("x.mp4", detect=detect)
        finally:
            auto.sample_clip = original

        self.assertIsNotNone(points, report.get("reason"))
        self.assertEqual(int(preview[0][0][0]), 0, "the first sample, not the last")
        self.assertEqual(len(seen), 12, "every sample is offered to the detector")


class TraceZoneEndpointTests(TestCase):
    """DetectionJobViewSet.trace_zone — the HTTP contract around the tracer.

    It lives on the detection-job route, not on a camera, because the clip it
    reads is one already staged for a RUN: that is where the operator is
    standing when they need the area drawn, and the file is already on the
    server by then.

    The tracer itself is covered above on synthetic boxes; these pin down what
    the Run Detection page is handed.
    """

    URL = "/api/detection-jobs/trace-zone/"

    def setUp(self):
        from rest_framework.test import APIClient
        from core.models import User
        self.admin = User.objects.create_user(
            username="adm", password="x", role="admin", display_name="Admin")
        self.dispatcher = User.objects.create_user(
            username="dsp", password="x", role="dispatcher", display_name="Dis")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    # ---- helpers -----------------------------------------------------------

    def _staged(self, name="clip.mp4"):
        """A staged clip on disk, and the token that names it."""
        import uuid
        from django.conf import settings as dj
        token = f"{uuid.uuid4().hex}_{name}"
        path = dj.MEDIA_ROOT / "uploads" / token
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not really a video")
        self.addCleanup(lambda: path.exists() and path.unlink())
        return token, path

    def _clip_upload(self, name="clip.mp4"):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, b"not really a video",
                                  content_type="video/mp4")

    def _patched(self, points, report, preview):
        """Stands in for the whole trace, so no weights are loaded in a test."""
        from unittest import mock
        return mock.patch("core.vision.zone_autodetect.trace_clip",
                          return_value=(points, report, preview))

    def _frame(self):
        import numpy as np
        return np.full((480, 640, 3), 120, dtype=np.uint8)

    def _report(self, **over):
        base = {"samples": 40, "detections": 105, "tracks": 9,
                "moving_tracks": 9, "ground_points": 105, "cloud": [(1, 2)] * 105}
        base.update(over)
        return base

    def _post(self, **data):
        return self.client.post(self.URL, data, format="multipart")

    # ---- the staged path ---------------------------------------------------

    def test_a_staged_clip_is_traced_without_being_re_uploaded(self):
        """The whole reason this takes a token: the clip is routinely 200 MB and
        is already on the server by the time the area is being drawn."""
        token, _path = self._staged()
        points = [(10.0, 20.0), (600.0, 30.0), (600.0, 400.0), (20.0, 440.0)]
        with self._patched(points, self._report(), self._frame()) as traced:
            res = self._post(staged_token=token)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["points"]), 4)
        self.assertEqual(str(traced.call_args.args[0]).endswith(token), True)

    def test_the_staged_clip_survives_being_looked_at(self):
        """It belongs to the run that staged it. Deleting it here would pull the
        footage out from under the detection the operator is about to start."""
        token, path = self._staged()
        with self._patched([(0.0, 0.0), (9.0, 0.0), (9.0, 9.0)],
                           self._report(), self._frame()):
            self._post(staged_token=token)
        self.assertTrue(path.is_file(), "the staged clip must still be there")

    def test_the_points_come_back_with_the_resolution_they_are_in(self):
        """The page draws them over a frame it got separately, so it has to be
        able to check the two agree rather than silently misplacing the zone."""
        with self._patched([(0.0, 0.0), (9.0, 0.0), (9.0, 9.0)],
                           self._report(), self._frame()):
            res = self._post(staged_token=self._staged()[0])
        self.assertEqual((res.data["width"], res.data["height"]), (640, 480))

    def test_a_token_that_is_not_a_token_is_refused(self):
        """Resolved into a MEDIA_ROOT path, so the shape is the traversal
        guard — the same regex create() anchors on."""
        with self._patched(None, self._report(), None) as traced:
            res = self._post(staged_token="../../etc/passwd")
        self.assertEqual(res.status_code, 400)
        traced.assert_not_called()

    def test_an_expired_staged_clip_says_to_upload_it_again(self):
        import uuid
        res = self._post(staged_token=f"{uuid.uuid4().hex}_gone.mp4")
        self.assertEqual(res.status_code, 400)
        self.assertIn("expired", res.data["detail"])

    # ---- the direct-file path ----------------------------------------------

    def test_a_direct_file_still_works_for_scripts_and_tests(self):
        with self._patched([(0.0, 0.0), (9.0, 0.0), (9.0, 9.0)],
                           self._report(), self._frame()):
            res = self._post(file=self._clip_upload())
        self.assertEqual(res.status_code, 200)

    def test_a_direct_upload_is_cleaned_up(self):
        """Unlike a staged clip, this copy is scratch and belongs to nobody."""
        from django.conf import settings as dj
        uploads = dj.MEDIA_ROOT / "uploads"
        before = set(uploads.glob("*")) if uploads.exists() else set()
        with self._patched([(0.0, 0.0), (9.0, 0.0), (9.0, 9.0)],
                           self._report(), self._frame()):
            self._post(file=self._clip_upload())
        after = set(uploads.glob("*")) if uploads.exists() else set()
        self.assertEqual(after - before, set())

    def test_an_unsupported_file_type_is_refused_before_any_inference(self):
        with self._patched(None, self._report(), None) as traced:
            res = self._post(file=self._clip_upload("notes.txt"))
        self.assertEqual(res.status_code, 400)
        traced.assert_not_called()

    def test_neither_a_token_nor_a_file_is_a_bad_request(self):
        res = self._post()
        self.assertEqual(res.status_code, 400)

    # ---- what comes back ---------------------------------------------------

    def test_the_point_cloud_is_not_shipped_to_the_browser(self):
        """One entry per sighting — as long as the traffic was busy — and a
        diagnostic for the CLI's preview, not something the page draws."""
        with self._patched([(0.0, 0.0), (9.0, 0.0), (9.0, 9.0)],
                           self._report(), self._frame()):
            res = self._post(staged_token=self._staged()[0])
        self.assertNotIn("cloud", res.data["report"])
        self.assertEqual(res.data["report"]["moving_tracks"], 9)

    def test_footage_with_no_traffic_is_422_with_a_sentence_to_show(self):
        """The request was perfectly well formed; the clip just cannot answer
        the question. The operator fixes that with different footage, not a
        different request."""
        report = self._report(moving_tracks=0, ground_points=0, cloud=[],
                              reason="Vehicles were detected but none of them moved.")
        with self._patched(None, report, self._frame()):
            res = self._post(staged_token=self._staged()[0])
        self.assertEqual(res.status_code, 422)
        self.assertIn("none of them moved", res.data["detail"])
        self.assertNotIn("cloud", res.data["report"])

    def test_a_failure_inside_the_tracer_explains_itself(self):
        from unittest import mock
        with mock.patch("core.vision.zone_autodetect.trace_clip",
                        side_effect=RuntimeError("no weights")):
            res = self._post(staged_token=self._staged()[0])
        self.assertEqual(res.status_code, 500)
        self.assertIn("no weights", res.data["detail"])

    def test_a_failed_trace_still_leaves_the_staged_clip_alone(self):
        from unittest import mock
        token, path = self._staged()
        with mock.patch("core.vision.zone_autodetect.trace_clip",
                        side_effect=RuntimeError("boom")):
            self._post(staged_token=token)
        self.assertTrue(path.is_file())

    # ---- gates -------------------------------------------------------------

    def test_a_dispatcher_may_not_trace_a_zone(self):
        """Same admin gate as the rest of the detection harness."""
        self.client.force_authenticate(self.dispatcher)
        res = self._post(staged_token=self._staged()[0])
        self.assertEqual(res.status_code, 403)

    def test_an_anonymous_caller_may_not_trace_a_zone(self):
        self.client.force_authenticate(None)
        res = self._post(staged_token=self._staged()[0])
        self.assertIn(res.status_code, (401, 403))

    def test_a_server_that_runs_no_detectors_says_so(self):
        """It would have to load the vehicle model, which is exactly what such
        a server does not have."""
        with self.settings(DETECTION_ENABLED=False):
            res = self._post(staged_token=self._staged()[0])
        self.assertEqual(res.status_code, 503)

    # ---- budget ------------------------------------------------------------

    def test_the_web_budget_is_smaller_than_the_cli_one(self):
        """A held-open HTTP connection cannot afford the CLI's thoroughness."""
        self.assertLess(auto.WEB_MAX_SAMPLES, auto.MAX_SAMPLES)

    def test_the_endpoint_uses_the_web_budget(self):
        with self._patched([(0.0, 0.0), (9.0, 0.0), (9.0, 9.0)],
                           self._report(), self._frame()) as traced:
            self._post(staged_token=self._staged()[0])
        self.assertEqual(traced.call_args.kwargs["max_samples"],
                         auto.WEB_MAX_SAMPLES)

    def test_it_is_not_on_the_camera_route_any_more(self):
        """It moved to the detection-job route when it started reading staged
        clips; a stale camera-scoped copy would be a second way in with its own
        upload path."""
        from django.urls import get_resolver
        names = {k for k in get_resolver().reverse_dict if isinstance(k, str)}
        self.assertIn("detectionjob-trace-zone", names)
        self.assertNotIn("camera-autodetect-zone", names)


class AutoZoneWiringTests(TestCase):
    """watch_parking works the no-parking area out from the footage when none
    is saved, instead of refusing to start.

    The point of the whole feature: pressing Start on a parking run should not
    require tracing a polygon first.
    """

    def test_the_command_exposes_an_opt_out(self):
        from core.management.commands import watch_parking
        parser = watch_parking.Command().create_parser("manage.py", "watch_parking")
        self.assertIn("no_auto_zone", {a.dest for a in parser._actions})

    def test_the_fallback_runs_before_the_run_is_refused(self):
        """Ordering is the whole behaviour: _load_zone used to print
        draw-it-yourself instructions and return None."""
        from core.management.commands import watch_parking
        source = inspect.getsource(watch_parking.Command._load_zone)
        self.assertIn("_auto_zone", source)
        self.assertLess(source.index("_auto_zone"),
                        source.index("Trace the road by hand"),
                        "the trace must be attempted before giving up")

    def test_a_drawn_area_still_wins(self):
        """An operator who traced the road must get the polygon they traced;
        the automatic one is a fallback, never an override."""
        from core.management.commands import watch_parking
        source = inspect.getsource(watch_parking.Command._load_zone)
        self.assertLess(source.index("_zone_points_from_camera"),
                        source.index("_auto_zone"))

    def test_it_uses_the_shared_tracer(self):
        """Not a second implementation: an auto-started run and a hand-checked
        one must be judged by a polygon built the same way."""
        from core.management.commands import watch_parking
        source = inspect.getsource(watch_parking.Command._auto_zone)
        self.assertIn("autodetect.trace_clip", source)
        self.assertIn("detect_vehicles", source)


class AutoZonePersistenceTests(TestCase):
    """Where a traced area may and may not be written."""

    def _command(self, camera):
        from core.management.commands import watch_parking
        cmd = watch_parking.Command()
        cmd.camera = camera
        cmd.stdout = types.SimpleNamespace(write=lambda *a, **k: None)
        cmd.style = types.SimpleNamespace(
            SUCCESS=lambda m: m, WARNING=lambda m: m, ERROR=lambda m: m)
        cmd.auto_zone = True        # what watch_parking.handle sets
        return cmd, watch_parking

    def _traced(self):
        import numpy as np
        points = [(100.0, 600.0), (900.0, 600.0), (900.0, 400.0), (100.0, 400.0)]
        report = {"samples": 40, "detections": 90, "tracks": 6,
                  "moving_tracks": 6, "ground_points": 88, "cloud": []}
        return points, report, np.zeros((720, 1280, 3), dtype="uint8")

    def _run(self, camera):
        from unittest import mock
        cmd, mod = self._command(camera)
        with mock.patch.object(mod.autodetect, "trace_clip",
                               return_value=self._traced()):
            return cmd._auto_zone({"source": "clip.mp4", "no_auto_zone": False},
                                  {"alert_score": 120.0, "moving_weight": 0.0,
                                   "enter_fraction": 0.35})

    def test_a_real_camera_keeps_what_was_traced(self):
        """A fixed view, so the next run should not pay for this again — and
        the operator can see and adjust what the detector decided."""
        camera = Camera.objects.create(code="CAM-REAL-01", name="Street")
        zone, note = self._run(camera)
        self.assertIsNotNone(zone)
        self.assertIn("auto-traced", note)
        camera.refresh_from_db()
        self.assertEqual(camera.edges["road"]["type"], "zone")
        self.assertEqual((camera.edges_width, camera.edges_height), (1280, 720))

    def test_a_TEST_camera_does_not_keep_it(self):
        """-TEST cameras are per-RUN scratch for uploaded clips. Persisting
        there would hand the NEXT clip — a different street — this clip's
        road, which is exactly the silent wrongness auto-tracing removes."""
        camera = Camera.objects.create(code="CAM-PARKING-TEST", name="Parking Monitor")
        zone, _note = self._run(camera)
        self.assertIsNotNone(zone, "the run itself still gets its area")
        camera.refresh_from_db()
        self.assertEqual(camera.edges, {})

    def test_an_area_somebody_drew_is_never_overwritten(self):
        drawn = {"road": {"type": "zone", "points": [[1, 1], [9, 1], [9, 9]]}}
        camera = Camera.objects.create(code="CAM-REAL-02", name="Street",
                                       edges=drawn, edges_width=100, edges_height=100)
        self._run(camera)
        camera.refresh_from_db()
        self.assertEqual(camera.edges, drawn)

    def test_the_opt_out_skips_it_entirely(self):
        from unittest import mock
        camera = Camera.objects.create(code="CAM-REAL-03", name="Street")
        cmd, mod = self._command(camera)
        cmd.auto_zone = False       # what --no-auto-zone leaves it as
        with mock.patch.object(mod.autodetect, "trace_clip") as traced:
            zone, _note = cmd._auto_zone(
                {"source": "clip.mp4"},
                {"alert_score": 120.0, "moving_weight": 0.0, "enter_fraction": 0.35})
        traced.assert_not_called()
        self.assertIsNone(zone)

    def test_it_is_off_unless_a_caller_switches_it_on(self):
        """The default has to be OFF. watch_merged_all shares _load_zone and is
        documented to refuse without an area; a default of "trace one" would
        hand it a fallback it never asked for, which is exactly what happened
        when this was read from the options dict."""
        from unittest import mock
        camera = Camera.objects.create(code="CAM-REAL-06", name="Street")
        cmd, mod = self._command(camera)
        del cmd.auto_zone           # a caller that never set it
        with mock.patch.object(mod.autodetect, "trace_clip") as traced:
            zone, _note = cmd._auto_zone(
                {"source": "clip.mp4"},
                {"alert_score": 120.0, "moving_weight": 0.0, "enter_fraction": 0.35})
        traced.assert_not_called()
        self.assertIsNone(zone)

    def test_merged_all_keeps_refusing_without_an_area(self):
        """It has no fallback on purpose — see its module docstring."""
        from core.management.commands.watch_merged_all import Command as MergedAll
        cmd = MergedAll()
        cmd.camera = Camera.objects.create(code="CAM-MERGED-X", name="M")
        self.assertFalse(getattr(cmd, "auto_zone", False))

    def test_footage_with_no_traffic_leaves_the_manual_route_open(self):
        """An unusable automatic answer must not take the draw-it-by-hand
        instructions away with it."""
        from unittest import mock
        camera = Camera.objects.create(code="CAM-REAL-04", name="Street")
        cmd, mod = self._command(camera)
        report = {"samples": 40, "detections": 0, "tracks": 0, "moving_tracks": 0,
                  "ground_points": 0, "cloud": [], "reason": "No vehicles."}
        with mock.patch.object(mod.autodetect, "trace_clip",
                               return_value=(None, report, None)):
            zone, _note = cmd._auto_zone(
                {"source": "clip.mp4", "no_auto_zone": False},
                {"alert_score": 120.0, "moving_weight": 0.0, "enter_fraction": 0.35})
        self.assertIsNone(zone)

    def test_a_crash_in_the_tracer_is_not_fatal(self):
        from unittest import mock
        camera = Camera.objects.create(code="CAM-REAL-05", name="Street")
        cmd, mod = self._command(camera)
        with mock.patch.object(mod.autodetect, "trace_clip",
                               side_effect=RuntimeError("no weights")):
            zone, _note = cmd._auto_zone(
                {"source": "clip.mp4", "no_auto_zone": False},
                {"alert_score": 120.0, "moving_weight": 0.0, "enter_fraction": 0.35})
        self.assertIsNone(zone, "degrades to 'draw it by hand', never raises")


class ParkingStartsWithoutAnAreaTests(TestCase):
    """What the Run Detection page may now send, and what the server stages."""

    def setUp(self):
        from rest_framework.test import APIClient
        from core.models import User
        self.admin = User.objects.create_user(
            username="adm", password="x", role="admin", display_name="Admin")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def _start(self, **body):
        from unittest import mock
        with mock.patch("subprocess.Popen") as popen, mock.patch("threading.Thread"):
            popen.return_value.pid = 4321
            with mock.patch("cv2.VideoCapture") as cap:
                cap.return_value.isOpened.return_value = True
                return self.c.post("/api/detection-jobs/", body)

    def _upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile("clip.mp4", b"\x00" * 64, content_type="video/mp4")

    def test_a_parking_run_starts_with_no_area_drawn(self):
        """The whole point: pressing Start should not require a polygon."""
        res = self._start(violation_type="parking", file=self._upload())
        self.assertEqual(res.status_code, 201, res.content)

    def test_thresholds_reach_the_run_even_with_no_area(self):
        """They are parking's rule, not a property of the polygon — an
        auto-traced run has to be judged by the numbers the operator typed."""
        res = self._start(violation_type="parking", file=self._upload(),
                          obstruction_pct="55", obstruction_minutes="4")
        self.assertEqual(res.status_code, 201, res.content)
        cam = Camera.objects.get(code="CAM-PARKING-TEST")
        self.assertEqual(cam.obstruction_pct, 55)
        self.assertEqual(cam.obstruction_minutes, 4.0)

    def test_a_stale_area_from_a_previous_run_is_cleared(self):
        """-TEST cameras are shared scratch. Inheriting the last operator's
        polygon would make "start it without drawing" quietly mean "reuse
        somebody else's road"."""
        Camera.objects.create(
            code="CAM-PARKING-TEST", name="Parking Monitor",
            edges={"road": {"type": "zone", "points": [[1, 1], [9, 1], [9, 9]]}},
            edges_width=100, edges_height=100)
        res = self._start(violation_type="parking", file=self._upload(),
                          obstruction_pct="40", obstruction_minutes="2")
        self.assertEqual(res.status_code, 201, res.content)
        cam = Camera.objects.get(code="CAM-PARKING-TEST")
        self.assertEqual(cam.edges, {})
        self.assertIsNone(cam.edges_width)

    def test_a_drawn_area_is_still_staged(self):
        drawn = {"road": {"type": "zone", "points": [[10, 10], [90, 10], [90, 90]]}}
        res = self._start(violation_type="parking", file=self._upload(),
                          edges=json.dumps(drawn), edges_width="640",
                          edges_height="480", obstruction_pct="35",
                          obstruction_minutes="2")
        self.assertEqual(res.status_code, 201, res.content)
        cam = Camera.objects.get(code="CAM-PARKING-TEST")
        self.assertEqual(cam.edges, drawn)
        self.assertEqual((cam.edges_width, cam.edges_height), (640, 480))

    def test_merged4_still_refuses_without_an_area(self):
        """It has no tracing fallback, so rejecting it here keeps a visible
        error on the page instead of a FAILED job with the reason in a log."""
        res = self._start(violation_type="merged4", file=self._upload())
        self.assertEqual(res.status_code, 400)
