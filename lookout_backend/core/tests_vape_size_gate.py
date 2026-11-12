"""Vape: small-object puffs and the Bottle size gate (tracking.VAPE_BOTTLE_MAX_FRAC)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from django.test import TestCase

from core.management.commands.watch_drinking import Command as DrinkingCommand
from core.management.commands.watch_merged import holds_real_bottle
from core.management.commands.watch_smoking import Command as SmokingCommand
from core.models import Alert, Camera, ViolationType
from core.vision import location_momentum, scoring, tracking
from core.tests_puff_only_off import needs_puff_only

BOX = (400, 200, 520, 500)           # 300 px tall: 10% is 30 px
SMALL = (440, 260, 460, 275, 0.8, "Bottle")     # 15 px  = 5%
BIG = (440, 260, 460, 300, 0.8, "Bottle")       # 40 px  = 13%
EDGE = (440, 260, 460, 290, 0.8, "Bottle")      # 30 px  = exactly 10%


def person(tid=1, box=BOX):
    return tracking.Track(tid, box, 0.0, 5.0)


class SizeHelper(unittest.TestCase):
    def test_fraction_of_the_person_height(self):
        self.assertAlmostEqual(tracking.bottle_height_frac(SMALL, BOX), 0.05)
        self.assertAlmostEqual(tracking.bottle_height_frac(BIG, BOX), 40 / 300)

    def test_constants(self):
        self.assertEqual(tracking.VAPE_BOTTLE_MAX_FRAC, 0.10)
        self.assertEqual(tracking.VAPE_PUFF_MIN_HOLD_S, 0.7)


class StandDownSizeGate(unittest.TestCase):
    """Rule 2: stand-down only for a Bottle >= 10% of the person height AND conf >= 0.5 (the >= 3 s run is _bottle_standdown's own)."""

    def test_a_small_bottle_never_stands_down(self):
        self.assertFalse(holds_real_bottle(person(), [SMALL]))

    def test_a_big_confident_bottle_does(self):
        self.assertTrue(holds_real_bottle(person(), [BIG]))
        self.assertTrue(holds_real_bottle(person(), [EDGE]))            # exactly 10% counts

    def test_a_big_weak_bottle_does_not(self):
        self.assertFalse(holds_real_bottle(person(), [(440, 260, 460, 300, 0.45, "Bottle")]))

    def test_the_3_second_run_is_still_required(self):
        cmd = SmokingCommand()
        t = person()
        t.ext_id = 7
        for ts in (-1.3, -1.0, -0.5, 0.0):
            cmd.bottle_seen.append((7, BOX, ts))
        self.assertFalse(cmd._bottle_standdown(t, 0.0))
        cmd.bottle_seen[:] = [(7, BOX, -3.0 + i * 0.5) for i in range(7)]
        self.assertTrue(cmd._bottle_standdown(t, 0.0))


class SmallBottlePuffRun(unittest.TestCase):
    def test_counts_only_after_0_7_s(self):
        r = tracking.SmallBottlePuff()
        self.assertIsNone(r.update(0.0, True))
        self.assertIsNone(r.update(0.6, True))
        run, when, hold = r.update(0.7, True)
        self.assertEqual((run, round(when, 2), round(hold, 2)), (1, 0.7, 0.7))

    def test_hold_grows_while_the_run_goes_on(self):
        r = tracking.SmallBottlePuff()
        for t in (0.0, 0.5, 1.0):
            last = r.update(t, True)
        self.assertEqual(last[0], 1)
        self.assertAlmostEqual(last[2], 1.0)

    def test_a_gap_over_a_second_is_a_new_run(self):
        r = tracking.SmallBottlePuff()
        for t in (0.0, 0.4, 0.8):
            r.update(t, True)
        self.assertIsNone(r.update(2.5, True))
        self.assertEqual(r.update(3.3, True)[0], 2)

    def test_out_of_the_zone_counts_nothing(self):
        r = tracking.SmallBottlePuff()
        for t in (0.0, 0.5, 1.0, 1.5):
            self.assertIsNone(r.update(t, False))


class ObjectPuffsOnTheTrack(unittest.TestCase):
    def test_two_object_puffs_are_a_strong_gesture(self):
        t = person()
        t.note_object_puff(("a", 1), 10.0, 0.9)
        self.assertFalse(t.strong_gesture(11.0))
        t.note_object_puff(("a", 2), 40.0, 1.1)
        self.assertTrue(t.strong_gesture(41.0))
        self.assertEqual(t.no_item_puffs(41.0), [0.9, 1.1])

    def test_a_run_still_going_updates_instead_of_adding(self):
        t = person()
        t.note_object_puff(("a", 1), 10.0, 0.7)
        t.note_object_puff(("a", 1), 10.0, 1.4)
        self.assertEqual(t.no_item_puffs(12.0), [1.4])

    def test_an_object_puff_next_to_a_pose_draw_is_the_same_draw(self):
        t = person()
        t.strong_draws.append(20.0)
        t.strong_draw_holds.append((20.0, 1.2))
        t.note_object_puff(("a", 1), 19.0, 0.8)           # within VAPE_PUFF_DEDUP_S of the pose draw
        self.assertEqual(t.no_item_puffs(21.0), [1.2])
        self.assertFalse(t.strong_gesture(21.0))

    def test_a_pose_draw_plus_a_separate_object_puff_make_two(self):
        t = person()
        t.strong_draws.append(20.0)
        t.strong_draw_holds.append((20.0, 1.2))
        t.note_object_puff(("a", 1), 60.0, 0.8)
        self.assertTrue(t.strong_gesture(61.0))

    def test_object_puffs_never_reach_the_cigarette_puff_counter(self):
        t = person()
        t.note_object_puff(("a", 1), 10.0, 0.9)
        t.note_object_puff(("a", 2), 40.0, 0.9)
        self.assertEqual(len(location_momentum.loc_of(t).puffs), 0)
        self.assertEqual(t.gesture_count(41.0), 0)


class SmokingTakesObjectPuffs(TestCase):
    def setUp(self):
        patcher = mock.patch.object(location_momentum, "LOCATION_AWARE_MOMENTUM", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision DS-2CD1047G2")
        cmd = SmokingCommand()
        cmd.dry_run = False
        cmd.camera = self.cam
        cmd.smoking_type = ViolationType.objects.create(code="smoking", label="Smoking", color="#000", icon="x")
        cmd.violations_dir = Path(self.tmp.name)
        cmd.clip = None
        cmd.ablate = set()
        cmd._save_clips = lambda base, frame, now: ("/media/x.mp4", "/media/x_raw.mp4", 15.0)
        self.cmd = cmd
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        self.track = tracking.Track(1, BOX, 0.0, 5.0)
        self.track.ext_id = 5

    def step(self, t):
        self.track.box = BOX
        self.track.last_seen = t
        self.cmd._process_track(self.track, [], t, 2.0, 120, self.frame, False)

    def levels(self):
        return [a.level for a in Alert.objects.order_by("id")]

    @needs_puff_only
    def test_two_small_bottle_puffs_reach_possible_never_likely(self):
        self.cmd.vape_puffs += [{"key": ("d", 1), "ext": 5, "box": BOX, "t": 10.0, "hold": 0.9},
                                {"key": ("d", 2), "ext": 5, "box": BOX, "t": 40.0, "hold": 1.0}]
        for i in range(60):
            self.step(41.0 + i * 0.25)
        self.assertEqual(self.levels(), [scoring.WARNING])
        row = Alert.objects.get()
        self.assertEqual(row.cues["no_item_puffs"]["count"], 2)
        self.assertEqual(len(location_momentum.loc_of(self.track).puffs), 0)

    def test_one_small_bottle_puff_is_not_enough(self):
        self.cmd.vape_puffs.append({"key": ("d", 1), "ext": 5, "box": BOX, "t": 10.0, "hold": 0.9})
        for i in range(30):
            self.step(41.0 + i * 0.25)
        self.assertNotIn(scoring.WARNING, self.levels())

    def test_another_persons_puffs_are_ignored(self):
        self.cmd.vape_puffs += [{"key": ("d", 1), "ext": 9, "box": BOX, "t": 10.0, "hold": 0.9},
                                {"key": ("d", 2), "ext": 9, "box": BOX, "t": 40.0, "hold": 1.0}]
        for i in range(30):
            self.step(41.0 + i * 0.25)
        self.assertEqual(self.levels(), [])

    def test_a_person_with_a_smoking_item_does_not_take_them(self):
        self.track.smoke_item_seen = True
        self.cmd.vape_puffs += [{"key": ("d", 1), "ext": 5, "box": BOX, "t": 10.0, "hold": 0.9},
                                {"key": ("d", 2), "ext": 5, "box": BOX, "t": 40.0, "hold": 1.0}]
        for i in range(10):
            self.step(41.0 + i * 0.25)
        self.assertEqual(self.track.object_puffs, {})


class DrinkingSmallBottle(TestCase):
    def setUp(self):
        patcher = mock.patch.object(location_momentum, "LOCATION_AWARE_MOMENTUM", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision DS-2CD1047G2")
        cmd = DrinkingCommand()
        cmd.dry_run = False
        cmd.camera = self.cam
        cmd.drinking_type = ViolationType.objects.create(code="drinking", label="Drinking", color="#000", icon="x")
        cmd.violations_dir = Path(self.tmp.name)
        cmd.clip = None
        cmd.ablate = set()
        cmd._save_clips = lambda base, frame, now: ("/media/x.mp4", "/media/x_raw.mp4", 15.0)
        cmd._gathering_params = (2, 600.0)
        cmd.vape_puffs = []
        cmd._posture = lambda frame, track, dets, now, prox: "at-mouth"
        self.cmd = cmd
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        self.track = tracking.Track(1, BOX, 0.0, 5.0)

    def run_frames(self, det, seconds=6.0):
        t = 0.0
        while t <= seconds:
            self.track.box = BOX
            self.track.last_seen = t
            self.cmd._process_track(self.track, [det], t, 2.0, 120, self.frame, False,
                                    held_dwell_seconds=5.0, mouth_proximity=3.0, cooldown_center_dist=0.5)
            t += 0.25

    def test_a_small_bottle_is_never_bottle_held_or_a_sip(self):
        self.run_frames(SMALL)
        s = self.track.solo_sip.summary()
        self.assertEqual((s["bottle_held_s"], s["sip_s"]), (0.0, 0.0))
        self.assertFalse(self.track.solo_sip.satisfied)

    def test_a_big_bottle_still_counts(self):
        self.run_frames(BIG)
        s = self.track.solo_sip.summary()
        self.assertTrue(s["bottle_ok"] and s["sip_ok"])

    def test_a_small_bottle_in_the_mouth_zone_for_0_7_s_is_one_puff(self):
        self.run_frames(SMALL, seconds=1.0)
        self.assertEqual(len(self.cmd.vape_puffs), 1)
        self.assertGreaterEqual(self.cmd.vape_puffs[0]["hold"], 0.7)

    def test_a_small_bottle_for_under_0_7_s_is_not(self):
        self.run_frames(SMALL, seconds=0.5)
        self.assertEqual(self.cmd.vape_puffs, [])

    def test_a_big_bottle_is_not_a_vape_puff(self):
        self.run_frames(BIG, seconds=3.0)
        self.assertEqual(self.cmd.vape_puffs, [])

    def test_a_weak_small_bottle_is_not_a_puff(self):
        self.run_frames((440, 260, 460, 275, 0.4, "Bottle"), seconds=3.0)
        self.assertEqual(self.cmd.vape_puffs, [])

    def test_outside_the_mouth_zone_is_not_a_puff(self):
        self.cmd._posture = lambda frame, track, dets, now, prox: "held"
        self.run_frames(SMALL, seconds=3.0)
        self.assertEqual(self.cmd.vape_puffs, [])

    def test_the_small_bottle_alone_never_makes_a_drinking_possible(self):
        self.run_frames(SMALL, seconds=8.0)
        self.assertNotIn(scoring.WARNING, [a.level for a in Alert.objects.all()])
