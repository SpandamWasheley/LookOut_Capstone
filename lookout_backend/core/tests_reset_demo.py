import datetime
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (Alert, Camera, Citation, DetectionJob, Officer, SystemSettings,
                         ViolationType, Violator, Zone)


class ResetDemoDataTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media = Path(self.tmp.name)
        (self.media / "violations" / "ai" / "alert1").mkdir(parents=True)
        (self.media / "uploads").mkdir()
        for rel in ("violations/a.jpg", "violations/a.mp4", "violations/orphan.jpg",
                    "violations/ai/alert1/f1.jpg", "uploads/clip.mp4"):
            (self.media / rel).write_bytes(b"x" * 10)
        self.ctx = override_settings(MEDIA_ROOT=self.media)
        self.ctx.enable()
        self.addCleanup(self.ctx.disable)

        self.user = get_user_model().objects.create_user(username="admin1", password="x", email="a@b.c")
        self.zone = Zone.objects.create(name="Z")
        self.vtype = ViolationType.objects.get_or_create(code="smoking", defaults={"label": "Smoking"})[0]
        self.cam = Camera.objects.create(name="Cam", zone=self.zone)
        self.officer = Officer.objects.create(name="Off")
        SystemSettings.load()
        self.alert = Alert.objects.create(
            type=self.vtype, camera=self.cam, timestamp=timezone.now(), confidence=0.8,
            image_url="/media/violations/a.jpg", video_url="/media/violations/a.mp4",
            ai={"frame_files": ["/media/violations/ai/alert1/f1.jpg"]})
        violator = Violator.objects.create(first_name="A", last_name="B")
        cit = Citation.objects.create(alert=self.alert, violator=violator, officer=self.officer,
                                      first_name_entered="A", last_name_entered="B",
                                      violator_barangay=Citation._meta.get_field("violator_barangay").choices[0][0])
        cit.violations.add(self.vtype)
        DetectionJob.objects.create(violation_type="smoking", source_filename="c.mp4",
                                    source_path="x", status="done")
        DetectionJob.objects.create(violation_type="smoking", source_filename="d.mp4",
                                    source_path="y", status="running")

    def run_cmd(self, *args):
        out = StringIO()
        call_command("reset_demo_data", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        out = self.run_cmd("--dry-run", "--orphans")
        self.assertIn("DRY RUN", out)
        self.assertEqual(Alert.objects.count(), 1)
        self.assertEqual(Citation.objects.count(), 1)
        self.assertEqual(Violator.objects.count(), 1)
        self.assertEqual(DetectionJob.objects.count(), 2)
        for rel in ("violations/a.jpg", "violations/orphan.jpg", "violations/ai/alert1/f1.jpg"):
            self.assertTrue((self.media / rel).exists(), rel)

    def test_real_run_removes_test_data_keeps_the_rest(self):
        self.run_cmd("--yes")
        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(Citation.objects.count(), 0)
        self.assertEqual(Violator.objects.count(), 0)
        self.assertEqual(list(DetectionJob.objects.values_list("status", flat=True)), ["running"])
        self.assertTrue(get_user_model().objects.filter(username="admin1").exists())
        self.assertEqual(Officer.objects.count(), 1)
        self.assertEqual(Camera.objects.count(), 1)
        self.assertEqual(Zone.objects.count(), 1)
        self.assertTrue(ViolationType.objects.filter(code="smoking").exists())
        self.assertEqual(SystemSettings.objects.count(), 1)
        self.assertFalse((self.media / "violations/a.jpg").exists())
        self.assertFalse((self.media / "violations/a.mp4").exists())
        self.assertFalse((self.media / "violations/ai/alert1").exists())
        # Orphans and uploads are left alone without --orphans.
        self.assertTrue((self.media / "violations/orphan.jpg").exists())
        self.assertTrue((self.media / "uploads/clip.mp4").exists())

    def test_orphans_option_and_next_code(self):
        self.run_cmd("--yes", "--orphans")
        self.assertFalse((self.media / "violations/orphan.jpg").exists())
        self.assertTrue((self.media / "uploads/clip.mp4").exists())
        new = Alert.objects.create(type=self.vtype, camera=self.cam, timestamp=timezone.now(),
                                   confidence=0.5)
        self.assertEqual(new.code, "ALT-0001")
