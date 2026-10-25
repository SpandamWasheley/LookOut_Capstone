"""v6.2.8: far-range puff from the cigarette's own movement inside the person box (pure logic, no database)."""
import unittest
from collections import Counter
from types import SimpleNamespace

import numpy as np

from core.vision import location_momentum as lm
from core.vision import scoring

FRAME_H = 1440


def kpts(good=True):
    k = np.zeros((17, 3), dtype=float)
    if good:
        k[0] = (100, 40, 0.9)
        k[5], k[6] = (60, 80, 0.9), (140, 80, 0.9)
        k[9], k[10] = (60, 160, 0.9), (140, 160, 0.9)
    return k


def make(box_h, good_kp=False, now=0.0):
    t = SimpleNamespace(id=1, box=(1000, 500, 1000 + box_h // 3, 500 + box_h))
    t.loc = lm.TrackLoc()
    if good_kp:
        t.loc.kp, t.loc.kp_at = kpts(True), now
    cmd = SimpleNamespace(stats=Counter(), _frame_height=FRAME_H)
    return cmd, t


def cig_at(t, rel_y, conf=0.7, cls="Cigarette"):
    bh = t.box[3] - t.box[1]
    cy = t.box[1] + rel_y * bh
    cx = (t.box[0] + t.box[2]) / 2
    return (cx - 3, cy - 3, cx + 3, cy + 3, conf, cls)


def step(cmd, t, now, dets):
    t.loc.kp_at = now if t.loc.kp is not None else None
    return lm.update_puffs(cmd, t, dets, now)


class FarPuffTests(unittest.TestCase):
    def setUp(self):
        lm.reset()

    def test_far_person_cigarette_at_chest_then_head_is_a_puff(self):
        cmd, t = make(200)                                        # 200 px of 1440 = 14 % of the frame: far
        step(cmd, t, 1.0, [cig_at(t, 0.5)])                       # at the chest / hand
        n = step(cmd, t, 2.5, [cig_at(t, 0.10)])                  # raised to the head zone
        self.assertEqual(n, 1)
        self.assertEqual((t.loc.n_object_puffs, t.loc.n_pose_puffs), (1, 0))

    def test_head_then_chest_is_a_puff_too(self):
        cmd, t = make(200)
        step(cmd, t, 1.0, [cig_at(t, 0.15)])
        self.assertEqual(step(cmd, t, 3.0, [cig_at(t, 0.55)]), 1)

    def test_cigarette_only_at_the_head_the_whole_time_is_not_a_puff(self):
        cmd, t = make(200)
        for i in range(60):
            n = step(cmd, t, 1.0 + i * 0.1, [cig_at(t, 0.12)])
        self.assertEqual(n, 0)

    def test_cigarette_only_at_the_chest_the_whole_time_is_not_a_puff(self):
        cmd, t = make(200)
        for i in range(60):
            n = step(cmd, t, 1.0 + i * 0.1, [cig_at(t, 0.5)])
        self.assertEqual(n, 0)

    def test_the_two_sightings_must_be_within_4_seconds(self):
        cmd, t = make(200)
        step(cmd, t, 1.0, [cig_at(t, 0.5)])
        self.assertEqual(step(cmd, t, 5.5, [cig_at(t, 0.1)]), 0)

    def test_hand_zone_only_30_to_70_percent_counts_as_lower(self):
        cmd, t = make(200)
        step(cmd, t, 1.0, [cig_at(t, 0.85)])                      # at the legs: not the chest / hand zone
        self.assertEqual(step(cmd, t, 2.0, [cig_at(t, 0.1)]), 0)

    def test_at_most_one_puff_per_two_seconds(self):
        cmd, t = make(200)
        step(cmd, t, 1.0, [cig_at(t, 0.5)])
        step(cmd, t, 1.2, [cig_at(t, 0.1)])                       # puff 1
        step(cmd, t, 1.4, [cig_at(t, 0.5)])
        self.assertEqual(step(cmd, t, 1.6, [cig_at(t, 0.1)]), 1)  # too soon: not a second puff
        step(cmd, t, 3.0, [cig_at(t, 0.5)])
        self.assertEqual(step(cmd, t, 3.3, [cig_at(t, 0.1)]), 2)

    def test_near_person_with_good_keypoints_does_not_use_the_fallback(self):
        cmd, t = make(500, good_kp=True)                          # 500 px = 35 % of the frame, nose and wrists confident
        step(cmd, t, 1.0, [cig_at(t, 0.5)])
        self.assertEqual(step(cmd, t, 2.0, [cig_at(t, 0.1)]), 0)
        self.assertEqual(t.loc.n_object_puffs, 0)

    def test_a_near_person_whose_nose_and_wrists_are_unreadable_does_use_it(self):
        cmd, t = make(500, good_kp=False)                         # no keypoints at all
        step(cmd, t, 1.0, [cig_at(t, 0.5)])
        self.assertEqual(step(cmd, t, 2.0, [cig_at(t, 0.1)]), 1)

    def test_a_far_person_uses_it_even_when_keypoints_exist(self):
        cmd, t = make(200, good_kp=True)
        step(cmd, t, 1.0, [cig_at(t, 0.5)])
        self.assertEqual(step(cmd, t, 2.0, [cig_at(t, 0.1)]), 1)

    def test_switch_off(self):
        cmd, t = make(200)
        old = lm.FAR_PUFF_FROM_OBJECT
        lm.FAR_PUFF_FROM_OBJECT = False
        try:
            step(cmd, t, 1.0, [cig_at(t, 0.5)])
            self.assertEqual(step(cmd, t, 2.0, [cig_at(t, 0.1)]), 0)
        finally:
            lm.FAR_PUFF_FROM_OBJECT = old

    def test_the_object_puff_lifts_the_no_puff_cap_and_two_make_likely_possible(self):
        cmd, t = make(200)
        self.assertEqual(lm.puff_cap(t, 5.0), ("monitoring", lm.CAP_NO_PUFF_TEXT))
        step(cmd, t, 1.0, [cig_at(t, 0.5)])
        step(cmd, t, 2.0, [cig_at(t, 0.1)])
        self.assertEqual(lm.puff_cap(t, 5.0), ("warning", lm.CAP_FEW_PUFFS_TEXT))   # Possible allowed, Likely needs a second
        step(cmd, t, 6.0, [cig_at(t, 0.5)])
        step(cmd, t, 7.0, [cig_at(t, 0.1)])
        self.assertEqual(lm.puff_cap(t, 8.0), (None, ""))


if __name__ == "__main__":
    unittest.main()
