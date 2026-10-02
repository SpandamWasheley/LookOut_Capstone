"""Tests for the v3 AI context checker (Qwen3-VL 4B, local through Ollama).

Source of truth: LookOut_Indicators_Scoring_v3, 29 September 2026.

Four things are worth testing here; the rest is the model's problem.

1. THE QUESTION SET IS FIXED. v3 §9 names every field. Pinning them looks
   pedantic until someone "improves" a prompt and the weight beside it quietly
   starts measuring something else.

2. FAIL-OPEN, ALWAYS. A stopped Ollama server, a model that was never pulled, a
   timeout, a malformed reply -- every one must yield "no answer, publish on the
   system indicators alone". A checker outage silently disabling a barangay's
   alerts would be far worse than any false positive it removes.

3. THE CHECKER CANNOT ALERT ALONE. Capped at 40/35/45, scaled by its own
   confidence, and low confidence earns nothing.

4. IT CAN CUT A SCORE. The scene reading is the only thing standing between a
   fish vendor and a holdup alert, so the 0.25 multiplier and its floor matter
   as much as any additive weight.

No network is touched: the Ollama HTTP call is faked, so what is verified is OUR
handling of its answers and failures, not that Qwen3-VL answers well.
"""

import json
import unittest
from unittest import mock

import numpy as np

from core.vision import scoring, theft, vlm


def _frame(w=200, h=300):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _verifier(reply, model="qwen3-vl:4b"):
    """An OllamaVerifier whose HTTP calls are faked.

    `reply` is the JSON text the model "returns", or an exception to raise.
    """
    v = vlm.OllamaVerifier(model=model)
    v._ensure_client = lambda: True
    calls = []

    def fake_post(path, payload):
        calls.append((path, payload))
        if isinstance(reply, Exception):
            raise reply
        return {"message": {"content": reply}}

    v._post = fake_post
    v.calls = calls
    return v


# --- the question set (v3 §9) -----------------------------------------------

