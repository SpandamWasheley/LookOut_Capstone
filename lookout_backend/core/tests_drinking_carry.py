"""Drinking only: the Bottle's momentum hold (gap carry-over) and the upper-body location rule."""
import collections
import unittest
from types import SimpleNamespace
from unittest import mock

from core.management.commands.watch_drinking import Command as DrinkingCommand
from core.management.commands.watch_smoking import Command as SmokingCommand
from core.management.commands.watch_thief import Command as ThiefCommand
from core.vision import location_momentum as lm
from core.tests_location_momentum import kpts
from core.vision import momentum


def person(kp=None, now=1.0, box=(40, 0, 160, 300)):          # upper 60% is y <= 180
    t = SimpleNamespace(id=1, box=box, stats=None)
    t.loc = lm.TrackLoc()
    t.loc.kp, t.loc.kp_at = kp, now
    return t


def obj(cx, cy, cls="Bottle", conf=0.8, half=6):
    return (cx - half, cy - half, cx + half, cy + half, conf, cls)


class BottleHold(unittest.TestCase):
    def book(self, hold):
        cfg = momentum.DEFAULT_CONFIG
        import dataclasses
        per_class = {"bottle": dataclasses.replace(cfg, hold=hold)} if hold else {}
        return momentum.MomentumBook(cfg, per_class=per_class)

    def burst(self, book, start, n=3):
        for i in range(n):
            book.step(1, {"bottle": 0.8}, start + i * 0.1)

    def test_the_constant_is_one_second(self):
        self.assertEqual(momentum.BOTTLE_HOLD_SECONDS, 1.0)

    def test_momentum_is_frozen_through_a_gap_up_to_one_second(self):
        book = self.book(1.0)
        self.burst(book, 0.0)
        before = book.slots[(1, "bottle")].momentum
        for i in range(1, 8):                                       # 0.7 s with no accepted sighting
            book.step(1, {"bottle": 0.0}, 0.2 + i * 0.1)
        self.assertAlmostEqual(book.slots[(1, "bottle")].momentum, before)

    def test_without_the_hold_it_decays(self):
        book = self.book(0.0)
        self.burst(book, 0.0)
        before = book.slots[(1, "bottle")].momentum
        for i in range(1, 8):
            book.step(1, {"bottle": 0.0}, 0.2 + i * 0.1)
        self.assertLess(book.slots[(1, "bottle")].momentum, before * 0.6)

    def test_bursts_chain_into_one_sighting(self):
        book = self.book(1.0)
        for start in (0.0, 0.9, 1.8, 2.7):                          # 0.3 s bursts 0.6 s apart: gaps under the hold
            self.burst(book, start)
            for i in range(1, 6):
                book.step(1, {"bottle": 0.0}, start + 0.2 + i * 0.1)
        slot = book.slots[(1, "bottle")]
        self.assertTrue(slot.cue_on)
        self.assertTrue(momentum.confirmed(slot, 3.5, 2.0))

    def test_after_more_than_a_second_it_decays_again(self):
        book = self.book(1.0)
        self.burst(book, 0.0)
        before = book.slots[(1, "bottle")].momentum
        for i in range(1, 30):                                      # 2.9 s of nothing
            book.step(1, {"bottle": 0.0}, 0.2 + i * 0.1)
        self.assertLess(book.slots[(1, "bottle")].momentum, before * 0.3)

    def test_only_drinking_gets_the_bottle_hold(self):
        self.assertEqual(DrinkingCommand.momentum_holds, {"bottle": 1.0})
        self.assertEqual(SmokingCommand.momentum_holds, {"knife": momentum.KNIFE_HOLD_SECONDS})
        self.assertEqual(ThiefCommand.momentum_holds, {"knife": momentum.KNIFE_HOLD_SECONDS})
        smoking = SmokingCommand()
        self.assertEqual(smoking._momentum_book().config_for("cigarette").hold, 0.0)
        self.assertEqual(smoking._momentum_book().config_for("bottle").hold, 0.0)
        drinking = DrinkingCommand()
        book = drinking._momentum_book()
        self.assertEqual(book.config_for("bottle").hold, 1.0)
        self.assertEqual(book.config_for("cigarette").hold, 0.0)
        self.assertEqual(book.config_for("knife").hold, 0.0)

    def test_the_confirm_time_is_unchanged(self):
        self.assertEqual(DrinkingCommand()._confirm_seconds_for("bottle"), DrinkingCommand().confirm_seconds)


