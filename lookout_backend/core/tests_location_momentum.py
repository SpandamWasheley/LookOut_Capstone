"""v6.2 location-aware momentum + puff gate (pure logic, no database)."""
import unittest
from types import SimpleNamespace

import numpy as np

from core.vision import location_momentum as lm
from core.vision import scoring


def kpts(lw=None, rw=None, nose=(100, 40), shoulders=((60, 80), (140, 80))):
    k = np.zeros((17, 3), dtype=float)
    k[0] = (*nose, 0.9)
    k[1], k[2], k[3], k[4] = (90, 35, .9), (110, 35, .9), (80, 40, .9), (120, 40, .9)
    k[5] = (*shoulders[0], 0.9)
    k[6] = (*shoulders[1], 0.9)
    if lw is not None:
        k[9] = (*lw, 0.9)
    if rw is not None:
        k[10] = (*rw, 0.9)
    return k


def person(kp, now, box=(40, 0, 160, 300)):
    t = SimpleNamespace(id=1, box=box, stats=None)
    t.loc = lm.TrackLoc()
    t.loc.kp, t.loc.kp_at = kp, now
    return t


def cig(cx, cy, conf=0.8, cls="Cigarette", half=6):
    return (cx - half, cy - half, cx + half, cy + half, conf, cls)


class LocationGateTests(unittest.TestCase):
    def setUp(self):
        lm.reset()
        self.cmd = SimpleNamespace(stats=__import__("collections").Counter())

    def test_hit_at_wrist_is_accepted_and_off_anchor_is_not(self):
        t = person(kpts(lw=(60, 160)), 1.0)
        out, why = lm.evaluate_track(self.cmd, t, [cig(60, 160)], 1.0)
        self.assertAlmostEqual(out["cigarette"], 0.8 * min(1, 0.8 / 0.6))
        lm.reset()
        t = person(kpts(lw=(60, 160)), 1.0)
        out, why = lm.evaluate_track(self.cmd, t, [cig(100, 220)], 1.0)    # chest / legs
        self.assertEqual((out["cigarette"], why), (0.0, "off-anchor"))

    def test_missing_keypoints_add_nothing(self):
        t = person(None, 1.0)
        out, why = lm.evaluate_track(self.cmd, t, [cig(60, 160)], 1.0)
        self.assertEqual((out["cigarette"], why), (0.0, "no-kp"))

    def test_confidence_weighting(self):
        t = person(kpts(lw=(60, 160)), 1.0)
        out, _ = lm.evaluate_track(self.cmd, t, [cig(60, 160, conf=0.3)], 1.0)
        self.assertAlmostEqual(out["cigarette"], 0.3 * 0.5)

    def test_jump_rejected_but_wrist_to_mouth_is_not(self):
        t = person(kpts(lw=(60, 160), rw=(150, 60)), 1.0)
        lm.evaluate_track(self.cmd, t, [cig(60, 160)], 1.0)               # at the left wrist
        t.loc.kp_at = 1.1
        out, why = lm.evaluate_track(self.cmd, t, [cig(150, 60)], 1.1)    # right wrist, far away: a jump
        self.assertEqual((out["cigarette"], why), (0.0, "jump"))
        lm.reset()
        t = person(kpts(lw=(60, 160), nose=(100, 40)), 1.0)
        mouth = (100, 40 + 0.29 * 20)
        lm.evaluate_track(self.cmd, t, [cig(60, 160)], 1.0)
        t.loc.kp_at = 1.1
        t.loc.kp = kpts(lw=(100, 55))                                     # wrist now at the mouth
        out, why = lm.evaluate_track(self.cmd, t, [cig(100, 55)], 1.1)    # wrist -> mouth move
        self.assertGreater(out["cigarette"], 0.0, why)

    def test_class_flip(self):
        t = person(kpts(lw=(60, 160)), 1.0)
        lm.note_detections(1.0, [cig(60, 160, cls="Bottle")])
        out, why = lm.evaluate_track(self.cmd, t, [cig(60, 160)], 1.0)
        self.assertEqual((out["cigarette"], why), (0.0, "flip"))

    def test_knife_is_untouched(self):
        t = person(None, 1.0)
        out, why = lm.evaluate_track(self.cmd, t, [cig(60, 160, cls="knife")], 1.0)
        self.assertEqual(out, {})


