"""v6.2.4: holdup fake-person filter (shadows) and the unprimed holdup AI prompt / people_count rules."""
import unittest
from unittest import mock

import numpy as np

from core.tests_layer_e import Scene, pbox
from core.vision import ai_checker, ai_status
from core.vision import location_momentum as lm
from core.vision import scoring


def skeleton(n_good=15, face=True):
    k = np.zeros((17, 3), dtype=float)
    for i in range(n_good):
        k[i] = (100 + i, 100 + i, 0.9)
    if not face:
        k[0:3, 2] = 0.1
    return k


class Tr:
    def __init__(self, tid, box):
        self.id, self.box = tid, box


class FakePersonFilterTests(unittest.TestCase):
    def setUp(self):
        lm.reset()

    def test_skeleton_needs_6_keypoints_and_a_face_keypoint(self):
        self.assertTrue(lm.skeleton_ok(skeleton(15)))
        self.assertFalse(lm.skeleton_ok(skeleton(5, face=True)))          # too few keypoints
        self.assertFalse(lm.skeleton_ok(skeleton(15, face=False)))        # body only, no nose / eye (a shadow's blob)
        k = skeleton(15, face=False)
        k[1] = (50, 50, 0.31)                                              # one eye is enough
        self.assertTrue(lm.skeleton_ok(k))

    def test_a_person_with_a_skeleton_is_real_and_a_shadow_is_not(self):
        man, shade = Tr(1, (2038, 597, 2158, 868)), Tr(7, (1986, 691, 2064, 842))
        for i in range(10):
            lm.update_person_evidence([man, shade], 1.0 + i * 0.1, poses=[(man.box, skeleton())])
        self.assertTrue(lm.is_real_person(man, 2.0))
        self.assertFalse(lm.is_real_person(shade, 2.0))

    def test_one_stray_pose_match_does_not_make_a_shadow_real(self):
        shade = Tr(7, (1986, 691, 2064, 842))
        for i in range(10):
            poses = [(shade.box, skeleton())] if i == 4 else []
            lm.update_person_evidence([shade], 1.0 + i * 0.1, poses=poses)
        self.assertFalse(lm.is_real_person(shade, 2.0))

    def test_no_pose_information_fails_open(self):
        t = Tr(3, (0, 0, 60, 100))
        self.assertTrue(lm.is_real_person(t, 5.0))                         # never watched
        self.assertFalse(lm.update_person_evidence([t], 5.0, poses=None) and False)
        with mock.patch.object(lm, "_frame_poses", return_value=None):
            self.assertFalse(lm.update_person_evidence([t], 5.0))
        self.assertTrue(lm.is_real_person(t, 5.0))

    def test_the_skeleton_must_be_recent(self):
        t = Tr(3, (0, 0, 60, 100))
        for i in range(5):
            lm.update_person_evidence([t], 1.0 + i * 0.1, poses=[(t.box, skeleton())])
        self.assertTrue(lm.is_real_person(t, 1.5))
        self.assertFalse(lm.is_real_person(t, 9.0))                        # the evidence is older than the window

    def test_a_pair_with_a_shadow_never_forms_a_frozen_pair(self):
        man_box, shade_box = pbox(600, 300), pbox(685, 300)
        both = [(man_box, skeleton()), (shade_box, skeleton())]
        only_man = [(man_box, skeleton())]
        with mock.patch.object(lm, "_frame_poses", return_value=only_man):
            s = Scene()
            s.hold(6.0, persons=[man_box, shade_box])
            self.assertFalse(any(st.freeze_ok for st in s.engine.pairs.values()))
        with mock.patch.object(lm, "_frame_poses", return_value=both):
            s = Scene()
            s.hold(6.0, persons=[man_box, shade_box])
            self.assertTrue(any(st.freeze_ok for st in s.engine.pairs.values()))

    def test_switch_off_restores_the_old_pairing(self):
        man_box, shade_box = pbox(600, 300), pbox(685, 300)
        with mock.patch.object(lm, "_frame_poses", return_value=[(man_box, skeleton())]), \
                mock.patch.object(lm, "LOCATION_AWARE_MOMENTUM", False):
            s = Scene()
            s.hold(6.0, persons=[man_box, shade_box])
            self.assertTrue(any(st.freeze_ok for st in s.engine.pairs.values()))