class QuestionSetTests(unittest.TestCase):
    """Every field v3 names, and nothing else."""

    FIELDS = {
        "drinking": {"group_appears_to_be_drinking_together",
                     "table_chairs_or_seating_visible",
                     "drinking_items_visible",
                     "scene_type"},
        "smoking": {"smoking_item_visible", "hand_to_mouth_activity"},
        "holdup": {"object_pointed_at_a_person", "victim_response_visible",
                   "appears_to_be_a_holdup", "scene_type"},
    }

    def test_each_spec_asks_exactly_its_fields(self):
        for kind, want in self.FIELDS.items():
            self.assertEqual({c.name for c in vlm.SPECS[kind].cues}, want, kind)

    def test_the_removed_questions_stay_removed(self):
        """v3 §9 dropped six fields that repeated YOLO or were too small to see.

        The reasoning is worth keeping visible: in local testing the 4B model
        called a bottle held to the mouth a cigarette, then a bottle when the
        same image was asked about differently. YOLO answers what is there; the
        checker answers what is happening.
        """
        asked = set()
        for kind in self.FIELDS:
            asked |= {c.name for c in vlm.SPECS[kind].cues}
        for gone in ("beverage_container_visible", "drinking_glass_or_cup_visible",
                     "food_or_snacks_visible", "smoking_item_at_hand_or_lips",
                     "hand_raised_to_mouth", "person_appears_to_be_smoking",
                     "knife_or_sharp_object_visible"):
            self.assertNotIn(gone, asked, gone)

    def test_smoking_has_no_separate_verdict_question(self):
        """v3 folded "is he smoking" into hand_to_mouth_activity.

        That question has to be asked anyway to tell smoking from drinking,
        eating and a phone call, so a separate yes/no would be asking the same
        thing twice.
        """
        spec = vlm.SPECS["smoking"]
        self.assertEqual(spec.verdict_cue, "hand_to_mouth_activity")
        self.assertEqual(spec.verdict_values, ("smoking",))

    def test_the_choice_fields_and_their_options(self):
        def enum(kind, name):
            return next(c for c in vlm.SPECS[kind].cues if c.name == name)

        self.assertEqual(set(enum("drinking", "scene_type").values),
                         {"drinking_session", "other_activity", "unclear"})
        self.assertEqual(set(enum("smoking", "hand_to_mouth_activity").values),
                         {"smoking", "drinking", "eating", "phone", "unclear"})
        self.assertEqual(set(enum("holdup", "scene_type").values),
                         {"confrontation", "other_activity", "unclear"})

    def test_ordinary_activity_cuts_to_a_quarter(self):
        for kind, field, ordinary in (("drinking", "scene_type", "other_activity"),
                                      ("smoking", "hand_to_mouth_activity", "eating"),
                                      ("holdup", "scene_type", "other_activity")):
            cue = next(c for c in vlm.SPECS[kind].cues if c.name == field)
            self.assertEqual(cue.multiplier_for(ordinary),
                             scoring.SCENE_MULTIPLIER, kind)

    def test_unclear_never_cuts(self):
        """"I cannot tell" is not "this is harmless".

        CCTV crops are poor and the model hedges often. If hedging cut the
        score, real violations would be lost every time it was unsure.
        """
        for kind, field in (("drinking", "scene_type"),
                            ("smoking", "hand_to_mouth_activity"),
                            ("holdup", "scene_type")):
            cue = next(c for c in vlm.SPECS[kind].cues if c.name == field)
            self.assertEqual(cue.multiplier_for("unclear"), 1.0, kind)

    def test_the_prompt_carries_every_field_and_its_definition(self):
        for kind, want in self.FIELDS.items():
            prompt = vlm._build_prompt(vlm.SPECS[kind])
            for field in want:
                self.assertIn(field, prompt, f"{kind}/{field}")

    def test_the_shared_instructions_match_v3(self):
        for phrase in ("barangay street camera", "The first image is the full scene",
                       "Do not guess", "Reply only with the JSON"):
            self.assertIn(phrase, vlm.SYSTEM_PROMPT, phrase)

    def test_every_field_is_priced_or_deliberately_not(self):
        for key in vlm.WEIGHT_KEYS.values():
            name = "vlm_" + key
            self.assertTrue(name in scoring.DRINKING_WEIGHTS
                            or name in scoring.SMOKING_WEIGHTS, name)
        self.assertEqual(set(scoring.HOLDUP_CUE_CODES),
                         {"object_pointed_at_a_person", "victim_response_visible"})

    def test_the_v3_totals(self):
        def tot(w, is_vlm):
            return sum(v for k, v in w.items() if k.startswith("vlm_") == is_vlm)

        self.assertAlmostEqual(tot(scoring.DRINKING_WEIGHTS, False), 0.85)
        self.assertAlmostEqual(tot(scoring.DRINKING_WEIGHTS, True), 0.40)
        self.assertAlmostEqual(tot(scoring.SMOKING_WEIGHTS, False), 0.95)
        self.assertAlmostEqual(tot(scoring.SMOKING_WEIGHTS, True), 0.35)
        self.assertAlmostEqual(sum(scoring.HOLDUP_VLM_WEIGHTS.values()), 0.45)
        self.assertEqual(scoring.VLM_CAP,
                         {"drinking": 0.40, "smoking": 0.35, "holdup": 0.45})


# --- parsing ----------------------------------------------------------------

