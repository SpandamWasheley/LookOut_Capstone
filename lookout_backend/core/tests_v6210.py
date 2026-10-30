"""v6.2.10: scene-baseline seed, zoomed pose -> gesture counter (+ min gap, stationary switch), E20 only with a clip time."""
import datetime
import unittest
from unittest import mock

import numpy as np

from core.management.commands import watch_thief
from core.management.commands.watch_smoking import Command as Smoking
from core.vision import location_momentum as lm
from core.vision import scoring, theft, tracking


def track(tid=1, box=(100, 100, 140, 300)):
    t = tracking.Track(tid, box, 0.0)
    t.last_seen = 0.0
    return t


def walk(t, secs, step_px=0.0, dt=0.05, t0=0.0):
    """Feeds `secs` of video time to the track, moving step_px per frame to the right."""
    n = int(round(secs / dt))
    x1, y1, x2, y2 = t.box
    for i in range(n + 1):
        now = t0 + i * dt
        t.box = (x1 + i * step_px, y1, x2 + i * step_px, y2)
        t.last_seen = now
        t.observe(now)
    return t0 + n * dt


class SceneBaselineSeed(unittest.TestCase):
    def test_a_one_sample_artifact_cannot_seed_the_baseline(self):
        # two trail points 1 ms apart and 8 px apart on a 200 px person = 40 heights/s: the clip's real seed
        t = track()
        t.observe(0.0)
        t.box = (108, 100, 148, 300)
        t.last_seen = 0.001
        t.observe(0.001)
        base = tracking.SceneBaseline()
        base.update([t], 0.001)
        self.assertIsNone(base.value)                          # nothing seeded
        self.assertEqual(base.reference, tracking.MIN_SCENE_SPEED)

    def test_the_old_behaviour_is_one_switch_away(self):
        t = track()
        t.observe(0.0)
        t.box = (108, 100, 148, 300)
        t.last_seen = 0.06
        t.observe(0.06)
        with mock.patch.object(tracking, "SCENE_BASELINE_MIN_TRAIL_S", 0.0):
            base = tracking.SceneBaseline()
            base.update([t], 0.06)
        self.assertIsNotNone(base.value)

    def test_walkers_with_a_real_trail_set_the_baseline_and_read_as_moving(self):
        a, b = track(1), track(2, (300, 100, 340, 300))
        for tr in (a, b):
            end = walk(tr, 1.0, step_px=7.0)                     # 7 px / 0.05 s = 140 px/s = 0.7 heights/s
        base = tracking.SceneBaseline()
        base.update([a, b], end)
        self.assertAlmostEqual(base.reference, 0.7, delta=0.1)
        self.assertGreater(base.norm(a, end), 0.8)               # moving (> PASS_MOVING_SPEED 0.3), not 0.08

    def test_a_clip_starting_with_a_blip_does_not_blind_the_walker_check(self):
        base = tracking.SceneBaseline()
        blip = track(9)
        blip.observe(0.0)
        blip.box = (110, 100, 150, 300)
        blip.last_seen = 0.001
        blip.observe(0.001)
        base.update([blip], 0.001)
        a = track(1)
        end = walk(a, 3.0, step_px=7.0)
        base.update([a], end)
        self.assertLess(base.reference, 1.0)                     # was ~13, decaying over a minute
        self.assertGreater(base.norm(a, end), 0.3)

    def test_speed_is_zero_when_the_samples_are_closer_than_50_ms(self):
        t = track()
        t.observe(0.0)
        t.box = (108, 100, 148, 300)
        t.last_seen = 0.02
        t.observe(0.02)
        self.assertEqual(t.speed(0.02), 0.0)
        with mock.patch.object(tracking, "SPEED_MIN_DT", 0.0):
            self.assertGreater(t.speed(0.02), 0.0)


def kp(ratio):
    """17x3 keypoints: nose at (100, 40), shoulders 80 px apart, closest wrist `ratio` shoulder-widths from the nose."""
    k = np.zeros((17, 3), dtype=float)
    k[0] = (100, 40, 0.9)
    k[1], k[2] = (90, 30, 0.9), (110, 30, 0.9)               # both eyes visible: the person faces the camera
    k[5], k[6] = (60, 80, 0.9), (140, 80, 0.9)
    k[9] = (100 + ratio * 80, 40, 0.9)
    k[10] = (140, 200, 0.2)
    return k


