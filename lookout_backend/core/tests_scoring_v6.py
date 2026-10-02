"""Scoring spec v6: worked examples (section 11) and the rules around them.

The expected values here come from docs/specs/LookOut_Scoring_Spec_v6.md, not
from the code. A failing test means the code disagrees with the spec; report
it, do not edit the expectation.
"""
import datetime

from django.test import SimpleTestCase

from core.vision import ai_status, scoring, theft
from core.vision.scoring import (MONITORING, NONE, VIOLATION, WARNING, DRINKING_WEIGHTS,
                                 HOLDUP_WEIGHTS, SMOKING_WEIGHTS, Score)


def drinking(cues, **kw):
    return Score("drinking", DRINKING_WEIGHTS, cues, **kw)


def smoking(cues, **kw):
    return Score("smoking", SMOKING_WEIGHTS, cues, **kw)


def holdup(cues, hour, **kw):
    mult = {"E20": scoring.manila_time_multiplier(datetime.time(hour, 0))}
    return Score("holdup", HOLDUP_WEIGHTS, cues, mult, **kw)


class Tables(SimpleTestCase):
    def test_point_tables_match_the_spec_totals(self):
        self.assertAlmostEqual(sum(DRINKING_WEIGHTS.values()), 0.85)
        self.assertAlmostEqual(sum(SMOKING_WEIGHTS.values()), 1.10)
        self.assertAlmostEqual(sum(HOLDUP_WEIGHTS.values()), 0.75)

    def test_no_ai_points_anywhere_in_the_official_score(self):
        for table in (DRINKING_WEIGHTS, SMOKING_WEIGHTS, HOLDUP_WEIGHTS):
            self.assertFalse([k for k in table if k.startswith("vlm") or k in ("E30", "E32", "E33", "E34")])
        for gone in ("VLM_CAP", "SCENE_MULTIPLIER", "HOLDUP_VLM_WEIGHTS", "POSE_EXCEPTION_RELEASE"):
            self.assertFalse(hasattr(scoring, gone), gone)

    def test_thresholds(self):
        self.assertEqual(scoring.SCORE_WARNING, 0.55)
        self.assertEqual(scoring.SCORE_VIOLATION, 0.75)
        self.assertEqual(scoring.label_of(WARNING), "Possible")
        self.assertEqual(scoring.label_of(VIOLATION), "Likely")
        self.assertEqual(scoring.label_of(MONITORING), "Monitoring")

    def test_time_blocks(self):
        want = {1: 0.76, 4: 0.80, 7: 0.56, 10: 1.25, 13: 1.31, 16: 1.36, 19: 1.09, 22: 0.87}
        for hour, factor in want.items():
            self.assertEqual(scoring.manila_time_multiplier(datetime.time(hour, 30)), factor)


class DrinkingExamples(SimpleTestCase):
    def test_a_real_inuman(self):
        # 19:44 three people stop near a store: 10, no bottle -> not shown
        s = drinking({"gathering"})
        self.assertEqual(round(s.score * 100), 10)
        self.assertEqual(s.level, NONE)
        # 19:53 still there 10 minutes, evening: 30, not shown
        s = drinking({"gathering", "gathering_duration", "time_band"})
        self.assertEqual(round(s.score * 100), 30)
        self.assertEqual(s.level, NONE)
        # 19:55 bottle seen: 70 -> Possible (skips Monitoring)
        s = drinking({"gathering", "gathering_duration", "time_band", "bottle"})
        self.assertEqual(round(s.score * 100), 70)
        self.assertEqual(s.level, WARNING)
        self.assertTrue(s.alerting)
        # 19:56 bottle at the mouth: 85 -> Likely
        s = drinking({"gathering", "gathering_duration", "time_band", "bottle", "at_mouth"})
        self.assertEqual(round(s.score * 100), 85)
        self.assertEqual(s.level, VIOLATION)

    def test_someone_carrying_a_bottle_is_monitoring_only(self):
        s = drinking({"bottle"})
        self.assertEqual(round(s.score * 100), 40)
        self.assertEqual(s.level, MONITORING)
        self.assertTrue(s.stored)
        self.assertFalse(s.alerting)             # watchlist: no notification

    def test_at_mouth_needs_the_bottle(self):
        s = drinking({"at_mouth"})
        self.assertEqual(s.level, NONE)
        self.assertEqual(s.score, 0.0)
        self.assertIn("at_mouth", s.suppressed)

    def test_the_ai_wrong_example_leaves_the_official_status_alone(self):
        s = drinking({"gathering", "gathering_duration", "time_band", "bottle", "at_mouth"})
        self.assertEqual(s.level, VIOLATION)
        reply = {"observations": "x", "drinking_likelihood": "unlikely",
                 "table_chairs_or_seating_visible": False, "drinking_items_visible": False,
                 "scene_type": "other_activity", "confidence": "high"}
        sug = ai_status.suggest_status(s.level, "drinking", reply)
        self.assertEqual(sug["suggested"], "Possible")
        self.assertEqual(s.level, VIOLATION)      # unchanged
        self.assertEqual(round(s.score * 100), 85)