class ShadowPairTests(unittest.TestCase):
    def frame(self, bright_left, bright_right):
        f = np.zeros((400, 400, 3), dtype=np.uint8)
        f[:, :200] = bright_left
        f[:, 200:] = bright_right
        return f

    def test_person_and_own_shadow(self):
        a, b = (100, 100, 190, 300), (190, 120, 280, 298)                  # feet level, touching sideways
        frame = self.frame(190, 80)                                        # right box is about 42 % as bright
        self.assertTrue(lm.is_shadow_pair(a, b, frame=frame))
        frame = self.frame(190, 150)                                       # 79 %: a second person in the dark, not a shadow
        self.assertFalse(lm.is_shadow_pair(a, b, frame=frame))

    def test_feet_apart_or_boxes_apart_is_not_a_shadow_pair(self):
        a = (100, 100, 190, 300)
        frame = self.frame(190, 60)
        self.assertFalse(lm.is_shadow_pair(a, (190, 100, 280, 200), frame=frame))    # bottoms 100 px apart
        self.assertFalse(lm.is_shadow_pair(a, (230, 120, 330, 300), frame=frame))    # not touching sideways

    def test_no_frame_means_no_decision(self):
        self.assertFalse(lm.is_shadow_pair((100, 100, 190, 300), (190, 120, 280, 298), frame=None))


REPLY = {"observations": "x", "people_count": 2, "object_pointed_at_a_person": True, "victim_response_visible": False,
         "holdup_likelihood": "likely", "scene_type": "confrontation", "confidence": "high"}


class HoldupQwenTests(unittest.TestCase):
    def test_holdup_prompt_has_no_detection_hints_but_smoking_and_drinking_do(self):
        note = "knife detected; two people standing still close together"
        hold = ai_checker.build_messages("holdup", note, [])[0]["content"]
        self.assertNotIn("System detections", hold)
        self.assertNotIn("two people standing still", hold)
        self.assertIn("people_count", hold)
        for kind in ("smoking", "drinking"):
            text = ai_checker.build_messages(kind, "bottle detected", [])[0]["content"]
            self.assertIn("System detections (from the object and pose models): bottle detected", text)
            self.assertNotIn("people_count", text)

    def test_two_people_confrontation_high_confidence_may_suggest_an_upgrade(self):
        sug = ai_status.suggest_status("warning", "holdup", REPLY)
        self.assertEqual((sug["suggested"], sug["direction"]), ("Likely", "up"))

    def test_fewer_than_two_people_is_ordinary_and_never_upgrades(self):
        one = dict(REPLY, people_count=1, object_pointed_at_a_person=False)
        self.assertEqual(ai_status.ai_badge("holdup", one)["code"], "ordinary")
        sug = ai_status.suggest_status("warning", "holdup", one)
        self.assertEqual((sug["suggested"], sug["changed"]), ("Possible", False))

    def test_one_person_but_something_pointed_at_a_person_is_contradictory(self):
        bad = dict(REPLY, people_count=1, object_pointed_at_a_person=True)
        self.assertEqual(ai_status.ai_badge("holdup", bad)["code"], "unclear")
        self.assertFalse(ai_status.suggest_status("warning", "holdup", bad)["changed"])

    def test_the_upgrade_needs_confrontation_and_high_confidence_too(self):
        for change in ({"scene_type": "unclear"}, {"confidence": "medium"}, {"holdup_likelihood": "possible"}):
            self.assertFalse(ai_status.suggest_status("warning", "holdup", dict(REPLY, **change))["changed"], change)

    def test_a_reply_without_people_count_stays_valid_but_cannot_upgrade(self):
        legacy = {k: v for k, v in REPLY.items() if k != "people_count"}
        self.assertIsNotNone(ai_status.validate_reply("holdup", legacy))
        self.assertFalse(ai_status.suggest_status("warning", "holdup", legacy)["changed"])

    def test_people_count_must_be_a_whole_number(self):
        for bad in ("two", 2.5, -1, True):
            self.assertIsNone(ai_status.validate_reply("holdup", dict(REPLY, people_count=bad)), bad)

    def test_people_count_is_served_to_the_ai_details_panel(self):
        ctx = ai_status.ai_context("holdup", {"state": "done", "reply": REPLY, "model": "m"}, "warning")
        self.assertEqual(ctx["people_count"], 2)
        self.assertIn({"field": "people_count", "label": "Distinct real people seen", "value": 2}, ctx["checklist"])

    def test_spec_version(self):
        self.assertEqual(scoring.SCORING_SPEC_VERSION, "v6.2.4")


if __name__ == "__main__":
    unittest.main()
