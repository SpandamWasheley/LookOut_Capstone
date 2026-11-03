"""solo_sip: separate short sips inside a window also qualify as the sip."""
import unittest
from unittest import mock

from core.vision import solo_sip


def feed(gate, start, seconds, in_zone=True, step=0.1):
    t = start
    while t <= start + seconds + 1e-9:
        gate.update(t, 0.8, in_zone)
        t += step
    return t


def bottle_held(gate, upto=4.0):
    t = 0.0
    while t < upto:
        gate.update(t, 0.8, False)
        t += 0.1


class SipCount(unittest.TestCase):
    def test_constants(self):
        self.assertTrue(solo_sip.SIP_COUNT_ENABLED)
        self.assertEqual((solo_sip.SIP_MIN_EACH_SECONDS, solo_sip.SIPS_NEEDED, solo_sip.SIP_WINDOW_SECONDS), (0.5, 2, 30.0))

    def two_sips(self, first=(5.0, 0.7), second=(8.0, 0.7)):
        g = solo_sip.SoloSipGate()
        bottle_held(g, 4.0)
        t = 4.0
        for start, length in (first, second):
            while t < start:
                g.update(t, 0.8, False); t += 0.1
            t = feed(g, start, length)
        return g

    def test_two_separate_short_sips_qualify(self):
        g = self.two_sips()
        self.assertLess(g.sip.best, solo_sip.SIP_MIN_HOLD_SECONDS)
        self.assertTrue(g.sip_ok)
        self.assertTrue(g.satisfied)

    def test_one_short_sip_does_not(self):
        g = solo_sip.SoloSipGate()
        bottle_held(g, 4.0)
        feed(g, 5.0, 0.7)
        self.assertFalse(g.sip_ok)
        self.assertFalse(g.satisfied)

    def test_a_sip_under_half_a_second_is_not_counted(self):
        g = self.two_sips(first=(5.0, 0.3), second=(8.0, 0.3))
        self.assertFalse(g.sip_ok)

    def test_sips_closer_than_the_gap_are_one_run_not_two(self):
        g = solo_sip.SoloSipGate()
        bottle_held(g, 4.0)
        feed(g, 5.0, 0.6)
        feed(g, 5.9, 0.6)                       # 0.3 s gap: the same run, 1.5 s long, passes as a continuous hold
        self.assertTrue(g.sip_ok)
        self.assertEqual(g.sip_count(6.5), 1)

    def test_the_sips_must_fall_inside_the_window(self):
        g = solo_sip.SoloSipGate()
        bottle_held(g, 4.0)
        feed(g, 5.0, 0.7)
        feed(g, 45.0, 0.7)                      # 40 s later: the first is out of the 30 s window
        self.assertFalse(g.sip_ok)

    def test_it_latches(self):
        g = self.two_sips()
        for i in range(400):
            g.update(20.0 + i * 0.1, 0.8, False)
        self.assertTrue(g.sip_ok)

    def test_the_bottle_rule_is_unchanged(self):
        g = solo_sip.SoloSipGate()
        feed(g, 0.0, 1.0, in_zone=False)
        feed(g, 5.0, 0.7); feed(g, 8.0, 0.7)
        self.assertTrue(g.sip_ok)
        self.assertFalse(g.bottle_ok)           # best bottle run is 1 s, not 3 s
        self.assertFalse(g.satisfied)

    def test_a_small_bottle_is_never_fed_so_nothing_counts(self):
        g = solo_sip.SoloSipGate()
        self.assertEqual(g.sip_count(10.0), 0)
        self.assertFalse(g.satisfied)

    def test_the_switch_restores_the_old_rule(self):
        with mock.patch.object(solo_sip, "SIP_COUNT_ENABLED", False):
            g = self.two_sips()
            self.assertFalse(g.sip_ok)

    def test_the_testing_view_shows_the_count(self):
        g = solo_sip.SoloSipGate()
        bottle_held(g, 4.0)
        t = feed(g, 5.0, 0.7)
        rows = [r["name"] for r in solo_sip.indicators(g.summary(t))]
        self.assertEqual(rows[-1], "sips 1/2")


if __name__ == "__main__":
    unittest.main()
