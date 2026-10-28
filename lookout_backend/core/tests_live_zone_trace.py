"""Live zone trace: core/vision/live_trace.py and CameraViewSet.trace_zone.

The geometry is zone_autodetect.trace (covered in tests_zone_autodetect); these
pin the live-stream glue: the sampling loop with a scripted camera and clock,
each failure reason, the HTTP contract (admin only, nothing saved, one trace at
a time) and how a saved zone is applied to the running monitor.
"""
import threading
import time
from unittest import mock

import numpy as np
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from core.models import Camera, User
from core.vision import live_trace

FRAME = np.zeros((480, 640, 3), dtype=np.uint8)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeCap:
    """A stream that delivers a frame every 40 ms of fake time."""

    def __init__(self, clock, opened=True, frames=10_000):
        self.clock, self.opened, self.left = clock, opened, frames
        self.released = False

    def isOpened(self):
        return self.opened

    def _next(self):
        if self.left <= 0:
            return False
        self.left -= 1
        self.clock.t += 0.04
        return True

    def grab(self):
        return self._next()

    def read(self):
        return (True, FRAME) if self._next() else (False, None)

    def release(self):
        self.released = True

    def set(self, *_):
        pass


def scripted_detector(per_sample):
    """detect(frame) -> boxes from a function of the sample index."""
    calls = {"n": 0}

    def detect(_frame):
        i = calls["n"]
        calls["n"] += 1
        return per_sample(i)
    return detect


def driving(i):
    # three cars in separate lanes crossing the frame (a box is 60 px wide and
    # each moves 5 px a sample), so the footprints sweep a real area
    return [(10 + 5 * i, y, 70 + 5 * i, y + 40, 0.9, "car") for y in (200, 280, 360)]


def parked(_i):
    return [(100, 300, 160, 340, 0.9, "car")]


def run(detect, cap_kwargs=None, job=None, clock=None):
    clock = clock or FakeClock()
    cap = FakeCap(clock, **(cap_kwargs or {}))
    job = job if job is not None else {}
    return live_trace.run_trace("rtsp://x", detect, job, open_capture=lambda _s: cap,
                                clock=clock), cap, job


class RunTraceTests(TestCase):
    def test_samples_on_the_clock_and_traces_a_zone(self):
        (points, report, shape), cap, job = run(scripted_detector(driving))
        self.assertIsNotNone(points)
        self.assertEqual(report["samples"], live_trace.MAX_SAMPLES)
        self.assertEqual(report["moving_tracks"], 3)
        self.assertNotIn("cloud", report)
        self.assertEqual(shape[:2], (480, 640))
        self.assertEqual(job["samples"], live_trace.MAX_SAMPLES)
        self.assertTrue(cap.released, "the connection must be released")

    def test_about_forty_eight_seconds_of_stream_time(self):
        clock = FakeClock()
        run(scripted_detector(driving), clock=clock)
        self.assertAlmostEqual(clock.t, 120 * 0.4, delta=5.0)

    def test_no_vehicles(self):
        (points, report, _), _, _ = run(scripted_detector(lambda i: []))
        self.assertIsNone(points)
        self.assertEqual(live_trace.classify_reason(report), live_trace.NO_VEHICLES)

    def test_none_moved(self):
        (points, report, _), _, _ = run(scripted_detector(parked))
        self.assertIsNone(points)
        self.assertEqual(live_trace.classify_reason(report), live_trace.NONE_MOVED)

    def test_too_few_sightings(self):
        # a vehicle seen only 5 times: it moves, but under MIN_TRACK_SAMPLES+... evidence
        det = scripted_detector(lambda i: driving(i) if i < 5 else [])
        (points, report, _), _, _ = run(det)
        self.assertIsNone(points)
        self.assertIn(live_trace.classify_reason(report),
                      (live_trace.TOO_FEW, live_trace.NO_VEHICLES, live_trace.NONE_MOVED))
        self.assertIn("reason", report)

    def test_unreachable_stream(self):
        with self.assertRaises(ConnectionError):
            run(scripted_detector(driving), cap_kwargs={"opened": False})

    def test_stream_that_opens_but_sends_nothing_releases_and_fails(self):
        clock = FakeClock()
        cap = FakeCap(clock, frames=0)
        with self.assertRaises(ConnectionError) as ctx:
            live_trace.run_trace("rtsp://x", lambda f: [], {}, open_capture=lambda _s: cap,
                                 clock=clock)
        self.assertIn("frames", str(ctx.exception))
        self.assertTrue(cap.released)

    def test_deadline(self):
        clock = FakeClock()

        def slow(frame):
            clock.t += 5.0       # each inference takes 5 s -> blows the 90 s budget
            return []
        cap = FakeCap(clock)
        with self.assertRaises(TimeoutError):
            live_trace.run_trace("rtsp://x", slow, {}, open_capture=lambda _s: cap, clock=clock)
        self.assertTrue(cap.released)

    def test_cancel(self):
        job = {}

        def detect(frame):
            job["cancel"] = True
            return []
        clock = FakeClock()
        cap = FakeCap(clock)
        with self.assertRaises(live_trace.Cancelled):
            live_trace.run_trace("rtsp://x", detect, job, open_capture=lambda _s: cap, clock=clock)
        self.assertTrue(cap.released)

    def test_a_stall_after_some_frames_uses_what_was_seen(self):
        (points, report, _), _, _ = run(scripted_detector(driving), cap_kwargs={"frames": 40 * 30})
        self.assertGreater(report["samples"], 10)
        self.assertLess(report["samples"], live_trace.MAX_SAMPLES)
        self.assertIsNotNone(points)


