"""watch_merged_all: the fourth engine, and the refusal to run without an area.

Covers the two things that are easy to get wrong when a fourth violation is
bolted onto the three-engine merged command:

  * the extra engine is driven on every frame, INCLUDING frames where all three
    merged engines are switched off — the old loop skipped such frames outright;
  * it refuses to start with no no-parking area drawn, rather than quietly
    running watch_parking's plain dwell rule instead (see the command's module
    docstring);
  * the rule it does run is the obstruction one, all the way to an Alert row
    (ObstructionEndToEndTests).

Nothing here loads a model or opens a capture: the hooks are exercised directly,
_setup_extra is called with the options dict a real run would hand it, and the
two model calls in the obstruction path are stubbed so the geometry, the dwell
timer and the alert write are the real code.
"""
import pathlib
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.management.commands.watch_merged import Command as MergedCommand
from core.management.commands.watch_merged_all import Command as MergedAllCommand
from core.models import Camera
from core.views import DETECTION_COMMANDS, EDGE_CAPABLE, EDGE_REQUIRED


# A kerb line down the middle of a 400x300 frame, footpath on the left.
EDGE_SPEC = {"left": {"points": [[200, 0], [200, 300]], "side": -1}}
ZONE_SPEC = {"road": {"type": "zone",
                      "points": [[50, 50], [350, 50], [350, 250], [50, 250]]}}


def _options(**over):
    """The subset of a real run's options dict that _setup_extra reads."""
    base = {"edges": None, "obstruction_pct": None, "obstruction_minutes": None,
            "parking_confidence": None}
    base.update(over)
    return base


class FrameDispatchTests(TestCase):
    """_process_frame's contract, with no detection involved."""

    class Spy(MergedCommand):
        EXTRA_ENGINES = ("parking",)

        def __init__(self):
            self.calls = []

        def _extra_frame(self, name, frame, now, cfg, debug):
            self.calls.append(("extra", name))

        def _merged_pass(self, *a, **kw):
            self.calls.append(("merged", None))

    def test_the_extra_engine_runs_before_the_merged_pass(self):
        # Order is load-bearing, not cosmetic: parking draws the road edge and
        # the vehicle it is judging, and the merged engines buffer the frame
        # into their evidence clips at the end of their own pass. Running
        # parking second would leave every clip but parking's showing a street
        # with no line on it.
        spy = self.Spy()
        spy._process_frame(None, 0.0, None, ["smoking"], False, extra=["parking"])
        self.assertEqual(spy.calls, [("extra", "parking"), ("merged", None)])

    def test_the_extra_engine_still_runs_when_no_merged_engine_is_active(self):
        # The pre-existing loop did `if not active: continue`, which would skip
        # the frame entirely — so with smoking, drinking and theft all disabled
        # in Settings, parking would never see a single frame.
        spy = self.Spy()
        spy._process_frame(None, 0.0, None, [], False, extra=["parking"])
        self.assertEqual(spy.calls, [("extra", "parking")])

    def test_no_extra_engine_means_the_merged_pass_alone(self):
        spy = self.Spy()
        spy._process_frame(None, 0.0, None, ["thief"], False, extra=[])
        self.assertEqual(spy.calls, [("merged", None)])

    def test_the_base_command_has_no_extra_engines_and_its_hooks_do_nothing(self):
        # watch_merged itself must behave exactly as it did before the hooks
        # existed — no fourth engine, and the hooks inert.
        self.assertEqual(MergedCommand.EXTRA_ENGINES, ())
        base = MergedCommand()
        self.assertIsNone(base._setup_extra(_options()))
        self.assertIsNone(base._extra_source("clip.mp4", False))
        self.assertIsNone(base._extra_frame("parking", None, 0.0, None, False))


