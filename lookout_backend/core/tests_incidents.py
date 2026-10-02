"""Incident lifecycle (spec v6 sections 2-3) driven through the real smoking
watcher: Monitoring -> Possible -> Likely on ONE Alert row, a clip only once the
event is Possible / Likely, and the capped puff-only path.
"""
import tempfile
from pathlib import Path

import numpy as np
from django.test import TestCase

from core.management.commands.watch_smoking import Command as SmokingCommand
from core.models import Alert, Camera, ViolationType
from core.vision import scoring, tracking, vlm

BOX = (400, 200, 520, 500)
CIG = (440, 230, 450, 245, 0.8, "Cigarette")


class SmokingIncidentTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision DS-2CD1047G2")
        self.vtype = ViolationType.objects.create(code="smoking", label="Smoking", color="#000", icon="x")
        cmd = SmokingCommand()
        cmd.dry_run = False
        cmd.camera = self.cam
        cmd.smoking_type = self.vtype
        cmd.violations_dir = Path(self.tmp.name)
        cmd.clip = None
        cmd.vlm = vlm.DisabledVerifier("test")
        cmd.vlm_async = False
        cmd.ablate = set()
        self.clip_calls = []

        def fake_save_clips(base, frame, now):
            self.clip_calls.append(now)
            return f"/media/violations/{base}.mp4", f"/media/violations/{base}_raw.mp4"

        cmd._save_clips = fake_save_clips
        self.cmd = cmd
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        self.track = tracking.Track(1, BOX, 0.0, 5.0)

    def step(self, t, dets, near_mouth=False):
        """One processed frame at time `t`."""
        self.track.box = BOX
        self.track.last_seen = t
        if near_mouth:
            self.track.last_mouth_ratio, self.track.last_mouth_seen = 0.8, t
        self.cmd._process_track(self.track, dets, t, 2.0, 120, self.frame, False)

    def puff(self, t):
        """One completed hand-to-mouth cycle (wrist at the face, then down)."""
        self.track.update_gesture(0.3, t)
        self.track.update_gesture(2.0, t + 0.3)

    def rows(self):
        return list(Alert.objects.order_by("id"))

    def test_object_alone_is_monitoring_with_an_image_and_no_clip(self):
        for i in range(30):
            self.step(i * 0.25, [CIG])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].level, scoring.MONITORING)
        self.assertEqual(rows[0].video_url, "")           # no clip while Monitoring
        self.assertTrue(rows[0].image_url)                 # one image
        self.assertEqual(self.clip_calls, [])
        self.assertAlmostEqual(rows[0].confidence, 0.40, places=2)

    def test_one_row_rises_to_possible_then_likely_and_the_clip_follows(self):
        for i in range(30):
            self.step(i * 0.25, [CIG])                       # Monitoring (40)
        self.puff(8.0)
        for i in range(8):
            self.step(8.0 + i * 0.25, [CIG])                 # + 1 puff = 60 -> Possible
        self.assertEqual(self.rows()[-1].level, scoring.WARNING)
        self.assertTrue(self.clip_calls, "no clip written on reaching Possible")
        for i in range(8):
            self.step(10.0 + i * 0.25, [CIG], near_mouth=True)   # + at mouth = 75 -> Likely
        rows = self.rows()
        self.assertEqual(len(rows), 1, "a new row was created instead of updating the old one")
        self.assertEqual(rows[0].level, scoring.VIOLATION)
        self.assertAlmostEqual(rows[0].confidence, 0.75, places=2)
        self.assertTrue(rows[0].video_url)
        self.assertEqual(rows[0].cues["level"], scoring.VIOLATION)
        self.assertIn("near_mouth", rows[0].cues["cues"])

    def test_puff_only_is_possible_capped_and_tagged(self):
        for t in (1.0, 20.0, 40.0):
            self.puff(t)
        self.step(60.0, [])                                  # no cigarette anywhere
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].level, scoring.WARNING)
        self.assertTrue(rows[0].cues["puff_only"])
        self.assertIn("based on hand movement only", rows[0].cues["tag"])
        self.assertAlmostEqual(rows[0].confidence, 0.55, places=2)

    def test_one_or_two_puffs_without_an_item_are_only_logged(self):
        self.puff(1.0)
        self.puff(20.0)
        self.step(40.0, [])
        self.assertEqual(self.rows(), [])
        self.assertTrue(any(k.startswith("puffs logged only") for k in self.cmd.stats))

    def test_the_incident_closes_after_the_object_goes(self):
        for i in range(30):
            self.step(i * 0.25, [CIG])
        self.cmd._incident_gc(60.0, self.frame)
        self.assertEqual(self.cmd.stats["incident ended"], 1)
        self.assertEqual(self.cmd._incident_book(), {})
        self.assertEqual(len(self.rows()), 1)                # the record stays

    def test_default_alert_list_hides_monitoring(self):
        from rest_framework.test import APIClient
        from core.models import User
        for i in range(30):
            self.step(i * 0.25, [CIG])
        user = User.objects.create_user(username="a", password="x", role="admin")
        client = APIClient()
        client.force_authenticate(user)
        res = client.get("/api/alerts/")
        body = res.json()
        listed = body["results"] if isinstance(body, dict) else body
        self.assertEqual(listed, [])
        res = client.get("/api/alerts/?level=monitoring")
        body = res.json()
        listed = body["results"] if isinstance(body, dict) else body
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["level_label"], "Monitoring")