class JobTests(TestCase):
    def setUp(self):
        live_trace.reset()
        self.addCleanup(live_trace.reset)

    def _job(self, **kw):
        return live_trace.start(1, "rtsp://x", lambda f: [], background=False, **kw)

    def test_failure_codes_are_mapped(self):
        for exc, code in [(ConnectionError("The camera stream could not be opened."),
                           live_trace.UNREACHABLE),
                          (ConnectionError("The camera stream returned no frames."),
                           live_trace.NO_FRAMES),
                          (TimeoutError("late"), live_trace.TIMEOUT),
                          (RuntimeError("boom"), live_trace.ERROR)]:
            with mock.patch.object(live_trace, "run_trace", side_effect=exc):
                job, _ = self._job()
            self.assertEqual(job["state"], "failed")
            self.assertEqual(job["code"], code)

    def test_no_zone_reports_its_reason(self):
        report = {"samples": 3, "detections": 0, "moving_tracks": 0, "ground_points": 0,
                  "reason": "No vehicles were detected."}
        with mock.patch.object(live_trace, "run_trace", return_value=(None, report, (480, 640, 3))):
            job, _ = self._job()
        self.assertEqual((job["state"], job["code"]), ("failed", live_trace.NO_VEHICLES))
        self.assertEqual(job["reason"], "No vehicles were detected.")

    def test_success_carries_points_and_frame_size(self):
        report = {"samples": 120, "moving_tracks": 4}
        with mock.patch.object(live_trace, "run_trace",
                               return_value=([(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)], report,
                                             (1440, 2560, 3))):
            job, _ = self._job()
        self.assertEqual(job["state"], "done")
        self.assertEqual((job["width"], job["height"]), (2560, 1440))
        self.assertEqual(len(job["points"]), 3)


@override_settings(DETECTION_ENABLED=True)
class TraceEndpointTests(TestCase):
    def setUp(self):
        live_trace.reset()
        self.addCleanup(live_trace.reset)
        self.admin = User.objects.create_user(username="adm", password="x", role="admin",
                                              display_name="A")
        self.dispatcher = User.objects.create_user(username="dsp", password="x",
                                                   role="dispatcher", display_name="D")
        self.camera = Camera.objects.create(name="Cam", stream_url="rtsp://u:p@10.0.0.2/s")
        self.url = f"/api/cameras/{self.camera.pk}/trace-zone/"
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _wait(self, state="running"):
        for _ in range(200):
            body = self.client.get(self.url).data
            if body["state"] != state:
                return body
            time.sleep(0.02)
        self.fail("trace did not finish")

    def test_admin_only(self):
        for user, expected in [(self.dispatcher, 403), (None, 401)]:
            c = APIClient()
            if user:
                c.force_authenticate(user)
            self.assertEqual(c.post(self.url).status_code, expected)
            self.assertEqual(c.get(self.url).status_code, expected)
            self.assertEqual(c.delete(self.url).status_code, expected)

    def test_success_and_nothing_is_saved(self):
        before = (self.camera.edges, self.camera.edges_width, self.camera.edges_height)
        report = {"samples": 120, "moving_tracks": 5, "detections": 200, "tracks": 6,
                  "ground_points": 90}
        with mock.patch.object(live_trace, "run_trace",
                               return_value=([(1.0, 2.0), (30.0, 4.0), (5.0, 60.0)], report,
                                             (480, 640, 3))):
            res = self.client.post(self.url)
            self.assertEqual(res.status_code, 202)
            body = self._wait()
        self.assertEqual(body["state"], "done")
        self.assertEqual((body["width"], body["height"]), (640, 480))
        self.assertEqual(body["report"]["moving_tracks"], 5)
        self.camera.refresh_from_db()
        self.assertEqual((self.camera.edges, self.camera.edges_width, self.camera.edges_height),
                         before)

    def test_failure_reason_is_returned(self):
        with mock.patch.object(live_trace, "run_trace",
                               side_effect=ConnectionError("The camera stream could not be opened.")):
            self.client.post(self.url)
            body = self._wait()
        self.assertEqual(body["state"], "failed")
        self.assertEqual(body["code"], live_trace.UNREACHABLE)
        self.assertIn("could not be opened", body["reason"])

    def test_second_request_while_running_is_rejected(self):
        gate = threading.Event()

        def slow(*_a, **_k):
            gate.wait(5)
            raise ConnectionError("The camera stream could not be opened.")
        with mock.patch.object(live_trace, "run_trace", side_effect=slow):
            self.assertEqual(self.client.post(self.url).status_code, 202)
            self.assertEqual(self.client.post(self.url).status_code, 409)
            gate.set()
            self._wait()
            # once finished, a new one may start
            gate.clear()
            gate.set()
            self.assertEqual(self.client.post(self.url).status_code, 202)
            self._wait()

    def test_cancel(self):
        def until_cancelled(source, detect, job, *a, **k):
            for _ in range(500):
                if job.get("cancel"):
                    raise live_trace.Cancelled()
                time.sleep(0.01)
        with mock.patch.object(live_trace, "run_trace", side_effect=until_cancelled):
            self.client.post(self.url)
            self.assertTrue(self.client.delete(self.url).data["cancelled"])
            self.assertEqual(self._wait()["state"], "cancelled")

    def test_camera_without_a_stream_is_refused(self):
        self.camera.stream_url = ""
        self.camera.save()
        with override_settings(STREAM_URL=""):
            res = self.client.post(self.url)
        self.assertEqual(res.status_code, 400)

    @override_settings(DETECTION_ENABLED=False)
    def test_refused_where_detection_is_disabled(self):
        self.assertEqual(self.client.post(self.url).status_code, 503)

    def test_idle_before_any_trace(self):
        self.assertEqual(self.client.get(self.url).data, {"state": "idle"})