class ParseTests(unittest.TestCase):

    def _parse(self, kind, reply):
        return vlm._parse_reply(json.dumps(reply), vlm.SPECS[kind],
                                "qwen3-vl:4b", 0.5)

    def test_a_good_drinking_reply(self):
        v = self._parse("drinking", {
            "group_appears_to_be_drinking_together": True,
            "table_chairs_or_seating_visible": True,
            "drinking_items_visible": True,
            "scene_type": "drinking_session",
            "confidence": "high", "reason": "four men seated around a table"})
        self.assertTrue(v.ok)
        self.assertEqual(v.verdict, vlm.YES)
        self.assertEqual(v.confidence, 1.0)
        self.assertEqual(v.fired_cues(),
                         {"vlm_verdict", "vlm_seating", "vlm_drinking_items"})
        self.assertEqual(v.enum_multipliers(), {})

    def test_the_smoking_verdict_comes_from_the_activity_choice(self):
        smoking = self._parse("smoking", {
            "smoking_item_visible": True, "hand_to_mouth_activity": "smoking",
            "confidence": "high", "reason": "r"})
        self.assertEqual(smoking.verdict, vlm.YES)
        self.assertIn("vlm_verdict", smoking.fired_cues())

        eating = self._parse("smoking", {
            "smoking_item_visible": False, "hand_to_mouth_activity": "eating",
            "confidence": "high", "reason": "r"})
        self.assertEqual(eating.verdict, vlm.NO)
        self.assertEqual(eating.enum_multipliers(),
                         {"hand_to_mouth_activity": scoring.SCENE_MULTIPLIER})

    def test_low_confidence_forces_a_scene_reading_to_unclear(self):
        """v3 §8, and the most consequential rule in the whole checker.

        De-escalating by 75% is the single strongest thing the model can say.
        A model that is mostly guessing must not be able to say it.
        """
        v = self._parse("holdup", {
            "object_pointed_at_a_person": True, "victim_response_visible": True,
            "appears_to_be_a_holdup": True, "scene_type": "other_activity",
            "confidence": "low", "reason": "r"})
        self.assertTrue(v.ok)
        self.assertEqual(v.cues["scene_type"], "unclear")
        self.assertEqual(v.enum_multipliers(), {})

    def test_medium_confidence_halves_the_points(self):
        v = self._parse("drinking", {
            "group_appears_to_be_drinking_together": True,
            "table_chairs_or_seating_visible": False,
            "drinking_items_visible": False,
            "scene_type": "unclear", "confidence": "medium", "reason": "r"})
        self.assertEqual(v.confidence, 0.5)
        s = scoring.Score("drinking", scoring.DRINKING_WEIGHTS,
                          {"bottle"} | v.fired_cues(), vlm_confidence=v.confidence)
        self.assertAlmostEqual(s.vlm_score, s.vlm_raw * 0.5, places=6)

    def test_an_unknown_choice_falls_back_to_the_default(self):
        v = self._parse("holdup", {"scene_type": "who knows",
                                   "confidence": "high", "reason": "r"})
        self.assertEqual(v.cues["scene_type"], "unclear")

    def test_missing_fields_default_to_false(self):
        v = self._parse("drinking", {"confidence": "high", "reason": "r"})
        self.assertTrue(v.ok)
        self.assertFalse(v.cues["drinking_items_visible"])
        self.assertEqual(v.verdict, vlm.NO)

    def test_a_numeric_confidence_still_works(self):
        """A provider that ignores the schema must not break scoring."""
        for raw, want in ((0.95, 1.0), (0.5, 0.5), (0.05, 0.0)):
            v = self._parse("drinking", {"confidence": raw, "reason": "r"})
            self.assertEqual(scoring.confidence_scale(v.confidence), want, raw)

    def test_unparseable_and_wrong_shape(self):
        self.assertIn("unparseable",
                      vlm._parse_reply("not json", vlm.DRINKING_SPEC, "m", 0).error)
        self.assertIn("shape",
                      vlm._parse_reply("[1,2]", vlm.DRINKING_SPEC, "m", 0).error)


# --- fail-open --------------------------------------------------------------