class GestureFromZoom(unittest.TestCase):
    def setUp(self):
        self.cmd = Smoking()
        self.cmd.puff_window = 300.0
        self.cmd.stdout = mock.Mock()
        self.t = track()
        self.frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
        self.now = 0.0

    def feed(self, ratio, whole=()):
        zoom = mock.patch.object(lm, "ensure_keypoints", return_value=kp(ratio))
        poses = mock.patch.object(lm, "shared_poses", return_value=list(whole))
        with zoom, poses:
            self.cmd._feed_pose(self.frame, [self.t], self.now)
        self.now += 0.05

    def raise_and_drop(self, gap=0.6):
        # a held raise: 15 samples 0.05 s apart = 0.7 s at the face (the apex hold is 0.6 s), then the hand drops
        for r in [2.0] + [0.5] * 15 + [2.0]:
            self.feed(r)
        self.now += gap

    def test_a_track_the_whole_frame_pass_missed_is_counted_from_its_zoomed_pose(self):
        for _ in range(3):
            self.raise_and_drop(gap=1.2)
        self.assertEqual(self.t.gesture_count(self.now), 3)
        self.assertGreater(self.cmd.stats["gesture counter fed by zoomed pose"], 0)

    def test_the_switch_restores_the_old_behaviour(self):
        with mock.patch.object(lm, "GESTURE_FROM_ZOOM", False):
            for _ in range(3):
                self.raise_and_drop(gap=1.2)
        self.assertEqual(self.t.gesture_count(self.now), 0)

    def test_a_track_with_a_whole_frame_pose_does_not_use_the_zoom(self):
        whole = [(self.t.box, kp(2.0))]
        with mock.patch.object(lm, "ensure_keypoints") as ek:
            with mock.patch.object(lm, "shared_poses", return_value=whole):
                self.cmd._feed_pose(self.frame, [self.t], 0.0)
            ek.assert_not_called()

    def test_two_cycles_inside_one_second_are_one_gesture(self):
        # each raise is held 0.7 s (the apex hold is 0.6 s); the second drop comes 0.7 s after the first
        seq = ((0.0, 0.5), (0.7, 0.5), (0.8, 2.0), (0.9, 0.5), (1.6, 0.5), (1.7, 2.0),
               (3.0, 0.5), (3.7, 0.5), (3.8, 2.0))
        for now, r in seq:
            self.t.update_gesture(r, now)
        self.assertEqual(self.t.gesture_count(4.0), 2)           # 0.8 and 3.8; the 1.7 one is a re-dip
        with mock.patch.object(tracking, "GESTURE_MIN_GAP_SECONDS", 0.0):
            u = track(2)
            for now, r in seq[:6]:
                u.update_gesture(r, now)
            self.assertEqual(u.gesture_count(2.0), 2)

    def test_the_vaper_pattern_reaches_the_puff_only_threshold(self):
        # 3 raises 1.2 s apart inside the 5 minute window = the puff_pattern cue (>= 3) -> a tagged puff-only Possible
        for _ in range(3):
            self.raise_and_drop(gap=1.2)
        cues = {"gesture", "puffs", "puff_pattern"}
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, cues, object_on=False)
        self.assertTrue(s.puff_only)
        self.assertEqual(s.level, scoring.WARNING)


class StationarySwitch(unittest.TestCase):
    def test_mostly_stationary(self):
        still = track()
        walk(still, 3.0, step_px=0.0)
        self.assertTrue(still.mostly_stationary(3.0))
        mover = track(2)
        end = walk(mover, 3.0, step_px=7.0)
        self.assertFalse(mover.mostly_stationary(end))
        fresh = track(3)
        walk(fresh, 0.5)
        self.assertFalse(fresh.mostly_stationary(0.5))           # too short a trail to say

    def test_gestures_are_tagged_still_or_moving_and_the_count_can_ask_for_still_only(self):
        t = track()
        t.update_gesture(0.5, 0.0, still=True)
        t.update_gesture(0.5, 0.7, still=True)
        t.update_gesture(2.0, 0.8, still=True)                   # still
        t.update_gesture(0.5, 2.0, still=False)
        t.update_gesture(0.5, 2.7, still=False)
        t.update_gesture(2.0, 2.8, still=False)                  # walking
        self.assertEqual(t.gesture_count(3.0), 2)
        self.assertEqual(t.gesture_count(3.0, still_only=True), 1)

    def test_feed_pose_tags_a_walking_person_as_not_still(self):
        cmd = Smoking()
        cmd.puff_window = 300.0
        cmd.stdout = mock.Mock()
        t = track()
        end = walk(t, 3.0, step_px=7.0)
        frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
        now = end
        for r in [0.5] * 15 + [2.0]:                             # held at the face 0.7 s, then dropped
            with mock.patch.object(lm, "ensure_keypoints", return_value=kp(r)), mock.patch.object(lm, "shared_poses", return_value=[]):
                cmd._feed_pose(frame, [t], now)
            now += 0.05
        self.assertEqual(t.gesture_count(now), 1)
        self.assertEqual(t.gesture_count(now, still_only=True), 0)

    def test_the_switch_is_off_by_default(self):
        self.assertFalse(lm.PUFF_ONLY_REQUIRE_STATIONARY)


