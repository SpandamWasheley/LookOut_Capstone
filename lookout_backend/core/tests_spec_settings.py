import datetime

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from core.models import SystemSettings
from core.vision import spec_settings


class ConfirmationTests(SimpleTestCase):
    def test_a_cue_must_stay_on_for_the_confirmation_time(self):
        from core.vision import momentum
        slot = momentum.Slot()
        cfg = momentum.MomentumConfig()
        momentum.update_momentum(slot, 0.9, cfg, now=10.0)
        momentum.update_momentum(slot, 0.9, cfg, now=10.1)
        self.assertTrue(slot.cue_on)                                  # momentum turned ON within two frames ...
        self.assertFalse(momentum.confirmed(slot, 10.1, 2.0))        # ... but it is not confirmed yet
        momentum.update_momentum(slot, 0.9, cfg, now=12.2)
        self.assertTrue(momentum.confirmed(slot, 12.2, 2.0))

    def test_going_off_restarts_the_confirmation_clock(self):
        from core.vision import momentum
        slot = momentum.Slot()
        cfg = momentum.MomentumConfig()
        for t in (0.0, 0.1):
            momentum.update_momentum(slot, 0.9, cfg, now=t)
        for t in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.4):
            momentum.update_momentum(slot, 0.0, cfg, now=t)
        self.assertFalse(slot.cue_on)
        self.assertIsNone(slot.on_since)

    def test_points_and_cutoffs_are_not_adjustable(self):
        adjustable = {f for fields in spec_settings.GROUPS.values() for f in fields}
        for name in adjustable:
            self.assertFalse(any(w in name for w in ("points", "cutoff", "threshold_55", "threshold_75")), name)

    def test_the_new_timings_have_spec_defaults(self):
        self.assertEqual(spec_settings.SPEC_DEFAULTS["cue_hold_seconds"], 5.0)
        self.assertEqual(spec_settings.SPEC_DEFAULTS["monitoring_min_seconds"], 3.0)


class SettingsApiTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="admin1", password="x", role="admin")
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_defaults_are_the_spec_values(self):
        cfg = SystemSettings.load()
        for field, value in spec_settings.SPEC_DEFAULTS.items():
            self.assertEqual(getattr(cfg, field), value, field)

    def test_reset_restores_one_violation_only(self):
        cfg = SystemSettings.load()
        cfg.drinking_min_group, cfg.smoking_puff_count, cfg.holdup_near_person_heights = 5, 6, 3.0
        cfg.save()
        r = self.client.post("/api/settings/reset/", {"violation": "drinking"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        cfg.refresh_from_db()
        self.assertEqual(cfg.drinking_min_group, 2)
        self.assertEqual(cfg.smoking_puff_count, 6)             # untouched
        self.assertEqual(cfg.holdup_near_person_heights, 3.0)   # untouched
        self.assertEqual(self.client.post("/api/settings/reset/", {"violation": "nope"}, format="json").status_code, 400)

    def test_values_outside_the_allowed_range_are_rejected(self):
        r = self.client.patch("/api/settings/", {"smoking_puff_count": 1}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.patch("/api/settings/", {"smoking_puff_count": 4, "object_confirm_seconds": 3.0}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["spec_defaults"]["object_confirm_seconds"], 2.0)

    def test_snapshot_lists_every_adjustable_timing(self):
        snap = spec_settings.snapshot(SystemSettings.load())
        self.assertEqual(snap["drinking_start"], "16:00")
        for field in spec_settings.SPEC_DEFAULTS:
            self.assertIn(field, snap)


class TestingToolsSettingTests(TestCase):
    def test_testing_tools_are_off_by_default_and_admin_can_switch_them_on(self):
        user = get_user_model().objects.create_user(username="admin2", password="x", role="admin")
        client = APIClient()
        client.force_authenticate(user)
        self.assertIs(client.get("/api/settings/").json()["show_testing_tools"], False)
        r = client.patch("/api/settings/", {"show_testing_tools": True}, format="json")
        self.assertIs(r.json()["show_testing_tools"], True)
        officer = get_user_model().objects.create_user(username="off1", password="x", role="officer")
        client.force_authenticate(officer)
        self.assertEqual(client.get("/api/settings/").status_code, 403)       # settings stay admin-only
