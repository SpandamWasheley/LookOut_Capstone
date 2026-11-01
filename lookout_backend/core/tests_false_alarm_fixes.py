"""Smoking pose-only false alarms and the holdup "2 people" rule (CHANGES.md parts A and B)."""
import unittest
from unittest import mock

import numpy as np

from core.management.commands.watch_smoking import Command as Smoking
from core.vision import location_momentum as lm
from core.vision import recognition, scoring, theft, tracking


def kp(ratio=2.0, nose=0.9, eyes=0.9, wrist=0.9, shoulder_px=80.0):
    """17x3 keypoints: nose at (100, 40), shoulders `shoulder_px` apart, closest wrist `ratio` shoulder-widths from the nose."""
    k = np.zeros((17, 3), dtype=float)
    k[0] = (100, 40, nose)
    k[1], k[2] = (90, 30, eyes), (110, 30, eyes)
    k[5], k[6] = (100 - shoulder_px / 2, 80, 0.9), (100 + shoulder_px / 2, 80, 0.9)
    k[9] = (100 + ratio * shoulder_px, 40, wrist)
    k[10] = (140, 200, 0.1)
    return k


def person(tid, box=(100, 100, 140, 300), born=0.0, now=0.0, conf=0.8):
    t = tracking.Track(tid, box, born)
    t.last_seen = now
    t.conf = conf
    t.note_position(born)
    t.note_position(now)
    return t


class GestureRatioRules(unittest.TestCase):
    def test_a_clear_front_facing_person_gives_a_ratio(self):
        ratio, why = recognition.gesture_ratio(kp(0.5), person_height=200)
        self.assertAlmostEqual(ratio, 0.5, places=2)
        self.assertEqual(why, "")

    def test_a_nose_under_the_gesture_confidence_is_unknown(self):
        self.assertEqual(recognition.gesture_ratio(kp(0.5, nose=0.45), 200), (None, "facing away"))

    def test_a_wrist_under_the_gesture_confidence_is_unknown_even_when_it_clears_the_general_floor(self):
        self.assertGreaterEqual(0.45, recognition.KP_MIN_CONF)          # the old bar would have used it
        self.assertEqual(recognition.gesture_ratio(kp(0.5, wrist=0.45), 200), (None, "keypoints unsure"))

    def test_only_a_confident_wrist_competes_for_closest(self):
        k = kp(2.0)
        k[10] = (100 + 0.4 * 80, 40, 0.3)                              # a closer wrist, but a guess
        self.assertAlmostEqual(recognition.gesture_ratio(k, 200)[0], 2.0, places=2)

    def test_a_person_with_no_eye_visible_is_facing_away(self):
        self.assertEqual(recognition.gesture_ratio(kp(0.5, eyes=0.3), 200), (None, "facing away"))

    def test_one_eye_is_enough(self):
        k = kp(0.5)
        k[2][2] = 0.1
        self.assertEqual(recognition.gesture_ratio(k, 200)[1], "")

    def test_a_short_person_box_is_skipped(self):
        self.assertEqual(recognition.gesture_ratio(kp(0.5), person_height=119), (None, "small person"))
        self.assertEqual(recognition.gesture_ratio(kp(0.5), person_height=120)[1], "")

    def test_narrow_shoulders_are_skipped(self):
        self.assertEqual(recognition.gesture_ratio(kp(0.5, shoulder_px=24.0), 200), (None, "small person"))
        self.assertEqual(recognition.gesture_ratio(kp(0.5, shoulder_px=26.0), 200)[1], "")


