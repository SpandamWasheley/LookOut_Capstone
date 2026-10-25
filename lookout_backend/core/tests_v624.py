"""v6.2.4: holdup fake-person filter (shadows) and the unprimed holdup AI prompt / people_count rules."""
import unittest
from unittest import mock

import numpy as np

from core.tests_layer_e import Scene, pbox
from core.vision import ai_checker, ai_status
from core.vision import location_momentum as lm
from core.vision import scoring


def dark_shadow_frame():
    f = np.zeros((720, 1280, 3), dtype=np.uint8)
    f[250:350, 570:630] = 200                                              # the man
    f[250:350, 630:680] = 60                                               # his shadow on the shutter
    return f


class ShadowEngineTests(unittest.TestCase):
    """The engine ignores a person + own shadow, and (v6.2.5) no longer needs skeletons."""

    def tearDown(self):
        lm.CURRENT_FRAME["frame"] = None

    def test_a_person_and_own_shadow_never_form_a_pair(self):
        lm.CURRENT_FRAME["frame"] = dark_shadow_frame()
        s = Scene()
        s.hold(6.0, persons=[pbox(600, 300), pbox(650, 300)])
        self.assertEqual(s.engine.pairs, {})

    def test_two_real_people_standing_close_do_form_a_pair_without_any_pose_data(self):
        lm.CURRENT_FRAME["frame"] = np.full((720, 1280, 3), 150, dtype=np.uint8)   # equally lit
        s = Scene()
        s.hold(6.0, persons=[pbox(600, 300), pbox(650, 300)])
        self.assertTrue(any(st.freeze_ok for st in s.engine.pairs.values()))

    def test_switch_off_restores_the_old_pairing(self):
        lm.CURRENT_FRAME["frame"] = dark_shadow_frame()
        with mock.patch.object(lm, "FAKE_PERSON_FILTER", False):
            s = Scene()
            s.hold(6.0, persons=[pbox(600, 300), pbox(650, 300)])
            self.assertTrue(s.engine.pairs)


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

    def test_a_much_shorter_box_on_the_same_foot_line_is_a_shadow_even_if_not_dark(self):
        man, shade = (100, 100, 190, 400), (190, 215, 270, 395)            # 300 vs 180 px tall: ratio 0.6
        self.assertTrue(lm.is_shadow_pair(man, shade, frame=self.frame(150, 140)))
        self.assertFalse(lm.is_shadow_pair(man, (190, 160, 270, 395), frame=self.frame(150, 140)))   # 235 px: ratio 0.78, a real person
        with mock.patch.object(lm, "SHADOW_HEIGHT_RATIO", 0):
            self.assertFalse(lm.is_shadow_pair(man, shade, frame=self.frame(150, 140)))

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
        self.assertEqual(scoring.SCORING_SPEC_VERSION, "v6.2.8")


if __name__ == "__main__":
    unittest.main()
