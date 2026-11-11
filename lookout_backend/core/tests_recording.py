"""The continuous tape: naming, listing, the ring buffer, and serving a segment.

Nothing here runs ffmpeg or touches a camera. Segments are fabricated as files
whose names and contents carry the only two facts the module reads from them --
when they started (the name) and whether ffmpeg closed them (a moov atom near
the front, which +faststart guarantees).
"""
import datetime
import os
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from core import recording

CODE = "CAM-SMOKE-01"


def _media():
    """A throwaway RECORDINGS_ROOT. Each test gets its own so a sweep can
    delete with abandon."""
    tmp = tempfile.TemporaryDirectory()
    return tmp, override_settings(RECORDINGS_ROOT=tmp.name)


class SegmentFileTests(TestCase):
    def setUp(self):
        self.tmp, self.override = _media()
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.tmp.cleanup)
        recording.camera_dir(CODE).mkdir(parents=True, exist_ok=True)

    def write(self, name, size=1024, finished=True, mtime=None):
        path = recording.camera_dir(CODE) / name
        head = b"\x00\x00\x00\x18ftypmp42" + (b"moov" if finished else b"wide")
        path.write_bytes(head + b"\x00" * max(0, size - len(head)))
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    # ---- names -------------------------------------------------------------

    def test_a_segment_name_is_read_as_the_local_moment_it_started(self):
        at = recording.started_at("2026-11-11_14-30-00.mp4")
        self.assertEqual((at.year, at.month, at.day, at.hour, at.minute), (2026, 11, 11, 14, 30))
        # Aware, with the RECORDER machine's offset: ffmpeg's -strftime writes
        # local time while Django's TIME_ZONE is UTC, and reading one as the
        # other would move every segment eight hours in Manila.
        self.assertIsNotNone(at.tzinfo)

    def test_anything_not_named_like_a_segment_is_not_footage(self):
        for name in (".recorder.pid", "notes.txt", "2026-13-01_00-00-00.mp4",
                     "2026-02-30_10-00-00.mp4", "../../secrets.mp4"):
            self.assertIsNone(recording.started_at(name), name)

    def test_only_a_closed_segment_counts_as_finished(self):
        self.assertTrue(recording.is_finished(self.write("2026-11-11_10-00-00.mp4")))
        self.assertFalse(recording.is_finished(
            self.write("2026-11-11_10-10-00.mp4", finished=False)))

    # ---- listing -----------------------------------------------------------

    def test_a_segment_is_measured_to_the_start_of_the_next_one(self):
        self.write("2026-11-11_10-00-00.mp4")
        self.write("2026-11-11_10-10-00.mp4")
        first = recording.list_segments(CODE)[0]
        self.assertEqual(first["seconds"], 600)

    def test_a_gap_in_the_tape_is_not_reported_as_footage(self):
        # The PC was off between these two. Without the clamp the 10:00
        # segment would claim to cover four hours nobody recorded.
        self.write("2026-11-11_10-00-00.mp4")
        self.write("2026-11-11_14-00-00.mp4")
        first = recording.list_segments(CODE, max_seconds=600)[0]
        self.assertEqual(first["seconds"], 600)

    def test_days_are_summarised_newest_first_and_ignore_other_files(self):
        self.write("2026-11-10_23-50-00.mp4", size=2048)
        self.write("2026-11-11_00-00-00.mp4", size=4096)
        self.write("2026-11-11_00-10-00.mp4", size=4096)
        (recording.camera_dir(CODE) / "readme.txt").write_text("not footage")
        days = recording.list_days(CODE, max_seconds=600)
        self.assertEqual([d["date"].isoformat() for d in days], ["2026-11-11", "2026-11-10"])
        self.assertEqual(days[0]["segments"], 2)
        self.assertEqual(days[0]["bytes"], 8192)

    def test_usage_reports_the_span_the_archive_reaches_back_to(self):
        self.write("2026-11-09_08-00-00.mp4", size=100)
        self.write("2026-11-11_08-00-00.mp4", size=100)
        use = recording.usage(CODE)
        self.assertEqual(use["segments"], 2)
        self.assertEqual(use["bytes"], 200)
        self.assertEqual(use["oldest"].date(), datetime.date(2026, 11, 9))
        self.assertEqual(use["newest"].date(), datetime.date(2026, 11, 11))

    # ---- the ring buffer ---------------------------------------------------

    def _dated(self, days_ago, hour=12, size=1024):
        when = datetime.datetime.now() - datetime.timedelta(days=days_ago)
        when = when.replace(hour=hour, minute=0, second=0, microsecond=0)
        return self.write(when.strftime(recording.SEGMENT_NAME_FORMAT) + ".mp4", size=size)

    def test_footage_past_the_retention_window_goes_oldest_first(self):
        old = self._dated(30)
        recent = self._dated(1)
        newest = self._dated(0)
        deleted, _freed, reason = recording.sweep(CODE, retention_days=7, max_gb=100)
        self.assertEqual(deleted, 1)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists() and newest.exists())
        self.assertEqual(reason, "the retention window")

    def test_the_disk_budget_deletes_oldest_first_even_inside_the_window(self):
        a = self._dated(2, hour=8, size=4 << 20)
        b = self._dated(2, hour=9, size=4 << 20)
        c = self._dated(2, hour=10, size=4 << 20)
        deleted, freed, reason = recording.sweep(
            CODE, retention_days=30, max_gb=9 / 1024)        # 9 MB of budget
        self.assertEqual(deleted, 1)
        self.assertFalse(a.exists())
        self.assertTrue(b.exists() and c.exists())
        self.assertEqual(freed, 4 << 20)
        self.assertEqual(reason, "the disk budget")

    def test_the_segment_being_recorded_is_never_swept(self):
        # Every file is older than the window and the budget is zero-ish, but
        # the newest one is the file ffmpeg has open: deleting it loses the
        # ten minutes being recorded AND the ten after it.
        self._dated(40, hour=8)
        live = self._dated(40, hour=9)
        deleted, _freed, _reason = recording.sweep(CODE, retention_days=1, max_gb=0.000001)
        self.assertEqual(deleted, 1)
        self.assertTrue(live.exists())

    def test_sweeping_an_archive_inside_both_bounds_deletes_nothing(self):
        self._dated(1)
        self._dated(0)
        self.assertEqual(recording.sweep(CODE, retention_days=7, max_gb=100)[0], 0)

    def test_an_unfinished_leftover_is_discarded_at_startup(self):
        good = self.write("2026-11-11_10-00-00.mp4")
        killed = self.write("2026-11-11_10-10-00.mp4", finished=False)
        gone = recording.discard_unfinished(CODE)
        self.assertEqual(gone, ["2026-11-11_10-10-00.mp4"])
        self.assertTrue(good.exists())
        self.assertFalse(killed.exists())

    # ---- paths -------------------------------------------------------------

    def test_a_segment_path_cannot_be_walked_out_of_its_directory(self):
        self.write("2026-11-11_10-00-00.mp4")
        self.assertIsNotNone(recording.segment_path(CODE, "2026-11-11_10-00-00.mp4"))
        for name in ("../../db.sqlite3", "..\\..\\db.sqlite3", "2026-11-11_10-00-00.mp4.txt"):
            self.assertIsNone(recording.segment_path(CODE, name), name)

    def test_a_camera_code_cannot_be_walked_out_of_the_recordings_root(self):
        self.assertTrue(str(recording.camera_dir("../../etc")).endswith("etc"))
        self.assertEqual(recording.camera_dir("../../etc").parent, recording.root())
        with self.assertRaises(ValueError):
            recording.camera_dir("../")


