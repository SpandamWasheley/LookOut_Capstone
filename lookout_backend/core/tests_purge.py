import datetime
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Alert, Camera, SystemSettings, ViolationType


class PurgeOldEvidenceTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.dir = Path(self.tmp.name) / "violations"
        self.dir.mkdir()
        self.vtype = ViolationType.objects.create(code="smoking", label="Smoking", color="#000", icon="x")
        self.live = Camera.objects.create(code="CAM-SMOKE-01", name="Live")
        self.test_cam = Camera.objects.create(code="CAM-SMOKING-TEST", name="Upload test")
        SystemSettings.load()

    def _alert(self, camera, days_old, name):
        for suffix in ("jpg", "mp4", "raw.mp4"):
            (self.dir / f"{name}.{suffix}").write_bytes(b"x" * 100)
        return Alert.objects.create(
            type=self.vtype, camera=camera, confidence=0.5,
            timestamp=timezone.now() - datetime.timedelta(days=days_old),
            image_url=f"/media/violations/{name}.jpg",
            video_url=f"/media/violations/{name}.mp4",
            raw_video_url=f"/media/violations/{name}.raw.mp4",
        )

    def _run(self, *args):
        out = StringIO()
        call_command("purge_old_evidence", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        old = self._alert(self.live, 60, "old")
        self._run("--dry-run", "--days", "30")
        old.refresh_from_db()
        self.assertTrue(old.image_url)
        self.assertTrue((self.dir / "old.jpg").exists())

    def test_deletes_old_evidence_keeps_the_alert_and_recent_evidence(self):
        old = self._alert(self.live, 60, "old")
        new = self._alert(self.live, 5, "new")
        self._run("--days", "30")
        old.refresh_from_db(); new.refresh_from_db()
        self.assertEqual((old.image_url, old.video_url, old.raw_video_url), ("", "", ""))
        self.assertTrue(Alert.objects.filter(pk=old.pk).exists())       # record kept
        self.assertFalse((self.dir / "old.jpg").exists())
        self.assertTrue(new.image_url)
        self.assertTrue((self.dir / "new.jpg").exists())

    def test_test_cameras_are_skipped_unless_included(self):
        t = self._alert(self.test_cam, 60, "uploaded")
        self._run("--days", "30")
        t.refresh_from_db()
        self.assertTrue(t.image_url)
        self.assertTrue((self.dir / "uploaded.jpg").exists())
        self._run("--days", "30", "--include-test")
        t.refresh_from_db()
        self.assertEqual(t.image_url, "")

    def test_auto_is_a_no_op_while_the_setting_is_off(self):
        old = self._alert(self.live, 60, "old")
        out = self._run("--auto", "--days", "30")
        self.assertIn("OFF", out)
        old.refresh_from_db()
        self.assertTrue(old.image_url)
        cfg = SystemSettings.load()
        cfg.evidence_auto_purge = True
        cfg.save()
        self._run("--auto", "--days", "30")
        old.refresh_from_db()
        self.assertEqual(old.image_url, "")

    def test_auto_purge_defaults_to_off(self):
        self.assertFalse(SystemSettings.load().evidence_auto_purge)

    def test_a_url_cannot_point_outside_the_violations_folder(self):
        outside = Path(self.tmp.name) / "secret.txt"
        outside.write_text("keep me")
        a = Alert.objects.create(
            type=self.vtype, camera=self.live, confidence=0.5,
            timestamp=timezone.now() - datetime.timedelta(days=60),
            image_url="/media/violations/../secret.txt")
        self._run("--days", "30")
        self.assertTrue(outside.exists())
        a.refresh_from_db()