class ApexHold(unittest.TestCase):
    def test_a_face_touch_shorter_than_the_hold_is_not_a_cycle(self):
        t = person(1)
        for now, r in ((0.0, 2.0), (0.1, 0.5), (0.3, 0.5), (0.4, 2.0)):
            t.update_gesture(r, now)
        self.assertEqual(t.gesture_count(1.0), 0)

    def test_a_held_raise_is_one_cycle(self):
        t = person(1)
        for now, r in ((0.0, 0.5), (0.7, 0.5), (0.8, 2.0)):
            t.update_gesture(r, now)
        self.assertEqual(t.gesture_count(1.0), 1)

    def test_a_long_vape_draw_still_counts(self):
        t = person(1)
        t.update_gesture(0.5, 0.0)
        for now in (1.0, 2.0, 3.0, 3.9):
            t.update_gesture(0.5, now)
        t.update_gesture(2.0, 4.0)
        self.assertEqual(t.gesture_count(5.0), 1)                       # no upper bound on the hold

    def test_leaving_the_raised_band_before_the_hold_restarts_it(self):
        t = person(1)
        for now, r in ((0.0, 0.5), (0.4, 0.5), (0.5, 1.0), (0.6, 0.5), (1.0, 0.5), (1.1, 2.0)):
            t.update_gesture(r, now)
        self.assertEqual(t.gesture_count(2.0), 0)                       # 0.4 s, then 0.4 s: neither reached 0.6 s

    def test_a_gap_in_the_samples_does_not_count_as_holding(self):
        t = person(1)
        t.update_gesture(0.5, 0.0)
        t.update_gesture(0.5, 3.0)                                      # nothing known for 3 s (> GESTURE_APEX_MAX_GAP_SECONDS)
        t.update_gesture(2.0, 3.1)
        self.assertEqual(t.gesture_count(4.0), 0)

    def test_the_hold_is_one_switch_away(self):
        with mock.patch.object(tracking, "GESTURE_APEX_HOLD_SECONDS", 0.0):
            t = person(1)
            for now, r in ((0.0, 0.5), (0.1, 2.0)):
                t.update_gesture(r, now)
            self.assertEqual(t.gesture_count(1.0), 1)


class FeedPoseSkips(unittest.TestCase):
    def setUp(self):
        self.cmd = Smoking()
        self.cmd.puff_window = 300.0
        self.cmd.stdout = mock.Mock()
        self.frame = np.zeros((1440, 2560, 3), dtype=np.uint8)

    def hold_bottle(self, ext, box, seconds=3.0):
        """A Bottle on this person for `seconds`, sampled every 0.5 s and ending at video time 0."""
        for i in range(int(seconds / 0.5) + 1):
            self.cmd.bottle_seen.append((ext, box, -seconds + i * 0.5))

    def feed(self, track, keypoints, now):
        with mock.patch.object(lm, "ensure_keypoints", return_value=keypoints), \
                mock.patch.object(lm, "shared_poses", return_value=[]):
            self.cmd._feed_pose(self.frame, [track], now)

    def run_cycle(self, track, keypoints_for, t0=0.0):
        now = t0
        for r in [2.0] + [0.5] * 15 + [2.0]:
            self.feed(track, keypoints_for(r), now)
            now += 0.05
        return now

    def test_a_back_turned_person_never_scores(self):
        t = person(1)
        self.run_cycle(t, lambda r: kp(r, nose=0.2, eyes=0.1))
        self.assertEqual(t.gesture_count(5.0), 0)
        self.assertGreater(self.cmd.stats["gesture skipped: facing away"], 0)

    def test_a_tiny_person_never_scores(self):
        t = person(1, box=(100, 100, 120, 200))                         # 100 px tall
        self.run_cycle(t, lambda r: kp(r))
        self.assertEqual(t.gesture_count(5.0), 0)
        self.assertGreater(self.cmd.stats["gesture skipped: small person"], 0)

    def test_unknown_frames_neither_raise_nor_lower_the_hand(self):
        t = person(1)
        now = 0.0
        for r in [2.0] + [0.5] * 15:
            self.feed(t, kp(r), now)
            now += 0.05
        self.assertEqual(t.hand_zone, "atface")
        self.feed(t, kp(2.0, nose=0.1), now)                           # an unknown frame while the hand is "down" in the pixels
        self.assertEqual(t.hand_zone, "atface")
        self.assertEqual(t.gesture_count(now), 0)

    def test_a_clear_person_is_counted(self):
        t = person(1)
        self.run_cycle(t, lambda r: kp(r))
        self.assertEqual(t.gesture_count(5.0), 1)

    def test_a_bottle_on_the_same_person_stands_the_gesture_counter_down(self):
        t = person(1)
        t.ext_id = 7
        self.hold_bottle(7, tuple(t.box))
        end = self.run_cycle(t, lambda r: kp(r))
        self.assertEqual(t.gesture_count(end), 0)
        self.assertGreater(self.cmd.stats["gesture skipped: bottle on this person (drinking)"], 0)

    def test_a_bottle_seen_for_under_two_seconds_in_total_does_not_stand_down(self):
        t = person(1)
        t.ext_id = 7
        self.hold_bottle(7, tuple(t.box), seconds=1.5)
        end = self.run_cycle(t, lambda r: kp(r))
        self.assertEqual(t.gesture_count(end), 1)

    def test_a_gap_between_bottle_samples_is_not_credited(self):
        t = person(1)
        t.ext_id = 7
        for ts in (-6.0, 0.0):                                          # two lone samples 6 s apart: 1 s credit at most
            self.cmd.bottle_seen.append((7, tuple(t.box), ts))
        self.assertFalse(self.cmd._bottle_standdown(t, 0.0))

    def test_the_stand_down_is_time_bounded(self):
        t = person(1)
        t.ext_id = 7
        self.hold_bottle(7, tuple(t.box))
        end = self.run_cycle(t, lambda r: kp(r), t0=11.0)               # BOTTLE_STANDDOWN_SECONDS is 10
        self.assertEqual(t.gesture_count(end), 1)

    def test_a_bottle_on_someone_else_does_not_stand_this_person_down(self):
        t = person(1)
        t.ext_id = 7
        self.hold_bottle(8, (500, 100, 540, 300))
        end = self.run_cycle(t, lambda r: kp(r))
        self.assertEqual(t.gesture_count(end), 1)

    def test_greedy_tracking_falls_back_to_the_box(self):
        t = person(1)
        self.hold_bottle(None, tuple(t.box))
        end = self.run_cycle(t, lambda r: kp(r))
        self.assertEqual(t.gesture_count(end), 0)

    def test_the_raise_started_during_a_sip_is_not_completed_afterwards(self):
        t = person(1)
        t.ext_id = 7
        self.hold_bottle(7, tuple(t.box))
        self.feed(t, kp(0.5), 1.0)                                      # hand up while the bottle is on them
        self.assertNotEqual(t.hand_zone, "atface")                      # the state was reset, not remembered
        t.hand_zone = "down"
        self.assertEqual(t.gesture_count(20.0), 0)