class SmokingExamples(SimpleTestCase):
    def test_walking_smoker_with_a_cigarette(self):
        s = smoking({"cigarette"})
        self.assertEqual((round(s.score * 100), s.level), (40, MONITORING))
        s = smoking({"cigarette", "gesture"})
        self.assertEqual((round(s.score * 100), s.level), (60, WARNING))
        s = smoking({"cigarette", "gesture", "near_mouth"})
        self.assertEqual((round(s.score * 100), s.level), (75, VIOLATION))

    def test_walking_smoker_cigarette_missed_then_found(self):
        s = smoking({"gesture"})                       # one puff, no item
        self.assertEqual((round(s.score * 100), s.level), (20, NONE))
        s = smoking({"gesture", "cigarette"})          # the high-res check finds it
        self.assertEqual((round(s.score * 100), s.level), (60, WARNING))

    def test_lingering_puff_only(self):
        self.assertEqual(smoking({"gesture"}).level, NONE)
        two = smoking({"gesture", "puffs"})
        self.assertEqual((round(two.score * 100), two.level), (40, NONE))   # logged only
        three = smoking({"gesture", "puffs", "puff_pattern"})
        self.assertEqual(round(three.score * 100), 55)
        self.assertEqual(three.level, WARNING)
        self.assertTrue(three.puff_only)
        self.assertIn("based on hand movement only", three.tag)

    def test_puff_only_is_never_above_possible_even_as_a_suggestion(self):
        three = smoking({"gesture", "puffs", "puff_pattern"})
        reply = {"observations": "x", "smoking_item_visible": False,
                 "hand_to_mouth_activity": "smoking", "confidence": "high"}
        sug = ai_status.suggest_status(three.level, "smoking", reply, puff_only=True)
        self.assertEqual(sug["text"], "No change — Possible")
        eating = dict(reply, hand_to_mouth_activity="other_activity")
        sug = ai_status.suggest_status(three.level, "smoking", eating, puff_only=True)
        self.assertEqual(sug["suggested"], "Monitoring")

    def test_near_mouth_needs_the_item(self):
        s = smoking({"near_mouth", "gesture"})
        self.assertIn("near_mouth", s.suppressed)
        self.assertEqual(s.level, NONE)

    def test_cap_at_100(self):
        s = smoking({"cigarette", "gesture", "puffs", "puff_pattern", "near_mouth"})
        self.assertAlmostEqual(s.raw_score, 1.10)
        self.assertEqual(s.score, 1.0)
        self.assertEqual(s.level, VIOLATION)


class HoldupExamples(SimpleTestCase):
    def test_a_vendor_in_the_afternoon(self):
        # 16:30, x1.36 on every row
        knife = holdup({"E14"}, 16)
        self.assertEqual(round(knife.score * 100), 61)
        self.assertEqual(knife.level, WARNING)         # second person near
        frozen = holdup({"E14", "E12"}, 16)
        self.assertEqual(round(frozen.score * 100), 88)
        self.assertEqual(frozen.level, VIOLATION)

    def test_a_real_one_at_night(self):
        # 22:15, x0.87 on every row
        self.assertEqual(round(holdup({"E10"}, 22).score * 100), 9)
        self.assertEqual(holdup({"E10"}, 22).level, NONE)               # no knife
        self.assertEqual(round(holdup({"E10", "E12"}, 22).score * 100), 26)
        self.assertEqual(holdup({"E10", "E12"}, 22).level, NONE)
        full = holdup({"E14", "E12", "E10"}, 22)
        self.assertEqual(round(full.score * 100), 65)
        self.assertEqual(full.level, WARNING)
        reply = {"observations": "x", "object_pointed_at_a_person": True,
                 "victim_response_visible": True, "holdup_likelihood": "likely",
                 "scene_type": "confrontation", "confidence": "high"}
        sug = ai_status.suggest_status(full.level, "holdup", reply)
        self.assertEqual(sug["text"], "Possible → Likely (suggested) — AI sees a confrontation")
        self.assertEqual(full.level, WARNING)        # the AI cannot raise it

    def test_knife_alone_at_0700_is_monitoring(self):
        s = holdup({"E14"}, 7)
        self.assertEqual(round(s.score * 100), 25)     # 45 x 0.56
        self.assertEqual(s.level, MONITORING)
        self.assertTrue(s.stored)
        self.assertFalse(s.alerting)

    def test_one_person_with_a_knife_gets_no_holdup_alert(self):
        # Peak afternoon would be 61 (Possible) -- but nobody is near the holder.
        s = holdup({"E14"}, 16, people_near=False)
        self.assertEqual(s.level, MONITORING)
        self.assertTrue(s.holdup_capped)
        self.assertFalse(s.alerting)
        # even with every other cue
        s = holdup({"E14", "E12", "E10"}, 16, people_near=False)
        self.assertEqual(s.level, MONITORING)

    def test_no_knife_nothing_is_shown(self):
        self.assertEqual(holdup({"E12", "E10"}, 16).level, NONE)

    def test_theft_evidence_follows_the_same_rules(self):
        solo = theft.Evidence("weapon", (0, 0, 10, 10), {"E14": 0.45}, {"E20": 1.36},
                              set(), [1], "x", people_near=False)
        self.assertEqual(solo.band, MONITORING)
        near = theft.Evidence("weapon", (0, 0, 10, 10), {"E14": 0.45}, {"E20": 1.36},
                              set(), [1], "x", people_near=True)
        self.assertEqual(near.band, WARNING)
        pair = theft.Evidence("holdup", (0, 0, 10, 10), {"E12": 0.2, "E10": 0.1}, {},
                              set(), [1, 2], "x")
        self.assertEqual(pair.band, NONE)               # no knife


