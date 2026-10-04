"""seed_core: the rows a fresh hosted database cannot run without.

Every ViolationType is otherwise created by a get_or_create inside a watch_*
command, and in a split deployment those run only on the edge PC. The hosted
database never sees them, and the failure is quiet: the dashboard loads with
empty violation filters and an incoming alert has no type to attach to.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.management.commands.seed_core import VIOLATION_TYPES
from core.models import Camera, SystemSettings, ViolationType


class SeedCoreTests(TestCase):
    def seed(self, *args):
        out = StringIO()
        call_command("seed_core", *args, stdout=out)
        return out.getvalue()

    def test_it_creates_every_violation_type(self):
        self.seed()
        self.assertEqual(
            set(ViolationType.objects.values_list("code", flat=True)),
            {"smoking", "drinking", "theft", "parking"},
        )

    def test_holdup_is_stored_as_theft_not_thief(self):
        # watch_thief uses "thief" for the COMMAND and "theft" for the type;
        # migration 0026 merged the two after they diverged once. Seeding the
        # wrong one would split holdup alerts across two types again.
        self.seed()
        self.assertTrue(ViolationType.objects.filter(code="theft").exists())
        self.assertFalse(ViolationType.objects.filter(code="thief").exists())

    def test_the_seeded_values_match_what_the_detectors_create(self):
        # A detector's get_or_create matches on CODE alone, so a mismatched
        # label here would survive forever and show the wrong name on cards.
        self.seed()
        for code, label, color, icon in VIOLATION_TYPES:
            vt = ViolationType.objects.get(code=code)
            self.assertEqual((vt.label, vt.color, vt.icon), (label, color, icon))

    def test_it_creates_the_single_camera(self):
        self.seed()
        cam = Camera.objects.get(code="CAM-SMOKE-01")
        self.assertEqual(cam.name, "Hikvision DS-2CD1047G2")

    def test_the_camera_gets_no_stream_url(self):
        # The RTSP address is edge-local and must never be written into a
        # hosted database.
        self.seed()
        self.assertEqual(Camera.objects.get(code="CAM-SMOKE-01").stream_url, "")

    def test_no_camera_skips_it(self):
        self.seed("--no-camera")
        self.assertFalse(Camera.objects.exists())
        self.assertEqual(ViolationType.objects.count(), 4)

    def test_the_settings_singleton_exists_afterwards(self):
        self.seed()
        self.assertIsNotNone(SystemSettings.load())

    def test_running_it_twice_changes_nothing(self):
        self.seed()
        before = list(ViolationType.objects.values_list("id", "code", "label"))
        cam_id = Camera.objects.get(code="CAM-SMOKE-01").id
        self.seed()
        self.assertEqual(list(ViolationType.objects.values_list("id", "code", "label")), before)
        self.assertEqual(Camera.objects.get(code="CAM-SMOKE-01").id, cam_id)
        self.assertEqual(SystemSettings.objects.count(), 1)

    def test_it_leaves_an_existing_row_alone(self):
        # A type the detectors already made, or an operator renamed, must not
        # be reverted by a later deploy running this again.
        ViolationType.objects.create(code="smoking", label="Renamed By Hand",
                                     color="#000000", icon="x")
        Camera.objects.create(code="CAM-SMOKE-01", name="Renamed Camera",
                              stream_url="rtsp://keep/me")
        self.seed()
        self.assertEqual(ViolationType.objects.get(code="smoking").label, "Renamed By Hand")
        cam = Camera.objects.get(code="CAM-SMOKE-01")
        self.assertEqual(cam.name, "Renamed Camera")
        self.assertEqual(cam.stream_url, "rtsp://keep/me")

    def test_a_custom_camera_name_is_used_only_on_creation(self):
        self.seed("--camera-name", "Gate Camera")
        self.assertEqual(Camera.objects.get(code="CAM-SMOKE-01").name, "Gate Camera")
        self.seed("--camera-name", "Something Else")
        self.assertEqual(Camera.objects.get(code="CAM-SMOKE-01").name, "Gate Camera")
