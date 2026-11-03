"""Improper disposal has its OWN watch area on the camera (disposal_edges).

It used to read parking's `edges`, so tracing or saving a no-parking road
polygon silently confined disposal to the carriageway.
"""
from io import StringIO
from unittest import mock

from django.test import TestCase

from core.management.commands.watch_disposal import Command
from core.models import Camera, SystemSettings

ROAD = {"road": {"type": "zone", "points": [[0, 0], [100, 0], [100, 50]]}}
BIN_SPOT = {"road": {"type": "zone", "points": [[10, 10], [50, 10], [50, 40]]}}


def _command(camera):
    cmd = Command()
    cmd.camera = camera
    cmd.stdout = StringIO()
    return cmd


class DisposalAreaSourceTests(TestCase):
    def test_parkings_zone_is_not_used(self):
        cam = Camera(code="CAM-SMOKE-01", name="c", edges=ROAD,
                     edges_width=100, edges_height=50)
        self.assertIsNone(_command(cam)._area_points_from_camera())

    def test_disposal_edges_are_normalised_by_their_own_frame_size(self):
        cam = Camera(code="CAM-SMOKE-01", name="c", edges=ROAD,
                     edges_width=999, edges_height=999,
                     disposal_edges=BIN_SPOT,
                     disposal_edges_width=100, disposal_edges_height=50)
        self.assertEqual(_command(cam)._area_points_from_camera(),
                         [[0.1, 0.2], [0.5, 0.2], [0.5, 0.8]])

    def test_no_recorded_frame_size_means_whole_frame(self):
        cam = Camera(code="CAM-SMOKE-01", name="c", disposal_edges=BIN_SPOT)
        cmd = _command(cam)
        self.assertIsNone(cmd._area_points_from_camera())
        self.assertIn("frame size", cmd.stdout.getvalue())

    def test_saving_via_the_serializer_round_trips(self):
        from core.serializers import CameraSerializer
        cam = Camera.objects.create(code="CAM-SMOKE-01", name="c")
        ser = CameraSerializer(cam, data={
            "disposal_edges": BIN_SPOT,
            "disposal_edges_width": 640, "disposal_edges_height": 480,
        }, partial=True)
        self.assertTrue(ser.is_valid(), ser.errors)
        ser.save()
        cam.refresh_from_db()
        self.assertEqual(cam.disposal_edges, BIN_SPOT)
        self.assertEqual(cam.edges, {})


class ApplyDisposalZoneTests(TestCase):
    """A saved disposal area swaps only the disposal process."""

    def setUp(self):
        from core.monitor import LiveMonitor
        self.camera = Camera.objects.create(code="CAM-SMOKE-01", name="C",
                                            stream_url="rtsp://x")
        settings_row = SystemSettings.load()
        settings_row.disposal_enabled = True
        settings_row.save()
        self.m = LiveMonitor()
        self.merged = mock.Mock(pid=1)
        self.disposal = mock.Mock(pid=2)
        self.m._want = True
        self.m._procs = {"merged": self.merged, "disposal": self.disposal}

    def test_not_running_does_nothing(self):
        self.m._want = False
        self.assertEqual(self.m.apply_disposal_zone(self.camera), "not_running")

    def test_restarts_only_disposal(self):
        fresh = mock.Mock(pid=3)
        with mock.patch.object(self.m, "_spawn", return_value=(fresh, 99)) as spawn, \
                mock.patch.object(self.m, "_kill") as kill:
            self.assertEqual(self.m.apply_disposal_zone(self.camera), "restarting")
            self.m._swap_thread.join(5)
        spawn.assert_called_once_with("disposal", self.camera)
        kill.assert_called_once_with(self.disposal)
        self.assertIs(self.m._procs["merged"], self.merged)
        self.assertIs(self.m._procs["disposal"], fresh)

    def test_disabled_setting_retires_without_relaunching(self):
        row = SystemSettings.load()
        row.disposal_enabled = False
        row.save()
        with mock.patch.object(self.m, "_spawn") as spawn, \
                mock.patch.object(self.m, "_kill"):
            self.assertEqual(self.m.apply_disposal_zone(self.camera), "stopped")
            self.m._swap_thread.join(5)
        spawn.assert_not_called()
        self.assertNotIn("disposal", self.m._procs)
