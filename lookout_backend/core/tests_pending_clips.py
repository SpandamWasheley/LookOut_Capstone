"""Deferred evidence clips — PendingClips, and the detectors that use it.

A rolling buffer holds the frames BEFORE now, so saving one the instant a
violation fires produces a clip that ENDS on the detection. These cover the
deferral that puts the moment in the middle instead.
"""

import inspect

from django.test import TestCase

from core.vision import recognition


class FakeRecorder:
    """Stands in for ClipRecorder / RawFrameRecorder: same add/save/span."""

    def __init__(self, span=30.0, ok=True):
        self._span, self._ok = span, ok
        self.saved = []

    def span(self):
        return self._span

    def save(self, path):
        if self._ok:
            self.saved.append(path)
        return self._ok


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

    def test_the_offset_lands_in_the_middle_of_a_full_buffer(self):
        """30s of footage, detection 15s before the save -> 15s in."""
        q = recognition.PendingClips()
        q.schedule("a", FakeRecorder(span=30.0), "x.mp4", now=100.0, post_seconds=15)
        (_t, _p, offset), = q.due(115.0)
        self.assertAlmostEqual(offset, 15.0, places=3)

    def test_a_short_buffer_puts_the_detection_later_not_off_the_end(self):
        """Measured from the clip ACTUALLY written: a run that ends early
        saves a truncated clip, and a marker past its end points at nothing."""
        q = recognition.PendingClips()
        q.schedule("a", FakeRecorder(span=6.0), "x.mp4", now=100.0, post_seconds=15)
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

    def test_deferral_is_off_unless_the_caller_drains(self):
        """watch_merged_all builds a parking Command and runs its OWN loop, so
        it never drains. Scheduling there would lose every annotated clip;
        falling back to the immediate save keeps the old, correct behaviour."""
        from core.management.commands.watch_parking import Command as Parking
        self.assertIsNone(Parking().pending)
        source = inspect.getsource(Parking._schedule_clip)
        self.assertIn("if self.pending is None:", source)
        self.assertIn("self.clip.save(path)", source)

    def test_a_file_run_keeps_the_raw_clip_s_own_offset(self):
        """The raw clip is cut from the source and spans both sides of the
        detection; overwriting its offset with the annotated clip's would mark
        the wrong clip's moment."""
        for name in self.WATCHERS:
            with self.subTest(watcher=name):
                source = self._source(name)
                self.assertIn("self._source_path is None", source)


class ClipWindowTests(TestCase):
    def test_the_raw_clip_is_symmetric(self):
        """POST was 2s, so the thing the alert is ABOUT arrived just as the
        clip ended. A reviewer needs what happened next."""
        self.assertEqual(recognition.RAW_CLIP_POST_SECONDS,
                         recognition.RAW_CLIP_PRE_SECONDS)

    def test_the_detection_lands_in_the_middle(self):
        pre, post = recognition.RAW_CLIP_PRE_SECONDS, recognition.RAW_CLIP_POST_SECONDS
        self.assertAlmostEqual(pre / (pre + post), 0.5, places=3)
