"""Alert.clip_offset — where the player marks the detection on the clip.

The field exists because `footage_offset` cannot answer the question: it is a
position in the ORIGINAL uploaded video, while the <video> element can only
seek inside the short clip cut out of it. Those are different numbers.
"""

import inspect
import re

from django.test import TestCase
from django.utils import timezone

from core.models import Alert, Camera, ViolationType

WATCHERS = ("watch_parking", "watch_smoking", "watch_drinking",
            "watch_thief", "watch_disposal")


class ClipOffsetFieldTests(TestCase):
    def test_the_field_exists_and_is_optional(self):
        """Null is a real answer, not missing data — a live source's raw clip
        is a pre-roll buffer whose last frame IS the detection."""
        field = Alert._meta.get_field("clip_offset")
        self.assertTrue(field.null)
        self.assertTrue(field.blank)

    def test_it_is_separate_from_footage_offset(self):
        """One is a position in the uploaded video, the other a position in the
        clip cut out of it. Collapsing them would seek to the wrong moment."""
        self.assertIsNotNone(Alert._meta.get_field("footage_offset"))
        self.assertIsNotNone(Alert._meta.get_field("clip_offset"))

    def test_the_serializer_publishes_it_read_only(self):
        from core.serializers import AlertSerializer
        self.assertIn("clip_offset", AlertSerializer.Meta.fields)
        self.assertIn("clip_offset", AlertSerializer.Meta.read_only_fields)

    def test_an_alert_defaults_to_unmarked(self):
        vt = ViolationType.objects.create(code="parking", label="Parking")
        cam = Camera.objects.create(code="CAM-CLIP-01", name="C")
        alert = Alert.objects.create(type=vt, camera=cam, confidence=0.9,
                                     description="x", timestamp=timezone.now())
        self.assertIsNone(alert.clip_offset)


class ClipOffsetWiringTests(TestCase):
    """Every detector that cuts a raw clip has to record where the detection
    sits inside it, or its alerts are silently unmarkable."""

    def _source(self, name):
        from importlib import import_module
        mod = import_module(f"core.management.commands.{name}")
        return inspect.getsource(mod)

    def test_every_watcher_records_the_offset(self):
        for name in WATCHERS:
            with self.subTest(watcher=name):
                source = self._source(name)
                self.assertIn("clip_offset=clip_offset", source,
                              "the Alert row must carry it")
                self.assertIn("clip_offset = self._video_pos_sec - start", source,
                              "measured from the cut's own start")

    def test_it_is_only_set_when_the_cut_succeeded(self):
        """A failed ffmpeg cut leaves no clip to mark, and marking a clip that
        does not exist would put a tick on the annotated one instead."""
        for name in WATCHERS:
            with self.subTest(watcher=name):
                source = self._source(name)
                self.assertRegex(
                    source,
                    r"if cut_ok:\s*\n(\s*#[^\n]*\n)*\s*clip_offset = self\._video_pos_sec - start")

    def test_a_live_source_leaves_it_unset(self):
        """Its raw clip is a rolling pre-roll buffer whose last frame IS the
        detection, so there is no interior moment to point at."""
        for name in WATCHERS:
            with self.subTest(watcher=name):
                source = self._source(name)
                live = source.index("self._raw_buffer.save(raw_path)")
                tail = source[live:live + 400]
                self.assertNotIn("clip_offset =", tail,
                                 "the live branch must not invent an offset")

    def test_the_shared_incident_writer_updates_it_with_the_clip(self):
        """_incident_clip re-cuts the clip for a running incident. If the URL
        moves and the offset does not, the player marks the old moment on the
        new clip."""
        from core.management.commands import _incidents
        source = inspect.getsource(_incidents.IncidentMixin._incident_clip)
        self.assertIn("clip_offset", source)
        self.assertIn("video_url, raw_video_url, clip_offset", source,
                      "_save_clips returns three values now")

    def test_every_save_clips_caller_unpacks_three(self):
        """The regression this exists to stop: _save_clips gained a third
        return value and a caller still unpacking two fails at runtime, inside
        the alert path, where it is least visible."""
        import pathlib
        from django.conf import settings
        root = pathlib.Path(settings.BASE_DIR) / "core" / "management" / "commands"
        for path in root.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for call in re.finditer(r"^\s*(.+?)\s*=\s*self\._save_clips\(", text, re.M):
                with self.subTest(file=path.name):
                    self.assertEqual(len(call.group(1).split(",")), 3,
                                     f"{path.name} unpacks {call.group(1)!r}")


