"""Improper disposal — the static-object rule and the zero-shot confirmation.

Stage 1 is tested on synthetic frames rather than footage, so these run
without a GPU, a camera or the YOLO-World weights: the thing under test is the
dual-background RULE (does a moving object get ignored and a dumped one
survive?), and a drawn rectangle exercises that honestly.

Stage 2 is tested against a fake model object, because what matters is the
DECISION — garbage must outscore every reject class — not ultralytics' own
inference, which is not ours to test.
"""

import inspect
import json
import os
import pathlib
import shutil
import sys
import tempfile
import types
from unittest import mock

import numpy as np
from django.test import TestCase

from django.core.management import call_command

from core import descriptions
from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import disposal
from core.vision import scoring


def scene(width=640, height=360, shade=90):
    """A plain, slightly textured background. Pure flat grey makes MOG2's
    variance degenerate, so a little noise keeps it realistic."""
    rng = np.random.default_rng(0)
    frame = np.full((height, width, 3), shade, dtype=np.uint8)
    noise = rng.integers(-4, 5, size=frame.shape, dtype=np.int16)
    return np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def with_object(frame, x, y, w=60, h=45, shade=235):
    out = frame.copy()
    out[y:y + h, x:x + w] = shade
    return out


def warm_up(detector, frames=80, step=0.1, start=0.0):
    """Feed the empty scene until the background is learned."""
    now = start
    for _ in range(frames):
        detector.update(scene(), now)
        now += step
    return now