class PuffOnlyCap(unittest.TestCase):
    CUES = {"gesture", "puffs", "puff_pattern"}

    def test_no_item_ever_seen_holds_the_status_at_monitoring(self):
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, self.CUES, object_on=False, puff_only_max=scoring.MONITORING)
        self.assertTrue(s.puff_only)
        self.assertEqual(s.level, scoring.MONITORING)
        self.assertEqual(s.capped, scoring.PUFF_ONLY_NO_ITEM_TEXT)
        self.assertAlmostEqual(s.score, 0.55)

    def test_an_item_seen_earlier_restores_possible(self):
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, self.CUES, object_on=False)
        self.assertEqual(s.level, scoring.WARNING)
        self.assertEqual(s.capped, "")

    def test_fewer_puffs_still_show_nothing(self):
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, {"gesture", "puffs"}, object_on=False,
                          puff_only_max=scoring.MONITORING)
        self.assertEqual(s.level, scoring.NONE)

    def test_the_object_path_is_untouched(self):
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, {"cigarette", "gesture"}, object_on=True,
                          puff_only_max=scoring.MONITORING)
        self.assertEqual(s.level, scoring.WARNING)


class PartnerRules(unittest.TestCase):
    def holder(self):
        return person(1, box=(400, 200, 520, 500), born=-5.0, now=10.0)

    def partner(self, **kw):
        base = dict(box=(560, 200, 680, 500), born=5.0, now=10.0)
        base.update(kw)
        return person(2, **base)

    def test_a_standing_detected_adult_is_a_valid_partner(self):
        self.assertEqual(theft.partner_problem(self.holder(), self.partner(), 10.0), "")

    def test_a_track_not_matched_this_frame_is_not_a_partner(self):
        self.assertEqual(theft.partner_problem(self.holder(), self.partner(now=9.5), 10.0), "not detected this frame")

    def test_a_small_box_is_not_a_partner(self):
        small = self.partner(box=(560, 400, 600, 500))                  # 100 px against the holder's 300
        self.assertEqual(theft.partner_problem(self.holder(), small, 10.0), "too small next to the holder")

    def test_a_low_confidence_box_is_not_a_partner(self):
        self.assertEqual(theft.partner_problem(self.holder(), self.partner(conf=0.39), 10.0), "low detection confidence")

    def test_a_passer_by_is_not_a_partner(self):
        p = self.partner(box=(100, 200, 220, 500), born=7.0)
        p.box = (560, 200, 680, 500)                                    # moved a whole box width in 3 s
        p.note_position(10.0)
        self.assertEqual(theft.partner_problem(self.holder(), p, 10.0), "moving, not standing")

    def test_a_track_that_just_appeared_cannot_be_judged_standing(self):
        self.assertEqual(theft.partner_problem(self.holder(), self.partner(born=9.5), 10.0), "seen too briefly to be standing")

    def test_a_scene_track_is_not_a_person(self):
        scene = tracking.Track(-1, (560, 200, 680, 500), 0.0, 5.0, is_scene=True)
        self.assertEqual(theft.partner_problem(self.holder(), scene, 10.0), "not a person")