class FfmpegCommandTests(TestCase):
    def test_clip_mode_records_at_exactly_the_evidence_clips_quality(self):
        cmd = recording.ffmpeg_command("rtsp://cam/1", "out/%Y.mp4", mode=recording.MODE_CLIP)
        self.assertIn("libx264", cmd)
        self.assertIn("28", cmd[cmd.index("-crf") + 1])
        self.assertEqual(cmd[cmd.index("-vf") + 1], recording.CLIP_SCALE)
        self.assertIn("-rtsp_transport", cmd)        # TCP for a 4 MP stream
        self.assertIn("-an", cmd)                    # never any audio

    def test_the_recorder_and_the_evidence_clips_share_one_encode(self):
        """If this fails, the tape and the clips have drifted into two
        different pictures — fix one of the two lists, do not re-baseline."""
        try:
            from core.vision import recognition
        except Exception:                                        # noqa: BLE001
            self.skipTest("the CV dependencies are not installed here")
        self.assertEqual(recording.CLIP_H264_ARGS, recognition.H264_ARGS)
        self.assertEqual(recording.CLIP_SCALE, recognition.EVIDENCE_SCALE)

    def test_source_mode_copies_the_cameras_own_video(self):
        cmd = recording.ffmpeg_command("rtsp://cam/1", "out/%Y.mp4", mode=recording.MODE_SOURCE)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
        self.assertNotIn("libx264", cmd)
        self.assertNotIn("-vf", cmd)                 # nothing is drawn on a copy

    def test_segments_are_cut_on_their_own_keyframes_and_finalised(self):
        cmd = recording.ffmpeg_command("rtsp://cam/1", "out/%Y.mp4", segment_seconds=600)
        self.assertEqual(cmd[cmd.index("-segment_time") + 1], "600")
        self.assertIn("expr:gte(t,n_forced*600)", cmd)
        self.assertIn("movflags=+faststart", cmd)
        self.assertEqual(cmd[cmd.index("-strftime") + 1], "1")

    def test_a_file_source_is_read_in_real_time_so_its_segment_names_mean_something(self):
        self.assertIn("-re", recording.ffmpeg_command("clip.mp4", "out/%Y.mp4", rate_limit=True))
        self.assertNotIn("-re", recording.ffmpeg_command("rtsp://cam/1", "out/%Y.mp4"))

    def test_the_burnt_in_clock_escapes_the_two_things_ffmpeg_cannot_parse(self):
        cmd = recording.ffmpeg_command("rtsp://cam/1", "out/%Y.mp4",
                                       label=CODE, font="C:/Windows/Fonts/arial.ttf",
                                       base_time_us=1234)
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn(r"fontfile='C\:/Windows/Fonts/arial.ttf'", vf)   # quoted, colon escaped
        self.assertIn("expansion=strftime", vf)                         # not %{localtime:...}
        self.assertIn("basetime=1234", vf)
        self.assertIn(CODE, vf)

    def test_no_stamp_means_no_filter_beyond_the_scale(self):
        cmd = recording.ffmpeg_command("rtsp://cam/1", "out/%Y.mp4", font=None)
        self.assertEqual(cmd[cmd.index("-vf") + 1], recording.CLIP_SCALE)


