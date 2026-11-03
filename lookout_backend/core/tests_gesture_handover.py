"""A person re-issued a new track ID keeps the hand-to-mouth draws the old ID counted (vision/gesture_handover.py)."""
import unittest
from unittest import mock

from core.management.commands.watch_smoking import Command as Smoking
from core.vision import gesture_handover as gh
from core.vision import tracking


def track(tid, box, draws=0):
    t = tracking.Track(tid, box, 0.0)
    for i in range(draws):
        t.gestures.append(10.0 + i)
        t.strong_draws.append(10.0 + i)
        t.strong_draw_holds.append((10.0 + i, 1.2))
    return t


class GestureHandoverRules(unittest.TestCase):
    def setUp(self):
        self.h = gh.GestureHandover()

    def test_a_new_id_inside_the_vanished_box_adopts_the_draws(self):
        old = track(1, (100, 100, 200, 400), draws=1)
        self.h.note(old, old.box, 10.0)
        new = track(15, (110, 105, 205, 400))
        got = self.h.adopt(new, new.box, 10.5)
        self.assertIs(got, old)
        gh.GestureHandover.carry(got, new)
        self.assertEqual(len(new.gestures), 1)
        self.assertEqual(len(new.strong_draws), 1)
        self.assertEqual(len(new.strong_draw_holds), 1)

    def test_a_live_track_is_never_robbed(self):
        old = track(1, (100, 100, 200, 400), draws=1)
        self.h.note(old, old.box, 10.0)
        self.assertIsNone(self.h.adopt(track(15, (110, 105, 205, 400)), (110, 105, 205, 400), 10.1))

    def test_an_old_id_gone_too_long_is_not_adopted(self):
        old = track(1, (100, 100, 200, 400), draws=1)
        self.h.note(old, old.box, 10.0)
        self.assertIsNone(self.h.adopt(track(15, (110, 105, 205, 400)), (110, 105, 205, 400), 12.0))

    def test_a_person_standing_elsewhere_adopts_nothing(self):
        old = track(1, (100, 100, 200, 400), draws=1)
        self.h.note(old, old.box, 10.0)
        self.assertIsNone(self.h.adopt(track(15, (900, 100, 1000, 400)), (900, 100, 1000, 400), 10.6))

    def test_each_old_id_is_adopted_once(self):
        old = track(1, (100, 100, 200, 400), draws=1)
        self.h.note(old, old.box, 10.0)
        self.assertIs(self.h.adopt(track(15, (110, 105, 205, 400)), (110, 105, 205, 400), 10.6), old)
        self.assertIsNone(self.h.adopt(track(16, (110, 105, 205, 400)), (110, 105, 205, 400), 10.7))

    def test_an_old_id_with_no_draws_is_not_worth_adopting(self):
        old = track(1, (100, 100, 200, 400), draws=0)
        self.h.note(old, old.box, 10.0)
        self.assertIsNone(self.h.adopt(track(15, (110, 105, 205, 400)), (110, 105, 205, 400), 10.6))

    def test_the_switch_turns_it_off(self):
        old = track(1, (100, 100, 200, 400), draws=1)
        self.h.note(old, old.box, 10.0)
        with mock.patch.object(gh, "GESTURE_HANDOVER", False):
            self.assertIsNone(self.h.adopt(track(15, (110, 105, 205, 400)), (110, 105, 205, 400), 10.6))


class WatcherCarryOver(unittest.TestCase):
    def setUp(self):
        self.cmd = Smoking()
        self.cmd.stdout = mock.Mock()

    def test_the_watcher_carries_draws_to_a_reissued_id(self):
        old = track(1, (100, 100, 200, 400), draws=1)
        old.last_seen = 10.0
        self.cmd._carry_draws_over(old, 10.0)
        new = track(15, (110, 105, 205, 400))
        new.first_seen = new.last_seen = 10.0 + 0.6
        self.cmd._carry_draws_over(new, 10.6)
        self.assertEqual(len(new.strong_draws), 1)
        self.assertEqual(self.cmd.stats["draws carried over to a re-issued track ID"], 1)

    def test_a_young_track_keeps_looking_until_the_old_id_vanishes(self):
        old = track(1, (100, 100, 200, 400), draws=1)
        new = track(15, (110, 105, 205, 400))
        new.first_seen = 10.0
        for now in (10.0, 10.1, 10.2):                                 # both on screen: nothing to adopt yet
            old.last_seen = new.last_seen = now
            self.cmd._carry_draws_over(old, now)
            self.cmd._carry_draws_over(new, now)
        self.assertEqual(len(new.strong_draws), 0)
        new.last_seen = 10.6                                           # the old ID stopped being matched at 10.2: it is gone
        self.cmd._carry_draws_over(old, 10.6)
        self.cmd._carry_draws_over(new, 10.6)
        self.assertEqual(len(new.strong_draws), 1)

    def test_an_old_track_never_adopts(self):
        old = track(1, (100, 100, 200, 400), draws=1)
        old.last_seen = 10.0
        self.cmd._carry_draws_over(old, 10.0)
        new = track(15, (110, 105, 205, 400))
        new.first_seen = 0.0                                           # tracked for 10 s already
        new.last_seen = 10.6
        self.cmd._carry_draws_over(new, 10.6)
        self.assertEqual(len(new.strong_draws), 0)

    def test_a_person_with_a_bottle_on_them_inherits_nothing(self):
        old = track(1, (100, 100, 200, 400), draws=1)
        old.last_seen = 10.0
        self.cmd._carry_draws_over(old, 10.0)
        self.cmd.bottle_seen.append((99, (105, 100, 200, 400), 9.0))   # a real Bottle on whoever stands there
        new = track(15, (110, 105, 205, 400))
        new.first_seen = new.last_seen = 10.0 + 0.6
        self.cmd._carry_draws_over(new, 10.6)
        self.assertEqual(len(new.strong_draws), 0)


if __name__ == "__main__":
    unittest.main()