class ThiefAndDrinkingTests(TestCase):
    def setUp(self):
        from core.management.commands.watch_thief import Command as ThiefCommand
        from core.management.commands.watch_drinking import Command as DrinkingCommand
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision DS-2CD1047G2")
        self.thief = ThiefCommand()
        self.thief.dry_run = False
        self.thief.camera = self.cam
        self.thief.thief_type = ViolationType.objects.create(code="theft", label="Holdup", color="#000", icon="x")
        self.thief.violations_dir = Path(self.tmp.name)
        self.thief.clip = None
        self.thief.ablate = set()
        self.thief._save_clips = lambda base, frame, now: ("/media/x.mp4", "/media/x_raw.mp4")
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        self.drink = DrinkingCommand()

    def _knife_track(self, tid, box):
        t = tracking.Track(tid, box, 0.0, 5.0)
        return t

    def test_a_knife_with_nobody_near_is_monitoring_only(self):
        holder = self._knife_track(1, (400, 200, 520, 500))
        self.thief._frame_tracks = [holder]
        ev = self.thief._knife_evidence(holder, "knife", 3.0, 10.0, holder.box)
        self.assertEqual(ev.band, scoring.MONITORING)
        self.assertFalse(ev.people_near)

    def test_a_second_person_near_the_holder_allows_possible(self):
        holder = self._knife_track(1, (400, 200, 520, 500))          # 300 px tall
        near = self._knife_track(2, (560, 200, 680, 500))            # ~1.0 heights away
        far = self._knife_track(3, (1100, 200, 1220, 500))           # ~2.3 heights away
        import datetime
        from unittest import mock
        with mock.patch("core.management.commands.watch_thief.datetime") as dt:
            dt.datetime.now.return_value = datetime.datetime(2026, 10, 2, 16, 0)   # x1.36
            self.thief._frame_tracks = [holder, far]
            self.assertEqual(self.thief._knife_evidence(holder, "knife", 3.0, 10.0, holder.box).band,
                             scoring.MONITORING)
            self.thief._frame_tracks = [holder, near]
            ev = self.thief._knife_evidence(holder, "knife", 3.0, 10.0, holder.box)
        self.assertTrue(ev.people_near)
        self.assertEqual(ev.band, scoring.WARNING)
        self.assertAlmostEqual(ev.score, 0.45 * 1.36, places=3)

    def test_the_knife_row_stores_the_score_not_the_yolo_confidence(self):
        holder = self._knife_track(1, (400, 200, 520, 500))
        self.thief._frame_tracks = [holder]
        ev = self.thief._knife_evidence(holder, "knife", 3.0, 10.0, holder.box)
        self.thief._incident_sync(
            ("knife", 1), ev, 10.0, frame=self.frame, box=holder.box,
            create=lambda level, with_clip: self.thief._create_alert(
                ev.score, "knife", self.frame, "d", now=10.0, evidence=ev,
                with_clip=with_clip, object_confidence=0.84))
        row = Alert.objects.get()
        self.assertEqual(row.level, scoring.MONITORING)
        self.assertLess(row.confidence, 0.80)                 # not the 0.84 box score
        self.assertAlmostEqual(row.object_confidence, 0.84)
        self.assertEqual(row.video_url, "")

    def test_a_bottle_on_the_table_counts_for_the_gathering(self):
        class C:
            bbox = (300, 200, 700, 500)
        scene = tracking.Track(-1, (450, 520, 480, 560), 0.0, 5.0, is_scene=True)
        person = tracking.Track(7, (320, 200, 420, 480), 0.0, 5.0)
        per_track = {person: [], scene: [(450, 520, 480, 560, 0.7, "Bottle")]}
        found = self.drink._scene_dets_near(C(), per_track)
        self.assertEqual(len(found), 1)
        far = {scene: [(1200, 100, 1230, 140, 0.7, "Bottle")]}
        self.assertEqual(self.drink._scene_dets_near(C(), far), [])


class HiResCheckTests(SmokingIncidentTests):
    """Path 2 (spec v6 section 6): a puff with no item triggers a high-resolution
    cigarette check on that person's head-and-hands crop for a few seconds."""

    def test_a_puff_without_an_item_runs_the_check_and_a_find_joins_the_normal_path(self):
        from unittest import mock
        from core.vision import recognition
        self.puff(1.0)
        self.track.hires_until = 1.3 + 4.0           # what _feed_pose sets on a new puff
        found = [(440, 230, 450, 245, 0.7, "Cigarette")]
        with mock.patch.object(recognition, "detect_smoking_cascade", return_value=found) as cascade:
            out = self.cmd._hires_check(self.frame, {self.track: []}, 2.0)
        self.assertTrue(cascade.called)
        # the crop handed to the detector is the head-and-hands region (top of the box)
        top_box = cascade.call_args[0][1][0]
        self.assertLess(top_box[3], BOX[3])
        self.assertEqual(out[self.track], found)
        self.assertEqual(self.cmd.stats["hires check: cigarette found"], 1)

    def test_nothing_found_changes_nothing_and_the_check_stops_after_the_window(self):
        from unittest import mock
        from core.vision import recognition
        self.track.hires_until = 5.0
        with mock.patch.object(recognition, "detect_smoking_cascade", return_value=[]):
            out = self.cmd._hires_check(self.frame, {self.track: []}, 2.0)
        self.assertEqual(out[self.track], [])
        with mock.patch.object(recognition, "detect_smoking_cascade") as cascade:
            self.cmd._hires_check(self.frame, {self.track: []}, 6.0)   # window over
        self.assertFalse(cascade.called)