class SetupExtraTests(TestCase):
    """_setup_extra: what it accepts, what it refuses, and how it wires up."""

    def setUp(self):
        self.camera = Camera.objects.create(code="CAM-MERGED4-TEST", name="Monitor")
        self.cmd = MergedAllCommand()
        self.cmd.camera = self.camera
        self.cmd.violations_dir = "/tmp/violations"
        self.cmd.dry_run = True
        self.cmd.far = True
        self.cmd.tiles = (2, 2)

    def test_it_refuses_when_no_area_is_drawn(self):
        refused = self.cmd._setup_extra(_options())
        self.assertIsNotNone(refused)
        self.assertIn("No no-parking area is marked", refused)
        self.assertIn(self.camera.code, refused)
        # The refusal must say WHY it isn't just falling back, or the next
        # person to hit it will "fix" it by adding the fallback.
        self.assertIn("dwell", refused)

    def test_an_edge_on_the_camera_record_is_enough_to_start(self):
        self.camera.edges = EDGE_SPEC
        self.camera.save(update_fields=["edges"])
        self.assertIsNone(self.cmd._setup_extra(_options()))
        self.assertEqual(list(self.cmd.parking._edge_specs), ["left"])

    def test_a_road_zone_is_enough_too(self):
        self.camera.edges = ZONE_SPEC
        self.camera.save(update_fields=["edges"])
        self.assertIsNone(self.cmd._setup_extra(_options()))
        self.assertEqual(list(self.cmd.parking._edge_specs), ["road"])

    def test_a_spec_with_too_few_points_does_not_count_as_an_area(self):
        # One point is a click, not a line. _load_edges drops it, which must
        # read as "nothing drawn" rather than as an area with no geometry.
        self.camera.edges = {"left": {"points": [[200, 0]], "side": -1}}
        self.camera.save(update_fields=["edges"])
        self.assertIsNotNone(self.cmd._setup_extra(_options()))

    def test_the_parking_engine_is_pointed_at_the_shared_camera_and_dry_run(self):
        self.camera.edges = EDGE_SPEC
        self.camera.save(update_fields=["edges"])
        self.cmd._setup_extra(_options())
        parking = self.cmd.parking
        self.assertEqual(parking.camera, self.camera)
        self.assertEqual(parking.violations_dir, "/tmp/violations")
        self.assertTrue(parking.dry_run)
        self.assertEqual(parking.parking_type.code, "parking")
        # Monitors are built from the first real frame's shape, not here —
        # the area was drawn at some other resolution.
        self.assertIsNone(parking.monitors)
        # watch_merged runs no frame enhancement, so parking must not either:
        # it would otherwise judge a brightened copy of the frame the other
        # three engines were judged on.
        self.assertFalse(parking.preprocess)

    def test_the_thresholds_come_from_the_camera_record(self):
        self.camera.edges = EDGE_SPEC
        self.camera.obstruction_pct = 70
        self.camera.obstruction_minutes = 2
        self.camera.save(update_fields=["edges", "obstruction_pct", "obstruction_minutes"])
        self.cmd._setup_extra(_options())
        self.assertAlmostEqual(self.cmd.parking._enter_fraction, 0.70)
        self.assertAlmostEqual(self.cmd.parking._obstruction_seconds, 120.0)

    def test_cli_flags_win_over_the_camera_record(self):
        self.camera.edges = EDGE_SPEC
        self.camera.obstruction_pct = 70
        self.camera.save(update_fields=["edges", "obstruction_pct"])
        self.cmd._setup_extra(_options(obstruction_pct=40, obstruction_minutes=1))
        self.assertAlmostEqual(self.cmd.parking._enter_fraction, 0.40)
        self.assertAlmostEqual(self.cmd.parking._obstruction_seconds, 60.0)

    def test_a_file_source_cuts_raw_clips_from_the_file_a_live_one_buffers(self):
        self.camera.edges = EDGE_SPEC
        self.camera.save(update_fields=["edges"])
        self.cmd._setup_extra(_options())

        self.cmd._extra_source("clip.mp4", is_live=False)
        self.assertEqual(self.cmd.parking._source_path, "clip.mp4")
        self.assertIsNone(self.cmd.parking._raw_buffer)

        self.cmd._extra_source("rtsp://cam/1", is_live=True)
        self.assertIsNone(self.cmd.parking._source_path)
        self.assertIsNotNone(self.cmd.parking._raw_buffer)