class Hysteresis(SimpleTestCase):
    def test_likely_survives_a_small_dip(self):
        self.assertEqual(scoring.level_with_hysteresis(0.76, None), VIOLATION)
        self.assertEqual(scoring.level_with_hysteresis(0.72, VIOLATION), VIOLATION)

    def test_likely_is_left_below_70(self):
        self.assertEqual(scoring.level_with_hysteresis(0.69, VIOLATION), WARNING)

    def test_possible_is_left_below_50(self):
        self.assertEqual(scoring.level_with_hysteresis(0.52, WARNING), WARNING)
        self.assertEqual(scoring.level_with_hysteresis(0.49, WARNING), MONITORING)

    def test_rising_is_immediate(self):
        self.assertEqual(scoring.level_with_hysteresis(0.80, MONITORING), VIOLATION)
        self.assertEqual(scoring.level_with_hysteresis(0.55, MONITORING), WARNING)

    def test_inside_a_score(self):
        s = drinking({"bottle", "gathering", "gathering_duration", "time_band"},
                     previous_level=VIOLATION)        # 70 -> stays Likely (>= 70)
        self.assertEqual(s.level, VIOLATION)
        s = drinking({"bottle", "gathering", "gathering_duration"}, previous_level=VIOLATION)
        self.assertEqual(round(s.score * 100), 65)    # 65 < 70 -> Possible
        self.assertEqual(s.level, WARNING)

    def test_monitoring_ends_when_the_object_goes(self):
        s = drinking({"gathering", "gathering_duration"}, previous_level=MONITORING)
        self.assertEqual(s.level, NONE)


class AiSuggestion(SimpleTestCase):
    def smoking_reply(self, **kw):
        base = {"observations": "x", "smoking_item_visible": True,
                "hand_to_mouth_activity": "smoking", "confidence": "high"}
        base.update(kw)
        return base

    def test_invalid_json_gives_unavailable_and_changes_nothing(self):
        for bad in (None, {}, {"observations": "x"}, "not json", {"confidence": "high"}):
            sug = ai_status.suggest_status(WARNING, "smoking", bad)
            self.assertEqual(sug["text"], "AI context unavailable")
            self.assertFalse(sug["changed"])
            self.assertEqual(ai_status.ai_badge("smoking", bad)["code"], "unavailable")
        self.assertEqual(smoking({"cigarette", "gesture"}).level, WARNING)

    def test_medium_or_low_confidence_is_no_change(self):
        for conf in ("medium", "low"):
            for r in (self.smoking_reply(confidence=conf),
                      self.smoking_reply(hand_to_mouth_activity="other_activity", confidence=conf)):
                sug = ai_status.suggest_status(WARNING, "smoking", r)
                self.assertEqual(sug["text"], "No change — Possible")
                self.assertFalse(sug["changed"])

    def test_one_step_down_and_the_monitoring_note(self):
        r = self.smoking_reply(hand_to_mouth_activity="other_activity")
        self.assertEqual(ai_status.suggest_status(VIOLATION, "smoking", r)["suggested"], "Possible")
        self.assertEqual(ai_status.suggest_status(WARNING, "smoking", r)["suggested"], "Monitoring")
        mon = ai_status.suggest_status(MONITORING, "smoking", r)
        self.assertEqual(mon["suggested"], "Monitoring")
        self.assertIn("likely ordinary activity", mon["text"])

    def test_going_up_is_stricter(self):
        drink = {"observations": "x", "drinking_likelihood": "likely",
                 "table_chairs_or_seating_visible": True, "drinking_items_visible": True,
                 "scene_type": "drinking_session", "confidence": "high"}
        up = ai_status.suggest_status(WARNING, "drinking", drink)
        self.assertEqual(up["text"], "Possible → Likely (suggested) — AI sees a drinking session")
        # one agreeing answer is not enough for drinking
        one = dict(drink, scene_type="unclear")
        self.assertEqual(ai_status.suggest_status(WARNING, "drinking", one)["text"], "No change — Possible")
        # already Likely and supported
        self.assertEqual(ai_status.suggest_status(VIOLATION, "drinking", drink)["text"], "Likely — AI agrees")
        # smoking needs only the one answer
        self.assertEqual(ai_status.suggest_status(MONITORING, "smoking", self.smoking_reply())["suggested"], "Possible")

    def test_ai_never_changes_the_official_status(self):
        s = smoking({"cigarette"})
        before = (s.score, s.level)
        ai_status.suggest_status(s.level, "smoking", self.smoking_reply())
        self.assertEqual((s.score, s.level), before)

    def test_observations_are_cut_to_20_words(self):
        r = self.smoking_reply(observations=" ".join(["word"] * 40))
        self.assertEqual(len(ai_status.validate_reply("smoking", r)["observations"].split()), 20)

    def test_badges(self):
        self.assertEqual(ai_status.ai_badge("smoking", self.smoking_reply())["code"], "supports")
        self.assertEqual(ai_status.ai_badge("smoking", self.smoking_reply(hand_to_mouth_activity="other_activity"))["code"], "ordinary")
        self.assertEqual(ai_status.ai_badge("smoking", self.smoking_reply(hand_to_mouth_activity="none"))["code"], "unclear")


