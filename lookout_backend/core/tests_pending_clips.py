"""Deferred evidence clips — PendingClips, and the detectors that use it.

A rolling buffer holds the frames BEFORE now, so saving one the instant a
violation fires produces a clip that ENDS on the detection. These cover the
deferral that puts the moment in the middle instead.
"""

import inspect

from django.test import TestCase

from core.vision import recognition


class FakeRecorder:
    """Stands in for ClipRecorder / RawFrameRecorder: same add/save/span.

    `save` takes `mark_at` and answers on `mark_offset`, as the real recorders
    do -- only they can say where a moment landed in the written clip, because
    only they know how long each frame was held."""

    def __init__(self, span=30.0, ok=True, fps=12.0):
        self._span, self._ok, self._fps = span, ok, fps
        self.saved = []
        self.mark_offset = None
        self.marked_at = None

    def span(self):
        return self._span

    def save(self, path, mark_at=None):
        self.mark_offset = None
        if not self._ok:
            return False
        self.saved.append(path)
        self.marked_at = mark_at
        if mark_at is not None:
            # The buffer is taken to end at the save, as the real one does:
            # the moment is `span` back from its last frame at most.
            start = self.marked_at + 0.0
            self.mark_offset = max(0.0, min(self._span, self._span - (self._now_gap())))
        return True

    def _now_gap(self):
        return getattr(self, "gap_after_mark", 15.0)


class PendingClipsTests(TestCase):
    def test_nothing_is_saved_before_the_post_roll_has_played(self):
        """The whole point: saving now would produce the clip this exists to
        stop — one that ends on the detection."""
        q = recognition.PendingClips()
        rec = FakeRecorder()
        q.schedule("a", rec, "x.mp4", now=100.0, post_seconds=15)
        self.assertEqual(q.due(105.0), [])
        self.assertEqual(rec.saved, [])
        self.assertEqual(len(q), 1)

    def test_it_is_saved_once_the_wait_is_over(self):
        q = recognition.PendingClips()
        rec = FakeRecorder()
        q.schedule("a", rec, "x.mp4", now=100.0, post_seconds=15)
        out = q.due(115.0)
        self.assertEqual([t for t, _p, _o in out], ["a"])
        self.assertEqual(rec.saved, ["x.mp4"])
        self.assertEqual(len(q), 0, "and it is not offered twice")

    def test_the_recorder_is_asked_where_the_detection_landed(self):
        """The queue no longer works the position out from `now` and the span.
        That assumed frames arrived evenly AND that the buffer still ended at
        `now` -- false for the final flush at the end of a run, and false for
        any clip whose frames were held unevenly."""
        q = recognition.PendingClips()
        rec = FakeRecorder(span=30.0)
        q.schedule("a", rec, "x.mp4", now=100.0, post_seconds=15)
        (_t, _p, offset), = q.due(115.0)
        self.assertEqual(rec.marked_at, 100.0, "the moment, not the save time")
        self.assertEqual(offset, rec.mark_offset)

    def test_a_marker_is_never_past_the_end_of_the_clip(self):
        """A run that ends early saves a truncated clip, and a marker past its
        end points at nothing."""
        q = recognition.PendingClips()
        rec = FakeRecorder(span=6.0)
        rec.gap_after_mark = 15.0
        q.schedule("a", rec, "x.mp4", now=100.0, post_seconds=15)
        (_t, _p, offset), = q.due(115.0)
        self.assertGreaterEqual(offset, 0.0)
        self.assertLessEqual(offset, 6.0)

    def test_flush_writes_what_is_still_owed(self):
        """A run that reaches the end of its footage still owes every clip it
        scheduled — an alert whose evidence was never written is a lost alert."""
        q = recognition.PendingClips()
        rec = FakeRecorder()
        q.schedule("a", rec, "x.mp4", now=100.0, post_seconds=15)
        out = q.flush(103.0)          # nowhere near due
        self.assertEqual([t for t, _p, _o in out], ["a"])
        self.assertEqual(rec.saved, ["x.mp4"])
        self.assertEqual(len(q), 0)

    def test_a_failed_save_yields_nothing(self):
        """No file, so no row update — a video_url pointing at a clip that was
        never written is worse than an empty one."""
        q = recognition.PendingClips()
        q.schedule("a", FakeRecorder(ok=False), "x.mp4", now=100.0, post_seconds=15)
        self.assertEqual(q.due(115.0), [])

    def test_several_clips_come_due_independently(self):
        q = recognition.PendingClips()
        q.schedule("a", FakeRecorder(), "a.mp4", now=100.0, post_seconds=15)
        q.schedule("b", FakeRecorder(), "b.mp4", now=110.0, post_seconds=15)
        self.assertEqual([t for t, _p, _o in q.due(116.0)], ["a"])
        self.assertEqual(len(q), 1)
        self.assertEqual([t for t, _p, _o in q.due(126.0)], ["b"])

    def test_both_real_recorders_report_a_span(self):
        """The offset is measured from it, so a recorder without one would
        silently produce unmarked clips."""
        for cls in (recognition.ClipRecorder, recognition.RawFrameRecorder):
            with self.subTest(recorder=cls.__name__):
                self.assertTrue(hasattr(cls, "span"))
                self.assertEqual(cls().span(), 0.0, "empty buffer, no span")