class ApplyZoneTests(TestCase):
    """A saved zone reaches the running monitor; only parking is touched."""

    ZONE = {"road": {"type": "zone", "points": [[0, 0], [10, 0], [10, 10]]}}

    def setUp(self):
        from core.monitor import LiveMonitor
        self.camera = Camera.objects.create(code="CAM-SMOKE-01", name="C",
                                            stream_url="rtsp://x", edges=self.ZONE)
        self.m = LiveMonitor()
        self.merged = mock.Mock(pid=1)
        self.merged.poll.return_value = None
        self.m._want = True
        self.m._procs = {"merged": self.merged}

    def _join(self):
        self.m._swap_thread.join(5)

    def test_not_running_does_nothing(self):
        self.m._want = False
        self.assertEqual(self.m.apply_zone(self.camera), "not_running")

    def test_starts_parking_when_it_was_absent(self):
        parking = mock.Mock(pid=2)
        with mock.patch.object(self.m, "_spawn", return_value=(parking, 99)) as spawn:
            self.assertEqual(self.m.apply_zone(self.camera), "started")
            self._join()
        spawn.assert_called_once()
        self.assertIs(self.m._procs["parking"], parking)
        self.assertIs(self.m._procs["merged"], self.merged)
        self.merged.terminate.assert_not_called()

    def test_restarts_only_parking_when_it_was_running(self):
        old = mock.Mock(pid=3)
        old.poll.return_value = None
        self.m._procs["parking"] = old
        new = mock.Mock(pid=4)
        with mock.patch.object(self.m, "_spawn", return_value=(new, 100)):
            self.assertEqual(self.m.apply_zone(self.camera), "restarting")
            self._join()
        old.terminate.assert_called()
        self.assertIs(self.m._procs["parking"], new)
        self.merged.terminate.assert_not_called()

    def test_clearing_the_zone_stops_parking(self):
        old = mock.Mock(pid=3)
        old.poll.return_value = None
        self.m._procs["parking"] = old
        self.camera.edges = {}
        self.assertEqual(self.m.apply_zone(self.camera), "stopped")
        self._join()
        self.assertNotIn("parking", self.m._procs)

    def test_no_zone_still_runs_the_other_violations(self):
        """With no zone the live monitor launches merged (+ disposal when
        enabled) and simply leaves parking out."""
        from core.models import SystemSettings
        from core.monitor import LiveMonitor
        self.camera.edges = {}
        self.camera.save()
        m = LiveMonitor()
        spawned = []

        def fake_spawn(name, camera, args=None, ldir=None, env=None):
            spawned.append(name)
            return mock.Mock(pid=len(spawned)), len(spawned)
        s = SystemSettings.load()
        s.disposal_enabled = True
        s.save()
        with mock.patch.object(m, "_spawn", side_effect=fake_spawn):
            m._launch(self.camera)
        self.assertEqual(spawned, ["merged", "disposal"])

    def test_save_through_the_api_applies_the_zone(self):
        admin = User.objects.create_user(username="adm2", password="x", role="admin",
                                         display_name="A")
        c = APIClient()
        c.force_authenticate(admin)
        with mock.patch("core.monitor.monitor.apply_zone", return_value="restarting") as ap:
            res = c.patch(f"/api/cameras/{self.camera.pk}/",
                          {"edges": self.ZONE, "edges_width": 640, "edges_height": 480},
                          format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["zone_applied"], "restarting")
        ap.assert_called_once()
