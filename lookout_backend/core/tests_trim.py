"""Running a detector over part of a clip instead of all of it.

The span is applied by SEEKING, not by cutting a second file (the machine this
runs on has been at 98% disk), and it is measured in FOOTAGE seconds — the same
clock the watchers already use for dwell and duration — so a trim lines up with
what somebody selected while scrubbing the clip in a browser.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Camera
from core.vision.trim import Trim


class TrimValueTests(TestCase):
    def test_nothing_set_is_the_whole_clip(self):
        t = Trim()
        self.assertFalse(t)
        self.assertEqual(t.describe(), "whole clip")
        self.assertIsNone(t.end)

    def test_zero_end_means_run_to_the_end(self):
        # The API sends 0 for "not set"; a clip that genuinely ends at 0s does
        # not exist, so this is unambiguous.
        t = Trim(start=10, end=0)
        self.assertIsNone(t.end)
        self.assertTrue(t)
        self.assertEqual(t.describe(), "10s to end")

    def test_a_backwards_span_is_read_as_no_end(self):
        # Belt-and-braces: the API rejects this before it gets here, but an end
        # BEFORE the start must never become "stop immediately", which would
        # look like a detector that does nothing.
        self.assertIsNone(Trim(start=30, end=10).end)

    def test_a_negative_start_is_clamped(self):
        self.assertEqual(Trim(start=-5).start, 0.0)

    def test_a_real_span_describes_itself(self):
        self.assertEqual(Trim(20, 50).describe(), "20s to 50s")


class PastEndTests(TestCase):
    def test_it_stops_at_the_chosen_end(self):
        t = Trim(10, 20)
        self.assertFalse(t.past_end(19.9, is_live=False))
        self.assertTrue(t.past_end(20.0, is_live=False))
        self.assertTrue(t.past_end(25.0, is_live=False))

    def test_a_live_source_is_never_past_its_end(self):
        # An RTSP stream has no position to run past; applying a trim to one
        # would silently stop the camera after N seconds.
        self.assertFalse(Trim(10, 20).past_end(9999, is_live=True))

    def test_an_untrimmed_run_never_stops_early(self):
        self.assertFalse(Trim().past_end(1e6, is_live=False))

    def test_an_unknown_position_does_not_stop_the_run(self):
        # CAP_PROP_POS_MSEC can come back as None/garbage on some containers.
        # Treating that as "past the end" would end the run on frame one.
        self.assertFalse(Trim(0, 20).past_end(None, is_live=False))


class SeekTests(TestCase):
    def test_it_seeks_to_the_start_in_milliseconds(self):
        import cv2

        cap = mock.Mock()
        cap.set.return_value = True
        self.assertTrue(Trim(start=12.5).seek(cap, is_live=False))
        cap.set.assert_called_once_with(cv2.CAP_PROP_POS_MSEC, 12500.0)

    def test_it_does_not_seek_a_live_source(self):
        cap = mock.Mock()
        self.assertFalse(Trim(start=12.5).seek(cap, is_live=True))
        cap.set.assert_not_called()

    def test_a_zero_start_does_not_seek(self):
        # Seeking to 0 is harmless but not free on some containers, and the
        # "Trimmed to ..." line should not appear for an untrimmed run.
        cap = mock.Mock()
        self.assertFalse(Trim(start=0, end=30).seek(cap, is_live=False))
        cap.set.assert_not_called()


class ApiTests(TestCase):
    """What the Run Detection page sends, and what reaches the subprocess."""

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username="adm", password="x", role="admin")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)
        self.camera = Camera.objects.create(code="CAM-01", name="Gate",
                                            stream_url="rtsp://cam/1")

    def _argv(self, **body):
        """Starts a job with Popen stubbed, and returns the argv it was given."""
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value.pid = 4321
            with mock.patch("threading.Thread"):
                r = self.c.post("/api/detection-jobs/", body)
        self.assertEqual(r.status_code, 201, r.content)
        return popen.call_args[0][0]

    def _upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile("clip.mp4", b"\x00" * 64, content_type="video/mp4")

    def test_a_span_becomes_start_and_end_flags(self):
        with mock.patch("cv2.VideoCapture") as cap:
            cap.return_value.isOpened.return_value = True
            argv = self._argv(violation_type="smoking", file=self._upload(),
                              trim_start="20", trim_end="50")
        self.assertIn("--start", argv)
        self.assertEqual(argv[argv.index("--start") + 1], "20.000")
        self.assertEqual(argv[argv.index("--end") + 1], "50.000")

    def test_an_untrimmed_run_passes_neither_flag(self):
        with mock.patch("cv2.VideoCapture") as cap:
            cap.return_value.isOpened.return_value = True
            argv = self._argv(violation_type="smoking", file=self._upload())
        self.assertNotIn("--start", argv)
        self.assertNotIn("--end", argv)

    def test_a_live_camera_is_never_trimmed(self):
        # No position to seek to. Passing --start to a live watcher would make
        # it look broken for the first N seconds.
        argv = self._argv(violation_type="smoking", camera_id=self.camera.pk,
                          trim_start="20", trim_end="50")
        self.assertNotIn("--start", argv)
        self.assertNotIn("--end", argv)

    def test_a_backwards_span_is_rejected(self):
        with mock.patch("cv2.VideoCapture") as cap:
            cap.return_value.isOpened.return_value = True
            r = self.c.post("/api/detection-jobs/", {
                "violation_type": "smoking", "file": self._upload(),
                "trim_start": "50", "trim_end": "20"})
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("end after it starts", r.json()["detail"])

    def test_nonsense_is_rejected_rather_than_coerced(self):
        with mock.patch("cv2.VideoCapture") as cap:
            cap.return_value.isOpened.return_value = True
            r = self.c.post("/api/detection-jobs/", {
                "violation_type": "smoking", "file": self._upload(),
                "trim_start": "banana"})
        self.assertEqual(r.status_code, 400, r.content)


class WatcherFlagTests(TestCase):
    """Every detector the page can launch must accept the flags it is sent."""

    def test_all_launchable_watchers_take_start_and_end(self):
        from django.core.management import load_command_class

        from core.views import DETECTION_COMMANDS

        for key, name in DETECTION_COMMANDS.items():
            parser = load_command_class("core", name).create_parser("manage.py", name)
            flags = {a for action in parser._actions for a in action.option_strings}
            self.assertIn("--start", flags, f"{key} ({name}) cannot be trimmed")
            self.assertIn("--end", flags, f"{key} ({name}) cannot be trimmed")
