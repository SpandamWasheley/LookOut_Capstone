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

    def test_the_marker_is_only_drawn_on_the_raw_clip(self):
        """The annotated clip is a pre-roll buffer — its detection is the last
        frame, so a marker there says nothing a viewer cannot see."""
        source = self._player()
        self.assertIn("showingRaw && alert.clipOffset != null", source)

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
