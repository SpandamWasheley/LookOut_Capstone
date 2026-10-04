import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Alert, Camera, Officer, ViolationType
from core.serializers import AlertSerializer


class WorkflowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username="adm", password="x", role="admin", display_name="Admin A")
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision DS-2CD1047G2")
        self.vt = ViolationType.objects.create(code="smoking", label="Smoking", color="#000", icon="x")
        self.officer = Officer.objects.create(name="Ofc One")
        self.alert = Alert.objects.create(type=self.vt, camera=self.cam, confidence=0.6, level="warning",
                                          timestamp=timezone.now())
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def patch(self, **body):
        r = self.c.patch(f"/api/alerts/{self.alert.pk}/", body, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.alert.refresh_from_db()
        return r.json()

    def test_assign_records_true_then_dismiss_after_assignment_records_false(self):
        self.patch(status="dispatched", officers_assigned=[self.officer.pk])
        self.assertIs(self.alert.reviewed_valid, True)
        self.patch(status="acknowledged", notes="False positive — no violation present")
        self.assertIs(self.alert.reviewed_valid, False)
        self.assertEqual(self.alert.reviewed_by, self.admin)
        kinds = [e["type"] for e in self.alert.timeline]
        self.assertEqual(kinds, ["assigned", "dismissed"])
        self.assertEqual(self.alert.timeline[0]["label"], "Assigned to Ofc One")

    def test_reopen_goes_back_to_pending_work_and_is_on_the_timeline(self):
        self.patch(status="acknowledged", notes="x")
        body = self.patch(status="active")
        self.assertIsNone(self.alert.reviewed_by)
        self.assertEqual([e["type"] for e in self.alert.timeline], ["dismissed", "reopened"])
        self.assertEqual(body["status"], "active")

    def test_a_patch_never_overwrites_what_the_detector_wrote_meanwhile(self):
        stale = Alert.objects.get(pk=self.alert.pk)               # what the web request loaded
        Alert.objects.filter(pk=self.alert.pk).update(level="violation", confidence=0.9)   # detector moves on
        ser = AlertSerializer(stale, data={"status": "dispatched", "officers_assigned": [self.officer.pk]}, partial=True)
        self.assertTrue(ser.is_valid(), ser.errors)
        ser.save()
        self.alert.refresh_from_db()
        self.assertEqual((self.alert.level, self.alert.confidence), ("violation", 0.9))
        self.assertEqual(self.alert.status, "dispatched")

    def test_uploaded_footage_camera_is_shown_as_uploaded_footage(self):
        test_cam = Camera.objects.create(code="CAM-MERGED-TEST", name="Merged-Model Monitor")
        a = Alert.objects.create(type=self.vt, camera=test_cam, confidence=0.5, timestamp=timezone.now())
        data = self.c.get(f"/api/alerts/{a.pk}/").json()
        self.assertEqual(data["camera_zone"], "Uploaded footage")
        self.assertEqual(data["camera"], "CAM-MERGED-TEST")          # the code is unchanged
        self.assertEqual(data["time_source"], "processed")
        live = self.c.get(f"/api/alerts/{self.alert.pk}/").json()
        self.assertEqual(live["camera_zone"], "Hikvision DS-2CD1047G2")
        self.assertEqual(live["time_source"], "live")
        self.assertFalse(live["citation_issued"])
