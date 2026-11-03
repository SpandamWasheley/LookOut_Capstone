"""solo_sip.GateHandover: a re-issued track ID keeps the gate its old ID built, and only then."""
import unittest
from unittest import mock

from core.vision import solo_sip

BOX = (100, 100, 160, 300)


def gate_with_progress():
    g = solo_sip.SoloSipGate()
    for i in range(41):                                   # 4 s of a confident Bottle in the mouth zone
        g.update(i * 0.1, 0.8, True)
    return g


class Handover(unittest.TestCase):
    def setUp(self):
        self.h = solo_sip.GateHandover()
        self.gate = gate_with_progress()
        self.h.note(1, BOX, 4.0, self.gate)

    def test_the_constants(self):
        self.assertTrue(solo_sip.SOLO_GATE_HANDOVER)
        self.assertEqual((solo_sip.HANDOVER_MAX_GAP_S, solo_sip.HANDOVER_MIN_GAP_S), (4.0, 0.25))

    def test_a_new_id_at_the_same_spot_adopts_the_gate(self):
        got = self.h.adopt(2, (104, 102, 164, 302), 4.6)
        self.assertIs(got, self.gate)
        self.assertTrue(got.satisfied)

    def test_each_old_gate_is_adopted_once(self):
        self.assertIs(self.h.adopt(2, BOX, 4.6), self.gate)
        self.assertIsNone(self.h.adopt(3, BOX, 4.7))

    def test_stable_ids_never_adopt(self):
        self.assertIsNone(self.h.adopt(1, BOX, 4.6))              # same id
        self.h.note(1, BOX, 4.6, self.gate)
        self.assertIsNone(self.h.adopt(2, BOX, 4.7))              # old one was just seen: still alive

    def test_a_live_track_is_not_robbed(self):
        self.assertIsNone(self.h.adopt(2, BOX, 4.1))              # old seen 0.1 s ago < 0.25 s

    def test_a_gap_up_to_4_seconds_is_fine(self):
        self.assertIs(self.h.adopt(2, BOX, 7.5), self.gate)       # 3.5 s

    def test_too_long_a_gap(self):
        self.assertIsNone(self.h.adopt(2, BOX, 8.5))              # 4.5 s > 4.0 s

    def test_a_different_place(self):
        self.assertIsNone(self.h.adopt(2, (400, 100, 460, 300), 4.6))

    def test_the_new_centre_must_be_inside_the_old_box(self):
        self.assertIsNone(self.h.adopt(2, (140, 100, 200, 300), 4.6))        # centre x 170 > 160: outside
        self.assertIs(self.h.adopt(3, (120, 120, 180, 320), 4.6), self.gate)  # centre (150, 220): inside

    def test_the_switch_turns_it_off(self):
        with mock.patch.object(solo_sip, "SOLO_GATE_HANDOVER", False):
            self.assertIsNone(self.h.adopt(2, BOX, 4.6))

    def test_split_time_adds_up_across_ids(self):
        # 0.7 s of sip on the old ID + 0.6 s on the new one is one 1.3 s hold, which clears the 1 s sip rule.
        old = solo_sip.SoloSipGate()
        for i in range(4):
            old.update(i * 0.1, 0.8, True)                         # 0.3 s of sip on the old ID
        h = solo_sip.GateHandover()
        h.note(1, BOX, 0.3, old)
        new = h.adopt(2, BOX, 0.6)
        self.assertIs(new, old)
        for i in range(8):
            new.update(0.6 + i * 0.1, 0.8, True)
        self.assertGreaterEqual(new.sip.best, solo_sip.SIP_MIN_HOLD_SECONDS)


if __name__ == "__main__":
    unittest.main()