class FailOpenTests(unittest.TestCase):
    """Every path must end in "no answer, publish on geometry alone"."""

    def test_ollama_not_running(self):
        v = vlm.build_verifier(enabled=True, provider="ollama",
                               endpoint="http://127.0.0.1:1")
        self.assertIsInstance(v, vlm.DisabledVerifier)
        self.assertIn("ollama serve", v.reason)

    def test_model_not_pulled(self):
        verifier = vlm.OllamaVerifier(model="qwen3-vl:4b")
        with mock.patch("urllib.request.urlopen") as fake:
            fake.return_value.__enter__.return_value.read.return_value = json.dumps(
                {"models": [{"name": "llama3:8b"}]}).encode()
            with self.assertRaises(RuntimeError) as caught:
                verifier._ensure_client()
        self.assertIn("ollama pull", str(caught.exception))

    def test_switched_off(self):
        v = vlm.build_verifier(enabled=False)
        self.assertIsInstance(v, vlm.DisabledVerifier)
        self.assertIn("off", v.reason.lower())

    def test_unknown_provider(self):
        v = vlm.build_verifier(enabled=True, provider="gpt-9")
        self.assertIsInstance(v, vlm.DisabledVerifier)
        self.assertIn("provider", v.reason.lower())

    def test_a_dropped_connection(self):
        v = _verifier(OSError("connection reset")).verify(
            [vlm.encode_crop(_frame())], vlm.SMOKING_SPEC)
        self.assertFalse(v.ok)
        self.assertIn("connection reset", v.error)

    def test_an_empty_reply(self):
        verifier = _verifier("")
        v = verifier.verify([vlm.encode_crop(_frame())], vlm.SMOKING_SPEC)
        self.assertFalse(v.ok)
        self.assertIn("empty", v.error.lower())

    def test_a_failed_call_is_never_evidence(self):
        v = vlm.unavailable("timeout")
        self.assertFalse(v.ok)
        self.assertFalse(v.confirms)
        self.assertEqual(v.fired_cues(), set())
        self.assertEqual(v.enum_multipliers(), {})

    def test_an_outage_cannot_look_like_a_clearance(self):
        """The failure that matters most.

        A dead checker must add nothing -- never de-escalate. If an outage
        could cut scores, losing Ollama would quietly disarm the system.
        """
        v = vlm.unavailable("Ollama is not reachable")
        self.assertEqual(v.enum_multipliers(), {})
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS,
                          {"cigarette", "near_mouth", "gesture", "puffs"})
        self.assertTrue(s.alerting, s.summary())

    def test_the_disabled_verifier_costs_nothing(self):
        v = vlm.DisabledVerifier("off").verify("x", vlm.SMOKING_SPEC)
        self.assertFalse(v.ok)
        self.assertEqual(v.error, "disabled")


# --- what gets sent ---------------------------------------------------------

class RequestTests(unittest.TestCase):

    def test_json_mode_and_zero_temperature(self):
        """Why there is no parse-and-retry loop anywhere in this module."""
        v = _verifier(json.dumps({"confidence": "high", "reason": "r"}))
        v.verify([vlm.encode_crop(_frame())], vlm.DRINKING_SPEC)
        _, payload = v.calls[0]
        self.assertEqual(payload["format"], "json")
        self.assertEqual(payload["options"]["temperature"], 0.0)
        self.assertFalse(payload["stream"])

    def test_the_full_scene_is_sent_before_the_crops(self):
        """v3 §8. The scene questions -- is this a store? is there seating? --
        are answerable only from context a crop has thrown away."""
        v = _verifier(json.dumps({"confidence": "high", "reason": "r"}))
        buf = vlm.FrameBuffer()
        for i in range(3):
            buf.add(_frame(640, 480), now=i * 1.5)
        vlm.verify_frame(v, _frame(640, 480), "drinking", box=(10, 10, 80, 200),
                         frames=buf.recent())
        images = v.calls[0][1]["messages"][1]["images"]
        self.assertGreater(len(images), vlm.FRAME_COUNT,
                           "the full scene frame was not added")

    def test_faces_are_not_blurred_for_a_local_model(self):
        """Spec v6 §8: frames sent to a local model never leave the device, and
        blurring would hide the mouth the hand_to_mouth_activity question needs."""
        self.assertFalse(vlm.BLUR_FACES)
        self.assertFalse(vlm.blur_required("ollama"))
        self.assertFalse(vlm.blur_required(vlm.OllamaVerifier()))
        self.assertFalse(vlm.blur_required(vlm.DisabledVerifier()))   # sends nothing

    def test_faces_are_blurred_for_a_cloud_provider(self):
        """Spec v6 §8: cloud providers get blurred frames; unknown names are
        treated as cloud (assume it leaves the device)."""
        self.assertTrue(vlm.blur_required("gemini"))
        self.assertTrue(vlm.blur_required("some-new-provider"))

    def test_blur_faces_actually_blurs_and_does_not_raise(self):
        """BLUR_KERNEL was once undefined, so the blur silently never ran."""
        import numpy as np

        class _OneFace:
            def detectMultiScale(self, *a, **k):
                return [(10, 10, 40, 40)]

        frame = (np.random.default_rng(0).integers(0, 255, (120, 160, 3))).astype("uint8")
        out = vlm.blur_faces(frame, detector=_OneFace(), strict=True)
        self.assertFalse(np.array_equal(out[10:50, 10:50], frame[10:50, 10:50]))
        self.assertTrue(np.array_equal(out[60:, 60:], frame[60:, 60:]))

    def test_a_failed_required_blur_drops_the_image(self):
        """Fail closed: if blurring is required and breaks, nothing is sent."""
        import numpy as np

        class _Broken:
            def detectMultiScale(self, *a, **k):
                raise RuntimeError("cascade broke")

        frame = np.zeros((120, 160, 3), "uint8")
        with self.assertRaises(RuntimeError):
            vlm.blur_faces(frame, detector=_Broken(), strict=True)

    def test_the_crop_padding(self):
        self.assertEqual(vlm.CROP_PAD, 0.40)

    def test_an_unknown_kind_fails_open(self):
        v = vlm.verify_frame(_verifier("{}"), _frame(), "jaywalking")
        self.assertFalse(v.ok)
        self.assertIn("jaywalking", v.error)


