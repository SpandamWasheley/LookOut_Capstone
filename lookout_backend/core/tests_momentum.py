"""Momentum spec section 8 checklist: flicker holds the cue; a vanished object turns it OFF."""
from django.test import SimpleTestCase

from core.vision.momentum import MomentumBook, MomentumConfig, Slot, update_momentum

FLICKER = [0.71, 0.68, 0.22, 0.74, 0.09, 0.70, 0.66, 0.71, 0.18, 0.69]    # the spec's example


class MomentumTests(SimpleTestCase):
    def test_formula_and_cap(self):
        s = Slot()
        update_momentum(s, 1.0)
        self.assertAlmostEqual(s.momentum, 1.0)
        update_momentum(s, 0.5)
        self.assertAlmostEqual(s.momentum, 1.0 * 0.9 + 0.5)
        for _ in range(50):
            update_momentum(s, 1.0)
        self.assertEqual(s.momentum, 3.0)                      # MAX

    def test_the_cue_turns_on_and_holds_through_the_spec_flicker(self):
        s = Slot()
        states = []
        for conf in FLICKER * 2:
            update_momentum(s, conf)
            states.append(s.cue_on)
        first_on = states.index(True)
        self.assertLessEqual(first_on, 3)
        self.assertTrue(all(states[first_on:]), "the cue dropped during a flicker")

    def test_a_hard_streak_rule_would_have_failed_on_the_same_data(self):
        # the naive rule: confidence >= 0.3 in EVERY one of the last 5 frames
        streak = 0
        ever = False
        for conf in FLICKER * 2:
            streak = streak + 1 if conf >= 0.3 else 0
            ever = ever or streak >= 5
        self.assertFalse(ever)

    def test_the_cue_turns_off_when_the_object_really_leaves(self):
        s = Slot()
        for _ in range(10):
            update_momentum(s, 0.8)
        self.assertTrue(s.cue_on)
        for _ in range(60):
            update_momentum(s, 0.0)
        self.assertFalse(s.cue_on)
        self.assertLess(s.momentum, 0.4)

    def test_hysteresis_between_on_and_off(self):
        s = Slot()
        s.momentum, s.cue_on = 1.0, True                        # between OFF (0.4) and ON (1.5)
        update_momentum(s, 0.0)
        self.assertTrue(s.cue_on)                               # a sag is not "gone"
        s = Slot()
        s.momentum = 1.0                                        # OFF slot in the same band
        update_momentum(s, 0.0)
        self.assertFalse(s.cue_on)                              # and it does not turn on by itself

    def test_config_is_adjustable_per_class(self):
        book = MomentumBook(per_class={"knife": MomentumConfig(decay=0.95, on=2.0, off=0.5, max=4.0)})
        book.step(1, {"knife": 0.8, "bottle": 0.8})
        book.step(1, {"knife": 0.8, "bottle": 0.8})
        self.assertEqual(book.get(1, "knife").momentum > 0, True)
        self.assertEqual(book.snapshot(1, "knife")["decay"], 0.95)
        self.assertEqual(book.snapshot(1, "bottle")["decay"], 0.90)

    def test_slots_are_per_track_and_class_and_cleaned_up_with_the_track(self):
        book = MomentumBook()
        for _ in range(4):
            book.step(1, {"bottle": 0.8})
            book.step(2, {"bottle": 0.8, "knife": 0.6})
        self.assertEqual(len(book), 3)
        self.assertEqual(book.on_classes(1), ["bottle"])
        self.assertEqual(book.drop_missing([1]), 2)            # track 2 lost
        self.assertEqual(len(book), 1)
        self.assertIsNone(book.get(2, "bottle"))
        book.step(2, {"bottle": 0.8})                           # same id returns: starts from 0
        self.assertAlmostEqual(book.get(2, "bottle").momentum, 0.8)

    def test_no_slot_until_the_first_detection_and_decay_when_absent(self):
        book = MomentumBook()
        book.step(1, {})
        self.assertEqual(len(book), 0)
        book.step(1, {"bottle": 0.9})
        book.step(1, {})                                        # absent -> decays, not reset
        self.assertAlmostEqual(book.get(1, "bottle").momentum, 0.81)