class PuffTests(unittest.TestCase):
    def setUp(self):
        lm.reset()
        self.cmd = SimpleNamespace(stats=__import__("collections").Counter())

    def frame(self, t, now, wrist, dets):
        t.loc.kp, t.loc.kp_at = kpts(lw=wrist), now
        return lm.update_puffs(self.cmd, t, dets, now)

    def test_cigarette_at_wrist_then_wrist_to_mouth_is_a_puff(self):
        t = person(None, 0.0)
        self.assertEqual(self.frame(t, 1.0, (60, 160), [cig(60, 160)]), 0)
        self.assertEqual(self.frame(t, 1.5, (100, 50), []), 1)            # wrist reached the mouth
        self.assertEqual(self.frame(t, 2.0, (100, 50), []), 1)            # holding it there is not another puff

    def test_cigarette_held_still_at_mouth_is_not_a_puff(self):
        t = person(None, 0.0)
        for i in range(10):
            n = self.frame(t, 1.0 + i * 0.1, (100, 50), [cig(100, 50)])
        self.assertEqual(n, 0)

    def test_arming_expires(self):
        t = person(None, 0.0)
        self.frame(t, 1.0, (60, 160), [cig(60, 160)])
        self.assertEqual(self.frame(t, 6.0, (100, 50), []), 0)

    def test_cap_rules(self):
        t = person(None, 0.0)
        self.assertEqual(lm.puff_cap(t, 20.0), ("monitoring", lm.CAP_NO_PUFF_TEXT))
        t.loc.puffs.extend([15.0])
        self.assertEqual(lm.puff_cap(t, 20.0), ("warning", lm.CAP_FEW_PUFFS_TEXT))
        t.loc.puffs.extend([18.0])
        self.assertEqual(lm.puff_cap(t, 20.0), (None, ""))


class ScoreCapTests(unittest.TestCase):
    def test_cap_lowers_status_not_score(self):
        cues = {"cigarette", "near_mouth", "gesture"}                      # 0.75 = Likely
        free = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, cues, object_on=True)
        capped = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, cues, object_on=True,
                               cap=scoring.MONITORING, cap_reason="capped: no puff")
        self.assertEqual(free.level, scoring.VIOLATION)
        self.assertEqual((capped.level, capped.capped), (scoring.MONITORING, "capped: no puff"))
        self.assertEqual(capped.score, free.score)
        self.assertEqual(capped.as_dict()["checklist"]["capped"], "capped: no puff")
        self.assertEqual(capped.as_dict()["spec"], "v6.2")

    def test_puff_only_path_is_not_capped(self):
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, {"gesture", "puffs", "puff_pattern"},
                          object_on=False, cap=scoring.MONITORING, cap_reason="x")
        self.assertTrue(s.puff_only)
        self.assertEqual(s.capped, "")
        self.assertEqual(s.level, scoring.WARNING)


class ClusterTests(unittest.TestCase):
    def setUp(self):
        lm.reset()
        self.cmd = SimpleNamespace(stats=__import__("collections").Counter(), _gate_frame=None)

    def cluster(self):
        return SimpleNamespace(bbox=(0, 0, 400, 300), id=1)

    def test_tabletop_bottle_needs_to_be_stationary_in_the_group_area(self):
        c = self.cluster()
        t = person(None, 0.0, box=(0, 0, 100, 300))
        b = cig(200, 150, cls="Bottle")
        conf, why = lm.cluster_confidence(self.cmd, c, [], [b], 1.0)       # first sighting: not yet stationary
        self.assertEqual((conf, why), (0.0, "off-anchor"))
        conf, why = lm.cluster_confidence(self.cmd, c, [], [b], 1.1)       # same place a moment later
        self.assertGreater(conf, 0.0)
        far = cig(900, 150, cls="Bottle")                                  # outside the expanded area
        lm.cluster_confidence(self.cmd, c, [], [far], 1.2)
        conf, why = lm.cluster_confidence(self.cmd, c, [], [far], 1.3)
        self.assertEqual(conf, 0.0)

    def test_bottle_in_a_members_hand_is_accepted(self):
        c = self.cluster()
        t = person(kpts(lw=(60, 160)), 1.0, box=(40, 0, 160, 300))
        conf, _ = lm.cluster_confidence(self.cmd, c, [(t, cig(60, 160, cls="Bottle"))], [], 1.0)
        self.assertGreater(conf, 0.0)


if __name__ == "__main__":
    unittest.main()
