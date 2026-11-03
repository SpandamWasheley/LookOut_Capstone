"""Drinking's solo Possible rule (core/vision/solo_sip.py): Bottle >= 0.5 held 3 s continuous AND a 1.0 s sip."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from django.test import TestCase

from core.management.commands.watch_drinking import Command as DrinkingCommand
from core.models import Alert, Camera, ViolationType
from core.vision import location_momentum, scoring, solo_sip, tracking

BOX = (400, 200, 520, 500)


def bottle(conf=0.8):
    return (440, 260, 460, 300, conf, "Bottle")


class GateTests(unittest.TestCase):
    def run_gate(self, gate, start, end, conf=0.8, zone=False, step=0.25):
        t = start
        while t <= end + 1e-9:
            gate.update(t, conf, zone)
            t += step

    def test_bottle_held_needs_three_continuous_seconds(self):
        g = solo_sip.SoloSipGate()
        self.run_gate(g, 0.0, 2.75)
        self.assertFalse(g.bottle_ok)
        self.run_gate(g, 3.0, 3.0)
        self.assertTrue(g.bottle_ok)

    def test_a_bottle_under_half_confidence_does_not_count(self):
        g = solo_sip.SoloSipGate()
        self.run_gate(g, 0.0, 6.0, conf=0.45)
        self.assertEqual(g.summary()["bottle_held_s"], 0.0)
        self.assertFalse(g.bottle_ok)

    def test_a_gap_over_one_second_restarts_the_run(self):
        g = solo_sip.SoloSipGate()
        self.run_gate(g, 0.0, 2.0)
        self.run_gate(g, 3.5, 5.0)                                 # 1.5 s with no Bottle: a new run
        self.assertFalse(g.bottle_ok)
        self.assertEqual(g.summary()["bottle_held_s"], 2.0)

    def test_sip_needs_a_one_second_hold_in_the_mouth_zone(self):
        g = solo_sip.SoloSipGate()
        self.run_gate(g, 0.0, 0.75, zone=True)
        self.assertFalse(g.sip_ok)
        self.run_gate(g, 1.0, 1.0, zone=True)
        self.assertTrue(g.sip_ok)

    def test_short_dips_do_not_add_up_to_a_sip_under_the_old_rule(self):
        # superseded by the separate-sip count (tests_solo_sip_count.py); the switch restores this behaviour
        with mock.patch.object(solo_sip, "SIP_COUNT_ENABLED", False):
            g = solo_sip.SoloSipGate()
            for start in (0.0, 2.0, 4.0):
                self.run_gate(g, start, start + 0.5, zone=True)
                self.run_gate(g, start + 0.75, start + 1.75, zone=False)
            self.assertFalse(g.sip_ok)

    def test_separate_half_second_dips_count_as_sips_under_the_new_rule(self):
        g = solo_sip.SoloSipGate()
        for start in (0.0, 2.0, 4.0):
            self.run_gate(g, start, start + 0.5, zone=True)
            self.run_gate(g, start + 0.75, start + 1.75, zone=False)
        self.assertTrue(g.sip_ok)

    def test_a_sip_needs_a_confident_bottle(self):
        g = solo_sip.SoloSipGate()
        self.run_gate(g, 0.0, 3.0, conf=0.4, zone=True)
        self.assertFalse(g.sip_ok)

    def test_both_conditions_are_needed(self):
        g = solo_sip.SoloSipGate()
        self.run_gate(g, 0.0, 4.0, zone=False)
        self.assertFalse(g.satisfied)
        self.run_gate(g, 4.25, 5.5, zone=True)
        self.assertTrue(g.satisfied)

    def test_summary_and_testing_rows(self):
        g = solo_sip.SoloSipGate()
        self.run_gate(g, 0.0, 3.25)
        self.run_gate(g, 3.5, 4.9, zone=True, step=0.1)
        s = g.summary()
        self.assertEqual(s["bottle_held_s"], 4.9)
        self.assertEqual(s["sip_s"], 1.4)
        self.assertEqual([r["name"] for r in solo_sip.indicators(s)], ["bottle held 0.0 s (best 4.9 s)", "sip 0.0 s (best 1.4 s)"])

    def test_current_values_sit_next_to_the_best(self):
        g = solo_sip.SoloSipGate()
        self.run_gate(g, 0.0, 4.8, zone=True)                       # best run: 4.8 s bottle, 4.8 s sip
        self.run_gate(g, 6.0, 7.2, step=0.2)                        # a new run: 1.2 s of bottle, no sip
        s = g.summary(7.2)
        self.assertEqual((s["bottle_now_s"], s["bottle_held_s"]), (1.2, 4.8))
        self.assertEqual((s["sip_now_s"], s["sip_s"]), (0.0, 4.8))
        self.assertEqual([r["name"] for r in solo_sip.indicators(s)],
                         ["bottle held 1.2 s (best 4.8 s)", "sip 0.0 s (best 4.8 s)"])

    def test_current_drops_to_zero_once_the_bottle_is_gone(self):
        g = solo_sip.SoloSipGate()
        self.run_gate(g, 0.0, 2.0)
        self.assertEqual(g.summary(2.5)["bottle_now_s"], 2.0)
        self.assertEqual(g.summary(4.0)["bottle_now_s"], 0.0)       # more than RUN_GAP_SECONDS since the last sample


class SoloDrinkingTests(TestCase):
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
        self.cmd = cmd
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        self.track = tracking.Track(1, BOX, 0.0, 5.0)
        self.zone = False
        cmd._posture = lambda frame, track, dets, now, prox: "at-mouth" if self.zone else "held"

    def step(self, t, conf=0.8, zone=False):
        self.track.box = BOX
        self.track.last_seen = t
        self.zone = zone
        self.cmd._process_track(self.track, [bottle(conf)] if conf else [], t, 2.0, 120, self.frame, False,
                                held_dwell_seconds=5.0, mouth_proximity=3.0, cooldown_center_dist=0.5)

    def level(self):
        row = Alert.objects.order_by("id").last()
        return row.level if row else None

    def test_bottle_at_the_mouth_without_three_seconds_stays_monitoring(self):
        for i in range(12):                                        # 0 - 2.75 s, at the mouth throughout
            self.step(i * 0.25, zone=True)
        self.assertNotEqual(self.level(), scoring.WARNING)
        self.assertEqual(self.level(), scoring.MONITORING)

    def test_three_seconds_of_bottle_without_a_sip_stays_monitoring(self):
        for i in range(30):
            self.step(i * 0.25, zone=False)
        self.assertEqual(self.level(), scoring.MONITORING)

    def test_a_short_mouth_touch_is_not_a_sip(self):
        for i in range(30):
            self.step(i * 0.25, zone=(8 <= i < 11))                # 0.75 s at the mouth
        self.assertEqual(self.level(), scoring.MONITORING)

    def test_sustained_bottle_and_a_held_sip_reach_possible(self):
        for i in range(30):
            self.step(i * 0.25, zone=(8 <= i < 14))                # 1.25 s at the mouth, bottle held 7.25 s
        self.assertEqual(self.level(), scoring.WARNING)
        row = Alert.objects.order_by("id").last()
        self.assertTrue(row.cues["solo_sip"]["bottle_ok"])
        self.assertTrue(row.cues["solo_sip"]["sip_ok"])
        self.assertGreaterEqual(row.cues["solo_sip"]["sip_s"], 1.0)

    def test_a_weak_bottle_never_earns_the_gate(self):
        for i in range(40):
            self.step(i * 0.25, conf=0.45, zone=True)
        self.assertFalse(self.track.solo_sip.satisfied)
        self.assertNotEqual(self.level(), scoring.WARNING)

    def test_a_group_member_is_not_held_to_the_solo_rule(self):
        cluster = mock.Mock(id=9, member_ids={1, 2}, duration_held=1.0)
        cluster.stationary.return_value = False
        self.cmd._cluster_of = {1: cluster}
        with mock.patch("core.management.commands.watch_drinking.scoring.Score", wraps=scoring.Score) as score:
            for i in range(4):
                self.step(i * 0.25, zone=True)
        self.assertTrue(score.call_args_list)
        self.assertTrue(all(call.kwargs.get("cap") is None for call in score.call_args_list))

    def test_a_lone_person_is_capped_until_the_gate_is_met(self):
        with mock.patch("core.management.commands.watch_drinking.scoring.Score", wraps=scoring.Score) as score:
            self.step(0.0, zone=True)
        self.assertEqual(score.call_args.kwargs["cap"], scoring.MONITORING)