class UpperBodyGate(unittest.TestCase):
    def setUp(self):
        lm.reset()
        self.cmd = SimpleNamespace(stats=collections.Counter())

    def test_a_bottle_in_the_upper_60_percent_is_accepted_without_an_anchor(self):
        out, why = lm.evaluate_track(self.cmd, person(None), [obj(100, 100)], 1.0)    # no keypoints at all
        self.assertGreater(out["bottle"], 0.0)
        self.assertEqual(why, "")

    def test_a_bottle_in_the_lower_40_percent_is_still_rejected(self):
        out, why = lm.evaluate_track(self.cmd, person(None), [obj(100, 220)], 1.0)
        self.assertEqual((out["bottle"], why), (0.0, "no-kp"))

    def test_with_keypoints_the_lower_bottle_is_off_anchor(self):
        out, why = lm.evaluate_track(self.cmd, person(kpts(lw=(60, 160))), [obj(100, 220)], 1.0)
        self.assertEqual((out["bottle"], why), (0.0, "off-anchor"))

    def test_the_edge_of_the_zone(self):
        self.assertGreater(lm.evaluate_track(self.cmd, person(None), [obj(100, 174)], 1.0)[0]["bottle"], 0.0)   # centre at y 174 <= 180
        lm.reset()
        self.assertEqual(lm.evaluate_track(self.cmd, person(None), [obj(100, 190)], 1.0)[0]["bottle"], 0.0)

    def test_it_must_be_the_same_persons_box(self):
        out, why = lm.evaluate_track(self.cmd, person(None), [obj(300, 100)], 1.0)      # beside the person
        self.assertEqual(out["bottle"], 0.0)

    def test_a_cigarette_gets_no_such_rule(self):
        out, why = lm.evaluate_track(self.cmd, person(None), [obj(100, 100, cls="Cigarette")], 1.0)
        self.assertEqual((out["cigarette"], why), (0.0, "no-kp"))

    def test_a_flip_is_still_rejected(self):
        # a spot another engine just saw as a Cigarette is not a Bottle, wherever it is on the person
        lm.note_detections(0.9, [obj(100, 100, cls="Cigarette")])
        out, why = lm.evaluate_track(self.cmd, person(None, 1.0), [obj(100, 100)], 1.0)
        self.assertEqual((out["bottle"], why), (0.0, "flip"))

    def test_a_bottle_rising_chest_to_mouth_is_not_a_jump(self):
        lm.evaluate_track(self.cmd, person(None), [obj(100, 170)], 1.0)
        out, why = lm.evaluate_track(self.cmd, person(None, 1.1), [obj(100, 40)], 1.1)   # moved 130 px > 0.25 x 300
        self.assertGreater(out["bottle"], 0.0)
        self.assertEqual(why, "")

    def test_a_far_jump_is_still_a_jump_for_an_anchored_hit(self):
        kp = kpts(lw=(60, 250))                                                         # left wrist low
        self.assertGreater(lm.evaluate_track(self.cmd, person(kp), [obj(60, 250)], 1.0)[0]["bottle"], 0.0)   # at the wrist, lower body
        # a hit far from it that is NOT in the upper zone and not at an anchor is still rejected
        out, why = lm.evaluate_track(self.cmd, person(kp, 1.1), [obj(150, 270)], 1.1)
        self.assertEqual((out["bottle"], why), (0.0, "off-anchor"))