class E20NeedsAClipTime(unittest.TestCase):
    def thief(self, source, clock):
        t = watch_thief.Command()
        t._source_path = source
        t.clock_start = clock
        t.stdout = mock.Mock()
        t.style = mock.MagicMock()
        return t

    def test_a_file_without_a_clock_has_no_time_and_so_no_e20(self):
        t = self.thief("clip.mp4", None)
        self.assertIsNone(t._clock_now(12.0))
        self.assertEqual(scoring.manila_time_bonus(t._clock_now(12.0)), 0.0)
        self.assertEqual(scoring.manila_time_multiplier(t._clock_now(12.0)), 1.0)
        self.assertFalse(watch_thief._is_night(None))
        self.assertEqual(t.stats["E20 disabled: no clip time"], 1)   # logged once, not per frame
        t._clock_now(13.0)
        self.assertEqual(t.stats["E20 disabled: no clip time"], 1)
        self.assertEqual(t.stdout.write.call_count, 1)

    def test_a_file_with_a_clock_uses_clip_time_plus_position(self):
        t = self.thief("clip.mp4", datetime.datetime(2026, 8, 24, 16, 17))
        self.assertEqual(t._clock_now(60.0), datetime.datetime(2026, 8, 24, 16, 18))
        self.assertEqual(scoring.manila_time_points(t._clock_now(0.0)), 10)

    def test_live_keeps_the_wall_clock(self):
        t = self.thief(None, None)
        got = t._clock_now(5.0)
        self.assertIsNotNone(got)
        self.assertLess(abs((datetime.datetime.now(got.tzinfo) - got).total_seconds()), 5)

    def test_the_switch_restores_the_old_wall_clock_for_files(self):
        t = self.thief("clip.mp4", None)
        with mock.patch.object(lm, "E20_REQUIRES_CLIP_TIME", False):
            self.assertIsNotNone(t._clock_now(1.0))

    def test_the_knife_evidence_has_no_e20_without_a_clip_time(self):
        t = self.thief("clip.mp4", None)
        t.ablate = set()
        t.engine = None
        t._frame_tracks = []
        t._e14_times = {}
        t.cue_hold = 0
        ev = t._knife_evidence(track(), "knife", 1.0, 5.0, (0, 0, 10, 10))
        self.assertNotIn("E20", ev.cues)


class _T:
    box = (0, 0, 60, 100)

    def heading(self, now):
        return None


class HoldupAbsoluteSpeeds(unittest.TestCase):
    """E12's walking tests judge raw box-heights/s, not the baseline-relative speed (which is huge in a two-person scene)."""

    def run_pair(self, victim, robber, seconds=3.0, floor_norm=10.0):
        st = theft.PairState((1, 2), 0.0)
        now = 0.0
        while now < seconds:
            now += 0.1
            # normalised speeds as the floor-level baseline reports them: raw x 10
            st.update(_T(), _T(), 0.3, victim * floor_norm, robber * floor_norm, now, theft.REACH_NORM, sa=victim, sb=robber)
        return st

    def test_a_swaying_victim_and_a_still_robber_are_a_standoff(self):
        self.assertTrue(self.run_pair(0.15, 0.05).freeze_ok)

    def test_two_people_walking_together_are_not(self):
        self.assertFalse(self.run_pair(0.6, 0.7).freeze_ok)

    def test_the_old_normalised_rule_blocks_the_same_standoff(self):
        # the failure this switch fixes: raw 0.15 reads as 1.5 against the 0.1 floor, so both look like they are walking
        with mock.patch.object(lm, "HOLDUP_ABSOLUTE_SPEEDS", False):
            self.assertFalse(self.run_pair(0.15, 0.15).freeze_ok)

    def test_the_engine_passes_the_raw_speeds(self):
        calls = []
        orig = theft.PairState.update

        def spy(self, *a, **k):
            calls.append(k)
            return orig(self, *a, **k)
        eng = theft.TheftEngine()
        ta, tb = track(1, (100, 100, 140, 300)), track(2, (150, 100, 190, 300))
        for tr in (ta, tb):
            walk(tr, 1.0, step_px=0.0)
        with mock.patch.object(theft.PairState, "update", spy):
            eng.update([ta, tb], [], [], [], 1.0)
        self.assertTrue(calls and "sa" in calls[0] and "sb" in calls[0])


if __name__ == "__main__":
    unittest.main()