class Cap(SimpleTestCase):
    def test_a_score_over_100_is_capped(self):
        s = smoking({"cigarette", "gesture", "puffs", "puff_pattern", "near_mouth"})
        self.assertEqual(round(s.raw_score * 100), 110)
        self.assertEqual(round(s.score * 100), 100)


class FootageClock(SimpleTestCase):
    """--clock: uploaded footage is scored by the time it was FILMED, not run."""

    def test_parse(self):
        from core.vision import clock
        self.assertEqual(clock.parse_clock("2026-08-18 19:30"), datetime.datetime(2026, 8, 18, 19, 30))
        self.assertEqual(clock.parse_clock("2026-08-18T19:30:15"), datetime.datetime(2026, 8, 18, 19, 30, 15))
        self.assertIsNone(clock.parse_clock(""))
        with self.assertRaises(ValueError):
            clock.parse_clock("tonight")

    def test_file_sources_follow_the_video_position(self):
        from core.vision import clock
        start = datetime.datetime(2026, 8, 18, 23, 59, 0)
        self.assertEqual(clock.clock_now(start, 90, True), datetime.datetime(2026, 8, 19, 0, 0, 30))

    def test_live_streams_and_no_override_use_the_wall_clock(self):
        from core.vision import clock
        start = datetime.datetime(2020, 1, 1, 3, 0)
        live = clock.clock_now(start, 90, False)
        self.assertGreater(live, datetime.datetime(2026, 1, 1))
        self.assertGreater(clock.clock_now(None, 90, True), datetime.datetime(2026, 1, 1))

    def test_it_drives_both_time_indicators(self):
        from core.management.commands.watch_drinking import Command as Drinking
        d = Drinking()
        d._source_path = "clip.mp4"
        d.clock_start = datetime.datetime(2026, 8, 18, 19, 30)
        self.assertTrue(d._time_band_cue(0.0))             # 19:30 is in 16:00-24:00
        d.clock_start = datetime.datetime(2026, 8, 18, 10, 0)
        self.assertFalse(d._time_band_cue(0.0))            # 10:00 is not
        self.assertTrue(d._time_band_cue(6.5 * 3600))      # ...but 16:30 into the clip is
        d._source_path = None                              # live: override ignored
        d.clock_start = datetime.datetime(2026, 8, 18, 3, 0)
        self.assertEqual(d._time_band_cue(0.0),
                         scoring.in_time_band(datetime.datetime.now(), *scoring.DRINKING_HIGH_BAND))
        from core.management.commands.watch_thief import Command as Thief
        t = Thief()
        t._source_path = "clip.mp4"
        t.clock_start = datetime.datetime(2026, 8, 18, 7, 0)
        self.assertEqual(scoring.manila_time_multiplier(t._clock_now(0.0)), 0.56)
        t.clock_start = datetime.datetime(2026, 8, 18, 16, 0)
        self.assertEqual(scoring.manila_time_multiplier(t._clock_now(0.0)), 1.36)