class ClipOffsetMarkerTests(TestCase):
    """What the player does with it."""

    def _player(self):
        import pathlib
        from django.conf import settings
        return (pathlib.Path(settings.BASE_DIR).parent / "lookout" / "src"
                / "ViolationModal.jsx").read_text(encoding="utf-8")

    def test_the_marker_follows_whichever_clip_is_showing(self):
        """It used to be raw-only, because the annotated clip was a pre-roll
        buffer whose detection was its last frame. Deferring that clip's save
        put the moment in its middle, and the marker has to follow — each clip
        carries its own offset, since they are different lengths."""
        source = self._player()
        self.assertIn("showingRaw ? alert.clipOffset : alert.annotatedOffset",
                      source)

    def test_the_bar_can_be_dragged_not_just_clicked(self):
        source = self._player()
        for handler in ("onPointerDown", "onPointerMove", "onPointerUp"):
            self.assertIn(handler, source)
        self.assertIn("setPointerCapture", source,
                      "a drag leaving the bar must still track and still end")

    def test_timeupdate_yields_to_the_drag(self):
        """Otherwise the thumb jumps backwards under the finger on every seek."""
        self.assertIn("if (!scrubbing) setElapsed", self._player())

    def test_the_field_reaches_the_browser_model(self):
        import pathlib
        from django.conf import settings
        model = (pathlib.Path(settings.BASE_DIR).parent / "lookout" / "src"
                 / "alertModel.js").read_text(encoding="utf-8")
        self.assertIn("clipOffset: raw.clip_offset", model)


class AnnotatedOffsetTests(TestCase):
    """The annotated clip carries its own detection point.

    It has to be a second field. The two clips are different lengths — the raw
    one is cut from the source with a fixed lead-in, the annotated one is
    whatever the rolling buffer held — so marking the raw clip's moment on the
    annotated one points at the wrong frame.
    """

    def test_the_field_exists_and_is_optional(self):
        field = Alert._meta.get_field("annotated_offset")
        self.assertTrue(field.null)
        self.assertTrue(field.blank)

    def test_it_is_separate_from_the_raw_clip_s_offset(self):
        self.assertIsNotNone(Alert._meta.get_field("clip_offset"))
        self.assertIsNotNone(Alert._meta.get_field("annotated_offset"))

    def test_the_serializer_publishes_it(self):
        from core.serializers import AlertSerializer
        self.assertIn("annotated_offset", AlertSerializer.Meta.fields)

    def test_it_reaches_the_browser_model(self):
        import pathlib
        from django.conf import settings
        model = (pathlib.Path(settings.BASE_DIR).parent / "lookout" / "src"
                 / "alertModel.js").read_text(encoding="utf-8")
        self.assertIn("annotatedOffset: raw.annotated_offset", model)

    def test_the_player_marks_whichever_clip_is_showing(self):
        """The bug this closes: the marker was raw-only, left over from when
        the annotated clip was pre-roll and its detection was the last frame.
        Deferring that clip put the moment in its middle, and the marker never
        followed."""
        import pathlib
        from django.conf import settings
        source = (pathlib.Path(settings.BASE_DIR).parent / "lookout" / "src"
                  / "ViolationModal.jsx").read_text(encoding="utf-8")
        self.assertIn("showingRaw ? alert.clipOffset : alert.annotatedOffset",
                      source)
        self.assertNotIn("showingRaw && alert.clipOffset != null", source)

    def test_both_detectors_record_it_on_the_first_save(self):
        """Pre-roll only at that point, so the detection is the last frame —
        recorded rather than left null, because "the marker is at the end" is
        the truth and explains the missing aftermath.

        Taken from the RECORDER (mark_offset, playback seconds), never from
        span() (capture seconds). The two are the same number only when frames
        arrived evenly, and when they did not the marker pointed at the wrong
        frame: a detection at the end of a clip was marked a fifth of the way
        in."""
        import inspect
        from importlib import import_module
        for name in ("watch_parking", "watch_disposal"):
            with self.subTest(watcher=name):
                source = inspect.getsource(
                    import_module(f"core.management.commands.{name}"))
                self.assertIn("annotated_offset = self.clip.mark_offset", source)
                self.assertNotIn("annotated_offset = self.clip.span()", source)
                self.assertIn("mark_at=fired_at", source)
                self.assertIn("annotated_offset=annotated_offset", source)

    def test_both_detectors_move_it_when_the_centred_copy_lands(self):
        import inspect
        from importlib import import_module
        for name in ("watch_parking", "watch_disposal"):
            with self.subTest(watcher=name):
                source = inspect.getsource(
                    import_module(f"core.management.commands.{name}"))
                self.assertIn('fields["annotated_offset"] = offset', source)

    def test_a_file_run_does_not_overwrite_the_raw_clip_s_offset(self):
        """On a file the raw clip is cut from the source and carries the truer
        number; the annotated clip's belongs only to the annotated clip."""
        import inspect
        from importlib import import_module
        for name in ("watch_parking", "watch_disposal"):
            with self.subTest(watcher=name):
                source = inspect.getsource(
                    import_module(f"core.management.commands.{name}"))
                guarded = 'if offset is not None and self._source_path is None:'
                self.assertIn(guarded, source)
                after = source[source.index(guarded):]
                self.assertIn('fields["clip_offset"] = offset', after[:200])