class StaticObjectDetectorTests(TestCase):
    def test_nothing_is_reported_while_the_background_is_still_being_learned(self):
        det = disposal.StaticObjectDetector()
        out = det.update(scene(), 0.0)
        self.assertTrue(det.warming)
        self.assertEqual(out, [])

    def test_warming_ends_after_the_warmup_window(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        self.assertGreater(now, disposal.WARMUP_SECONDS)
        self.assertFalse(det.warming)

    def test_an_object_that_appears_and_stays_becomes_a_candidate(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(12):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.1
        self.assertTrue(det.candidates, "a dumped object should be tracked")

    def test_a_candidate_accrues_still_time_across_frames(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        first = now
        for _ in range(20):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.2
        cand = det.candidates[0]
        # Measured from when it was FIRST seen, not from the latest frame.
        self.assertGreater(cand.still_for(now), 1.0)
        self.assertGreaterEqual(cand.first, first)

    def test_a_candidate_is_dropped_once_it_goes_unseen_past_the_grace(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(12):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.1
        self.assertTrue(det.candidates)
        # The object is gone (picked up); nothing matches, so after the grace
        # window the candidate must not linger accruing still-time forever.
        now += disposal.CANDIDATE_GRACE + 1.0
        det.update(scene(), now)
        self.assertEqual(det.candidates, [])

    def test_due_for_check_waits_for_the_static_requirement(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(12):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.1
        self.assertTrue(det.candidates)
        # Nothing has been still for 60s, so nothing is due.
        self.assertEqual(det.due_for_check(now, static_seconds=60.0), [])
        # With a requirement it HAS met, it is due.
        self.assertTrue(det.due_for_check(now, static_seconds=0.2))

    def test_a_confirmed_candidate_is_never_offered_again(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(12):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.1
        cand = det.candidates[0]
        cand.confirmed = True
        self.assertEqual(det.due_for_check(now, static_seconds=0.2), [])

    def test_a_rejected_candidate_is_re_offered_only_after_the_recheck_window(self):
        """A rejected candidate gets another look — stage 2 saw one crop at one
        moment, and a bag half-occluded by a passer-by deserves a second."""
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(12):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.1
        cand = det.candidates[0]
        cand.rejected, cand.last_check = True, now
        self.assertEqual(det.due_for_check(now, static_seconds=0.2), [])
        later = now + disposal.RECHECK_SECONDS + 0.1
        cand.last = later          # keep it alive past the grace window
        self.assertIn(cand, det.due_for_check(later, static_seconds=0.2))


def dumped(detector, now, x=300, y=180, frames=12, step=0.1):
    """Drop an object into the scene and leave it until it is a candidate."""
    for _ in range(frames):
        detector.update(with_object(scene(), x, y), now)
        now += step
    return now


def forget(detector, cand, now, extra=1.0):
    """Let stage 1 lose this candidate's blob, the way the slow model does.

    One update after a jump past the grace window, which is the same shape as
    the drop test: feeding more empty frames would start teaching the
    background models the object's REMOVAL, and the ghost blob that produces is
    a different phenomenon from the absorption this is standing in for.
    """
    now += disposal.CANDIDATE_GRACE + extra
    detector.update(scene(), now)
    return now


class PinnedCandidateTests(TestCase):
    """What happens when the SLOW model absorbs the object too.

    This is the hole these tests exist for: LONG_LR only makes absorption slow,
    not impossible, so a bag that is genuinely still lying there eventually
    stops producing a blob at all. Before the pin, `candidate_grace` then
    dropped it two seconds later — the id vanished, the still-timer restarted
    and a weak match lost the recheck it was queued for. Stage 2 can still see
    the bag, so stage 2 is what holds it.
    """

    def test_a_confirmed_candidate_is_pinned_instead_of_dropped(self):
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = dumped(det, warm_up(det))
        cand = det.candidates[0]
        cand.confirmed = True
        now = forget(det, cand, now)
        self.assertIn(cand, det.candidates, "a confirmed object must survive")
        self.assertTrue(cand.pinned)

    def test_a_weak_candidate_is_pinned_so_its_recheck_still_happens(self):
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = dumped(det, warm_up(det))
        cand = det.candidates[0]
        cand.confirmed, cand.weak, cand.score = True, True, 0.11
        forget(det, cand, now)
        self.assertIn(cand, det.candidates)
        self.assertTrue(cand.pinned)

    def test_an_unchecked_candidate_is_still_dropped(self):
        """Stage 2 has never looked at it, so there is nothing to hold onto."""
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = dumped(det, warm_up(det))
        cand = det.candidates[0]
        self.assertFalse(cand.engaged)
        forget(det, cand, now)
        self.assertEqual(det.candidates, [])

    def test_a_rejected_candidate_is_not_pinned(self):
        """A reject verdict is an answer, not a maybe — do not keep paying for it."""
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = dumped(det, warm_up(det))
        cand = det.candidates[0]
        cand.rejected, cand.label = True, "dog 44%"
        forget(det, cand, now)
        self.assertEqual(det.candidates, [])

    def test_a_pinned_candidate_keeps_its_id_and_its_still_time(self):
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = dumped(det, warm_up(det))
        cand = det.candidates[0]
        cand.confirmed = True
        was_id, was_first, was_box = cand.id, cand.first, cand.box
        now = forget(det, cand, now)
        self.assertEqual(cand.id, was_id)
        self.assertEqual(cand.first, was_first)
        self.assertEqual(cand.box, was_box, "the pin holds the LAST known box")
        # The whole point: dwell keeps accruing while it is held, so the card
        # reads "still there for 4 minutes" rather than restarting at zero.
        self.assertGreater(cand.still_for(now), disposal.CANDIDATE_GRACE)

    def test_a_pin_expires_at_the_cap(self):
        """An unobservable claim must not be unfalsifiable."""
        det = disposal.StaticObjectDetector(suspect_window=False, pin_max_seconds=5.0)
        now = dumped(det, warm_up(det))
        det.candidates[0].confirmed = True
        now = forget(det, det.candidates[0], now)
        self.assertTrue(det.candidates)
        det.update(scene(), now + 6.0)
        self.assertEqual(det.candidates, [])

    def test_a_returning_blob_unpins_the_candidate(self):
        """Stage 1 can see it again, so stage 2 need not keep holding it."""
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = dumped(det, warm_up(det))
        cand = det.candidates[0]
        cand.confirmed = True
        now = forget(det, cand, now)
        self.assertTrue(cand.pinned)
        cand.pin_misses = 1
        det.update(with_object(scene(), 300, 180), now + 0.1)
        self.assertFalse(cand.pinned)
        self.assertEqual(cand.pin_misses, 0)
        self.assertIn(cand, det.candidates, "and it keeps its identity")


class PresenceCheckTests(TestCase):
    """The re-ask that decides whether a pinned object is still on the pavement."""

    def pinned_candidate(self, **kwargs):
        det = disposal.StaticObjectDetector(suspect_window=False, **kwargs)
        now = dumped(det, warm_up(det))
        det.candidates[0].confirmed = True
        now = forget(det, det.candidates[0], now)
        return det, det.candidates[0], now

    def test_a_newly_pinned_candidate_is_due_immediately(self):
        det, cand, now = self.pinned_candidate()
        self.assertIn(cand, det.due_for_presence(now))

    def test_an_unpinned_candidate_is_never_offered(self):
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = dumped(det, warm_up(det))
        det.candidates[0].confirmed = True
        self.assertEqual(det.due_for_presence(now), [])

    def test_the_interval_throttles_the_re_ask(self):
        det, cand, now = self.pinned_candidate()
        det.record_presence(cand, True, now)
        self.assertEqual(det.due_for_presence(now + 1.0, interval=15.0), [])
        self.assertIn(cand, det.due_for_presence(now + 15.1, interval=15.0))

    def test_an_occluded_pinned_candidate_is_not_asked(self):
        """The crop would be of whatever is standing in front of it."""
        det, cand, now = self.pinned_candidate()
        cand.occluded_since = now
        self.assertEqual(det.due_for_presence(now), [])

    def test_a_pinned_candidate_is_not_offered_to_due_for_check_as_well(self):
        det, cand, now = self.pinned_candidate()
        # Cleared so it is the PIN that skips it, not the confirmed short-circuit.
        cand.confirmed = False
        self.assertEqual(det.due_for_check(now, static_seconds=0.2), [])

    def test_a_present_answer_keeps_it_and_clears_the_misses(self):
        det, cand, now = self.pinned_candidate()
        cand.pin_misses = 1
        self.assertTrue(det.record_presence(cand, True, now))
        self.assertEqual(cand.pin_misses, 0)
        self.assertIn(cand, det.candidates)
        self.assertEqual(cand.last_presence_check, now)

    def test_one_missed_answer_does_not_release_the_pin(self):
        """A single crop can be spoiled by somebody walking through it."""
        det, cand, now = self.pinned_candidate()
        self.assertTrue(det.record_presence(cand, False, now))
        self.assertIn(cand, det.candidates)
        self.assertEqual(cand.pin_misses, 1)

    def test_the_pin_is_released_at_the_miss_limit(self):
        det, cand, now = self.pinned_candidate(pin_miss_limit=2)
        det.record_presence(cand, False, now)
        self.assertFalse(det.record_presence(cand, False, now + 15.0))
        self.assertNotIn(cand, det.candidates)

    def test_a_present_answer_resets_the_streak_rather_than_the_total(self):
        """Misses must be CONSECUTIVE, or a long hold eventually self-releases."""
        det, cand, now = self.pinned_candidate(pin_miss_limit=2)
        det.record_presence(cand, False, now)
        det.record_presence(cand, True, now + 15.0)
        self.assertTrue(det.record_presence(cand, False, now + 30.0))
        self.assertIn(cand, det.candidates)


class PinWiringTests(TestCase):
    """The command has to actually drive the pin, not just the CV layer."""

    def test_the_command_exposes_a_presence_interval_flag(self):
        from core.management.commands import watch_disposal
        parser = watch_disposal.Command().create_parser("manage.py", "watch_disposal")
        flags = {a.dest for a in parser._actions}
        self.assertIn("presence_seconds", flags)

    def test_the_command_runs_the_presence_loop(self):
        from core.management.commands import watch_disposal
        source = inspect.getsource(watch_disposal.Command._run_stream)
        self.assertIn("due_for_presence", source)
        self.assertIn("record_presence", source)

    def test_both_check_paths_share_one_stage_two_call(self):
        """Two copies would be two places for the crop or the floor to drift."""
        from core.management.commands import watch_disposal
        source = inspect.getsource(watch_disposal.Command._run_stream)
        self.assertEqual(source.count("_ask_stage2"), 2)
        self.assertNotIn("disposal_cv.confirm(", source)

    def test_a_late_confirmation_goes_through_the_same_alert_path(self):
        from core.management.commands import watch_disposal
        self.assertTrue(hasattr(watch_disposal.Command, "_alert"))
        source = inspect.getsource(watch_disposal.Command._alert)
        self.assertIn("cooldown.blocks", source)
        self.assertIn("_create_alert", source)


class CropTests(TestCase):
    def test_a_box_is_mapped_back_to_full_resolution_and_padded(self):
        frame = scene(1920, 1080)
        # A box found at PROC_WIDTH 960 means scale 0.5 against a 1920 frame.
        crop = disposal.crop_full_res(frame, (100, 100, 60, 40), scale=0.5)
        # Padding makes the crop strictly larger than the mapped 120x80 box.
        self.assertGreater(crop.shape[1], 120)
        self.assertGreater(crop.shape[0], 80)

    def test_a_tiny_box_still_yields_at_least_the_minimum_crop(self):
        frame = scene(1920, 1080)
        crop = disposal.crop_full_res(frame, (500, 500, 4, 4), scale=1.0)
        self.assertGreaterEqual(min(crop.shape[:2]), disposal.MIN_CROP - 1)

    def test_a_box_at_the_frame_edge_is_clamped_not_wrapped(self):
        frame = scene(640, 360)
        crop = disposal.crop_full_res(frame, (0, 0, 40, 30), scale=1.0)
        self.assertGreater(crop.size, 0)


ONE_BOX = (0.0, 0.0, 10.0, 10.0)


class FakeBoxes:
    def __init__(self, pairs, boxes=None):
        self.cls = _Listy(float(c) for c, _ in pairs)
        self.conf = _Listy(float(s) for _, s in pairs)
        # Every detection on ONE box unless a test says otherwise: a stage-2
        # crop is a close-up of a single object, so that is the realistic
        # default and it is what exercises combine_garbage.
        self.xyxy = _Listy(boxes or [ONE_BOX] * len(pairs))


class _Listy(list):
    def tolist(self):
        return list(self)


class FakeResult:
    def __init__(self, pairs, names, boxes=None):
        self.boxes = FakeBoxes(pairs, boxes)
        self.names = names


class FakeWorld:
    """Stands in for YOLOWorld. `pairs` are (class_id, score)."""

    def __init__(self, pairs, boxes=None):
        self.pairs = pairs
        self.boxes = boxes
        names = {i: n for i, n in enumerate(disposal.PROMPTS + disposal.REJECT)}
        self.names = names

    def predict(self, crop, **kwargs):
        return [FakeResult(self.pairs, self.names, self.boxes)]


class ConfirmTests(TestCase):
    """The decision, not the inference: garbage must BEAT every reject class."""

    def setUp(self):
        self.crop = scene(128, 128)
        self.garbage_ids = disposal.garbage_class_ids()
        # REJECT ids start at len(PROMPTS). Looked up BY NAME rather than by
        # position: this used to be len(PROMPTS) - 1, which silently became
        # 'pile of plastic bottles' the moment the prompt list grew.
        self.sack = disposal.PROMPTS.index("sack")
        self.person = len(disposal.PROMPTS)
        self.dog = len(disposal.PROMPTS) + 1

    def test_a_bag_alone_confirms(self):
        ok, (name, score), _ = disposal.confirm(
            FakeWorld([(0, 0.31)]), self.crop, self.garbage_ids)
        self.assertTrue(ok)
        self.assertEqual(name, "plastic bag")
        self.assertAlmostEqual(score, 0.31, places=5)

    def test_a_dog_scoring_higher_than_the_bag_is_rejected(self):
        """The whole point of stage 2. A bare confidence floor would pass this."""
        ok, _g, (r_name, _r) = disposal.confirm(
            FakeWorld([(0, 0.22), (self.dog, 0.55)]), self.crop, self.garbage_ids)
        self.assertFalse(ok)
        self.assertEqual(r_name, "dog")

    def test_a_bag_outscoring_a_person_confirms(self):
        ok, (name, _s), _r = disposal.confirm(
            FakeWorld([(self.sack, 0.48), (self.person, 0.20)]),
            self.crop, self.garbage_ids)
        self.assertTrue(ok)
        self.assertEqual(name, "sack")

    def test_a_tie_counts_as_garbage(self):
        """Stage 1 already established something was dumped and left, so a crop
        the model cannot separate is resolved in favour of the evidence."""
        ok, _g, _r = disposal.confirm(
            FakeWorld([(0, 0.30), (self.dog, 0.30)]), self.crop, self.garbage_ids)
        self.assertTrue(ok)

    def test_nothing_detected_is_not_garbage(self):
        ok, (name, score), _ = disposal.confirm(
            FakeWorld([]), self.crop, self.garbage_ids)
        self.assertFalse(ok)
        self.assertEqual((name, score), ("", 0.0))

    def test_only_reject_classes_is_not_garbage(self):
        ok, _g, (r_name, _s) = disposal.confirm(
            FakeWorld([(self.person, 0.9)]), self.crop, self.garbage_ids)
        self.assertFalse(ok)
        self.assertEqual(r_name, "person")

    def test_an_empty_crop_is_handled_without_calling_the_model(self):
        empty = np.zeros((0, 0, 3), dtype=np.uint8)
        ok, _g, _r = disposal.confirm(FakeWorld([(0, 0.9)]), empty, self.garbage_ids)
        self.assertFalse(ok)

    def test_garbage_ids_cover_exactly_the_prompt_classes(self):
        self.assertEqual(disposal.garbage_class_ids(), set(range(len(disposal.PROMPTS))))
        self.assertNotIn(self.person, disposal.garbage_class_ids())


class CombineGarbageTests(TestCase):
    """Nine prompts describe ONE object, so weak hits on it corroborate.

    The bug this closes: a real bag matching "plastic bag" 0.10, "garbage bag"
    0.09 and "sack" 0.08 was reported at 0.10 -- the strength of one witness
    out of three -- and fell under a 0.15 floor it should have cleared. The
    detector was penalising a bag for matching the prompt list too well.
    """

    BOX = (0.0, 0.0, 10.0, 10.0)
    FAR = (100.0, 100.0, 110.0, 110.0)

    def test_synonyms_on_one_object_corroborate(self):
        label, score = disposal.combine_garbage(
            [("plastic bag", 0.10, self.BOX),
             ("garbage bag", 0.09, self.BOX),
             ("sack", 0.08, self.BOX)])
        self.assertAlmostEqual(score, 1 - 0.90 * 0.91 * 0.92, places=6)
        self.assertGreater(score, disposal.CONFIRM_CONF,
                           "three weak witnesses should clear the default floor")
        self.assertEqual(label, "plastic bag", "the best single word wins")

    def test_a_single_hit_is_unchanged(self):
        """The no-regression guarantee: a clear match behaves exactly as it
        did before, so this cannot have moved the calibration of anything
        that was already confirming."""
        self.assertEqual(
            disposal.combine_garbage([("sack", 0.42, self.BOX)]),
            ("sack", 0.42))

    def test_a_prompt_cannot_corroborate_itself(self):
        """Duplicates of one class are deduplicated to their best. Otherwise
        a model that emitted the same class twice would manufacture
        confidence out of nothing."""
        _label, score = disposal.combine_garbage(
            [("plastic bag", 0.10, self.BOX), ("plastic bag", 0.09, self.BOX)])
        self.assertAlmostEqual(score, 0.10, places=6)

    def test_separate_objects_do_not_pool_their_evidence(self):
        """A bag at one end of the crop must not borrow from a box at the
        other -- that would be combining evidence about different things."""
        _label, score = disposal.combine_garbage(
            [("plastic bag", 0.10, self.BOX), ("sack", 0.09, self.FAR)])
        self.assertAlmostEqual(score, 0.10, places=6)

    def test_nothing_detected_is_zero(self):
        self.assertEqual(disposal.combine_garbage([]), ("", 0.0))

    def test_the_combination_can_never_reach_one(self):
        """Noisy-OR is bounded, so no number of weak hits fabricates certainty."""
        dets = [(f"p{i}", 0.3, self.BOX) for i in range(20)]
        self.assertLess(disposal.combine_garbage(dets)[1], 1.0)

    def test_combining_is_monotonic_in_the_evidence(self):
        weaker = disposal.combine_garbage(
            [("plastic bag", 0.10, self.BOX), ("sack", 0.05, self.BOX)])[1]
        stronger = disposal.combine_garbage(
            [("plastic bag", 0.10, self.BOX), ("sack", 0.12, self.BOX)])[1]
        self.assertGreater(stronger, weaker)


class IoUTests(TestCase):
    def test_identical_boxes_fully_overlap(self):
        self.assertEqual(disposal._iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)

    def test_disjoint_boxes_do_not_overlap(self):
        self.assertEqual(disposal._iou((0, 0, 10, 10), (20, 20, 30, 30)), 0.0)

    def test_touching_edges_are_not_an_overlap(self):
        self.assertEqual(disposal._iou((0, 0, 10, 10), (10, 0, 20, 10)), 0.0)

    def test_half_overlap(self):
        self.assertAlmostEqual(
            disposal._iou((0, 0, 10, 10), (5, 0, 15, 10)), 50 / 150, places=6)

    def test_a_degenerate_box_does_not_divide_by_zero(self):
        self.assertEqual(disposal._iou((0, 0, 0, 0), (0, 0, 10, 10)), 0.0)


class CombinedConfirmTests(TestCase):
    """The rule reaching confirm(), which is what actually decides an alert."""

    def test_three_weak_synonyms_now_confirm_at_the_default_floor(self):
        """The end-to-end point of the change: this crop used to report 10%
        and sit at Possible."""
        world = FakeWorld([(0, 0.10), (1, 0.09), (4, 0.08)])
        ok, best_g, _best_r = disposal.confirm(
            world, np.zeros((20, 20, 3), dtype=np.uint8), conf=0.15)
        self.assertTrue(ok)
        self.assertGreater(best_g[1], 0.15)

    def test_a_dog_still_wins_against_combined_garbage(self):
        """Combination must not let corroborated noise beat a clear reject."""
        dog_id = len(disposal.PROMPTS) + disposal.REJECT.index("dog")
        world = FakeWorld([(0, 0.10), (1, 0.09), (dog_id, 0.80)])
        ok, _best_g, best_r = disposal.confirm(
            world, np.zeros((20, 20, 3), dtype=np.uint8), conf=0.15)
        self.assertFalse(ok)
        self.assertEqual(best_r[0], "dog")

    def test_reject_classes_are_not_combined_with_each_other(self):
        """They are different OBJECTS, not different words for one. Two
        confused rejects must not pool into a rejection."""
        dog = len(disposal.PROMPTS) + disposal.REJECT.index("dog")
        bike = len(disposal.PROMPTS) + disposal.REJECT.index("bicycle")
        world = FakeWorld([(dog, 0.10), (bike, 0.09)])
        _ok, _best_g, best_r = disposal.confirm(
            world, np.zeros((20, 20, 3), dtype=np.uint8), conf=0.15)
        self.assertAlmostEqual(best_r[1], 0.10, places=6)

    def test_garbage_on_a_separate_box_does_not_pool(self):
        world = FakeWorld([(0, 0.10), (1, 0.09)],
                          boxes=[(0.0, 0.0, 10.0, 10.0),
                                 (100.0, 100.0, 110.0, 110.0)])
        _ok, best_g, _best_r = disposal.confirm(
            world, np.zeros((20, 20, 3), dtype=np.uint8), conf=0.15)
        self.assertAlmostEqual(best_g[1], 0.10, places=6)


class SpatialCooldownTests(TestCase):
    """Place-keyed, not id-keyed — the D4 lesson applied to this detector."""

    def test_the_same_spot_is_blocked_inside_the_window(self):
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((100.0, 100.0), 0.0)
        self.assertTrue(cd.blocks((110.0, 105.0), 30.0))

    def test_a_different_spot_is_not_blocked(self):
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((100.0, 100.0), 0.0)
        self.assertFalse(cd.blocks((400.0, 300.0), 30.0))

    def test_the_same_spot_is_allowed_once_the_window_lapses(self):
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((100.0, 100.0), 0.0)
        self.assertFalse(cd.blocks((100.0, 100.0), 121.0))

    def test_a_new_candidate_id_at_the_same_place_is_still_blocked(self):
        """The failure this exists to prevent: stage 1 drops a candidate after a
        couple of unseen seconds, and the replacement starts with a clean
        timer. An id-keyed cooldown would let one bag re-alert indefinitely."""
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((250.0, 180.0), 10.0)
        for t in (12.0, 20.0, 60.0, 119.0):
            self.assertTrue(cd.blocks((252.0, 181.0), t), f"should block at {t}s")


class DisposalSettingsTests(TestCase):
    def test_the_defaults_are_the_documented_ones(self):
        cfg = SystemSettings.load()
        self.assertTrue(cfg.disposal_enabled)
        self.assertEqual(cfg.disposal_confidence, 15)
        self.assertEqual(cfg.disposal_static_seconds, 2)
        self.assertEqual(cfg.disposal_cooldown, 120)

    def test_the_confidence_default_matches_the_vision_modules_floor(self):
        """Settings store a percent; the CV module stores a 0-1 fraction. If
        these drift, the dashboard and the detector disagree about the floor."""
        cfg = SystemSettings.load()
        self.assertAlmostEqual(cfg.disposal_confidence / 100.0,
                               disposal.CONFIRM_CONF, places=5)

    def test_the_static_default_matches_the_vision_module(self):
        cfg = SystemSettings.load()
        self.assertEqual(float(cfg.disposal_static_seconds), disposal.STATIC_SECONDS)

    def test_the_settings_endpoint_publishes_the_disposal_fields(self):
        from core.serializers import SystemSettingsSerializer
        data = SystemSettingsSerializer(SystemSettings.load()).data
        for field in ("disposal_enabled", "disposal_confidence",
                      "disposal_static_seconds", "disposal_cooldown"):
            self.assertIn(field, data)


class DisposalDescriptionTests(TestCase):
    """The officer-facing wording leads with LEFT BEHIND, not 'bag seen' — that
    is what the ordinance is about and what the static rule established."""

    def test_seconds_are_reported_in_seconds(self):
        text = descriptions.disposal("plastic bag", 8)
        self.assertIn("left behind", text)
        self.assertIn("8 s", text)

    def test_long_dwells_are_reported_in_minutes(self):
        text = descriptions.disposal("sack", 180)
        self.assertIn("3 min", text)

    def test_no_duration_still_reads_as_a_sentence(self):
        text = descriptions.disposal("garbage bag", 0)
        self.assertEqual(text, "Garbage bag left behind and unattended.")

    def test_a_missing_object_name_falls_back_to_garbage(self):
        self.assertTrue(descriptions.disposal(None, 0).startswith("Garbage"))

    def test_no_track_ids_or_scores_leak_into_the_wording(self):
        text = descriptions.disposal("plastic bag", 42)
        for leak in ("#", "track", "conf", "0."):
            self.assertNotIn(leak, text.lower())


class DisposalWiringTests(TestCase):
    def test_the_violation_type_is_seeded_green(self):
        from core.models import ViolationType
        from django.core.management import call_command
        call_command("seed_core", verbosity=0)
        vt = ViolationType.objects.get(code="disposal")
        self.assertEqual(vt.label, "Improper Disposal")
        self.assertEqual(vt.color.lower(), "#22c55e")

    def test_the_command_and_seed_core_agree_on_label_colour_and_icon(self):
        """get_or_create matches on CODE alone, so if watch_disposal's defaults
        and seed_core's row disagree, whichever ran first wins forever and the
        other's label/colour silently never applies."""
        from core.management.commands.seed_core import VIOLATION_TYPES
        seeded = {code: (label, color, icon)
                  for code, label, color, icon in VIOLATION_TYPES}
        self.assertEqual(seeded["disposal"], ("Improper Disposal", "#22c55e", "trash"))

        from core.management.commands import watch_disposal
        source = inspect.getsource(watch_disposal.Command.handle)
        for value in ("Improper Disposal", "#22c55e", "trash"):
            self.assertIn(value, source)

    def test_disposal_is_a_runnable_detection_type(self):
        from core.views import DETECTION_COMMANDS
        self.assertEqual(DETECTION_COMMANDS["disposal"], "watch_disposal")

    def test_disposal_is_never_gated_on_a_drawn_area(self):
        """An area NARROWS where rubbish is watched for; it is never required.

        This originally asserted disposal was not edge-capable at all. An area
        was added because on a 4MP frame the whole-frame rule raises a flood of
        noise blobs, and confining it to the spot that actually gets dumped on
        is the cheapest fix. The half that still matters is EDGE_REQUIRED: a
        run with no area drawn must keep working and watch everywhere, which is
        what disposal did before areas existed.
        """
        from core.views import EDGE_CAPABLE, EDGE_REQUIRED
        self.assertIn("disposal", EDGE_CAPABLE)
        self.assertNotIn("disposal", EDGE_REQUIRED)

    def test_the_command_is_importable_and_declares_its_flags(self):
        from core.management.commands import watch_disposal
        parser = watch_disposal.Command().create_parser("manage.py", "watch_disposal")
        flags = {a.dest for a in parser._actions}
        for expected in ("source", "camera", "confidence", "static_seconds",
                         "dry_run", "stride"):
            self.assertIn(expected, flags)

    def test_the_command_accepts_an_rtsp_source(self):
        """The stale limitation recorded against parking (webcam-only, no
        --source) must not be repeated here."""
        from core.management.commands import watch_disposal
        parser = watch_disposal.Command().create_parser("manage.py", "watch_disposal")
        opts = parser.parse_args(["--source", "rtsp://user:pass@10.0.0.2:554/Streaming/Channels/102"])
        self.assertTrue(opts.source.startswith("rtsp://"))


class PromptCacheTests(TestCase):
    """The prompt cache is purely a speed optimisation, so these assert the two
    things that make it SAFE rather than fast: that editing the prompts cannot
    silently keep using embeddings computed from the old wording, and that a bad
    cache file degrades to the slow path instead of taking the detector down.

    No YOLO-World here — `load_world_model` is driven against a stub ultralytics
    module, because the thing under test is the CACHE DECISION, not inference.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    # ---- fingerprinting ----

    def test_the_fingerprint_is_stable_across_calls(self):
        self.assertEqual(disposal.prompt_fingerprint(), disposal.prompt_fingerprint())

    def test_editing_the_prompts_changes_the_cache_file(self):
        """The whole point of keying the filename on the prompts."""
        before = disposal.prompt_cache_path(cache_dir=self.tmp)
        with mock.patch.object(disposal, "PROMPTS", disposal.PROMPTS + ["bin bag"]):
            after = disposal.prompt_cache_path(cache_dir=self.tmp)
        self.assertNotEqual(before.name, after.name)

    def test_editing_the_reject_list_also_changes_the_cache_file(self):
        before = disposal.prompt_cache_path(cache_dir=self.tmp)
        with mock.patch.object(disposal, "REJECT", disposal.REJECT + ["traffic cone"]):
            after = disposal.prompt_cache_path(cache_dir=self.tmp)
        self.assertNotEqual(before.name, after.name)

    def test_different_base_weights_get_different_caches(self):
        a = disposal.prompt_cache_path("yolov8s-worldv2.pt", cache_dir=self.tmp)
        b = disposal.prompt_cache_path("yolov8l-worldv2.pt", cache_dir=self.tmp)
        self.assertNotEqual(a.name, b.name)

    def test_the_cache_path_honours_an_explicit_directory(self):
        path = disposal.prompt_cache_path(cache_dir=self.tmp)
        self.assertEqual(path.parent, pathlib.Path(self.tmp))
        self.assertTrue(path.name.endswith(".pt"))

    def test_the_cache_directory_defaults_under_the_vision_package(self):
        self.assertEqual(disposal.PROMPT_CACHE_DIR.name, "world_cache")
        self.assertEqual(disposal.PROMPT_CACHE_DIR.parent, disposal.VISION_DIR)

    # ---- load_world_model ----

    def _stub_ultralytics(self, loaded):
        """Install a fake `ultralytics.YOLOWorld` that records how it was built.

        `loaded` collects one entry per construction, so a test can tell a cache
        hit (built from the cache path, no set_classes) from a miss (built from
        base weights, then prompted).
        """
        class FakeWorldModel:
            def __init__(self):
                self.clip_model = object()      # the 354 MB CLIP stands in here

        class FakeYOLOWorld:
            def __init__(self, weights):
                self.weights = str(weights)
                self.classes = None
                self.model = FakeWorldModel()
                loaded.append(self)

            def set_classes(self, classes):
                self.classes = list(classes)

            def save(self, path):
                pathlib.Path(path).write_bytes(b"baked")

        module = types.ModuleType("ultralytics")
        module.YOLOWorld = FakeYOLOWorld
        return mock.patch.dict(sys.modules, {"ultralytics": module})

    def test_a_cold_load_prompts_the_model_and_writes_the_cache(self):
        loaded = []
        with self._stub_ultralytics(loaded):
            disposal.load_world_model("base.pt", cache_dir=self.tmp)
        self.assertTrue(disposal.prompt_cache_path("base.pt", self.tmp).exists())
        self.assertEqual(loaded[0].weights, "base.pt")
        self.assertEqual(loaded[0].classes, disposal.PROMPTS + disposal.REJECT)

    def test_clip_is_stripped_before_the_cache_is_written(self):
        """354 MB that only set_classes needs, and the cached path never calls it."""
        loaded = []
        with self._stub_ultralytics(loaded):
            disposal.load_world_model("base.pt", cache_dir=self.tmp)
        self.assertIsNone(loaded[0].model.clip_model)

    def test_a_warm_load_skips_set_classes_entirely(self):
        """The whole saving: no set_classes on a later launch means no CLIP."""
        loaded = []
        with self._stub_ultralytics(loaded):
            disposal.load_world_model("base.pt", cache_dir=self.tmp)
            loaded.clear()
            model = disposal.load_world_model("base.pt", cache_dir=self.tmp)
        self.assertEqual(len(loaded), 1)
        self.assertIsNone(model.classes)
        self.assertEqual(pathlib.Path(model.weights),
                         disposal.prompt_cache_path("base.pt", self.tmp))

    def test_an_edited_prompt_list_does_not_reuse_the_old_cache(self):
        loaded = []
        with self._stub_ultralytics(loaded):
            disposal.load_world_model("base.pt", cache_dir=self.tmp)
            loaded.clear()
            with mock.patch.object(disposal, "PROMPTS", disposal.PROMPTS + ["bin bag"]):
                expected = disposal.PROMPTS + disposal.REJECT
                disposal.load_world_model("base.pt", cache_dir=self.tmp)
        self.assertEqual(loaded[0].classes, expected)

    def test_an_unreadable_cache_falls_back_and_is_rebuilt(self):
        """A truncated .pt from a process killed mid-save must not be fatal."""
        path = disposal.prompt_cache_path("base.pt", self.tmp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a torch checkpoint")
        loaded, notes = [], []

        class Boom:
            def __init__(self, weights):
                if str(weights) == str(path):
                    raise RuntimeError("corrupt checkpoint")
                self.weights, self.classes = str(weights), None
                self.model = types.SimpleNamespace(clip_model=object())
                loaded.append(self)

            def set_classes(self, classes):
                self.classes = list(classes)

            def save(self, p):
                pathlib.Path(p).write_bytes(b"baked")

        module = types.ModuleType("ultralytics")
        module.YOLOWorld = Boom
        with mock.patch.dict(sys.modules, {"ultralytics": module}):
            model = disposal.load_world_model("base.pt", cache_dir=self.tmp,
                                              log=notes.append)
        self.assertEqual(model.weights, "base.pt")
        self.assertEqual(model.classes, disposal.PROMPTS + disposal.REJECT)
        self.assertTrue(any("unreadable" in n for n in notes), notes)

    def test_a_cache_that_cannot_be_written_still_returns_a_working_model(self):
        """Read-only media or a full disk: the detector must still run."""
        loaded, notes = [], []
        stub = self._stub_ultralytics(loaded)
        with stub, mock.patch.object(disposal.Path, "mkdir",
                                     side_effect=OSError("read-only")):
            model = disposal.load_world_model("base.pt", cache_dir=self.tmp,
                                              log=notes.append)
        self.assertEqual(model.classes, disposal.PROMPTS + disposal.REJECT)
        self.assertTrue(any("uncached" in n for n in notes), notes)

    def test_the_cache_is_written_atomically(self):
        """No .tmp left behind, so a later launch never reads a partial file."""
        loaded = []
        with self._stub_ultralytics(loaded):
            disposal.load_world_model("base.pt", cache_dir=self.tmp)
        self.assertEqual(list(pathlib.Path(self.tmp).glob("*.tmp")), [])


class AreaTests(TestCase):
    """The watched-area polygon. Geometry only — no frames, no model.

    The anchor is the blob's BOTTOM-CENTRE (where it rests on the ground), so
    these pin that down: judging a bag by its centre would place one at the far
    kerb inside an area drawn on the near one.
    """

    # left half of the frame, full height
    LEFT_HALF = [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)]
    SHAPE = (400, 1000, 3)        # h, w, c

    def test_a_polygon_needs_at_least_three_points(self):
        for bad in ([], [(0.0, 0.0)], [(0.0, 0.0), (1.0, 1.0)]):
            with self.assertRaises(ValueError):
                disposal.Area(bad)

    def test_a_blob_inside_the_area_is_contained(self):
        area = disposal.Area(self.LEFT_HALF)
        self.assertTrue(area.contains_box((100, 100, 40, 40), self.SHAPE))

    def test_a_blob_outside_the_area_is_not(self):
        area = disposal.Area(self.LEFT_HALF)
        self.assertFalse(area.contains_box((800, 100, 40, 40), self.SHAPE))

    def test_the_ground_point_decides_not_the_centre(self):
        """A tall blob whose centre sits outside but whose base is inside."""
        area = disposal.Area([(0.0, 0.5), (1.0, 0.5), (1.0, 1.0), (0.0, 1.0)])
        # box top 100, height 140 -> centre y=170 (above the line at y=200),
        # base y=240 (below it). The base is what counts.
        self.assertTrue(area.contains_box((500, 100, 40, 140), self.SHAPE))

    def test_the_polygon_rescales_when_the_frame_size_changes(self):
        """One drawn area has to serve PROC_WIDTH, full res and the live view."""
        area = disposal.Area(self.LEFT_HALF)
        self.assertTrue(area.contains_box((100, 100, 40, 40), (400, 1000, 3)))
        # same relative position in a frame half the size
        self.assertTrue(area.contains_box((50, 50, 20, 20), (200, 500, 3)))
        self.assertFalse(area.contains_box((400, 50, 20, 20), (200, 500, 3)))


class AreaFiltersCandidatesTests(TestCase):
    """Stage 1 with an area: an out-of-area object must never become a
    candidate at all, rather than being created and discarded later."""

    def _run(self, x, area=None):
        det = disposal.StaticObjectDetector(area=area)
        now = warm_up(det)
        for _ in range(40):
            det.update(with_object(scene(), x, 150), now)
            now += 0.1
        return det

    def test_without_an_area_an_object_anywhere_is_a_candidate(self):
        self.assertTrue(self._run(500).candidates)

    def test_an_object_inside_the_area_is_still_found(self):
        area = disposal.Area([(0.0, 0.0), (0.4, 0.0), (0.4, 1.0), (0.0, 1.0)])
        self.assertTrue(self._run(40, area).candidates)

    def test_an_object_outside_the_area_never_becomes_a_candidate(self):
        """Not merely unconfirmed — never created, so it mints no id, accrues
        no still-time and never reaches the zero-shot check."""
        area = disposal.Area([(0.0, 0.0), (0.4, 0.0), (0.4, 1.0), (0.0, 1.0)])
        det = self._run(500, area)
        self.assertEqual(det.candidates, [])
        self.assertEqual(det.due_for_check(999.0, 1.0), [])

    def test_the_detector_defaults_to_no_area(self):
        """No area drawn must keep the pre-area behaviour: watch everything."""
        self.assertIsNone(disposal.StaticObjectDetector().area)


class CoverageGeometryTests(TestCase):
    """How much of a blob sits inside a person/vehicle box."""

    def test_a_blob_fully_inside_is_fully_covered(self):
        self.assertAlmostEqual(
            disposal._coverage((10, 10, 20, 20), (0, 0, 100, 100)), 1.0)

    def test_a_blob_entirely_outside_is_not_covered(self):
        self.assertAlmostEqual(
            disposal._coverage((200, 200, 20, 20), (0, 0, 100, 100)), 0.0)

    def test_a_half_overlapping_blob_is_half_covered(self):
        # blob x 90..110, box ends at 100 -> half its width inside
        self.assertAlmostEqual(
            disposal._coverage((90, 10, 20, 20), (0, 0, 100, 100)), 0.5)

    def test_a_zero_area_blob_does_not_divide_by_zero(self):
        self.assertEqual(disposal._coverage((10, 10, 0, 0), (0, 0, 100, 100)), 0.0)

    def test_padding_grows_the_box_around_its_centre(self):
        x, y, w, h = disposal._padded((100, 100, 100, 100), pad=0.1)
        self.assertEqual((x, y), (90, 90))
        self.assertEqual((w, h), (120, 120))


class IgnoreMoversTests(TestCase):
    """A person who stands still must never get a candidate id.

    Stage 1 asks "did something arrive and stop moving?", and a stationary
    person answers yes — measured at 19 of 37 candidates on real footage. The
    suppression therefore has to happen at CREATION, not at confirmation.
    """

    OBJ = dict(x=300, y=180, w=60, h=45)

    def _run(self, ignore_boxes=None, frames=14):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(frames):
            det.update(with_object(scene(), self.OBJ["x"], self.OBJ["y"]),
                       now, ignore_boxes=ignore_boxes)
            now += 0.1
        return det

    def test_without_ignore_boxes_the_object_is_a_candidate(self):
        """The baseline this must not disturb."""
        self.assertTrue(self._run().candidates)

    def test_a_blob_inside_a_person_box_never_becomes_a_candidate(self):
        box = (self.OBJ["x"] - 20, self.OBJ["y"] - 20,
               self.OBJ["w"] + 40, self.OBJ["h"] + 40)
        det = self._run(ignore_boxes=[box])
        self.assertEqual(det.candidates, [])

    def test_a_suppressed_blob_is_recorded_for_the_live_view(self):
        """Seen and passed over, not invisible — the Testing view shows these
        so a filter over the wrong thing cannot look like a blind detector."""
        box = (self.OBJ["x"] - 20, self.OBJ["y"] - 20,
               self.OBJ["w"] + 40, self.OBJ["h"] + 40)
        det = self._run(ignore_boxes=[box])
        self.assertTrue(det.ignored)

    def test_a_box_somewhere_else_suppresses_nothing(self):
        det = self._run(ignore_boxes=[(10, 10, 40, 40)])
        self.assertTrue(det.candidates)
        self.assertEqual(det.ignored, [])

    def test_an_empty_box_list_behaves_like_no_filter(self):
        self.assertTrue(self._run(ignore_boxes=[]).candidates)

    def test_ignored_resets_when_the_filter_is_switched_off(self):
        """Toggling disposal_ignore_movers off mid-run must not leave a stale
        suppression list feeding the live view."""
        box = (self.OBJ["x"] - 20, self.OBJ["y"] - 20,
               self.OBJ["w"] + 40, self.OBJ["h"] + 40)
        det = self._run(ignore_boxes=[box])
        self.assertTrue(det.ignored)
        det.update(with_object(scene(), self.OBJ["x"], self.OBJ["y"]), 99.0)
        self.assertEqual(det.ignored, [])


class DisposalSettingsTests(TestCase):
    """The tuning knobs are editable in Settings rather than hardcoded."""

    KEYS = [
        "disposal_enabled", "disposal_confidence", "disposal_static_seconds",
        "disposal_cooldown", "disposal_cooldown_dist", "disposal_min_area",
        "disposal_max_area", "disposal_warmup_seconds",
        "disposal_recheck_seconds", "disposal_ignore_movers",
    ]

    def test_every_knob_exists_on_the_settings_row(self):
        cfg = SystemSettings.load()
        for key in self.KEYS:
            self.assertTrue(hasattr(cfg, key), key)

    def test_the_defaults_match_the_module_constants(self):
        """A default that drifts from the CV module's constant means the
        dashboard and a bare CLI run quietly disagree."""
        cfg = SystemSettings.load()
        self.assertAlmostEqual(cfg.disposal_min_area / 100.0, disposal.MIN_AREA_FRAC)
        self.assertAlmostEqual(cfg.disposal_max_area / 100.0, disposal.MAX_AREA_FRAC)
        self.assertEqual(float(cfg.disposal_warmup_seconds), disposal.WARMUP_SECONDS)
        self.assertEqual(float(cfg.disposal_recheck_seconds), disposal.RECHECK_SECONDS)
        self.assertEqual(float(cfg.disposal_cooldown_dist), disposal.COOLDOWN_DIST)
        self.assertEqual(float(cfg.disposal_static_seconds), disposal.STATIC_SECONDS)
        self.assertEqual(float(cfg.disposal_cooldown), disposal.COOLDOWN_SECONDS)

    def test_the_api_exposes_every_knob(self):
        from core.serializers import SystemSettingsSerializer
        fields = set(SystemSettingsSerializer.Meta.fields)
        for key in self.KEYS:
            self.assertIn(key, fields, key)

    def test_the_size_bounds_are_a_percent_not_a_fraction(self):
        """0.08 means 0.08% of the view. Storing the fraction instead would be
        a hundredfold change that still looks plausible in the box."""
        cfg = SystemSettings.load()
        self.assertEqual(cfg.disposal_min_area, 0.08)
        self.assertEqual(cfg.disposal_max_area, 8.0)

    def test_the_detector_accepts_the_configured_bounds(self):
        det = disposal.StaticObjectDetector(min_area_frac=0.002, max_area_frac=0.5,
                                            warmup_seconds=9.0)
        self.assertEqual(det.min_area_frac, 0.002)
        self.assertEqual(det.max_area_frac, 0.5)
        self.assertEqual(det.warmup_seconds, 9.0)


# ---------------------------------------------------------------------------
# Owner proximity — the abandoned-object rule.
#
# Driven through the real detector where the point is integration (does a
# person box actually reach the candidate?) and through Candidate directly
# where the point is the rule itself, which deserves to be pinned down without
# depending on MOG2 happening to raise a blob on a synthetic frame.
#
# The test scene is 640 wide, under PROC_WIDTH, so scale is 1.0 and the
# full-resolution person boxes these tests pass are also the processing
# coordinates the rule compares against.
# ---------------------------------------------------------------------------

OBJ = (300, 180, 60, 45)            # centre (330, 202.5)
NEAR_PERSON = (380, 170, 40, 70)    # centre (400, 205)  — 70px away, no overlap
FAR_PERSON = (560, 150, 40, 70)     # centre (580, 185)  — 250px away


def drop(det, now, person_boxes=None, frames=12, step=0.1):
    """Dump an object into the scene and let it settle. Returns the new time."""
    for _ in range(frames):
        det.update(with_object(scene(), OBJ[0], OBJ[1]), now,
                   person_boxes=person_boxes)
        now += step
    return now


def held_candidate(t=0.0, owner=True, box=OBJ):
    """A Candidate with its owner state set the way the detector would."""
    cand = disposal.Candidate(1, box, t)
    centres = [(NEAR_PERSON[0] + NEAR_PERSON[2] / 2.0,
                NEAR_PERSON[1] + NEAR_PERSON[3] / 2.0)] if owner else []
    cand.claim_owners(centres, t)
    return cand


class OwnerProximityTests(TestCase):
    def test_an_owner_standing_over_it_holds_the_alert(self):
        """A vendor beside their own goods is not dumping. Held, not rejected:
        no model pass is spent, and the candidate is still there when they go."""
        cand = held_candidate(0.0)
        self.assertTrue(cand.had_owner)
        # They are still there at 20s — well past the 5s static requirement.
        for t in (6.0, 10.0, 20.0):
            cand.track_owners([(400.0, 205.0)], t)
            self.assertTrue(cand.held_by_owner(t, away_seconds=5.0, max_hold=60.0),
                            f"should still be held at {t}s")

    def test_the_alert_fires_once_the_owner_has_been_away_long_enough(self):
        cand = held_candidate(0.0)
        cand.track_owners([(400.0, 205.0)], 2.0)      # still beside it
        # They walk off. No person within the radius from here on, so
        # last_owner_near_at stops advancing and the clock starts at 2.0s.
        self.assertTrue(cand.held_by_owner(5.0, 5.0, 60.0))
        self.assertTrue(cand.held_by_owner(6.9, 5.0, 60.0))
        self.assertFalse(cand.held_by_owner(7.1, 5.0, 60.0),
                         "5s after the owner left, it is abandoned")
        self.assertAlmostEqual(cand.owner_away_for(7.1), 5.1, places=5)

    def test_a_passerby_arriving_after_the_drop_does_not_hold_it(self):
        """Nobody was there when it landed, so it has no owner to wait for —
        and a busy pavement must not be able to suppress every alert."""
        cand = held_candidate(0.0, owner=False)
        self.assertFalse(cand.had_owner)
        for t in (1.0, 4.0, 9.0):
            cand.track_owners([(400.0, 205.0)], t)    # somebody wanders past
            self.assertFalse(cand.held_by_owner(t, 5.0, 60.0))
        self.assertIsNone(cand.owner_away_for(9.0))

    def test_a_passerby_does_not_revive_a_hold_the_owner_already_released(self):
        cand = held_candidate(0.0)
        cand.track_owners([], 1.0)                    # owner gone from the frame
        self.assertFalse(cand.held_by_owner(7.0, 5.0, 60.0))
        # Someone else walks up to the bag afterwards. The owner anchors were
        # dropped when they left, so there is nobody left to re-associate to.
        cand.track_owners([(400.0, 205.0)], 8.0)
        self.assertFalse(cand.held_by_owner(8.0, 5.0, 60.0),
                         "a stranger beside the bag must not restart the hold")

    def test_an_owner_who_never_leaves_still_alerts_at_the_max_hold(self):
        """Otherwise a vendor who stands there all shift suppresses it all shift,
        and the rubbish is dumped rubbish either way."""
        cand = held_candidate(0.0)
        for t in (10.0, 30.0, 59.0):
            cand.track_owners([(400.0, 205.0)], t)
            self.assertTrue(cand.held_by_owner(t, 5.0, max_hold=60.0))
        cand.track_owners([(400.0, 205.0)], 60.5)
        self.assertFalse(cand.held_by_owner(60.5, 5.0, max_hold=60.0))

    def test_an_owner_is_only_claimed_if_they_are_inside_the_radius(self):
        cand = disposal.Candidate(1, OBJ, 0.0)
        cand.claim_owners([(FAR_PERSON[0] + 20.0, FAR_PERSON[1] + 35.0)], 0.0,
                          radius=disposal.OWNER_RADIUS)
        self.assertFalse(cand.had_owner)

    def test_the_detector_claims_an_owner_from_the_person_boxes_it_is_given(self):
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = warm_up(det)
        drop(det, now, person_boxes=[NEAR_PERSON])
        self.assertTrue(det.candidates)
        self.assertTrue(det.candidates[0].had_owner,
                        "a person beside the object as it appeared is its owner")

    def test_with_no_person_boxes_the_detector_behaves_exactly_as_before(self):
        """The stated guarantee: an alert is never lost because the person
        detector missed somebody, or was switched off, or failed to load."""
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = warm_up(det)
        now = drop(det, now)
        self.assertTrue(det.candidates)
        self.assertFalse(det.candidates[0].had_owner)
        due = det.due_for_check(now + 10.0, static_seconds=5.0)
        self.assertTrue(due, "no owner means no hold")

    def test_due_for_check_withholds_a_held_candidate_and_held_reports_it(self):
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = warm_up(det)
        now = drop(det, now, person_boxes=[NEAR_PERSON])
        cand = det.candidates[0]
        later = now + 6.0
        cand.track_owners([(400.0, 205.0)], later)   # owner is still standing there
        self.assertGreaterEqual(cand.still_for(later), 5.0)
        # max_hold passed explicitly: the DEFAULT is now 3s, which this
        # candidate is already past, so relying on it would assert the hold
        # while testing the release.
        self.assertEqual(det.due_for_check(later, 5.0, owner_away_seconds=5.0,
                                           owner_max_hold=60.0), [])
        self.assertEqual([c.id for c in det.held(later, 5.0, 5.0, 60.0)], [cand.id])


class ConfidenceFloorTests(TestCase):
    """The floor has to be applied HERE, not only handed to predict(): passing
    it to the model sets ultralytics' NMS threshold, which drops boxes — on a
    crop where everything is weak the surviving garbage box could still win the
    comparison on a score the operator had ruled too low to act on."""

    def setUp(self):
        self.crop = scene(128, 128)
        self.garbage_ids = disposal.garbage_class_ids()
        self.person = len(disposal.PROMPTS)
        self.dog = len(disposal.PROMPTS) + 1

    def test_a_tie_below_the_floor_does_not_alert(self):
        ok, (_n, score), _r = disposal.confirm(
            FakeWorld([(0, 0.09), (self.dog, 0.09)]),
            self.crop, self.garbage_ids, conf=0.15)
        self.assertFalse(ok, "a tie still has to clear the floor")
        self.assertAlmostEqual(score, 0.09, places=5)

    def test_garbage_leading_but_below_the_floor_does_not_alert(self):
        ok, _g, _r = disposal.confirm(
            FakeWorld([(0, 0.12), (self.person, 0.04)]),
            self.crop, self.garbage_ids, conf=0.15)
        self.assertFalse(ok)

    def test_garbage_on_the_floor_exactly_alerts(self):
        ok, _g, _r = disposal.confirm(
            FakeWorld([(0, 0.15)]), self.crop, self.garbage_ids, conf=0.15)
        self.assertTrue(ok, "the floor is a minimum, not an exclusive bound")

    def test_raising_the_floor_suppresses_a_detection_that_used_to_pass(self):
        """What the dashboard control is supposed to do."""
        pairs = [(0, 0.22), (self.person, 0.05)]
        self.assertTrue(disposal.confirm(
            FakeWorld(pairs), self.crop, self.garbage_ids, conf=0.15)[0])
        self.assertFalse(disposal.confirm(
            FakeWorld(pairs), self.crop, self.garbage_ids, conf=0.40)[0])

    def test_a_zero_floor_still_rejects_an_empty_result(self):
        ok, _g, _r = disposal.confirm(
            FakeWorld([]), self.crop, self.garbage_ids, conf=0.0)
        self.assertFalse(ok, "nothing detected is not garbage at any floor")

    def test_the_reject_comparison_still_applies_above_the_floor(self):
        """The floor is an addition, not a replacement: a dog that outscores the
        bag must still win even when both clear it."""
        ok, _g, (r_name, _s) = disposal.confirm(
            FakeWorld([(0, 0.30), (self.dog, 0.55)]),
            self.crop, self.garbage_ids, conf=0.15)
        self.assertFalse(ok)
        self.assertEqual(r_name, "dog")


class PromptListTests(TestCase):
    def test_the_commonly_dumped_things_are_wanted_classes(self):
        for name in ("cardboard box", "rice sack", "styrofoam",
                     "pile of plastic bottles"):
            self.assertIn(name, disposal.PROMPTS)

    def test_the_containers_that_live_there_are_reject_classes(self):
        """A bin or a bucket sits still because it belongs there, which is
        exactly the look-alike the comparison has to throw out."""
        for name in ("trash can", "garbage bin", "bucket", "potted plant"):
            self.assertIn(name, disposal.REJECT)

    def test_a_bare_box_is_no_longer_rejected(self):
        """It was rejecting every dumped carton via the very comparison that is
        supposed to protect against look-alikes."""
        self.assertNotIn("box", disposal.REJECT)

    def test_garbage_ids_still_cover_exactly_the_prompt_classes(self):
        self.assertEqual(disposal.garbage_class_ids(),
                         set(range(len(disposal.PROMPTS))))
        self.assertNotIn(len(disposal.PROMPTS), disposal.garbage_class_ids())

    def test_the_prompt_cache_key_changes_when_the_lists_change(self):
        """The cache is keyed on the prompts, so editing them rebuilds it
        rather than silently having no effect."""
        before = disposal.prompt_fingerprint()
        with mock.patch.object(disposal, "PROMPTS", disposal.PROMPTS + ["crate"]):
            after = disposal.prompt_fingerprint()
        self.assertNotEqual(before, after)
        self.assertEqual(disposal.prompt_cache_path().name,
                         f"disposal-world-{before}.pt")


class SuspectCropTests(TestCase):
    """The crop comes from the window around the DROP, because that is the only
    part of the footage the person who left it is still in."""

    def test_the_nearest_person_is_picked(self):
        frame = scene(640, 360)
        near, far = (320, 180, 40, 80), (560, 60, 40, 80)
        crop, box = disposal.pick_suspect([(frame, [far, near])], (330.0, 202.0))
        self.assertEqual(box, near)
        self.assertIsNotNone(crop)

    def test_between_equally_close_people_the_sharper_crop_wins(self):
        """Distance decides first, but only to within SUSPECT_NEAR_FACTOR: a
        blurred half-frame crop identifies nobody."""
        frame = scene(640, 360)
        rng = np.random.default_rng(1)
        # Same size, equal distance either side; one flat, one textured.
        flat, sharp = (290, 60, 40, 80), (370, 60, 40, 80)
        frame[60:140, 290:330] = 128
        frame[60:140, 370:410] = rng.integers(0, 255, size=(80, 40, 3), dtype=np.uint8)
        _crop, box = disposal.pick_suspect([(frame, [flat, sharp])], (350.0, 100.0))
        self.assertEqual(box, sharp)

    def test_no_people_means_no_crop(self):
        crop, box = disposal.pick_suspect([(scene(), [])], (330.0, 202.0))
        self.assertIsNone(crop)
        self.assertIsNone(box)

    def test_an_empty_sample_list_is_handled(self):
        self.assertEqual(disposal.pick_suspect([], (0.0, 0.0)), (None, None))

    def test_the_fast_mask_is_kept_for_the_carrier_fallback(self):
        """Fallback 1: whoever carried it was still MOVING as it landed, so it
        is in the fast model's foreground even when person detection misses."""
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = warm_up(det)
        drop(det, now)
        self.assertTrue(det.candidates)
        self.assertIsNotNone(det.fg_short)

    def test_the_lookback_buffer_holds_frames_from_before_the_drop(self):
        buf = disposal.SuspectLookback(seconds=3.0, interval=0.25)
        for i in range(20):
            buf.add(scene(160, 120), i * 0.25)
        window = buf.window(3.0, 4.75)
        self.assertTrue(window)
        self.assertTrue(all(3.0 <= t <= 4.75 for t, _j in window))
        self.assertIsNotNone(disposal.decode_frame(window[0][1]))

    def test_the_lookback_buffer_throttles_and_drops_old_frames(self):
        buf = disposal.SuspectLookback(seconds=1.0, interval=0.25)
        for i in range(40):
            buf.add(scene(160, 120), i * 0.05)      # offered 20x a second
        stamps = [t for t, _j in buf._buf]
        self.assertLessEqual(len(stamps), 6, "frames are throttled, not stored per frame")
        self.assertGreaterEqual(min(stamps), max(stamps) - 1.0, "old frames fall off")

    def test_a_candidate_copies_the_pre_drop_window_as_it_is_created(self):
        """The pre-window is already in the past by the time anything is known
        about the candidate, so it has to be taken at creation."""
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        drop(det, now)
        self.assertTrue(det.candidates)
        self.assertTrue(det.candidates[0].suspect_frames,
                        "the window around the drop must be captured")


class SuspectFallbackOrderTests(TestCase):
    """person -> carrier -> none, and nothing here may raise: an alert lost to
    a cropping error is not recoverable, while a missing crop is just a missing
    crop."""

    def _command(self):
        from core.management.commands import watch_disposal
        cmd = watch_disposal.Command()
        cmd.violations_dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cmd.violations_dir, True)
        cmd.stdout = types.SimpleNamespace(write=lambda *a, **k: None)
        cmd.style = types.SimpleNamespace(WARNING=lambda m: m)
        return cmd, watch_disposal

    def _candidate_with_frames(self):
        import cv2
        cand = disposal.Candidate(1, OBJ, 0.0)
        _ok, buf = cv2.imencode(".jpg", scene(640, 360))
        cand.suspect_frames = [(0.0, buf.tobytes())]
        return cand

    def test_a_detected_person_is_saved_as_the_suspect(self):
        cmd, mod = self._command()
        cand = self._candidate_with_frames()
        with mock.patch.object(mod.recognition, "detect_scene",
                               return_value=([(300, 180, 360, 280, 0.9, "person")],
                                             [], [], [])):
            url, status = cmd._save_suspect(cand, 1.0, "ts", "bag", None)
        self.assertEqual(status, "person")
        self.assertTrue(url)

    def test_no_person_falls_back_to_the_moving_carrier(self):
        cmd, mod = self._command()
        cand = self._candidate_with_frames()
        cand.carrier_box = (290, 170, 80, 70)
        with mock.patch.object(mod.recognition, "detect_scene",
                               return_value=([], [], [], [])):
            url, status = cmd._save_suspect(cand, 1.0, "ts", "bag", None)
        self.assertEqual(status, "carrier")
        self.assertTrue(url)

    def test_neither_records_none_so_the_card_can_say_so(self):
        cmd, mod = self._command()
        cand = self._candidate_with_frames()
        cand.carrier_box = None
        with mock.patch.object(mod.recognition, "detect_scene",
                               return_value=([], [], [], [])):
            url, status = cmd._save_suspect(cand, 1.0, "ts", "bag", None)
        self.assertEqual(status, "none")
        self.assertEqual(url, "")

    def test_a_failure_in_the_person_model_still_records_none_not_a_crash(self):
        cmd, mod = self._command()
        cand = self._candidate_with_frames()
        with mock.patch.object(mod.recognition, "detect_scene",
                               side_effect=RuntimeError("no weights")):
            url, status = cmd._save_suspect(cand, 1.0, "ts", "bag", None)
        self.assertEqual((url, status), ("", "none"))

    def test_a_candidate_with_no_window_records_none(self):
        cmd, _mod = self._command()
        self.assertEqual(cmd._save_suspect(None, 1.0, "ts", "bag", None),
                         ("", "none"))


class DisposalOwnerSettingsTests(TestCase):
    def test_the_new_defaults_are_the_documented_ones(self):
        cfg = SystemSettings.load()
        self.assertEqual(cfg.disposal_owner_radius, 80)
        self.assertEqual(cfg.disposal_owner_away_seconds, 3)
        self.assertEqual(cfg.disposal_owner_max_hold, 3)
        self.assertEqual(cfg.disposal_suspect_confidence, 25)

    def test_the_defaults_match_the_vision_module_constants(self):
        """Same contract as the confidence/static pair: if these drift, the
        dashboard and the detector disagree about the rule."""
        cfg = SystemSettings.load()
        self.assertEqual(float(cfg.disposal_owner_radius), float(disposal.OWNER_RADIUS))
        self.assertEqual(float(cfg.disposal_owner_away_seconds),
                         disposal.OWNER_AWAY_SECONDS)
        self.assertEqual(float(cfg.disposal_owner_max_hold), disposal.OWNER_MAX_HOLD)
        self.assertAlmostEqual(cfg.disposal_suspect_confidence / 100.0,
                               disposal.SUSPECT_CONFIDENCE, places=5)

    def test_the_settings_endpoint_publishes_the_new_fields(self):
        from core.serializers import SystemSettingsSerializer
        data = SystemSettingsSerializer(SystemSettings.load()).data
        for field in ("disposal_owner_radius", "disposal_owner_away_seconds",
                      "disposal_owner_max_hold", "disposal_suspect_confidence"):
            self.assertIn(field, data)


class DisposalSuspectAlertFieldTests(TestCase):
    def test_the_alert_carries_the_suspect_and_footage_fields(self):
        from core.models import Alert
        names = {f.name for f in Alert._meta.get_fields()}
        for field in ("suspect_image_url", "suspect_status", "owner_left_at",
                      "footage_offset"):
            self.assertIn(field, names)

    def test_suspect_status_offers_exactly_the_three_outcomes(self):
        from core.models import Alert
        self.assertEqual([c[0] for c in Alert.SuspectStatus.choices],
                         ["person", "carrier", "none"])

    def test_the_alert_serializer_publishes_them_read_only(self):
        from core.serializers import AlertSerializer
        meta = AlertSerializer.Meta
        for field in ("suspect_image_url", "suspect_status", "owner_left_at",
                      "footage_offset"):
            self.assertIn(field, meta.fields)
        for field in ("suspect_status", "owner_left_at", "footage_offset"):
            self.assertIn(field, meta.read_only_fields,
                          "the detectors write these, never a client")


class DisposalOwnerDescriptionTests(TestCase):
    def test_the_wording_names_the_walk_away(self):
        """The strongest fact the detector holds: somebody stood over this and
        then left it, which is dumping described exactly."""
        text = descriptions.disposal("garbage bag", 12, away_seconds=7)
        self.assertEqual(
            text,
            "Garbage bag left behind; person walked away 7 s ago, unattended for 12 s.")

    def test_without_an_owner_the_wording_is_unchanged(self):
        self.assertEqual(descriptions.disposal("sack", 8),
                         "Sack left behind and unattended for 8 s.")
        self.assertEqual(descriptions.disposal("sack", 8, away_seconds=None),
                         "Sack left behind and unattended for 8 s.")

    def test_no_track_ids_or_scores_leak_into_the_owner_wording(self):
        text = descriptions.disposal("plastic bag", 42, away_seconds=9)
        for leak in ("#", "track", "conf", "0."):
            self.assertNotIn(leak, text.lower())


class StaleLiveViewTests(TestCase):
    """A job id reused after the database was recreated must not inherit the
    previous job's published frame.

    The live-view directories are named after the job id and never swept, while
    the id sequence restarts with the database — so this is not a hypothetical:
    it showed a disposal run another detector's parking subjects over a frame
    from a different clip, for the ten seconds the model took to load.
    """

    def test_a_reused_job_directory_is_emptied_before_the_detector_starts(self):
        from core import debug_state, monitor as live_monitor
        job_dir = live_monitor.job_dir(999_001)
        os.makedirs(job_dir, exist_ok=True)
        self.addCleanup(shutil.rmtree, job_dir, True)
        # What a previous run left behind.
        (job_dir / "state.json").write_text(
            json.dumps({"seq": 42, "wall": 1, "media_ts": 1, "frame_w": 8,
                        "frame_h": 8, "area": None,
                        "subjects": [{"key": "parking:3", "id": 3,
                                      "violation": "parking", "kind": "track",
                                      "box": [0, 0, 1, 1], "status": "Possible",
                                      "score": 0, "indicators": [],
                                      "multipliers": {}, "momentum": 0.16,
                                      "seen": 1, "changed": 1}]}),
            encoding="utf8")
        (job_dir / "frame.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        self.assertTrue(debug_state.payload(job_dir)["available"])

        # The one line create() runs before handing the directory to a detector.
        shutil.rmtree(job_dir, ignore_errors=True)
        os.makedirs(job_dir, exist_ok=True)

        payload = debug_state.payload(job_dir)
        self.assertFalse(payload["available"],
                         "a new run must not publish the previous run's state")
        self.assertNotIn("frame", payload)

    def test_the_launcher_clears_the_directory(self):
        """Pins the behaviour to the code path, not just to the idea: if the
        rmtree is ever dropped from create(), this fails."""
        import inspect
        from core import views
        src = inspect.getsource(views.DetectionJobViewSet.create)
        self.assertIn("rmtree(view_dir", src.replace(" ", "").replace(
            "shutil.rmtree(view_dir", "rmtree(view_dir"))


class OcclusionTests(TestCase):
    """Reported from real footage: "it detects but when there's a car or
    anything passed by the id will vanish".

    The ignore rule deletes the bag's blob while a vehicle box covers it, then
    `candidate_grace` drops the candidate two seconds later and the bag comes
    back as a new id with a fresh timer. On the sample clip the mover filter
    suppressed 1262 blob-frames in 300 seconds, so on that road the 5s still
    requirement was effectively unreachable.
    """

    def _settled(self):
        det = disposal.StaticObjectDetector(suspect_window=False)
        now = warm_up(det)
        now = drop(det, now)
        self.assertTrue(det.candidates)
        return det, now, det.candidates[0]

    def test_a_vehicle_passing_in_front_does_not_take_the_id_with_it(self):
        det, now, cand = self._settled()
        first_id, first_seen = cand.id, cand.first
        # A vehicle box over the bag, for longer than candidate_grace.
        vehicle = [(OBJ[0] - 30, OBJ[1] - 30, OBJ[2] + 60, OBJ[3] + 60)]
        for _ in range(60):                       # 6s at 0.1s a frame
            det.update(scene(), now, ignore_boxes=vehicle)
            now += 0.1
        self.assertTrue(det.candidates, "the bag did not leave, it was hidden")
        self.assertEqual(det.candidates[0].id, first_id, "same object, same id")
        self.assertEqual(det.candidates[0].first, first_seen,
                         "still-time must not restart")
        self.assertGreater(det.candidates[0].still_for(now), 5.0)

    def test_the_still_time_survives_the_whole_pass(self):
        """The point of the fix: the bag reaches the static requirement even
        though it was invisible for most of the window."""
        det, now, cand = self._settled()
        vehicle = [(OBJ[0] - 30, OBJ[1] - 30, OBJ[2] + 60, OBJ[3] + 60)]
        for _ in range(40):
            det.update(scene(), now, ignore_boxes=vehicle)
            now += 0.1
        now = drop(det, now, frames=6)            # traffic clears, bag visible
        self.assertEqual(len(det.candidates), 1, "no duplicate id was minted")
        self.assertTrue(det.due_for_check(now, static_seconds=5.0),
                        "it should now be eligible, not starting over")

    def test_an_occluded_candidate_is_not_sent_to_the_bag_check(self):
        """Checking a crop of the motorcycle would answer a question nobody
        asked, mark the candidate rejected and start a recheck cooldown."""
        det, now, _cand = self._settled()
        vehicle = [(OBJ[0] - 30, OBJ[1] - 30, OBJ[2] + 60, OBJ[3] + 60)]
        for _ in range(60):
            det.update(scene(), now, ignore_boxes=vehicle)
            now += 0.1
        self.assertIsNotNone(det.candidates[0].occluded_since)
        self.assertEqual(det.due_for_check(now, static_seconds=5.0), [],
                         "withheld while hidden, not rejected")

    def test_the_hold_is_capped_so_a_parked_vehicle_cannot_hide_it_forever(self):
        det, now, _cand = self._settled()
        vehicle = [(OBJ[0] - 30, OBJ[1] - 30, OBJ[2] + 60, OBJ[3] + 60)]
        for _ in range(int(disposal.OCCLUSION_HOLD_SECONDS / 0.5) + 10):
            det.update(scene(), now, ignore_boxes=vehicle)
            now += 0.5
        self.assertEqual(det.candidates, [],
                         "an object hidden this long is genuinely unobservable")

    def test_an_object_actually_removed_is_still_dropped(self):
        """The guard against the fix going too far: nothing is covering the
        spot, so the bag really has been taken away."""
        det, now, _cand = self._settled()
        now += disposal.CANDIDATE_GRACE + 1.0
        det.update(scene(), now)
        self.assertEqual(det.candidates, [])

    def test_occlusion_clears_once_the_vehicle_moves_on(self):
        det, now, _cand = self._settled()
        vehicle = [(OBJ[0] - 30, OBJ[1] - 30, OBJ[2] + 60, OBJ[3] + 60)]
        for _ in range(30):
            det.update(scene(), now, ignore_boxes=vehicle)
            now += 0.1
        self.assertIsNotNone(det.candidates[0].occluded_since)
        now = drop(det, now, frames=6)
        self.assertIsNone(det.candidates[0].occluded_since,
                          "visible again, so no longer held")


class RecordingPublisher:
    """Captures what _publish_view would send, without touching the disk."""

    def __init__(self):
        self.entries = {}
        self.committed = 0

    def note(self, key, violation, ident, box, status, score_pct, indicators,
             multipliers, momentum, kind="track", detail=""):
        self.entries[ident] = {"status": status, "detail": detail,
                               "score": score_pct, "indicators": indicators}

    def commit(self, media_ts=None):
        self.committed += 1


class DisposalStatusFlowTests(TestCase):
    """Monitoring -> Possible -> Likely, plus Below Monitoring.

    Possible is a WAITING state and covers three different reasons for waiting;
    the sub-label is what tells them apart, because the colour cannot. None of
    them may write an Alert -- only Likely does.
    """

    def setUp(self):
        from core.management.commands import watch_disposal
        self.mod = watch_disposal
        self.cmd = watch_disposal.Command()
        self.cmd.debug_pub = RecordingPublisher()
        self.cmd.conf_override = None
        self.cmd.static_override = None
        self.det = disposal.StaticObjectDetector(suspect_window=False)
        self.det.scale = 1.0
        self.cand = disposal.Candidate(1, OBJ, 0.0)
        self.det.candidates = [self.cand]

    def publish(self, now, static_seconds=2.0, conf=0.15,
                away_seconds=3.0, max_hold=3.0):
        self.cmd.debug_pub.entries.clear()
        self.cmd._publish_view(self.det, self.det.candidates, now, static_seconds,
                               away_seconds, max_hold, conf)
        return self.cmd.debug_pub.entries[self.cand.id]

    # ---- the four statuses -------------------------------------------------

    def test_still_time_under_the_requirement_is_monitoring(self):
        e = self.publish(now=1.0, static_seconds=2.0)
        self.assertEqual(e["status"], "Monitoring")
        self.assertEqual(e["detail"], "")

    def test_still_and_owner_nearby_is_possible_owner_nearby(self):
        self.cand.claim_owners([(400.0, 205.0)], 0.0)
        self.cand.track_owners([(400.0, 205.0)], 2.5)
        e = self.publish(now=2.5, static_seconds=2.0, away_seconds=3.0, max_hold=60.0)
        self.assertEqual(e["status"], "Possible")
        self.assertEqual(e["detail"], "Owner nearby")

    def test_still_and_not_yet_checked_is_possible_checking(self):
        e = self.publish(now=5.0, static_seconds=2.0)
        self.assertEqual(e["status"], "Possible")
        self.assertEqual(e["detail"], "Checking")

    def test_garbage_below_the_floor_is_possible_weak_match(self):
        """The floor GATES: under it, no row. That is what makes
        disposal_confidence a precision control rather than a label."""
        self.cand.weak = True
        self.cand.label, self.cand.score = "garbage bag", 0.11
        e = self.publish(now=5.0, static_seconds=2.0, conf=0.15)
        self.assertEqual(e["status"], "Possible")
        self.assertEqual(e["detail"], "Weak match 11%")

    def test_a_weak_match_shows_the_floor_it_fell_under(self):
        """Without the gap the number is unreadable: 11% is only 'weak'
        relative to something."""
        self.cand.weak = True
        self.cand.label, self.cand.score = "garbage bag", 0.11
        e = self.publish(now=5.0, static_seconds=2.0, conf=0.15)
        names = {i["name"]: i["points"] for i in e["indicators"]}
        self.assertEqual(names["garbage bag"], 11)
        self.assertEqual(names["needs"], 15)

    def test_a_weak_match_that_is_also_held_says_both(self):
        self.cand.weak = True
        self.cand.label, self.cand.score = "garbage bag", 0.11
        self.cand.pinned_since = 1.0
        e = self.publish(now=5.0, static_seconds=2.0, conf=0.15)
        self.assertEqual(e["detail"], "Still there · weak 11%")

    def test_a_confirmed_candidate_is_likely(self):
        self.cand.confirmed = True
        self.cand.label, self.cand.score = "garbage bag", 0.42
        e = self.publish(now=5.0, static_seconds=2.0)
        self.assertEqual(e["status"], "Likely")
        self.assertEqual(e["score"], 100)

    def test_a_winning_reject_class_is_below_monitoring(self):
        self.cand.rejected = True
        self.cand.label = "dog 55%"
        e = self.publish(now=5.0, static_seconds=2.0)
        self.assertEqual(e["status"], "Below Monitoring")

    # ---- the score bands ---------------------------------------------------

    def test_monitoring_fills_zero_to_fiftyfour(self):
        self.assertEqual(self.publish(now=0.0, static_seconds=2.0)["score"], 0)
        self.assertEqual(self.publish(now=1.0, static_seconds=2.0)["score"], 27)
        self.assertEqual(self.publish(now=1.99, static_seconds=2.0)["score"], 54)

    def test_possible_sits_in_the_fiftyfive_to_ninetynine_band(self):
        waiting = self.publish(now=5.0, static_seconds=2.0)["score"]
        self.assertEqual(waiting, 55, "a waiting state has no score to place")
        self.cand.weak = True
        self.cand.label, self.cand.score = "sack", 0.149
        near = self.publish(now=5.0, static_seconds=2.0, conf=0.15)["score"]
        self.assertGreater(near, 55)
        self.assertLessEqual(near, 99)

    def test_possible_never_reaches_a_hundred(self):
        """100 is reserved for Likely — a full bar on an unconfirmed candidate
        would read as a stronger claim than the detector is making."""
        self.cand.weak = True
        self.cand.label, self.cand.score = "sack", 0.99
        self.assertEqual(self.publish(now=9.0, static_seconds=2.0, conf=0.15)["score"],
                         self.mod.Command.POSSIBLE_MAX_PCT)

    def test_the_bands_line_up_with_the_rest_of_the_system(self):
        """Possible starts at 0.55 everywhere else; the bar must mean the same
        thing on a disposal candidate as on a smoking one."""
        from core.vision import scoring
        self.assertEqual(self.mod.Command.POSSIBLE_MIN_PCT,
                         int(scoring.SCORE_WARNING * 100))
        self.assertEqual(self.mod.Command.MONITORING_MAX_PCT,
                         self.mod.Command.POSSIBLE_MIN_PCT - 1)

    # ---- Possible must not block, and must not alert -----------------------

    def test_possible_does_not_create_an_alert(self):
        """The hard rule: only Likely writes a row. Every Possible state is
        reached without _create_alert being called at all."""
        from core.models import Alert
        before = Alert.objects.count()
        self.cand.claim_owners([(400.0, 205.0)], 0.0)
        self.cand.track_owners([(400.0, 205.0)], 2.5)
        self.publish(now=2.5, static_seconds=2.0, max_hold=60.0)
        self.cand.weak, self.cand.score, self.cand.label = True, 0.11, "sack"
        self.publish(now=5.0, static_seconds=2.0)
        self.assertEqual(Alert.objects.count(), before)

    def test_the_owner_hold_releases_at_max_hold_so_it_cannot_block_an_alert(self):
        """Possible must never be a dead end: once the hold expires the
        candidate goes back to stage 2 and can still reach Likely."""
        self.cand.claim_owners([(400.0, 205.0)], 0.0)
        self.cand.track_owners([(400.0, 205.0)], 2.0)
        self.assertEqual(self.det.due_for_check(2.5, 2.0, owner_away_seconds=3.0,
                                                owner_max_hold=60.0), [])
        due = self.det.due_for_check(3.5, 2.0, owner_away_seconds=3.0,
                                     owner_max_hold=3.0)
        self.assertEqual([c.id for c in due], [self.cand.id])

    def test_a_weak_match_stays_in_the_recheck_queue(self):
        """It is a maybe, not a write-off: if the scene changes and the score
        clears the floor on a later look, it still becomes Likely."""
        self.cand.weak, self.cand.last_check = True, 5.0
        self.assertEqual(self.det.due_for_check(10.0, 2.0, recheck_seconds=10.0), [])
        due = self.det.due_for_check(16.0, 2.0, recheck_seconds=10.0)
        self.assertEqual([c.id for c in due], [self.cand.id])


class ConfidenceGatesTheAlertTests(TestCase):
    """disposal_confidence GATES the row; it is not merely a label.

    Pinned down because it was briefly the other way round, and the difference
    is the whole value of the control: if a weak match alerts, raising the
    dial relabels alerts instead of reducing them, and an operator with too
    many false positives has nothing left to turn.

    The cost is accepted and documented, not hidden: an open-vocab head scores
    a correct distant bag near 0.10, so a floor of 0.15 loses it. That is a
    precision/recall trade for the operator to make from the dashboard.
    """

    def setUp(self):
        from core.management.commands import watch_disposal
        self.source = inspect.getsource(watch_disposal.Command._run_stream)

    def test_a_weak_verdict_returns_before_the_alert_call(self):
        self.assertIn("if verdict == disposal_cv.WEAK:", self.source)
        between = self.source.split("if verdict == disposal_cv.WEAK:")[1]
        between = between.split("cand.confirmed")[0]
        self.assertIn("continue", between,
                      "a weak match must not fall through to _alert")

    def test_only_a_confirmed_verdict_sets_confirmed(self):
        """`confirmed` is what the live view reads for Likely and what the
        alert path keys on, so nothing weak may set it."""
        self.assertIn("cand.confirmed, cand.rejected, cand.weak = True, False, False",
                      self.source)

    def test_raising_the_floor_reduces_what_confirms(self):
        """The property that makes the dial worth having."""
        scores = [0.08, 0.12, 0.20, 0.35]
        at_low = [s for s in scores
                  if disposal.classify(("sack", s), ("dog", 0.02), conf=0.10)
                  == disposal.CONFIRMED]
        at_high = [s for s in scores
                   if disposal.classify(("sack", s), ("dog", 0.02), conf=0.25)
                   == disposal.CONFIRMED]
        self.assertEqual(at_low, [0.12, 0.20, 0.35])
        self.assertEqual(at_high, [0.35])
        self.assertLess(len(at_high), len(at_low))

    def test_the_settings_default_is_the_gate_the_vision_module_uses(self):
        from core.models import SystemSettings
        self.assertEqual(SystemSettings.load().disposal_confidence / 100.0,
                         disposal.CONFIRM_CONF)

    def test_the_vision_layer_still_reports_three_verdicts(self):
        """Policy lives in the command. classify() stays an honest three-way
        read of what the model said, which is what the live view and the
        Object confidence on the card are built from."""
        self.assertEqual(
            disposal.classify(("sack", 0.11), ("dog", 0.04), conf=0.15),
            disposal.WEAK)
        self.assertEqual(
            disposal.classify(("sack", 0.40), ("dog", 0.04), conf=0.15),
            disposal.CONFIRMED)
        self.assertEqual(
            disposal.classify(("sack", 0.03), ("dog", 0.40), conf=0.15),
            disposal.REJECTED)

    def test_the_reject_comparison_is_the_other_half_of_the_filter(self):
        """The floor is not the only guard: a dog outscoring a sack is
        REJECTED at any floor, including 0."""
        self.assertEqual(
            disposal.classify(("sack", 0.30), ("dog", 0.45), conf=0.0),
            disposal.REJECTED)

    def test_nothing_below_the_models_own_floor_can_alert(self):
        """WEAK_PREDICT_CONF is the bottom of the dial: boxes under it are
        dropped by NMS before classify() ever sees them, so lowering
        disposal_confidence past it buys nothing."""
        self.assertGreater(disposal.WEAK_PREDICT_CONF, 0.0)
        self.assertLess(disposal.WEAK_PREDICT_CONF, disposal.CONFIRM_CONF)


class ClassifyTests(TestCase):
    """The three outcomes, named once so the live view, the alert path and the
    tests cannot drift apart."""

    def test_garbage_leading_and_clearing_the_floor_is_confirmed(self):
        self.assertEqual(disposal.classify(("sack", 0.30), ("dog", 0.10), 0.15),
                         disposal.CONFIRMED)

    def test_garbage_leading_below_the_floor_is_weak(self):
        self.assertEqual(disposal.classify(("sack", 0.11), ("dog", 0.05), 0.15),
                         disposal.WEAK)

    def test_a_reject_class_winning_is_rejected_even_above_the_floor(self):
        self.assertEqual(disposal.classify(("sack", 0.30), ("dog", 0.55), 0.15),
                         disposal.REJECTED)

    def test_nothing_detected_is_rejected_not_weak(self):
        self.assertEqual(disposal.classify(("", 0.0), ("", 0.0), 0.15),
                         disposal.REJECTED)

    def test_a_tie_on_the_floor_is_confirmed(self):
        self.assertEqual(disposal.classify(("sack", 0.15), ("dog", 0.15), 0.15),
                         disposal.CONFIRMED)

    def test_the_model_is_asked_below_the_decision_floor(self):
        """Or weak matches could not exist: predict()'s conf is an NMS
        threshold and discards every box under it."""
        self.assertLess(disposal.WEAK_PREDICT_CONF, disposal.CONFIRM_CONF)

    def test_confirm_asks_the_model_for_sub_floor_boxes(self):
        seen = {}

        class Spy(FakeWorld):
            def predict(self, crop, **kwargs):
                seen.update(kwargs)
                return super().predict(crop, **kwargs)

        disposal.confirm(Spy([(0, 0.3)]), scene(64, 64),
                         disposal.garbage_class_ids(), 0.40)
        self.assertEqual(seen["conf"], disposal.WEAK_PREDICT_CONF)


class PossibleAlertTests(TestCase):
    """A weak bag check is REPORTED, as Possible, and a later confirmation
    lands on that SAME row.

    The thing these guard is the one-row rule. A weak lead that alerts and then
    confirms is one dumped bag, and filing it twice would put two cards for one
    object in front of a reviewer -- which is how a dashboard teaches people to
    stop reading it.
    """

    OBJ_BOX = (300, 180, 60, 45)

    @classmethod
    def setUpTestData(cls):
        # The violation types and the one camera are seeded, not fixtures:
        # _create_alert writes a real row and a real FK, and a stub type would
        # not catch a mismatch with what the dashboard actually filters on.
        call_command("seed_core", verbosity=0)

    def _command(self):
        from core.management.commands import watch_disposal
        cmd = watch_disposal.Command()
        cmd.violations_dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cmd.violations_dir, True)
        cmd.stdout = types.SimpleNamespace(write=lambda *a, **k: None)
        cmd.style = types.SimpleNamespace(SUCCESS=lambda m: m, WARNING=lambda m: m)
        cmd.dry_run = False
        cmd.clip = None
        cmd._raw_buffer = None
        cmd._source_path = None
        cmd._video_pos_sec = None
        cmd.camera = Camera.objects.get(code="CAM-SMOKE-01")
        cmd.disposal_type = ViolationType.objects.get(code="disposal")
        return cmd, watch_disposal

    def _fire(self, cmd, cand, level, score=0.11, cooldown=None, now=10.0):
        """One _alert call with everything stubbed but the decision."""
        return cmd._alert(
            cand, types.SimpleNamespace(scale=1.0), scene(640, 360),
            "garbage bag", score, now, SystemSettings.load(),
            cooldown if cooldown is not None else disposal.SpatialCooldown(
                seconds=120, distance=60),
            {"confirmed": 0, "possible": 0, "cooldown": 0, "upgraded": 0,
             "adopted": 0},
            level,
        )

    def _candidate(self):
        return disposal.Candidate(1, self.OBJ_BOX, 0.0)

    def test_a_weak_verdict_files_an_alert_at_possible(self):
        """The change this suite exists for: a sub-floor lead used to be held
        back from the dashboard entirely, so the detection the system was least
        sure about was also the only one a human could not adjudicate."""
        cmd, _mod = self._command()
        cand = self._candidate()
        self.assertTrue(self._fire(cmd, cand, scoring.WARNING))
        alert = Alert.objects.get(type__code="disposal")
        self.assertEqual(alert.level, scoring.WARNING)
        self.assertEqual(alert.peak_level, scoring.WARNING)
        self.assertEqual(cand.alert_pk, alert.pk)

    def test_a_confirmed_verdict_files_at_likely(self):
        cmd, _mod = self._command()
        self.assertTrue(self._fire(cmd, self._candidate(), scoring.VIOLATION))
        alert = Alert.objects.get(type__code="disposal")
        self.assertEqual(alert.level, scoring.VIOLATION)
        self.assertEqual(alert.peak_level, scoring.VIOLATION)

    def test_a_confirmation_upgrades_the_one_row_instead_of_filing_a_second(self):
        cmd, _mod = self._command()
        cand = self._candidate()
        self._fire(cmd, cand, scoring.WARNING)
        pk = cand.alert_pk
        self.assertTrue(self._fire(cmd, cand, scoring.VIOLATION, score=0.4))
        self.assertEqual(Alert.objects.filter(type__code="disposal").count(), 1)
        alert = Alert.objects.get(pk=pk)
        self.assertEqual(alert.level, scoring.VIOLATION)
        self.assertEqual(alert.peak_level, scoring.VIOLATION)
        self.assertAlmostEqual(alert.object_confidence, 0.4, places=5)

    def test_the_upgrade_is_recorded_on_the_timeline(self):
        """So the card can say the status rose, rather than silently showing a
        different badge than the reviewer saw a minute ago."""
        cmd, _mod = self._command()
        cand = self._candidate()
        self._fire(cmd, cand, scoring.WARNING)
        self._fire(cmd, cand, scoring.VIOLATION)
        events = Alert.objects.get(pk=cand.alert_pk).timeline
        self.assertEqual([e["label"] for e in events], ["Likely"])

    def test_a_weaker_second_look_lowers_the_status_but_not_the_peak(self):
        """peak_level is what the Potential Violations list reads, so an event a
        reviewer has already been shown must not vanish mid-review."""
        cmd, _mod = self._command()
        cand = self._candidate()
        self._fire(cmd, cand, scoring.VIOLATION)
        self.assertTrue(self._fire(cmd, cand, scoring.WARNING))
        alert = Alert.objects.get(pk=cand.alert_pk)
        self.assertEqual(alert.level, scoring.WARNING)
        self.assertEqual(alert.peak_level, scoring.VIOLATION)

    def test_restating_the_same_status_writes_nothing(self):
        """The presence loop re-asks every few seconds and almost always gets
        the same answer back; that is not news and must not log or write."""
        cmd, _mod = self._command()
        cand = self._candidate()
        self._fire(cmd, cand, scoring.WARNING)
        before = Alert.objects.get(pk=cand.alert_pk).level
        self.assertFalse(self._fire(cmd, cand, scoring.WARNING))
        self.assertEqual(Alert.objects.get(pk=cand.alert_pk).level, before)
        self.assertEqual(Alert.objects.filter(type__code="disposal").count(), 1)

    def test_a_replacement_candidate_joins_the_row_rather_than_opening_one(self):
        """Stage 1 dropped the candidate and raised a fresh one for the same
        bag. See OneRowPerPlaceTests for the place rule in full."""
        cmd, _mod = self._command()
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        first = self._candidate()
        self._fire(cmd, first, scoring.WARNING, cooldown=cd)
        replacement = disposal.Candidate(2, self.OBJ_BOX, 0.0)
        self.assertTrue(self._fire(cmd, replacement, scoring.VIOLATION,
                                   cooldown=cd, now=30.0))
        self.assertEqual(Alert.objects.filter(type__code="disposal").count(), 1)
        self.assertEqual(replacement.alert_pk, first.alert_pk)

    def test_the_cooldown_does_not_block_an_upgrade_of_its_own_row(self):
        """The regression a cooldown check in front of the restate would cause:
        a Possible filed at t=0 could never become Likely at t=30, because the
        window that exists to stop a SECOND row would be suppressing the only
        update the first one needed."""
        cmd, _mod = self._command()
        cand = self._candidate()
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        self._fire(cmd, cand, scoring.WARNING, cooldown=cd)
        self.assertTrue(cd.blocks(cand.center, 30.0), "same spot, inside the window")
        self.assertTrue(self._fire(cmd, cand, scoring.VIOLATION, cooldown=cd, now=30.0))
        self.assertEqual(Alert.objects.get(pk=cand.alert_pk).level, scoring.VIOLATION)

    def test_an_upgrade_re_shoots_the_still(self):
        """The caption carries the score, so a card badged Likely over a photo
        captioned with the weak percentage that opened it reads as the system
        contradicting itself."""
        cmd, _mod = self._command()
        cand = self._candidate()
        self._fire(cmd, cand, scoring.WARNING, score=0.11)
        with mock.patch.object(cmd, "_save_still",
                               return_value="/media/violations/x.jpg") as shot:
            self._fire(cmd, cand, scoring.VIOLATION, score=0.40)
        self.assertEqual(shot.call_count, 1)
        self.assertEqual(Alert.objects.get(pk=cand.alert_pk).image_url,
                         "/media/violations/x.jpg")

    def test_a_lowered_status_keeps_the_evidence_it_was_filed_with(self):
        """The still shows the strongest claim the detector made. Replacing it
        on a weaker look would erase the frame that earned the attention."""
        cmd, _mod = self._command()
        cand = self._candidate()
        self._fire(cmd, cand, scoring.VIOLATION, score=0.40)
        before = Alert.objects.get(pk=cand.alert_pk).image_url
        with mock.patch.object(cmd, "_save_still") as shot:
            self._fire(cmd, cand, scoring.WARNING, score=0.11)
        shot.assert_not_called()
        self.assertEqual(Alert.objects.get(pk=cand.alert_pk).image_url, before)

    def test_the_alert_carries_no_cue_vector(self):
        """Borrowing scoring's two status NAMES is not adopting its cue table:
        an empty breakdown under a real status reads as a detector whose
        indicators all failed rather than one that has none."""
        cmd, _mod = self._command()
        self._fire(cmd, self._candidate(), scoring.VIOLATION)
        self.assertEqual(Alert.objects.get(type__code="disposal").cues, {})

    def test_a_dry_run_writes_no_row_and_leaves_the_candidate_unmarked(self):
        cmd, _mod = self._command()
        cmd.dry_run = True
        cand = self._candidate()
        self.assertTrue(self._fire(cmd, cand, scoring.WARNING))
        self.assertFalse(Alert.objects.filter(type__code="disposal").exists())
        self.assertIsNone(cand.alert_pk)

    def test_a_possible_alert_is_listed_not_filtered_out_as_monitoring(self):
        """AlertViewSet hides level=monitoring from the feed, so Possible must
        not land in that bucket -- the row would exist and still be invisible."""
        cmd, _mod = self._command()
        self._fire(cmd, self._candidate(), scoring.WARNING)
        alert = Alert.objects.get(type__code="disposal")
        self.assertNotEqual(alert.peak_level, scoring.MONITORING)
        self.assertIn(alert.level, scoring.NOTIFY_LEVELS)


class PossibleAlertWiringTests(TestCase):
    """The main loop has to hand both verdicts to _alert, not just one."""

    def test_both_verdicts_reach_the_alert_path(self):
        from core.management.commands import watch_disposal
        source = inspect.getsource(watch_disposal.Command._run_stream)
        self.assertIn("scoring.WARNING", source)
        self.assertIn("scoring.VIOLATION", source)

    def test_every_alert_call_has_to_state_a_level(self):
        """A defaulted level would silently file a weak lead as Likely."""
        from core.management.commands import watch_disposal
        signature = inspect.signature(watch_disposal.Command._alert)
        self.assertIs(signature.parameters["level"].default,
                      inspect.Parameter.empty)


class OneRowPerPlaceTests(TestCase):
    """One place, one violation -- and a second place is a second violation.

    The failure these close: stage 1 forgets an object a couple of unseen
    seconds after the background models absorb it, and raises a FRESH candidate
    for the same bag. With the row anchored to the candidate, one bag sitting
    on one spot became a new alert every candidate lifetime.
    """

    LEFT = (120, 200, 60, 45)
    RIGHT = (700, 200, 60, 45)

    @classmethod
    def setUpTestData(cls):
        call_command("seed_core", verbosity=0)

    def _command(self):
        from core.management.commands import watch_disposal
        cmd = watch_disposal.Command()
        cmd.violations_dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cmd.violations_dir, True)
        cmd.stdout = types.SimpleNamespace(write=lambda *a, **k: None)
        cmd.style = types.SimpleNamespace(SUCCESS=lambda m: m, WARNING=lambda m: m)
        cmd.dry_run = False
        cmd.clip = None
        cmd._raw_buffer = None
        cmd._source_path = None
        cmd._video_pos_sec = None
        cmd.camera = Camera.objects.get(code="CAM-SMOKE-01")
        cmd.disposal_type = ViolationType.objects.get(code="disposal")
        return cmd

    def _fire(self, cmd, cand, cd, level=None, score=0.4, now=10.0):
        return cmd._alert(
            cand, types.SimpleNamespace(scale=1.0), scene(640, 360),
            "garbage bag", score, now, SystemSettings.load(), cd,
            {"confirmed": 0, "possible": 0, "cooldown": 0, "upgraded": 0,
             "adopted": 0},
            level or scoring.VIOLATION,
        )

    def _cooldown(self):
        return disposal.SpatialCooldown(seconds=120, distance=60)

    def _rows(self):
        return Alert.objects.filter(type__code="disposal")

    def test_a_fresh_candidate_for_the_same_bag_re_joins_the_one_row(self):
        cmd, cd = self._command(), self._cooldown()
        first = disposal.Candidate(1, self.LEFT, 0.0)
        self._fire(cmd, first, cd, now=10.0)
        # Stage 1 lost it and raised a new id for the same object.
        again = disposal.Candidate(2, self.LEFT, 60.0)
        self.assertFalse(self._fire(cmd, again, cd, now=70.0),
                         "same status on the same spot is not news")
        self.assertEqual(self._rows().count(), 1)
        self.assertEqual(again.alert_pk, first.alert_pk)

    def test_the_adopted_row_is_the_one_that_gets_the_upgrade(self):
        """The whole value of adopting: a Possible opened by a candidate that
        has since been forgotten still becomes Likely rather than being
        reported again from scratch."""
        cmd, cd = self._command(), self._cooldown()
        first = disposal.Candidate(1, self.LEFT, 0.0)
        self._fire(cmd, first, cd, level=scoring.WARNING, score=0.11, now=10.0)
        again = disposal.Candidate(2, self.LEFT, 60.0)
        self.assertTrue(self._fire(cmd, again, cd, level=scoring.VIOLATION,
                                   score=0.4, now=70.0))
        self.assertEqual(self._rows().count(), 1)
        row = self._rows().get()
        self.assertEqual(row.pk, first.alert_pk)
        self.assertEqual(row.level, scoring.VIOLATION)

    def test_rubbish_somewhere_else_is_its_own_violation(self):
        """Left and right are two things to go and clear. Reporting them as one
        would under-count the street."""
        cmd, cd = self._command(), self._cooldown()
        left = disposal.Candidate(1, self.LEFT, 0.0)
        right = disposal.Candidate(2, self.RIGHT, 5.0)
        self.assertTrue(self._fire(cmd, left, cd, now=10.0))
        self.assertTrue(self._fire(cmd, right, cd, now=15.0))
        self.assertEqual(self._rows().count(), 2)
        self.assertNotEqual(left.alert_pk, right.alert_pk)

    def test_a_place_that_keeps_being_seen_never_lapses_into_a_second_row(self):
        """The old cooldown was a fixed window, so a bag that simply stayed put
        re-alerted the moment it expired. The window is now idle time."""
        cmd, cd = self._command(), self._cooldown()
        cand = disposal.Candidate(1, self.LEFT, 0.0)
        self._fire(cmd, cand, cd, now=10.0)
        # Well past the 120s window, but reported throughout.
        for t in (100.0, 200.0, 300.0, 400.0):
            self._fire(cmd, cand, cd, now=t)
            replacement = disposal.Candidate(int(t), self.LEFT, t)
            self._fire(cmd, replacement, cd, now=t)
        self.assertEqual(self._rows().count(), 1)

    def test_a_spot_left_untouched_for_the_whole_window_is_released(self):
        """So fresh rubbish dumped on a spot that was cleared long ago is a new
        violation -- which is what the operator's cooldown setting is for."""
        cmd, cd = self._command(), self._cooldown()
        self._fire(cmd, disposal.Candidate(1, self.LEFT, 0.0), cd, now=10.0)
        self._fire(cmd, disposal.Candidate(2, self.LEFT, 400.0), cd, now=400.0)
        self.assertEqual(self._rows().count(), 2)

    def test_the_live_candidate_sweep_holds_a_confirmed_object_s_place(self):
        """A CONFIRMED candidate is never offered to stage 2 again, so nothing
        in the verdict paths would touch its place. The per-frame sweep in
        _run_stream is the only thing keeping it open."""
        from core.management.commands import watch_disposal
        source = inspect.getsource(watch_disposal.Command._run_stream)
        self.assertIn("for live in candidates:", source)
        self.assertIn("live.alert_pk is not None", source)


class SpatialPlaceRegistryTests(TestCase):
    """SpatialCooldown on its own -- no database, no detector."""

    def test_an_unreported_place_has_nothing_to_adopt(self):
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        self.assertIsNone(cd.alert_at((100.0, 100.0), 0.0))

    def test_a_reported_place_hands_back_its_row(self):
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((100.0, 100.0), 0.0, alert_pk=7, level=scoring.WARNING)
        self.assertEqual(cd.alert_at((108.0, 96.0), 30.0), (7, scoring.WARNING))

    def test_a_place_further_than_the_distance_is_a_different_place(self):
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((100.0, 100.0), 0.0, alert_pk=7, level=scoring.WARNING)
        self.assertIsNone(cd.alert_at((400.0, 300.0), 30.0))

    def test_remembering_a_live_place_refreshes_it_instead_of_adding_one(self):
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((100.0, 100.0), 0.0, alert_pk=7, level=scoring.WARNING)
        cd.remember((100.0, 100.0), 100.0, alert_pk=7, level=scoring.VIOLATION)
        self.assertEqual(cd.alert_at((100.0, 100.0), 200.0), (7, scoring.VIOLATION))
        self.assertTrue(cd.blocks((100.0, 100.0), 219.0))

    def test_the_anchor_does_not_follow_a_jittering_box(self):
        """Following the box would let a place walk across the frame over a long
        dwell until it no longer matched the object that opened it."""
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((100.0, 100.0), 0.0, alert_pk=7)
        for t, x in enumerate(range(140, 400, 40), start=1):
            cd.remember((float(x), 100.0), float(t), alert_pk=7)
        # The original anchor is still the one that matches; the drifted
        # positions opened places of their own rather than dragging it along.
        self.assertIsNotNone(cd.alert_at((100.0, 100.0), 10.0))

    def test_a_place_with_no_row_behind_it_blocks_but_cannot_be_adopted(self):
        """A dry run claims the spot without writing anything, so the nearest
        honest behaviour is to suppress rather than to adopt a row that is not
        there."""
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((100.0, 100.0), 0.0)
        self.assertTrue(cd.blocks((100.0, 100.0), 10.0))
        self.assertIsNone(cd.alert_at((100.0, 100.0), 10.0))