class FrozenPairUsesRealDetectionsOnly(unittest.TestCase):
    def pair(self, lost_after=None):
        eng = theft.TheftEngine()
        a = person(1, box=(400, 200, 520, 500), born=0.0, now=0.0)
        b = person(2, box=(560, 200, 680, 500), born=0.0, now=0.0)
        return eng, a, b

    def run_frames(self, eng, a, b, t_from, t_to, b_visible=True, step=0.25):
        t = t_from
        while t <= t_to + 1e-9:
            a.box, a.last_seen = (400, 200, 520, 500), t
            a.note_position(t)
            if b_visible:
                b.box, b.last_seen = (560, 200, 680, 500), t
                b.note_position(t)
            for tr in (a, b):
                tr.observe(t)
            eng.update([a, b], [], [], [], t)
            t += step
        return t

    def test_a_pair_of_standing_people_reaches_the_freeze(self):
        eng, a, b = self.pair()
        self.run_frames(eng, a, b, 0.0, 6.0)
        self.assertTrue(any(st.freeze_ok for st in eng.pairs.values()))

    def test_a_lost_partner_is_no_longer_frozen_with_the_holder(self):
        eng, a, b = self.pair()
        end = self.run_frames(eng, a, b, 0.0, 6.0)
        self.assertTrue(any(st.freeze_ok for st in eng.pairs.values()))
        self.run_frames(eng, a, b, end, end + 1.0, b_visible=False)     # b stays in the track list (grace) but is not detected
        self.assertFalse(any(st.freeze_ok for st in eng.pairs.values()))

    def test_a_partner_that_was_never_detected_never_freezes(self):
        eng, a, b = self.pair()
        self.run_frames(eng, a, b, 1.0, 8.0, b_visible=False)
        self.assertFalse(any(st.freeze_ok for st in eng.pairs.values()))

    def test_a_ghost_does_not_give_the_holder_e12(self):
        eng, a, b = self.pair()
        end = self.run_frames(eng, a, b, 0.0, 6.0)
        eng_st = next(iter(eng.pairs.values()))
        eng_st.freeze_start_ok = eng_st.freeze_start_ok or 0.0
        self.run_frames(eng, a, b, end, end + 0.5, b_visible=False)
        cues = eng.holdup_pair_cues(1, [end], end + 0.5, hold=5.0, holder=a)
        self.assertNotIn("E12", cues)


if __name__ == "__main__":
    unittest.main()


class StrongGesture(unittest.TestCase):
    """Sustained close-range draws lift the no-item cap (tracking.STRONG_GESTURE_*)."""

    def draw(self, t, start, hold=1.6, strong=True):
        for i in range(int(hold / 0.4) + 1):
            t.update_gesture(0.5, start + i * 0.4, strong=strong)
        t.update_gesture(2.0, start + hold + 0.1, strong=strong)

    def test_two_sustained_close_draws_within_90_s(self):
        t = person(1)
        self.draw(t, 0.0)
        self.draw(t, 30.0)
        self.assertTrue(t.strong_gesture(32.0))

    def test_one_draw_is_not_enough(self):
        t = person(1)
        self.draw(t, 0.0)
        self.assertFalse(t.strong_gesture(5.0))

    def test_draws_more_than_90_s_apart_do_not_pair(self):
        t = person(1)
        self.draw(t, 0.0)
        self.draw(t, 100.0)
        self.assertFalse(t.strong_gesture(102.0))

    def test_a_draw_held_under_1_5_s_does_not_count(self):
        t = person(1)
        self.draw(t, 0.0, hold=1.0)
        self.draw(t, 30.0, hold=1.0)
        self.assertFalse(t.strong_gesture(32.0))

    def test_one_far_sample_inside_the_draw_spoils_it(self):
        t = person(1)
        self.draw(t, 0.0)
        t.update_gesture(0.5, 30.0, strong=True)
        t.update_gesture(0.5, 30.8, strong=False)
        t.update_gesture(0.5, 31.7, strong=True)
        t.update_gesture(2.0, 31.8, strong=True)
        self.assertFalse(t.strong_gesture(32.0))

    def test_small_person_never_earns_it(self):
        t = person(1)
        self.draw(t, 0.0, strong=False)
        self.draw(t, 30.0, strong=False)
        self.assertFalse(t.strong_gesture(32.0))

    def test_the_exception_lifts_the_cap_and_tags_the_alert(self):
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, {"gesture", "puffs", "puff_pattern"}, object_on=False,
                          puff_only_max=scoring.WARNING, strong_gesture=True)
        self.assertEqual(s.level, scoring.WARNING)
        self.assertEqual(s.tag, scoring.PUFF_ONLY_STRONG_TAG)

    def test_feed_pose_marks_only_tall_boxes_as_close(self):
        cmd = Smoking()
        cmd.puff_window = 300.0
        cmd.stdout = mock.Mock()
        frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
        for height, expect in ((260, True), (240, False)):
            t = person(1, box=(100, 100, 160, 100 + height))
            now = 0.0
            for r in [0.5] * 40 + [2.0]:
                with mock.patch.object(lm, "ensure_keypoints", return_value=kp(r)), mock.patch.object(lm, "shared_poses", return_value=[]):
                    cmd._feed_pose(frame, [t], now)
                now += 0.05
            self.assertEqual(len(t.strong_draws) == 1, expect)