class ExtraFrameTests(TestCase):
    """The per-frame hook, with watch_parking's detector and rule layer stubbed
    — this is about the plumbing between them, not about either one."""

    def setUp(self):
        self.camera = Camera.objects.create(code="CAM-MERGED4-TEST", name="Monitor",
                                            edges=EDGE_SPEC)
        self.cmd = MergedAllCommand()
        self.cmd.camera = self.camera
        self.cmd.violations_dir = "/tmp/violations"
        self.cmd.dry_run = True
        self.cmd.far = True
        self.cmd.tiles = (2, 2)
        self.cmd._setup_extra(_options())

    def _frame(self):
        import numpy as np
        return np.zeros((300, 400, 3), dtype="uint8")

    def test_it_builds_the_monitors_once_from_the_first_frame(self):
        parking = self.cmd.parking
        with mock.patch.object(parking, "_detect", return_value=[]), \
             mock.patch.object(parking, "_run_obstruction") as run:
            cfg = mock.Mock(parking_confidence=35, alert_cooldown=60)
            self.cmd._extra_frame("parking", self._frame(), 1.0, cfg, False)
            first = parking.monitors
            self.assertEqual(list(first), ["left"])
            self.cmd._extra_frame("parking", self._frame(), 2.0, cfg, False)
            # Rebuilding would reset every dwell timer and every cooldown, so
            # a vehicle could never accumulate the minutes the rule needs.
            self.assertIs(parking.monitors, first)
        self.assertEqual(run.call_count, 2)

    def test_the_video_position_is_tracked_for_a_file_source(self):
        # _create_alert cuts the raw evidence clip from _video_pos_sec; left at
        # None it silently produces no raw clip at all.
        self.cmd._extra_source("clip.mp4", is_live=False)
        parking = self.cmd.parking
        with mock.patch.object(parking, "_detect", return_value=[]), \
             mock.patch.object(parking, "_run_obstruction"):
            cfg = mock.Mock(parking_confidence=35, alert_cooldown=60)
            self.cmd._extra_frame("parking", self._frame(), 7.5, cfg, False)
        self.assertAlmostEqual(parking._video_pos_sec, 7.5)

    def test_the_configured_confidence_is_used_unless_overridden(self):
        parking = self.cmd.parking
        cfg = mock.Mock(parking_confidence=35, alert_cooldown=60)
        with mock.patch.object(parking, "_detect", return_value=[]) as det, \
             mock.patch.object(parking, "_run_obstruction"):
            self.cmd._extra_frame("parking", self._frame(), 1.0, cfg, False)
            self.assertAlmostEqual(det.call_args[0][1], 0.35)
            parking.conf_override = 0.6
            self.cmd._extra_frame("parking", self._frame(), 2.0, cfg, False)
            self.assertAlmostEqual(det.call_args[0][1], 0.6)