# --- scoring behaviour ------------------------------------------------------

class CheckerCannotAlertAloneTests(unittest.TestCase):
    """v3 §4, rule 2."""

    def test_the_cap_binds(self):
        s = scoring.Score("drinking", scoring.DRINKING_WEIGHTS,
                          {"bottle", "vlm_verdict", "vlm_seating",
                           "vlm_drinking_items"}, vlm_confidence=1.0)
        self.assertLessEqual(s.vlm_score, scoring.VLM_CAP["drinking"])

    def test_checker_answers_alone_never_reach_a_shown_level(self):
        for kind, weights in (("drinking", scoring.DRINKING_WEIGHTS),
                              ("smoking", scoring.SMOKING_WEIGHTS)):
            only = {c for c in weights if c.startswith("vlm_")}
            s = scoring.Score(kind, weights, only, vlm_confidence=1.0)
            self.assertFalse(s.alerting, f"{kind}: {s.summary()}")

    def test_low_confidence_earns_nothing(self):
        s = scoring.Score("drinking", scoring.DRINKING_WEIGHTS,
                          {"bottle", "vlm_verdict"}, vlm_confidence=0.0)
        self.assertEqual(s.vlm_score, 0.0)
        self.assertAlmostEqual(s.score, s.system_score, places=6)


class SceneReadingTests(unittest.TestCase):
    """v3 §4, rule 3: the checker's one way to cut a score."""

    def test_it_drops_a_maximum_case_out_of_sight(self):
        """v3's own arithmetic: drinking 125 -> 31, smoking 130 -> 32.5."""
        for kind, weights in (("drinking", scoring.DRINKING_WEIGHTS),
                              ("smoking", scoring.SMOKING_WEIGHTS)):
            every = set(weights)
            full = scoring.Score(kind, weights, every, vlm_confidence=1.0)
            cut = scoring.Score(kind, weights, every, vlm_confidence=1.0,
                                multipliers={"scene": scoring.SCENE_MULTIPLIER})
            self.assertTrue(full.alerting, kind)
            self.assertFalse(cut.alerting, f"{kind}: {cut.summary()}")

    def test_but_the_event_is_still_logged(self):
        """Not zero, so it can be audited if the checker was wrong."""
        s = scoring.Score("drinking", scoring.DRINKING_WEIGHTS,
                          set(scoring.DRINKING_WEIGHTS), vlm_confidence=1.0,
                          multipliers={"scene": scoring.SCENE_MULTIPLIER})
        self.assertGreater(s.score, 0.0)
        self.assertTrue(s.cues)

    def test_the_fish_vendor(self):
        ev = theft.Evidence("holdup", (0, 0, 10, 10),
                            {"E14": 0.45, "E12": 0.20, "E10": 0.10}, {}, set(),
                            [1, 2], "freeze with weapon")
        self.assertTrue(theft.alerts_at(ev.band))
        ev.rescore({}, {scoring.HOLDUP_DENIAL_CODE: scoring.SCENE_MULTIPLIER})
        self.assertFalse(theft.alerts_at(ev.band), ev.score)


