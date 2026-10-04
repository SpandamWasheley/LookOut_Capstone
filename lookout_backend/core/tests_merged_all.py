"""watch_merged_all: the fourth engine, and the refusal to run without an area.

Covers what is easy to get wrong when a fourth violation is bolted onto the
three-engine merged command:

  * the extra engine is driven on every frame, INCLUDING frames where all three
    merged engines are switched off — the old loop skipped such frames outright;
  * it refuses to start with no no-parking area drawn, rather than quietly
    watching the whole frame;
  * the rule it runs is watch_parking's OWN zone rule, not a second
    implementation. This used to drive the older edge monitor in
    the edge monitor, so parking behaved differently depending on
    which command you started — and only watch_parking's got the fixes that
    came out of testing on real footage.

Nothing here loads a model or opens a capture: the hooks are exercised
directly, _setup_extra is called with the options dict a real run would hand
it, and the detector call is stubbed so the geometry, the stationary gate, the
dwell timer and the alert write are the real code.
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

ZONE_SPEC = {"road": {"type": "zone",
                      "points": [[50, 50], [350, 50], [350, 250], [50, 250]]}}
# The resolution those points were drawn at. Without it the loader cannot turn
# pixels into fractions and refuses the area outright — which is the behaviour,
# not a test detail: a polygon with no frame size is unusable.
DRAWN_AT = {"edges_width": 400, "edges_height": 300}
# The old kerb-line shape. Still storable, but the zone rule cannot use it:
# a half-plane running off to infinity is not a closed polygon.
EDGE_SPEC = {"left": {"points": [[200, 0], [200, 300]], "side": -1}}


def _options(**over):
    """The options dict a real run hands _setup_extra."""
    base = {"zone": None, "inside_pct": None, "alert_score": None,
            "moving_weight": 0.0, "parking_confidence": None,
            "tracker": "bytetrack"}
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
        # Order is load-bearing: parking draws the zone and the vehicle it is
        # judging, and the merged engines buffer the frame into their evidence
        # clips at the end of their own pass. Running parking second would
        # leave every clip but parking's showing a street with no polygon.
        spy = self.Spy()
        spy._process_frame(None, 0.0, None, ["smoking"], False, extra=["parking"])
        self.assertEqual(spy.calls, [("extra", "parking"), ("merged", None)])

    def test_the_extra_engine_still_runs_when_no_merged_engine_is_active(self):
        # The pre-existing loop did `if not active: continue`, so with smoking,
        # drinking and theft all disabled parking would never see a frame.
        spy = self.Spy()
        spy._process_frame(None, 0.0, None, [], False, extra=["parking"])
        self.assertEqual(spy.calls, [("extra", "parking")])

    def test_no_extra_engine_means_the_merged_pass_alone(self):
        spy = self.Spy()
        spy._process_frame(None, 0.0, None, ["thief"], False, extra=[])
        self.assertEqual(spy.calls, [("merged", None)])

    def test_the_base_command_has_no_extra_engines_and_its_hooks_do_nothing(self):
        self.assertEqual(MergedCommand.EXTRA_ENGINES, ())
        base = MergedCommand()
        self.assertIsNone(base._setup_extra(_options()))
        self.assertIsNone(base._extra_source("clip.mp4", False))
        self.assertIsNone(base._extra_frame("parking", None, 0.0, None, False))


class SetupExtraTests(TestCase):
    """_setup_extra: what it accepts, what it refuses, and how it wires up."""

    def setUp(self):
        self.camera = Camera.objects.create(code="CAM-MERGED4-TEST", name="Monitor",
                                            **DRAWN_AT)
        self.cmd = MergedAllCommand()
        self.cmd.camera = self.camera
        self.cmd.violations_dir = "/tmp/violations"
        self.cmd.dry_run = True
        self.cmd.far = True
        self.cmd.tiles = (2, 2)
        self.cmd.debug_pub = None

    def _zone(self, **camera_fields):
        self.camera.edges = ZONE_SPEC
        for key, value in camera_fields.items():
            setattr(self.camera, key, value)
        self.camera.save()

    def test_it_refuses_when_no_area_is_drawn(self):
        refused = self.cmd._setup_extra(_options())
        self.assertIsNotNone(refused)
        self.assertIn("no-parking area", refused)
        self.assertIn("Edge Zones", refused)

    def test_a_road_zone_on_the_camera_is_enough_to_start(self):
        self._zone()
        self.assertIsNone(self.cmd._setup_extra(_options()))
        self.assertEqual(len(self.cmd.parking.zone.points_norm), 4)

    def test_old_kerb_lines_are_not_usable_by_the_zone_rule(self):
        self.camera.edges = EDGE_SPEC
        self.camera.save()
        self.assertIsNotNone(self.cmd._setup_extra(_options()))

    def test_it_runs_watch_parkings_own_zone_not_a_second_rule(self):
        # The whole point of this change: one parking rule, whichever command
        # you start. Previously this engine drove the older edge monitor.
        from core.vision.obstruction_zone import ObstructionZone

        self._zone()
        self.cmd._setup_extra(_options())
        self.assertIsInstance(self.cmd.parking.zone, ObstructionZone)

    def test_the_parking_engine_is_pointed_at_the_shared_camera(self):
        self._zone()
        self.cmd._setup_extra(_options())
        parking = self.cmd.parking
        self.assertEqual(parking.camera, self.camera)
        self.assertEqual(parking.violations_dir, "/tmp/violations")
        self.assertTrue(parking.dry_run)
        self.assertEqual(parking.parking_type.code, "parking")
        self.assertEqual(parking.tracker_name, "bytetrack.yaml")
        # watch_merged runs no frame enhancement for its own three engines, so
        # parking must not either: it would otherwise judge a brightened copy
        # of the frame the other three were judged on.
        self.assertFalse(parking.preprocess)

    def test_the_thresholds_come_from_the_camera_record(self):
        # The "Inside the road %" and "for N minutes" boxes in the zone editor.
        self._zone(obstruction_pct=70, obstruction_minutes=2)
        self.cmd._setup_extra(_options())
        zone = self.cmd.parking.zone
        self.assertAlmostEqual(zone.enter_fraction, 0.70)
        self.assertAlmostEqual(zone.alert_score, 120.0)

    def test_cli_flags_win_over_the_camera_record(self):
        self._zone(obstruction_pct=70, obstruction_minutes=5)
        self.cmd._setup_extra(_options(inside_pct=40, alert_score=30))
        zone = self.cmd.parking.zone
        self.assertAlmostEqual(zone.enter_fraction, 0.40)
        self.assertAlmostEqual(zone.alert_score, 30.0)

    def test_moving_vehicles_earn_nothing_by_default(self):
        # A passing car used to outscore a parked motorcycle.
        self._zone()
        self.cmd._setup_extra(_options())
        self.assertEqual(self.cmd.parking.zone.moving_weight, 0.0)

    def test_a_file_source_cuts_raw_clips_from_the_file_a_live_one_buffers(self):
        self._zone()
        self.cmd._setup_extra(_options())

        self.cmd._extra_source("clip.mp4", is_live=False)
        self.assertEqual(self.cmd.parking._source_path, "clip.mp4")
        self.assertIsNone(self.cmd.parking._raw_buffer)

        self.cmd._extra_source("rtsp://cam/1", is_live=True)
        self.assertIsNone(self.cmd.parking._source_path)
        self.assertIsNotNone(self.cmd.parking._raw_buffer)


class ExtraFrameTests(TestCase):
    """The per-frame hook, with the vehicle detector stubbed."""

    def setUp(self):
        self.camera = Camera.objects.create(code="CAM-MERGED4-TEST", name="Monitor",
                                            edges=ZONE_SPEC, obstruction_pct=40,
                                            obstruction_minutes=0.1, **DRAWN_AT)
        self.cmd = MergedAllCommand()
        self.cmd.camera = self.camera
        self.cmd.violations_dir = "/tmp/violations"
        self.cmd.dry_run = True
        self.cmd.far = True
        self.cmd.tiles = (2, 2)
        self.cmd.debug_pub = None
        self.cmd._setup_extra(_options())

    @staticmethod
    def _frame():
        import numpy as np
        return np.zeros((300, 400, 3), dtype="uint8")

    @staticmethod
    def _cfg():
        return mock.Mock(parking_confidence=35, alert_cooldown=60)

    def test_it_drives_watch_parkings_zone_pass(self):
        with mock.patch.object(self.cmd.parking, "_run_zone") as run:
            self.cmd._extra_frame("parking", self._frame(), 1.0, self._cfg(), False)
        run.assert_called_once()
        self.assertAlmostEqual(run.call_args[0][1], 1.0)      # content time
        self.assertAlmostEqual(run.call_args[0][2], 0.35)     # configured floor

    def test_the_confidence_override_wins(self):
        self.cmd.parking.conf_override = 0.6
        with mock.patch.object(self.cmd.parking, "_run_zone") as run:
            self.cmd._extra_frame("parking", self._frame(), 1.0, self._cfg(), False)
        self.assertAlmostEqual(run.call_args[0][2], 0.6)

    def test_the_video_position_is_tracked_for_a_file_source(self):
        # _create_alert cuts the raw evidence clip from _video_pos_sec; left at
        # None it silently produces no raw clip at all.
        self.cmd._extra_source("clip.mp4", is_live=False)
        with mock.patch.object(self.cmd.parking, "_run_zone"):
            self.cmd._extra_frame("parking", self._frame(), 7.5, self._cfg(), False)
        self.assertAlmostEqual(self.cmd.parking._video_pos_sec, 7.5)


class ObstructionEndToEndTests(TestCase):
    """The whole fourth engine, from the drawn area to the Alert row.

    Only the detector call is stubbed — the geometry, the stationary gate, the
    dwell timer and _create_alert are the real code. This is the test that
    would fail if merged_all were wired to a different parking rule than
    watch_parking's own.
    """

    def setUp(self):
        import tempfile

        self.violations = pathlib.Path(tempfile.mkdtemp())
        self.camera = Camera.objects.create(
            code="CAM-MERGED4-TEST", name="Monitor", edges=ZONE_SPEC,
            # 40% of the vehicle inside, held 6 seconds: the same rule the
            # defaults express, small enough to drive in a test.
            obstruction_pct=40, obstruction_minutes=0.1, **DRAWN_AT,
        )
        self.cmd = MergedAllCommand()
        self.cmd.camera = self.camera
        self.cmd.violations_dir = self.violations
        self.cmd.dry_run = False
        self.cmd.far = True
        self.cmd.tiles = (2, 2)
        self.cmd.debug_pub = None
        self.assertIsNone(self.cmd._setup_extra(_options()))

    def _drive(self, box, seconds, step=0.5):
        """Holds one stationary vehicle in frame for `seconds` of content time."""
        import numpy as np

        cfg = mock.Mock(parking_confidence=35, alert_cooldown=60)
        tracked = [(*box, 0.9, "car", 7)]
        now = 0.0
        while now <= seconds:
            frame = np.zeros((300, 400, 3), dtype="uint8")
            with mock.patch("core.vision.recognition.detect_vehicles_tracked",
                            return_value=tracked), \
                 mock.patch("core.management.commands.watch_parking.recognition"
                            ".cut_raw_clip", return_value=False):
                self.cmd._extra_frame("parking", frame, now, cfg, False)
            now += step

    def test_a_stationary_vehicle_in_the_zone_raises_a_parking_alert(self):
        from core.models import Alert

        self._drive((150, 150, 230, 220), seconds=12.0)
        alert = Alert.objects.get(type__code="parking")
        self.assertEqual(alert.camera, self.camera)
        self.assertEqual(alert.suspect, "car")
        self.assertIn("Obstruction", alert.description)

    def test_a_vehicle_outside_the_zone_never_alerts(self):
        from core.models import Alert

        # Ground point below the zone's bottom edge, i.e. off the road.
        self._drive((150, 265, 230, 295), seconds=20.0)
        self.assertFalse(Alert.objects.filter(type__code="parking").exists())

    def test_passing_through_too_briefly_never_alerts(self):
        from core.models import Alert

        self._drive((150, 150, 230, 220), seconds=3.0)
        self.assertFalse(Alert.objects.filter(type__code="parking").exists())

    def test_dry_run_writes_no_alert_row(self):
        from core.models import Alert

        self.cmd.dry_run = True
        self.cmd.parking.dry_run = True
        self._drive((150, 150, 230, 220), seconds=12.0)
        self.assertFalse(Alert.objects.filter(type__code="parking").exists())


class RegistrationTests(TestCase):
    def test_merged4_runs_watch_merged_all_and_requires_an_area(self):
        self.assertEqual(DETECTION_COMMANDS["merged4"], "watch_merged_all")
        self.assertIn("merged4", EDGE_REQUIRED)
        self.assertIn("merged4", EDGE_CAPABLE)

    def test_parking_may_use_an_area_but_does_not_require_one(self):
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