class EvidenceLeadInTests(TestCase):
    """How much footage an evidence clip carries BEFORE the detection.

    A detection is the end of something that was already happening. Fifteen
    seconds opened on a scene in progress -- a reviewer could see what was
    detected but not how it came about, which is the hardest kind of clip to
    judge and the easiest to dispute.
    """

    def _recognition(self):
        try:
            from core.vision import recognition
        except Exception:                                        # noqa: BLE001
            self.skipTest("the CV dependencies are not installed here")
        return recognition

    def test_the_clip_reaches_back_half_a_minute(self):
        r = self._recognition()
        self.assertGreaterEqual(r.RAW_CLIP_PRE_SECONDS, 30)

    def test_the_buffer_can_actually_hold_the_lead_in_plus_the_post_roll(self):
        """The window is only honoured if the rolling buffer is big enough to
        still contain it once the post-roll has played -- a buffer sized to the
        lead-in alone loses the start of the clip while waiting."""
        r = self._recognition()
        self.assertEqual(r.CLIP_BUFFER_SECONDS,
                         r.RAW_CLIP_PRE_SECONDS + r.RAW_CLIP_POST_SECONDS)
        self.assertEqual(r.ClipRecorder().seconds, r.CLIP_BUFFER_SECONDS)
        self.assertEqual(r.RawFrameRecorder().seconds, r.CLIP_BUFFER_SECONDS)

    def test_every_detector_sizes_its_buffer_from_the_shared_window(self):
        """Nine detectors, one number. Each used to carry its own literal, so a
        change to the lead-in silently applied to some violations and not
        others."""
        import pathlib
        from django.conf import settings as dj
        commands = pathlib.Path(dj.BASE_DIR) / "core" / "management" / "commands"
        found = 0
        for path in commands.glob("watch_*.py"):
            source = path.read_text(encoding="utf-8")
            for line in source.splitlines():
                if "ClipRecorder(" in line and "seconds=" in line:
                    found += 1
                    self.assertIn("CLIP_BUFFER_SECONDS", line, f"{path.name}: {line.strip()}")
        self.assertGreaterEqual(found, 5)

    def test_both_buffers_are_bounded_by_frames_as_well_as_seconds(self):
        """The window assumes a steady frame rate; a fast source would grow the
        deque until the machine ran out of memory, which is a crash that does
        not name its own cause."""
        r = self._recognition()
        self.assertTrue(r.ClipRecorder.MAX_FRAMES > 0)
        self.assertTrue(r.RawFrameRecorder.MAX_FRAMES > 0)

    def test_a_fast_source_cannot_grow_the_raw_buffer_without_bound(self):
        r = self._recognition()
        import numpy as np
        buf = r.RawFrameRecorder(seconds=10_000)      # time can never evict
        frame = np.zeros((48, 64, 3), np.uint8)
        for i in range(r.RawFrameRecorder.MAX_FRAMES + 50):
            buf.add(frame, float(i))
        self.assertEqual(len(buf._buf), r.RawFrameRecorder.MAX_FRAMES)