class PossibleEvidenceRule(unittest.TestCase):
    """Possible needs Cigarette conf >= 0.45 and (a puff, or the cigarette accepted on the mouth anchor with momentum >= 1.0)."""

    def track(self, mouth_at=None, puff_at=None):
        t = person(1)
        loc = lm.loc_of(t)
        if mouth_at is not None:
            loc.last_hit["cigarette"] = (0.5, 0.1, frozenset({lm.MOUTH}), mouth_at)
        if puff_at is not None:
            loc.puffs.append(puff_at)
        return t

    def test_cigarette_with_neither_a_puff_nor_the_mouth_is_monitoring(self):
        t = self.track()
        self.assertEqual(lm.puff_cap(t, 5.0, conf=0.9, momentum=2.0), ("monitoring", lm.CAP_NO_PUFF_TEXT))

    def test_a_hit_accepted_at_a_wrist_is_not_the_mouth(self):
        t = self.track()
        lm.loc_of(t).last_hit["cigarette"] = (0.5, 0.5, frozenset({lm.LEFT_WRIST}), 5.0)
        self.assertEqual(lm.puff_cap(t, 5.0, conf=0.9, momentum=2.0)[0], "monitoring")

    def test_on_the_mouth_with_momentum_reaches_possible_not_likely(self):
        t = self.track(mouth_at=5.0)
        self.assertEqual(lm.puff_cap(t, 5.0, conf=0.9, momentum=1.0), ("warning", lm.CAP_FEW_PUFFS_TEXT))

    def test_on_the_mouth_under_momentum_1_is_monitoring(self):
        t = self.track(mouth_at=5.0)
        self.assertEqual(lm.puff_cap(t, 5.0, conf=0.9, momentum=0.9)[0], "monitoring")

    def test_a_stale_mouth_hit_does_not_count(self):
        t = self.track(mouth_at=1.0)
        self.assertEqual(lm.puff_cap(t, 5.0, conf=0.9, momentum=2.0)[0], "monitoring")

    def test_a_puff_is_enough_without_the_mouth(self):
        t = self.track(puff_at=4.0)
        self.assertEqual(lm.puff_cap(t, 5.0, conf=0.9, momentum=0.5)[0], "warning")

    def test_low_confidence_is_monitoring_even_with_a_puff(self):
        t = self.track(puff_at=4.0, mouth_at=5.0)
        self.assertEqual(lm.puff_cap(t, 5.0, conf=0.44, momentum=2.0), ("monitoring", lm.CAP_LOW_CONF_TEXT))
        self.assertEqual(lm.puff_cap(t, 5.0, conf=0.45, momentum=2.0)[0], "warning")

    def test_an_incident_already_at_possible_keeps_it(self):
        self.assertEqual(lm.puff_cap(self.track(), 5.0, "warning", conf=0.3, momentum=0.1), (None, ""))


class NearMouthNeedsAnAcceptedHit(unittest.TestCase):
    def setUp(self):
        self.cmd = Smoking()
        self.cmd.stdout = mock.Mock()
        self.t = person(1)
        self.t.last_mouth_seen, self.t.last_mouth_ratio = 5.0, 1.0       # the raw-detection check says "at the mouth"

    def test_a_hit_the_gate_rejected_is_not_near_mouth(self):
        self.assertFalse(self.cmd._near_mouth(self.t, 5.0))
        self.assertGreater(self.cmd.stats["near mouth withheld: not accepted on the mouth anchor"], 0)

    def test_a_hit_accepted_on_the_mouth_is_near_mouth(self):
        lm.loc_of(self.t).last_hit["cigarette"] = (0.5, 0.1, frozenset({lm.MOUTH}), 5.0)
        self.assertTrue(self.cmd._near_mouth(self.t, 5.0))