class RecordingApiTests(TestCase):
    def setUp(self):
        self.tmp, self.override = _media()
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.tmp.cleanup)
        recording.camera_dir(CODE).mkdir(parents=True, exist_ok=True)
        User = get_user_model()
        self.user = User.objects.create_user(username="desk", password="x", role="dispatcher")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.body = b"\x00\x00\x00\x18ftypmp42moov" + bytes(range(256)) * 4

    def write(self, name, finished=True):
        path = recording.camera_dir(CODE) / name
        path.write_bytes(self.body if finished else b"\x00\x00\x00\x18ftypmp42wide" + b"\x00" * 64)
        return path

    def test_the_archive_lists_days_then_a_days_segments(self):
        self.write("2026-11-11_10-00-00.mp4")
        self.write("2026-11-11_10-10-00.mp4")
        days = self.client.get("/api/recordings/").json()
        self.assertEqual(days["days"][0]["date"], "2026-11-11")
        self.assertEqual(days["days"][0]["segments"], 2)
        day = self.client.get("/api/recordings/?date=2026-11-11").json()
        self.assertEqual([s["name"] for s in day["segments"]],
                         ["2026-11-11_10-00-00.mp4", "2026-11-11_10-10-00.mp4"])
        self.assertIn("/api/recordings/file/2026-11-11_10-00-00.mp4/?t=",
                      day["segments"][0]["url"])

    def test_a_segment_still_being_written_is_listed_but_marked_unplayable(self):
        self.write("2026-11-11_10-00-00.mp4", finished=False)
        seg = self.client.get("/api/recordings/?date=2026-11-11").json()["segments"][0]
        self.assertFalse(seg["playable"])

    def test_a_bad_date_is_refused_rather_than_silently_listing_everything(self):
        self.assertEqual(self.client.get("/api/recordings/?date=last-tuesday").status_code, 400)

    def test_a_segment_plays_whole_and_in_ranges(self):
        self.write("2026-11-11_10-00-00.mp4")
        url = "/api/recordings/file/2026-11-11_10-00-00.mp4/"
        whole = self.client.get(url)
        self.assertEqual(whole.status_code, 200)
        self.assertEqual(whole["Accept-Ranges"], "bytes")
        self.assertEqual(b"".join(whole.streaming_content), self.body)

        part = self.client.get(url, headers={"Range": "bytes=10-19"})
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part["Content-Range"], f"bytes 10-19/{len(self.body)}")
        self.assertEqual(part.content, self.body[10:20])

    def test_an_open_ended_range_does_not_read_the_whole_segment_into_memory(self):
        self.write("2026-11-11_10-00-00.mp4")
        with self.settings():
            start, end = __import__("core.views", fromlist=["_byte_range"])._byte_range(
                "bytes=0-", 1 << 30)
        self.assertEqual(start, 0)
        self.assertEqual(end, (4 << 20) - 1)

    def test_the_segment_being_recorded_is_not_served_as_if_it_were_playable(self):
        self.write("2026-11-11_10-00-00.mp4", finished=False)
        r = self.client.get("/api/recordings/file/2026-11-11_10-00-00.mp4/")
        self.assertEqual(r.status_code, 409)

    def test_the_tape_is_not_on_the_unauthenticated_media_path(self):
        """The authenticated view would be theatre if the same bytes were also
        sitting under /media/, which Django and any production web server hand
        out to anyone who asks."""
        from django.conf import settings as django_settings
        root = recording.root().resolve()
        media = Path(django_settings.MEDIA_ROOT).resolve()
        self.assertFalse(str(root).startswith(str(media)))

    def test_footage_is_not_served_to_anyone_who_is_not_signed_in(self):
        self.write("2026-11-11_10-00-00.mp4")
        anon = APIClient()
        r = anon.get("/api/recordings/file/2026-11-11_10-00-00.mp4/")
        self.assertEqual(r.status_code, 401)

    def test_a_video_tag_plays_with_the_ticket_the_listing_minted(self):
        # <video src> cannot send an Authorization header. The listing is
        # authenticated, so that is where the credential for the file is made.
        self.write("2026-11-11_10-00-00.mp4")
        url = self.client.get("/api/recordings/?date=2026-11-11").json()["segments"][0]["url"]
        self.assertIn("?t=", url)
        anon = APIClient()
        r = anon.get(url.replace("http://testserver", ""))
        self.assertEqual(r.status_code, 200)
        r.close()      # a FileResponse nobody streamed still holds the file open

    def test_a_ticket_is_good_for_one_segment_only(self):
        # A capability, not a session: whoever holds a player URL holds that
        # clip and nothing else.
        from core.views import _recording_ticket
        self.write("2026-11-11_10-00-00.mp4")
        self.write("2026-11-11_10-10-00.mp4")
        other = _recording_ticket(CODE, "2026-11-11_10-10-00.mp4")
        anon = APIClient()
        r = anon.get(f"/api/recordings/file/2026-11-11_10-00-00.mp4/?t={other}")
        self.assertEqual(r.status_code, 401)

    def test_a_ticket_expires_and_cannot_be_forged(self):
        from unittest import mock
        from core import views
        self.write("2026-11-11_10-00-00.mp4")
        ticket = views._recording_ticket(CODE, "2026-11-11_10-00-00.mp4")
        anon = APIClient()
        url = "/api/recordings/file/2026-11-11_10-00-00.mp4/"
        with mock.patch.object(views, "RECORDING_TICKET_SECONDS", 0):
            self.assertEqual(anon.get(f"{url}?t={ticket}").status_code, 401)
        for tampered in (ticket[:-1] + ("x" if ticket[-1] != "x" else "y"),
                         "CAM-SMOKE-01|2026-11-11_10-00-00.mp4", "rubbish", ""):
            self.assertEqual(anon.get(f"{url}?t={tampered}").status_code, 401, tampered)

    def test_the_access_token_is_never_what_opens_a_segment(self):
        # It opens every endpoint in the system for thirty minutes; a URL is
        # the most leak-prone place to put one.
        from rest_framework_simplejwt.tokens import RefreshToken
        self.write("2026-11-11_10-00-00.mp4")
        token = str(RefreshToken.for_user(self.user).access_token)
        anon = APIClient()
        self.assertEqual(anon.get(
            f"/api/recordings/file/2026-11-11_10-00-00.mp4/?t={token}").status_code, 401)

    def test_a_name_that_is_not_a_segment_is_a_404_not_a_file_read(self):
        for name in ("db.sqlite3", "..%2f..%2fdb.sqlite3", "2026-11-11_10-00-00.txt"):
            r = self.client.get(f"/api/recordings/file/{name}/")
            self.assertIn(r.status_code, (404, 301), name)