class ObstructionEndToEndTests(TestCase):
    """The whole fourth engine, unmocked from the drawn area to the Alert row.

    Only the two model calls are stubbed (the vehicle detector, and the
    pedestrian pass _run_obstruction makes) — everything between them is the
    real watch_parking code: ObstructionMonitor's footprint fraction, its
    hysteresis, its dwell timer, and _create_alert. This is the test that would
    fail if merged4 were wired to parking's plain dwell rule instead.
    """

    def setUp(self):
        import tempfile

        self.violations = pathlib.Path(tempfile.mkdtemp())
        self.camera = Camera.objects.create(
            code="CAM-MERGED4-TEST", name="Monitor", edges=ZONE_SPEC,
            # 40% of the vehicle inside the road, held for 6 seconds — the same
            # rule the defaults express, just small enough to drive in a test.
            obstruction_pct=40, obstruction_minutes=0.1,
        )
        self.cmd = MergedAllCommand()
        self.cmd.camera = self.camera
        self.cmd.violations_dir = self.violations
        self.cmd.dry_run = False
        self.cmd.far = True
        self.cmd.tiles = (2, 2)
        self.assertIsNone(self.cmd._setup_extra(_options()))

    def _drive(self, box, seconds, step=1.0):
        """Holds one stationary vehicle in frame for `seconds` of content time."""
        import numpy as np

        frame = lambda: np.zeros((300, 400, 3), dtype="uint8")       # noqa: E731
        cfg = mock.Mock(parking_confidence=35, alert_cooldown=60)
        vehicle = (*box, 0.9, "car")
        now = 0.0
        while now <= seconds:
            with mock.patch.object(self.cmd.parking, "_detect", return_value=[vehicle]), \
                 mock.patch("core.management.commands.watch_parking.recognition"
                            ".detect_persons", return_value=[]), \
                 mock.patch("core.management.commands.watch_parking.recognition"
                            ".cut_raw_clip", return_value=False):
                self.cmd._extra_frame("parking", frame(), now, cfg, False)
            now += step

    def test_a_vehicle_held_inside_the_road_zone_raises_a_parking_alert(self):
        from core.models import Alert

        # Well inside the 50..350 x 50..250 zone.
        self._drive((150, 150, 230, 220), seconds=10.0)
        alert = Alert.objects.get(type__code="parking")
        self.assertEqual(alert.camera, self.camera)
        self.assertEqual(alert.suspect, "car")
        # The description must be the obstruction sentence, not the dwell one —
        # "blocking the road" is the whole claim being made.
        self.assertIn("blocking the", alert.description)
        self.assertIn("over the line", alert.description)

    def test_a_vehicle_outside_the_zone_never_alerts(self):
        from core.models import Alert

        # Below the zone's bottom edge (y > 250), i.e. off the road.
        self._drive((150, 265, 230, 295), seconds=20.0)
        self.assertFalse(Alert.objects.filter(type__code="parking").exists())

    def test_passing_through_too_briefly_never_alerts(self):
        from core.models import Alert

        # Inside the zone, but gone again before the dwell is up.
        self._drive((150, 150, 230, 220), seconds=3.0)
        self.assertFalse(Alert.objects.filter(type__code="parking").exists())

    def test_dry_run_writes_no_alert_row(self):
        from core.models import Alert

        self.cmd.dry_run = True
        self.cmd.parking.dry_run = True
        self._drive((150, 150, 230, 220), seconds=10.0)
        self.assertFalse(Alert.objects.filter(type__code="parking").exists())


class RegistrationTests(TestCase):
    def test_merged4_runs_watch_merged_all_and_requires_an_area(self):
        self.assertEqual(DETECTION_COMMANDS["merged4"], "watch_merged_all")
        self.assertIn("merged4", EDGE_REQUIRED)
        self.assertIn("merged4", EDGE_CAPABLE)

    def test_parking_may_use_an_area_but_does_not_require_one(self):
        # It falls back to its plain dwell rule, which is a valid run — so
        # rejecting it for want of an area would break the existing detector.
        self.assertIn("parking", EDGE_CAPABLE)
        self.assertNotIn("parking", EDGE_REQUIRED)

    def test_every_registered_command_exists(self):
        from django.core.management import get_commands
        known = get_commands()
        for key, command in DETECTION_COMMANDS.items():
            self.assertIn(command, known, f"{key} points at a missing command")


class LiveCameraRejectionTests(TestCase):
    """A live run takes the area off the camera row, so that is the only place
    the API can check it — and it must check, or the job starts and fails
    minutes later with the reason buried in a subprocess log."""

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username="adm", password="x", role="admin")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)
        self.camera = Camera.objects.create(code="CAM-01", name="Gate",
                                            stream_url="rtsp://cam/1")

    def post(self, **body):
        return self.c.post("/api/detection-jobs/", body)

    def test_merged4_on_a_camera_with_no_area_is_rejected(self):
        r = self.post(violation_type="merged4", camera_id=self.camera.pk)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("Edge Zones", r.json()["detail"])

    def test_parking_on_the_same_camera_is_allowed(self):
        # Stopped before the subprocess actually launches: this asserts the
        # edge check lets it through, not that a detector can run in a test.
        with mock.patch("subprocess.Popen", side_effect=OSError("not launched here")):
            with self.assertRaises(OSError):
                self.post(violation_type="parking", camera_id=self.camera.pk)
