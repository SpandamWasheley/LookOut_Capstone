"""Holdup scoring v2 -- docs/specs/holdup_scoring_v2.md.

Pure logic: no Django models and no YOLO. The scoring cases drive theft.Evidence
directly; the loiter, freeze and sequence cases drive PersonTracker + TheftEngine
over a simulated clock (the Scene helper from tests_layer_e).
"""
import datetime
import math
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest import mock

from core.management.commands import watch_thief
from core.tests_layer_e import DT, Scene, pbox
from core.vision import momentum, recognition, scoring, theft, tracking

W = theft.WEIGHTS
MONITORING, WARNING, VIOLATION = scoring.MONITORING, scoring.WARNING, scoring.VIOLATION


def evidence(cues, near=True, e20=0):
    """Evidence for a knife holder. `cues` are rule names; `e20` is the time-of-day
    table points (what the hour would add)."""
    weights = {c: W[c] for c in cues}
    if e20:
        weights["E20"] = e20 / 100.0
    return theft.Evidence("weapon", (0, 0, 10, 10), weights, {}, set(), [1], "x", people_near=near)


def pts(ev):
    return round(ev.score * 100)


class BaseScoreTests(unittest.TestCase):
    """The expected base scores and levels (no time of day)."""

    def test_knife_only_is_monitoring(self):
        ev = evidence({"E14"})
        self.assertEqual((pts(ev), ev.band), (45, MONITORING))

    def test_knife_and_loitering_is_possible(self):
        # pre-v6.2.1 rule (location switch off): loitering corroborated a knife
        with mock.patch("core.vision.location_momentum.LOCATION_AWARE_MOMENTUM", False):
            ev = evidence({"E14", "E10"})
            self.assertEqual((pts(ev), ev.band), (55, WARNING))

    def test_knife_and_loitering_alone_stay_monitoring_in_v621(self):
        with mock.patch("core.vision.location_momentum.HOLDUP_V621_RULES", True):
            ev = evidence({"E14", "E10"})
            self.assertEqual((pts(ev), ev.band), (55, MONITORING))
            self.assertEqual(ev.capped, "capped: no frozen pair")

    def test_knife_and_loitering_is_possible_by_default(self):
        # HOLDUP_V621_RULES is off by default: holdup keeps its pre-v6.2 rules
        ev = evidence({"E14", "E10"})
        self.assertEqual((pts(ev), ev.band), (55, WARNING))

    def test_knife_and_freeze_is_possible(self):
        ev = evidence({"E14", "E12"})
        self.assertEqual((pts(ev), ev.band), (65, WARNING))

    def test_knife_freeze_and_loitering_is_likely(self):
        ev = evidence({"E14", "E12", "E10"})
        self.assertEqual((pts(ev), ev.band), (75, VIOLATION))

    def test_knife_and_approach_then_freeze_is_likely(self):
        # E11->E12 is E12 (20) plus the sequence's own 10: 45 + 20 + 10 = 75
        ev = evidence({"E14", "E12", "E11E12"})
        self.assertEqual((pts(ev), ev.band), (75, VIOLATION))

    def test_no_knife_nothing_is_shown(self):
        self.assertEqual(evidence({"E12", "E10"}).band, scoring.NONE)

    def test_nobody_near_the_holder_stays_monitoring(self):
        ev = evidence({"E14", "E12", "E10"}, near=False)
        self.assertEqual(ev.band, MONITORING)

    def test_e11_alone_is_worth_nothing(self):
        # There is no E11 weight: only the E11->E12 sequence scores.
        self.assertNotIn("E11", W)
        self.assertEqual(W["E11E12"], 0.10)