class RecordingSettingsTests(TestCase):
    def setUp(self):
        # Its own tape directory. Without it `running` is answered from the
        # developer's REAL pid file, so the test passes or fails depending on
        # whether anything happens to be recording on this machine.
        self.tmp, self.override = _media()
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.tmp.cleanup)
        User = get_user_model()
        self.admin = User.objects.create_user(username="adm", password="x", role="admin")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_recording_is_on_by_default_and_editable(self):
        body = self.client.get("/api/settings/").json()
        self.assertTrue(body["recording_enabled"])
        self.assertEqual(body["recording_mode"], "clip")
        r = self.client.patch("/api/settings/", {"recording_retention_days": 14,
                                                 "recording_mode": "source"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["recording_retention_days"], 14)
        self.assertEqual(r.json()["recording_mode"], "source")

    def test_the_monitor_reports_what_the_tape_holds(self):
        from core.monitor import monitor
        body = monitor.recording_status()
        self.assertIn("enabled", body)
        self.assertIn("segments", body)
        self.assertFalse(body["running"])


class StopProtocolTests(TestCase):
    """How a recorder is asked to stop. On Windows there is no SIGTERM to send:
    os.kill() there is TerminateProcess for every signal value, including 0."""

    def setUp(self):
        self.tmp, self.override = _media()
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.tmp.cleanup)
        recording.camera_dir(CODE).mkdir(parents=True, exist_ok=True)

    def test_a_stop_is_asked_for_before_anything_is_killed(self):
        from unittest import mock
        recording.write_pids(CODE, 4242)
        with mock.patch("core.recording.is_running", side_effect=[True, False]),                 mock.patch("core.recording._kill_pid") as kill:
            self.assertTrue(recording.terminate_running(CODE, grace=5))
        kill.assert_not_called()
        self.assertFalse(recording.stop_requested(CODE))    # tidied up after itself
        self.assertEqual(recording.read_pids(CODE), [])

    def test_a_recorder_that_ignores_the_request_is_killed_in_the_end(self):
        from unittest import mock
        recording.write_pids(CODE, 4242, 4243)
        with mock.patch("core.recording.is_running", return_value=True),                 mock.patch("core.recording._kill_pid") as kill:
            self.assertFalse(recording.terminate_running(CODE, grace=0))
        # The manager AND its ffmpeg: ending the manager alone orphans an
        # ffmpeg that goes on writing segments nobody supervises.
        self.assertEqual([c.args[0] for c in kill.call_args_list], [4242, 4243])

    def test_stopping_a_camera_nothing_is_recording_is_not_a_wait(self):
        self.assertTrue(recording.terminate_running(CODE, grace=30))

    def test_a_recycled_pid_does_not_read_as_a_running_recorder(self):
        """After a power cut the pid file outlives the process and the system
        reuses the number. Without the start time, "is that pid alive?" answers
        yes about somebody else -- which reads as "already recording" and would
        stop a real recorder from ever starting."""
        import os as _os
        recording.write_pids(CODE, _os.getpid())        # alive, and really us
        self.assertTrue(recording.is_running(CODE))

        # Same pid, a start time from long ago: the file is stale.
        path = recording.pid_file(CODE)
        path.write_text("\n".join([str(_os.getpid()), "", "1.0", ""]), encoding="utf-8")
        self.assertFalse(recording.is_running(CODE))

    def test_a_pid_file_from_before_the_start_time_was_recorded_still_works(self):
        import os as _os
        recording.pid_file(CODE).parent.mkdir(parents=True, exist_ok=True)
        recording.pid_file(CODE).write_text("\n".join([str(_os.getpid()), "", ""]),
                                            encoding="utf-8")
        self.assertTrue(recording.is_running(CODE))
        self.assertEqual(recording.read_pids(CODE), [_os.getpid()])

    def test_the_flag_is_a_file_the_recorder_can_see_from_another_process(self):
        self.assertFalse(recording.stop_requested(CODE))
        recording.request_stop(CODE)
        self.assertTrue(recording.stop_requested(CODE))
        recording.clear_stop(CODE)
        self.assertFalse(recording.stop_requested(CODE))


