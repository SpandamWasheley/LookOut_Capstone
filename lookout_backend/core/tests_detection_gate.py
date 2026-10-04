"""DETECTION_ENABLED: this process can, or cannot, run a detector.

A detection job and the live monitor both launch `manage.py watch_*` as a real
subprocess. That needs the GPU, the model weights and the camera — all of which
live on the PC at the barangay and none of which exist on the hosted API.

Without the gate the hosted side accepts the request, creates the job row, and
the subprocess dies on a missing .pt file: the dashboard then shows a run that
is "processing" for ever, with the reason buried in a log nobody opens. A 503
says plainly that it is the server's capability at fault, not the request.

Explicit rather than inferred: DATABASE_URL cannot tell the two apart, because
the edge PC sets it too — pointing at the hosted Postgres.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import Camera, DetectionJob


class DetectionJobGateTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient
        User = get_user_model()
        self.admin = User.objects.create_user(username="adm", password="x", role="admin")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)
        self.camera = Camera.objects.create(code="CAM-01", name="Gate",
                                            stream_url="rtsp://cam/1")

    @override_settings(DETECTION_ENABLED=False)
    def test_a_hosted_server_refuses_with_503(self):
        r = self.c.post("/api/detection-jobs/",
                        {"violation_type": "smoking", "camera_id": self.camera.pk})
        self.assertEqual(r.status_code, 503, r.content)
        self.assertIn("does not run detectors", r.json()["detail"])

    @override_settings(DETECTION_ENABLED=False)
    def test_it_refuses_before_creating_a_job_row(self):
        # The whole point: no row, so nothing shows as "processing" for ever.
        with mock.patch("subprocess.Popen") as popen:
            self.c.post("/api/detection-jobs/",
                        {"violation_type": "smoking", "camera_id": self.camera.pk})
        popen.assert_not_called()
        self.assertEqual(DetectionJob.objects.count(), 0)

    @override_settings(DETECTION_ENABLED=False)
    def test_an_unknown_violation_type_is_still_refused_as_503(self):
        # Capability is checked first: on a server that cannot detect anything,
        # "which detector" is not the interesting question.
        r = self.c.post("/api/detection-jobs/", {"violation_type": "nonsense"})
        self.assertEqual(r.status_code, 503)

    @override_settings(DETECTION_ENABLED=False)
    def test_reading_the_job_list_still_works(self):
        # The hosted dashboard must still show history from the edge.
        r = self.c.get("/api/detection-jobs/")
        self.assertEqual(r.status_code, 200)

    @override_settings(DETECTION_ENABLED=True)
    def test_the_edge_still_launches_normally(self):
        with mock.patch("subprocess.Popen") as popen, mock.patch("threading.Thread"):
            popen.return_value.pid = 999
            r = self.c.post("/api/detection-jobs/",
                            {"violation_type": "smoking", "camera_id": self.camera.pk})
        self.assertEqual(r.status_code, 201, r.content)
        popen.assert_called_once()

    def test_it_defaults_to_enabled(self):
        # Absent configuration must mean "this is the edge", so an existing
        # local install keeps working after an upgrade.
        from django.conf import settings
        self.assertTrue(getattr(settings, "DETECTION_ENABLED", True))


class LiveMonitorGateTests(TestCase):
    @override_settings(DETECTION_ENABLED=False)
    def test_the_live_monitor_refuses_to_start(self):
        from core.monitor import monitor
        ok, message = monitor.start()
        self.assertFalse(ok)
        self.assertIn("does not run detectors", message)

    @override_settings(DETECTION_ENABLED=False)
    def test_it_refuses_even_with_a_camera_configured(self):
        # Checked before the camera lookup, so the answer does not depend on
        # whether a hosted database happens to hold a stream_url.
        Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision",
                              stream_url="rtsp://user:pass@192.168.1.64:554/x")
        from core.monitor import monitor
        ok, _ = monitor.start()
        self.assertFalse(ok)

    @override_settings(DETECTION_ENABLED=True)
    def test_on_the_edge_it_gets_as_far_as_looking_for_the_camera(self):
        # Not asserting it starts — that would spawn real detectors. Only that
        # the gate is not what stopped it.
        from core.monitor import monitor
        ok, message = monitor.start()
        self.assertFalse(ok)
        self.assertIn("not found", message)