class DeferredClipWiringTests(TestCase):
    """The two detectors that own their alert path. smoking/drinking/thief
    re-save through IncidentMixin._incident_clip when an incident ends, so
    their clips already carry footage from after the alert."""

    WATCHERS = ("watch_parking", "watch_disposal")
    # Every detector that writes an Alert, for the clip-before-row guarantee
    # below. smoking/drinking/thief never deferred, so they always had it —
    # the point of checking all five is that it stays true of all five.
    ALL_WATCHERS = ("watch_parking", "watch_disposal", "watch_smoking",
                    "watch_drinking", "watch_thief")

    def _source(self, name):
        from importlib import import_module
        return inspect.getsource(import_module(f"core.management.commands.{name}"))

    def test_they_schedule_instead_of_saving_at_alert_time(self):
        for name in self.WATCHERS:
            with self.subTest(watcher=name):
                source = self._source(name)
                self.assertIn("pending.schedule(", source)
                self.assertIn("_drain_pending", source)

    def test_the_queue_is_drained_every_frame_and_again_at_the_end(self):
        """Without the final flush, an alert near the end of a clip would
        schedule evidence the run then exits without ever writing."""
        for name in self.WATCHERS:
            with self.subTest(watcher=name):
                source = self._source(name)
                self.assertIn("self._drain_pending(now_ts)", source)
                self.assertIn("final=True", source)

    def test_every_alert_has_its_clip_before_the_row_exists(self):
        """The guarantee, and the thing deferring the only save broke: the
        annotated clip is written BEFORE Alert.objects.create, so a row never
        exists without one — not for an instant, not if the run is cancelled a
        second later, and not if SIGTERM ends the process before the flush in
        `finally` can run."""
        import re
        for name in self.ALL_WATCHERS:
            with self.subTest(watcher=name):
                source = self._source(name)
                # Either the recorder is saved inline, or via the shared
                # _save_clips helper — whichever, it has to come first.
                saved = min(m.start() for m in
                            re.finditer(r"clip\.save\(|_save_clips\(base", source))
                created = source.index("Alert.objects.create(")
                self.assertLess(saved, created,
                                "save the clip, then write the row")

    def test_deferral_only_ever_adds_the_centred_copy(self):
        """watch_merged_all builds a parking Command and runs its OWN loop, so
        it never drains. That must now cost only the centring, never the clip
        — which is why the first save no longer lives behind this check."""
        from core.management.commands.watch_parking import Command as Parking
        self.assertIsNone(Parking().pending)
        source = inspect.getsource(Parking._schedule_clip)
        self.assertIn("if self.pending is None:", source)
        self.assertIn("return", source)
        self.assertNotIn("Alert.objects.filter", source,
                         "the row is written with the clip already on it")

    def test_a_file_run_keeps_the_raw_clip_s_own_offset(self):
        """The raw clip is cut from the source and spans both sides of the
        detection; overwriting its offset with the annotated clip's would mark
        the wrong clip's moment."""
        for name in self.WATCHERS:
            with self.subTest(watcher=name):
                source = self._source(name)
                self.assertIn("self._source_path is None", source)


class ClipWindowTests(TestCase):
    """The shape of the evidence window either side of the detection.

    These two started out asserting SYMMETRY -- the fix for a 2-second
    post-roll, where the thing the alert was about arrived just as the clip
    ended. That half of it still holds and is still tested: there must be a
    real post-roll, because what settles most alerts is what happened next.

    What changed is the other half. A detection is the END of something that
    was already under way, so the lead-in does more work than the run-out: 15
    seconds routinely opened on a scene already in progress. The clip is now
    deliberately front-weighted, and these guard the shape rather than the
    symmetry it used to have.
    """

    def test_there_is_still_a_real_post_roll(self):
        """The original failure: a clip that ends on the detection shows the
        approach and then nothing."""
        self.assertGreaterEqual(recognition.RAW_CLIP_POST_SECONDS, 10)

    def test_the_lead_in_is_the_longer_half(self):
        pre, post = recognition.RAW_CLIP_PRE_SECONDS, recognition.RAW_CLIP_POST_SECONDS
        self.assertGreater(pre, post)
        # Far enough in that a viewer's instinct still finds it on the scrub
        # bar, rather than at the very end where it started.
        self.assertLess(pre / (pre + post), 0.8)