class RecorderLifetimeTests(TestCase):
    """The tape's lifetime is the SERVER's, not detection's."""

    def setUp(self):
        from unittest import mock

        from core.monitor import monitor
        self.monitor = monitor
        self.proc = mock.Mock()
        self.proc.poll.return_value = None          # a live recorder
        self.monitor._rec_proc = self.proc
        self.addCleanup(setattr, self.monitor, "_rec_proc", None)
        self.stopped = mock.patch.object(self.monitor, "_stop_recording")
        self.stop_mock = self.stopped.start()
        self.addCleanup(self.stopped.stop)

    def test_stopping_detection_leaves_the_tape_running(self):
        # Detection is stopped for ordinary reasons — tuning a threshold,
        # freeing the GPU. Footage missing from exactly those minutes is the
        # archive failing at the one thing it is for.
        self.monitor.stop()
        self.stop_mock.assert_not_called()

    def test_shutting_the_server_down_ends_the_tape_properly(self):
        self.monitor.shutdown()
        self.stop_mock.assert_called_once()
        self.assertFalse(self.monitor._recording_wanted)

    def test_starting_detection_does_not_stop_the_recorder_as_an_orphan(self):
        # _reap_orphans ends a recorder left by an EARLIER server. It cannot
        # tell that one from ours by the pid file alone, and ours is the tape
        # being written: ending it costs the open segment.
        from unittest import mock
        with mock.patch("core.recording.is_running", return_value=True),                 mock.patch("core.recording.request_stop") as ask:
            self.monitor._reap_orphans()
        ask.assert_not_called()

        self.proc.poll.return_value = 0             # ours has exited
        with mock.patch("core.recording.is_running", return_value=True),                 mock.patch("core.recording.request_stop") as ask:
            self.monitor._reap_orphans()
        ask.assert_called_once()

    def test_a_test_run_never_starts_a_real_recorder(self):
        """The recorder is a SUBPROCESS: it reaches the real database and the
        real camera, not the test ones. A suite that reached this spawned a
        genuine ffmpeg on the machine running it and left it there."""
        from unittest import mock
        import core.monitor as monitor_module
        self.monitor._rec_thread = None
        with mock.patch.object(monitor_module, "_running_tests", return_value=True),                 mock.patch("threading.Thread") as thread:
            self.monitor.ensure_recording()
        thread.assert_not_called()

    def test_starting_detection_does_not_start_the_tape_as_a_side_effect(self):
        """The supervisor is started with the SERVER and polls the setting, so
        Start has nothing to add -- and reaching it from a request or a test
        is how a recorder got spawned where none was wanted."""
        import inspect
        from core.monitor import LiveMonitor
        self.assertNotIn("ensure_recording", inspect.getsource(LiveMonitor.start))

    def test_a_server_that_runs_no_detectors_keeps_no_tape_either(self):
        from unittest import mock
        self.monitor._rec_thread = None
        with override_settings(DETECTION_ENABLED=False):
            with mock.patch("threading.Thread") as thread:
                self.monitor.ensure_recording()
        thread.assert_not_called()
