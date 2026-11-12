from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Alert, Camera, Citation, Officer, Violator, ViolationType


class CitationDeleteTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username="adm", password="x", role="admin")
        self.disp = User.objects.create_user(username="dsp", password="x", role="dispatcher")
        cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision DS-2CD1047G2")
        self.vt = ViolationType.objects.create(code="smoking", label="Smoking", color="#000", icon="x")
        self.officer = Officer.objects.create(name="Ofc One")
        self.alert = Alert.objects.create(type=self.vt, camera=cam, confidence=0.6, timestamp=timezone.now())
        self.violator = Violator.objects.create(first_name="Juan", last_name="Cruz")
        self.cit = self._cite(self.alert)

    def _cite(self, alert):
        c = Citation.objects.create(alert=alert, violator=self.violator, first_name_entered="Juan",
                                    last_name_entered="Cruz", officer=self.officer, violator_barangay="TETUAN")
        c.violations.add(self.vt)
        return c

    def _api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def _close_then_reopen(self):
        c = self._api(self.admin)
        c.patch(f"/api/alerts/{self.alert.pk}/", {"status": "resolved"}, format="json")
        c.patch(f"/api/alerts/{self.alert.pk}/", {"status": "active"}, format="json")

    def test_closed_alert_citation_cannot_be_deleted(self):
        self.alert.status = "resolved"
        self.alert.save()
        r = self._api(self.admin).delete(f"/api/citations/{self.cit.pk}/")
        self.assertEqual(r.status_code, 409)
        self.assertTrue(Citation.objects.filter(pk=self.cit.pk).exists())

    def test_reopened_flag_and_admin_delete_removes_sole_violator(self):
        self._close_then_reopen()
        r = self._api(self.admin).get(f"/api/citations/{self.cit.pk}/")
        self.assertIs(r.json()["alert_reopened"], True)
        r = self._api(self.admin).delete(f"/api/citations/{self.cit.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.json()["violator_deleted"], True)
        self.assertFalse(Violator.objects.filter(pk=self.violator.pk).exists())

    def test_violator_with_other_citations_is_kept(self):
        self._close_then_reopen()
        self._cite(None)
        r = self._api(self.admin).delete(f"/api/citations/{self.cit.pk}/")
        self.assertIs(r.json()["violator_deleted"], False)
        self.assertTrue(Violator.objects.filter(pk=self.violator.pk).exists())

    def test_non_admin_cannot_delete(self):
        self._close_then_reopen()
        r = self._api(self.disp).delete(f"/api/citations/{self.cit.pk}/")
        self.assertEqual(r.status_code, 403)
