import datetime

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from core.models import SystemSettings
from core.vision import spec_settings


class MomentumMappingTests(SimpleTestCase):
    def test_two_seconds_is_the_spec_momentum_default(self):
        cfg = spec_settings.momentum_config(2.0)
        self.assertEqual((cfg.decay, cfg.on, cfg.off, cfg.max), (0.90, 1.5, 0.4, 3.0))

    def test_longer_confirmation_raises_the_on_threshold_and_keeps_headroom(self):
        cfg = spec_settings.momentum_config(4.0)
        self.assertEqual(cfg.on, 3.0)
        self.assertGreaterEqual(cfg.max, cfg.on * 2)

    def test_points_and_cutoffs_are_not_adjustable(self):
        adjustable = {f for fields in spec_settings.GROUPS.values() for f in fields}
        for name in adjustable:
            self.assertFalse(any(w in name for w in ("points", "cutoff", "threshold_55", "threshold_75")), name)


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
