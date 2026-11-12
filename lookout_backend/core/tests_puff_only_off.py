"""Smoking needs a detected cigarette: the puff-only (no item) path and vape handling are retired.

`scoring.PUFF_ONLY_ENABLED` is False. Tests that exercise the old capped puff-only path import `needs_puff_only` from
here and are skipped while it is off, rather than deleted, so flipping the flag back on re-arms them.
"""
import unittest

from django.test import SimpleTestCase

from core.vision import scoring

needs_puff_only = unittest.skipUnless(
    scoring.PUFF_ONLY_ENABLED, "puff-only smoking (no cigarette) is retired: scoring.PUFF_ONLY_ENABLED is False")


class NoCigaretteNoSmokingEvent(SimpleTestCase):
    def test_the_puff_only_path_is_off(self):
        self.assertFalse(scoring.PUFF_ONLY_ENABLED)

    def test_hand_to_mouth_alone_never_stores_an_event(self):
        # every puff cue at once, no cigarette: previously the capped puff-only path, now nothing
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, {"gesture", "puffs", "puff_pattern"}, object_on=False)
        self.assertFalse(s.puff_only)
        self.assertEqual(s.level, scoring.NONE)
        self.assertFalse(s.stored)

    def test_a_cigarette_still_makes_an_event(self):
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, {"cigarette", "near_mouth", "gesture", "puffs"},
                          object_on=True)
        self.assertTrue(s.stored)

    def test_vape_is_not_a_smoking_class(self):
        from core.vision import recognition
        self.assertNotIn("vape", recognition.SMOKING_CLASSES)