# --- the visibility gate and its one exception ------------------------------

class GateTests(unittest.TestCase):

    def test_no_object_no_alert(self):
        s = scoring.Score("drinking", scoring.DRINKING_WEIGHTS,
                          {"gathering", "gathering_duration", "time_band"})
        self.assertFalse(s.gate_open)
        self.assertFalse(s.visible)

    def test_but_it_is_still_scored(self):
        s = scoring.Score("drinking", scoring.DRINKING_WEIGHTS,
                          {"gathering", "gathering_duration", "time_band"})
        self.assertGreater(s.raw_score, 0.0)
        self.assertTrue(s.cues)

    def test_puffs_alone_are_not_shown(self):
        """v3 §4's exception: the checker is called, but 0.40 stays invisible."""
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS,
                          {"gesture", "puffs"})
        self.assertAlmostEqual(s.score, 0.40)
        self.assertTrue(s.pose_exception)
        self.assertFalse(s.visible)

    def test_the_checker_is_what_makes_a_puff_only_case_visible(self):
        """0.40 + item 0.15 + smoking 0.20 = 0.75, exactly v3's worked example."""
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS,
                          {"gesture", "puffs", "vlm_smoking_item", "vlm_verdict"},
                          vlm_confidence=1.0)
        self.assertAlmostEqual(s.score, 0.75)


# --- what the tanod sees ----------------------------------------------------

class LevelTests(unittest.TestCase):

    def test_the_v3_names(self):
        self.assertEqual(scoring.label_of(scoring.WARNING), "Possible")
        self.assertEqual(scoring.label_of(scoring.VIOLATION), "Likely")

    def test_likely_not_confirmed(self):
        """The system proposes; the tanod confirms. Calling a machine reading
        "Confirmed" claims the officer's judgement for the model."""
        self.assertNotEqual(scoring.label_of(scoring.VIOLATION), "Confirmed")

    def test_nothing_below_55_is_shown(self):
        for raw in (0.10, 0.40, 0.54):
            self.assertFalse(scoring.alerts_at(scoring.level_of(raw)), raw)
        self.assertTrue(scoring.alerts_at(scoring.level_of(0.55)))

    def test_the_checklist_is_plain_language(self):
        s = scoring.Score("drinking", scoring.DRINKING_WEIGHTS,
                          {"bottle", "gathering", "gathering_duration"})
        found = s.checklist()["found"]
        self.assertIn("Bottle seen", found)
        self.assertIn("Stayed 10+ minutes", found)
        # No cue names and no numbers: a tanod should not have to learn the
        # schema to read an alert.
        self.assertFalse([line for line in found if "_" in line])

    def test_a_cut_score_says_why(self):
        s = scoring.Score("drinking", scoring.DRINKING_WEIGHTS,
                          {"bottle", "gathering"},
                          multipliers={"scene_type": scoring.SCENE_MULTIPLIER})
        self.assertIn("AI checker: ordinary activity",
                      s.checklist()["reduced_by"])

    def test_the_checklist_travels_on_the_stored_vector(self):
        s = scoring.Score("drinking", scoring.DRINKING_WEIGHTS, {"bottle"})
        self.assertEqual(s.as_dict()["checklist"], s.checklist())


if __name__ == "__main__":
    unittest.main()