class TimeOfDayCapTests(unittest.TestCase):
    """E20 is added points, capped so it can never change the band."""

    def test_e20_cannot_lift_possible_into_likely(self):
        # base 65 (Possible) + 10 would be 75 (Likely): capped at 74.
        ev = evidence({"E14", "E12"}, e20=10)
        self.assertEqual((pts(ev), ev.band), (74, WARNING))
        self.assertTrue(ev.e20_capped)
        self.assertEqual(round(ev.cues["E20"] * 100), 9)     # the points actually added

    def test_e20_adds_in_full_when_it_fits(self):
        ev = evidence({"E14", "E12"}, e20=5)                  # 65 + 5 = 70
        self.assertEqual((pts(ev), ev.band), (70, WARNING))
        self.assertFalse(ev.e20_capped)

    def test_e20_adds_in_full_once_likely(self):
        ev = evidence({"E14", "E12", "E10"}, e20=10)          # base 75 -> 85
        self.assertEqual((pts(ev), ev.band), (85, VIOLATION))

    def test_e20_is_not_counted_without_a_behavioural_cue(self):
        ev = evidence({"E14"}, e20=10)
        self.assertEqual((pts(ev), ev.band), (45, MONITORING))
        self.assertNotIn("E20", ev.cues)

    def test_a_base_under_55_never_reaches_possible_through_e20(self):
        # (guard) a corroborated base is always >= 55, so this exercises the
        # arithmetic of the lowest branch directly.
        ev = theft.Evidence("weapon", (0, 0, 10, 10), {"E14": 0.45, "E10": 0.0, "E20": 0.10},
                            {}, set(), [1], "x")
        self.assertLess(ev.score, scoring.SCORE_WARNING)

    def test_points_follow_the_manila_probabilities(self):
        by_block = {start: scoring.manila_time_points(datetime.datetime(2026, 10, 10, start, 30))
                    for start, *_ in scoring.MANILA_HOUR_BLOCKS}
        self.assertEqual(by_block, {0: 6, 3: 6, 6: 4, 9: 9, 12: 10, 15: 10, 18: 8, 21: 6})

    def test_hour_is_read_in_manila_time(self):
        # 08:00 UTC is 16:00 in Manila -> the 15-18 block (10 points).
        utc = datetime.datetime(2026, 10, 10, 8, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(scoring.manila_time_points(utc), 10)
        # A naive datetime is taken to already be Manila wall-clock time.
        self.assertEqual(scoring.manila_time_points(datetime.datetime(2026, 10, 10, 7, 0)), 4)

    def test_an_unreadable_time_adds_nothing(self):
        self.assertEqual(scoring.manila_time_points(None), 0)


class KnifeNearWristTests(unittest.TestCase):
    """E14 only counts a knife that is near a wrist (the existing 0.22 rule)."""

    def _rule(self, knife_box, poses):
        track = tracking.Track(1, (400, 100, 520, 400), 0.0)                  # 300 px tall
        per_track = {track: [(*knife_box, 0.8, "knife")]}
        cmd = SimpleNamespace(ablate=set(), stats=Counter())
        with mock.patch.object(recognition, "detect_pose", return_value=poses):
            out = watch_thief.Command._apply_weapon_region_rule(cmd, per_track, frame=None)
        return out[track]

    @staticmethod
    def _pose_with_wrist(x, y):
        kpts = [(0, 0, 0.0)] * 17
        kpts[recognition.KP_LWRIST] = (x, y, 0.9)
        return [((400, 100, 520, 400), kpts)]

    def test_a_knife_in_the_hand_is_kept(self):
        # wrist at (450, 300); knife centre 20 px away = 0.07 heights
        kept = self._rule((440, 290, 470, 320), self._pose_with_wrist(450, 300))
        self.assertEqual(len(kept), 1)

    def test_a_knife_with_no_wrist_nearby_is_cut(self):
        # on a table / stall: nothing near it, so it is never E14
        kept = self._rule((440, 290, 470, 320), self._pose_with_wrist(900, 300))
        self.assertEqual(kept, [])

    def test_a_knife_with_no_pose_at_all_is_cut(self):
        self.assertEqual(self._rule((440, 290, 470, 320), []), [])

    def test_the_wrist_limit_stays_at_0_22(self):
        self.assertEqual(watch_thief.KNIFE_MAX_WRIST_DIST_FRAC, 0.22)


class MomentumHoldTests(unittest.TestCase):
    def test_the_knife_hold_freezes_momentum_briefly(self):
        cfg = momentum.MomentumConfig(hold=1.0)
        slot = momentum.Slot()
        for i in range(3):
            momentum.update_momentum(slot, 0.8, cfg, now=i * 0.2)
        held = slot.momentum
        momentum.update_momentum(slot, 0.0, cfg, now=0.4 + 0.5)      # 0.5 s after the last hit
        self.assertEqual(slot.momentum, held)
        momentum.update_momentum(slot, 0.0, cfg, now=0.4 + 1.5)      # past the hold: decays
        self.assertLess(slot.momentum, held)

    def test_without_a_hold_it_decays_at_once(self):
        slot = momentum.Slot()
        momentum.update_momentum(slot, 0.8, momentum.MomentumConfig(), now=0.0)
        before = slot.momentum
        momentum.update_momentum(slot, 0.0, momentum.MomentumConfig(), now=0.1)
        self.assertLess(slot.momentum, before)

    def test_only_the_knife_class_gets_the_hold(self):
        from core.management.commands._incidents import IncidentMixin
        cmd = IncidentMixin()
        book = cmd._momentum_book()
        self.assertEqual(book.config_for("knife").hold, momentum.KNIFE_HOLD_SECONDS)
        self.assertEqual(book.config_for("cigarette").hold, 0.0)
        self.assertEqual(book.config_for("bottle").hold, 0.0)


class LoiterMemoryTests(unittest.TestCase):
    def _track(self, stamp):
        t = tracking.Track(1, pbox(300, 300)[:4], 0.0)
        t.loiter_last_seen = stamp
        return t

    def test_stamp_at_0_knife_at_40s_is_loitering(self):
        self.assertTrue(theft.TheftEngine.loiter_recent(self._track(0.0), 40.0))

    def test_stamp_at_0_knife_at_70s_is_not(self):
        self.assertFalse(theft.TheftEngine.loiter_recent(self._track(0.0), 70.0))

    def test_never_stamped_is_not_loitering(self):
        self.assertFalse(theft.TheftEngine.loiter_recent(self._track(None), 5.0))

    def test_a_loiterer_is_stamped_and_remembered_after_they_walk_off(self):
        s = Scene()
        for i in range(250):                                  # 25 s of milling about
            lx = 600 + 40 * math.sin(i * DT * 2 * math.pi / 5)
            s.step(persons=[pbox(lx, 300)])
        track = s.tracker.tracks[0]
        self.assertIsNotNone(track.loiter_last_seen, "a 25 s loiterer was never stamped")
        stamp = track.loiter_last_seen
        for i in range(150):                                  # then 15 s of walking
            s.step(persons=[pbox(600 + i * 8, 300)])
        self.assertGreater(track.path_efficiency(s.t), theft.EFF_LOITER)    # not loitering now...
        self.assertTrue(theft.TheftEngine.loiter_recent(track, s.t))        # ...but remembered
        self.assertLessEqual(stamp, s.t)

    def test_the_stamp_follows_a_track_that_reappears_next_to_where_it_was(self):
        engine = theft.TheftEngine()
        old = tracking.Track(1, pbox(300, 300)[:4], 0.0)
        old.loiter_last_seen = 10.0
        engine._relink_loiter_stamps([old], 10.0)             # remembered while alive
        newcomer = tracking.Track(2, pbox(320, 300)[:4], 12.0)           # 0.2 heights away
        engine._relink_loiter_stamps([newcomer], 12.0)        # old track gone, 2 s later
        self.assertEqual(newcomer.loiter_last_seen, 10.0)

    def test_the_stamp_is_not_copied_to_someone_far_away(self):
        engine = theft.TheftEngine()
        old = tracking.Track(1, pbox(300, 300)[:4], 0.0)
        old.loiter_last_seen = 10.0
        engine._relink_loiter_stamps([old], 10.0)
        stranger = tracking.Track(2, pbox(900, 300)[:4], 12.0)
        engine._relink_loiter_stamps([stranger], 12.0)
        self.assertIsNone(stranger.loiter_last_seen)


class FreezeNeedsKnifeTests(unittest.TestCase):
    """E12 only counts if the knife was seen during the freeze or <= 3 s before it."""

    def _frozen_pair(self):
        s = Scene()
        s.hold(5.0, persons=[pbox(600, 300), pbox(685, 300)])        # arm's length, still
        (a, b), state = next(iter(s.engine.pairs.items()))
        self.assertTrue(state.freeze_ok)
        return s, a, state

    def test_a_freeze_with_the_knife_during_it_counts(self):
        s, holder, state = self._frozen_pair()
        e14 = [state.freeze_start_ok + 1.0]
        cues = s.engine.holdup_pair_cues(holder, e14, s.t)
        self.assertIn("E12", cues)

    def test_a_knife_up_to_3_s_before_the_freeze_counts(self):
        s, holder, state = self._frozen_pair()
        e14 = [state.freeze_start_ok - 2.5]
        self.assertIn("E12", s.engine.holdup_pair_cues(holder, e14, s.t))

    def test_a_knife_long_before_the_freeze_does_not(self):
        s, holder, state = self._frozen_pair()
        e14 = [state.freeze_start_ok - 10.0]
        self.assertEqual(s.engine.holdup_pair_cues(holder, e14, s.t), {})

    def test_no_knife_at_all_does_not(self):
        s, holder, _ = self._frozen_pair()
        self.assertEqual(s.engine.holdup_pair_cues(holder, [], s.t), {})

    def test_a_knife_shortly_after_the_freeze_counts(self):
        # a knife is often drawn once the two are already standing off
        s, holder, state = self._frozen_pair()
        e14 = [state.freeze_last + 2.0]
        self.assertIn("E12", s.engine.holdup_pair_cues(holder, e14, s.t))

    def test_a_knife_well_after_the_freeze_does_not(self):
        s, holder, state = self._frozen_pair()
        e14 = [state.freeze_last + theft.E12_E14_TRAIL_SECONDS + 2.0]
        self.assertEqual(s.engine.holdup_pair_cues(holder, e14, s.t), {})

    def test_the_freeze_needs_2_seconds_before_v621(self):
        self.assertEqual(theft.FREEZE_SECONDS, 2.0)
        with mock.patch("core.vision.location_momentum.LOCATION_AWARE_MOMENTUM", False):
            s = Scene()
            s.hold(2.5, persons=[pbox(600, 300), pbox(685, 300)])
            self.assertTrue(next(iter(s.engine.pairs.values())).freeze_ok)

    def test_the_freeze_needs_3_seconds_in_v621(self):
        with mock.patch("core.vision.location_momentum.HOLDUP_V621_RULES", True):
            s = Scene()
            s.hold(2.5, persons=[pbox(600, 300), pbox(685, 300)])
            self.assertFalse(next(iter(s.engine.pairs.values())).freeze_ok)
            s.hold(1.0, persons=[pbox(600, 300), pbox(685, 300)])
            self.assertTrue(next(iter(s.engine.pairs.values())).freeze_ok)

    def test_the_freeze_is_2_seconds_by_default(self):
        s = Scene()
        s.hold(2.5, persons=[pbox(600, 300), pbox(685, 300)])
        self.assertTrue(next(iter(s.engine.pairs.values())).freeze_ok)

    def test_the_freeze_speed_limit_is_0_3(self):
        # speeds of 0.2-0.3 (gestures, sway) used to break the freeze
        self.assertEqual(theft.FREEZE_SPEED, 0.3)

    def test_a_pair_moving_fast_is_not_frozen(self):
        # both walking together at about one body height per second: close, but not still
        s = Scene()
        for i in range(60):
            s.step(persons=[pbox(600 + i * 10, 300), pbox(685 + i * 10, 300)])
        self.assertFalse(next(iter(s.engine.pairs.values())).freeze_ok)

    def test_a_knife_after_the_freeze_ended_does_not(self):
        s, holder, state = self._frozen_pair()
        for i in range(120):                                  # the pair breaks up and walks off
            s.step(persons=[pbox(600 - i * 6, 300), pbox(685 + i * 6, 300)])
        e14 = [s.t]                                           # a knife only now
        self.assertEqual(s.engine.holdup_pair_cues(holder, e14, s.t), {})


class KnifeConfirmationTests(unittest.TestCase):
    def test_the_knife_confirms_sooner_than_other_objects(self):
        from core.management.commands._incidents import IncidentMixin
        cmd = IncidentMixin()
        cmd.confirm_seconds = 2.0
        self.assertEqual(cmd._confirm_seconds_for("knife"), momentum.KNIFE_CONFIRM_SECONDS)
        self.assertEqual(cmd._confirm_seconds_for("Knife"), momentum.KNIFE_CONFIRM_SECONDS)
        self.assertEqual(cmd._confirm_seconds_for("cigarette"), 2.0)
        self.assertEqual(cmd._confirm_seconds_for("bottle"), 2.0)

    def test_it_is_never_longer_than_the_setting(self):
        from core.management.commands._incidents import IncidentMixin
        cmd = IncidentMixin()
        cmd.confirm_seconds = 0.3
        self.assertEqual(cmd._confirm_seconds_for("knife"), 0.3)

    def test_a_persistence_burst_is_remembered_for_3_seconds(self):
        self.assertEqual(watch_thief.KNIFE_PERSIST_MEMORY_SECONDS, 3.0)
        self.assertGreater(watch_thief.KNIFE_PERSIST_MEMORY_SECONDS, momentum.KNIFE_CONFIRM_SECONDS)


class ApproachThenFreezeTests(unittest.TestCase):
    def _scene(self, wait_before_freeze=0.0):
        s = Scene()
        s.hold(3.0, persons=[pbox(600, 300), pbox(1000, 300)])       # far apart (> 3 heights)
        ax, lx = 1000.0, 600.0
        while ax - lx > 85:                                          # E11: close in fast
            ax -= 12.6
            s.step(persons=[pbox(lx, 300), pbox(ax, 300)])
        if wait_before_freeze:
            # stay in motion (not frozen) but close, for a while before settling
            for i in range(int(wait_before_freeze / DT)):
                lx2 = lx + 12 * math.sin(i * 0.9)
                s.step(persons=[pbox(lx2, 300), pbox(ax, 300)])
        s.hold(5.0, persons=[pbox(lx, 300), pbox(ax, 300)])          # E12: freeze
        return s

    def test_a_rapid_approach_followed_by_a_freeze_is_the_sequence(self):
        s = self._scene()
        holder, state = next((a, st) for (a, b), st in s.engine.pairs.items())
        self.assertTrue(state.seq_ok)
        e14 = [state.freeze_start_ok]
        cues = s.engine.holdup_pair_cues(holder, e14, s.t)
        self.assertEqual(set(cues), {"E12", "E11E12"})

    def test_a_freeze_long_after_the_approach_is_not(self):
        s = self._scene(wait_before_freeze=8.0)
        state = next(iter(s.engine.pairs.values()))
        self.assertFalse(state.seq_ok)

    def test_no_approach_means_no_sequence(self):
        s = Scene()
        s.hold(6.0, persons=[pbox(600, 300), pbox(685, 300)])
        state = next(iter(s.engine.pairs.values()))
        self.assertTrue(state.freeze_ok)
        self.assertFalse(state.seq_ok)


if __name__ == "__main__":
    unittest.main()


class FrozenPairV621Tests(unittest.TestCase):
    """v6.2.1: E12 needs the pair close AND slow for 3 s; heavily overlapping frames are skipped.
    (These rules are behind HOLDUP_V621_RULES, off by default, so each test switches them on.)"""

    def setUp(self):
        patcher = mock.patch("core.vision.location_momentum.HOLDUP_V621_RULES", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def freeze_ok(self, s):
        return any(st.freeze_ok for st in s.engine.pairs.values())

    def test_a_walk_past_is_not_a_frozen_pair(self):
        s = Scene()
        for i in range(30):                                     # approach from 3 body heights away
            s.step(persons=[pbox(300 + i * 10, 300), pbox(900 - i * 10, 300)])
        s.hold(1.0, persons=[pbox(600, 300), pbox(685, 300)])   # about 1 s close and still
        for i in range(30):                                     # and walk off apart
            s.step(persons=[pbox(600 - i * 10, 300), pbox(685 + i * 10, 300)])
        self.assertFalse(self.freeze_ok(s))
        self.assertEqual(s.of_kind("holdup"), [])

    def test_two_people_close_and_still_for_4_seconds_is_a_frozen_pair(self):
        s = Scene()
        s.hold(4.0, persons=[pbox(600, 300), pbox(685, 300)])
        self.assertTrue(self.freeze_ok(s))

    @staticmethod
    def _pair_state():
        class T:
            def __init__(self, box):
                self.box = box

            def heading(self, now):
                return None
        return theft.PairState((1, 2), 0.0), T

    def _feed(self, st, T, t0, seconds, boxes):
        n = int(round(seconds / 0.1))
        for i in range(n):
            now = t0 + (i + 1) * 0.1
            st.update(T(boxes[0]), T(boxes[1]), 0.8, 0.0, 0.0, now, theft.REACH_NORM)
        return t0 + n * 0.1

    def test_overlap_frames_do_not_count_toward_the_3_seconds(self):
        st, T = self._pair_state()
        clean = ((570, 250, 630, 350), (655, 250, 715, 350))
        overlap = ((570, 250, 630, 350), (590, 250, 650, 350))          # IoU 0.5 > 0.4
        t = self._feed(st, T, 0.0, 1.0, clean)
        t = self._feed(st, T, t, 3.0, overlap)                           # skipped: not counted
        t = self._feed(st, T, t, 1.0, clean)
        self.assertFalse(st.freeze_ok)                                   # ~2 s of clean freezing in 5 s of wall time
        self._feed(st, T, t, 1.2, clean)
        self.assertTrue(st.freeze_ok)

    def test_overlap_frames_do_not_reset_it_either(self):
        st, T = self._pair_state()
        clean = ((570, 250, 630, 350), (655, 250, 715, 350))
        overlap = ((570, 250, 630, 350), (590, 250, 650, 350))
        t = self._feed(st, T, 0.0, 2.0, clean)
        t = self._feed(st, T, t, 0.5, overlap)
        self.assertIsNotNone(st.freeze_since)                            # not reset by the skipped frames
        self._feed(st, T, t, 1.2, clean)
        self.assertTrue(st.freeze_ok)

    def test_switch_off_restores_the_old_2_second_hold(self):
        with mock.patch("core.vision.location_momentum.LOCATION_AWARE_MOMENTUM", False):
            s = Scene()
            s.hold(2.5, persons=[pbox(600, 300), pbox(685, 300)])
            self.assertTrue(self.freeze_ok(s))