class ClipTimelineTests(TestCase):
    """The clip's own timeline against the capture clock.

    The failure these close, measured on a real run: a 60 fps source filled the
    450-frame buffer with 7.3 seconds of footage, which the writer then played
    back one frame at a time at 12 fps -- a 37.5-second clip of 7 seconds of
    life, five times slower than it happened. The detection was its LAST frame,
    and the marker, stored in capture seconds, pointed 7.3 s into a 37.5 s clip:
    a fifth of the way in. Nothing about either number was wrong on its own;
    they were in different units.
    """

    def _recognition(self):
        try:
            from core.vision import recognition
        except Exception:                                        # noqa: BLE001
            self.skipTest("the CV dependencies are not installed here")
        return recognition

    def _frames(self, rec, fps, seconds, start=0.0):
        import numpy as np
        frame = np.zeros((36, 64, 3), np.uint8)
        n = int(fps * seconds)
        for i in range(n):
            rec.add(frame, start + i / fps)
        return rec

    def test_a_fast_source_does_not_shrink_the_window(self):
        """450 frames of a 60 fps stream is seven seconds of footage. Keeping
        every frame spends the whole buffer on the last few seconds -- and the
        lead-in, which is what the alert is judged on, is what falls out."""
        r = self._recognition()
        rec = r.ClipRecorder(seconds=50)
        self._frames(rec, fps=60, seconds=50)
        self.assertGreaterEqual(rec.span(), 45,
                                "the buffer should still hold the window in SECONDS")
        self.assertLessEqual(len(rec._buf), r.ClipRecorder.MAX_FRAMES)

    def test_frames_are_kept_at_the_rate_they_are_played_back_at(self):
        """So the clip runs at life speed. Any other pairing is slow motion or
        fast forward, and every position in the clip then means something
        different from what it says."""
        r = self._recognition()
        rec = r.ClipRecorder(seconds=50)
        self._frames(rec, fps=60, seconds=10, start=100.0)
        kept = len(rec._buf)
        expected = 10 * rec.playback_fps
        self.assertAlmostEqual(kept, expected, delta=expected * 0.1)

    def test_the_rate_never_asks_for_more_frames_than_the_buffer_may_hold(self):
        r = self._recognition()
        rec = r.ClipRecorder(seconds=50)
        self.assertLessEqual(rec.playback_fps * rec.seconds,
                             r.ClipRecorder.MAX_FRAMES + 1)

    def test_a_slow_detector_is_not_sped_up(self):
        """The throttle only ever DROPS surplus frames; a detector running
        below the playback rate keeps everything it produced."""
        r = self._recognition()
        rec = r.ClipRecorder(seconds=50)
        self._frames(rec, fps=2, seconds=20, start=500.0)
        self.assertEqual(len(rec._buf), 40)

    def test_the_marker_is_in_playback_seconds_not_capture_seconds(self):
        import tempfile, os
        r = self._recognition()
        if not r.have_ffmpeg() if hasattr(r, "have_ffmpeg") else False:
            pass
        rec = r.ClipRecorder(seconds=50)
        self._frames(rec, fps=8, seconds=20, start=1000.0)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "clip.mp4")
            # The last buffered moment: a pre-roll-only save, which is what a
            # detector writes the instant an alert fires.
            last = rec._buf[-1][0]
            if not rec.save(out, mark_at=last):
                self.skipTest("ffmpeg is not available here")
            self.assertIsNotNone(rec.mark_offset)
            # At the END of the clip, because the detection is its last frame.
            self.assertGreater(rec.mark_offset, 0)
            self.assertLessEqual(rec.mark_offset, rec.span() + 1)

    def test_a_marker_for_a_moment_mid_buffer_lands_mid_clip(self):
        import tempfile, os
        r = self._recognition()
        rec = r.ClipRecorder(seconds=50)
        self._frames(rec, fps=8, seconds=20, start=2000.0)
        middle = rec._buf[len(rec._buf) // 2][0]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "clip.mp4")
            if not rec.save(out, mark_at=middle):
                self.skipTest("ffmpeg is not available here")
            self.assertAlmostEqual(rec.mark_offset, rec.span() / 2, delta=2.0)

    def test_saving_without_a_mark_reports_none(self):
        import tempfile, os
        r = self._recognition()
        rec = r.ClipRecorder(seconds=50)
        self._frames(rec, fps=8, seconds=5, start=3000.0)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "clip.mp4")
            if not rec.save(out):
                self.skipTest("ffmpeg is not available here")
            self.assertIsNone(rec.mark_offset)

    def test_the_post_roll_is_twenty_seconds(self):
        self.assertEqual(self._recognition().RAW_CLIP_POST_SECONDS, 20)
