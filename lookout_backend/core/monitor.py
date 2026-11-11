"""Live monitoring of the CCTV camera, started and stopped from the web UI.

One live monitor for the whole system (one camera, CAM-SMOKE-01). It runs two detector
processes against the camera's RTSP stream:

    watch_merged   smoking + drinking + holdup (one merged_v2 pass; writes the live processing view)
    watch_parking  illegal parking, judged against the camera's saved road edge

Parking is skipped, with a notice, when no road edge has been drawn for the camera.

A supervisor thread keeps it alive: if a detector exits, or the stream stops delivering frames, it
is restarted with a growing delay (5 s, 10 s, ... up to 2 minutes) and a warning is shown. This
lives inside the Django process, which is what this project already runs (see the detection job
watcher in views.py); it is not a general task queue.

It also keeps the CONTINUOUS RECORDER alive (manage.py record_cctv, core/recording.py) -- the
ordinary CCTV tape of everything the camera sees. That is NOT a detector and is supervised
separately, on its own switch:

  * it starts with the SERVER (core/apps.py), not with detection, because a traditional CCTV
    system records whether or not anything is analysing the picture, and because an archive that
    only existed when somebody had pressed Start would be missing exactly the footage nobody
    thought to ask for;
  * stopping detection leaves it running -- detection is stopped for ordinary reasons (tuning a
    threshold, freeing the GPU, re-drawing a zone) and footage missing from those minutes is the
    archive failing at the one thing it is for;
  * a detector dying does not cut the tape, and a dropped recording does not restart a detector;
  * it is the one process here with a GRANDCHILD (ffmpeg), which on Windows outlives a terminated
    parent, so ending it means signalling the pid file rather than killing a subprocess.

Its only switch is `recording_enabled` in Settings, re-read every few seconds, so it can be turned
off and on without touching detection.
"""
import os
import subprocess
import sys
import threading
import time

from django.conf import settings
from django.utils import timezone

from core import zones

CAMERA_CODE = "CAM-SMOKE-01"
LIVE_DIR_NAME = "live"
STATE_STALE_SECONDS = 30       # no new frame published for this long = the stream is not delivering
STARTUP_GRACE_SECONDS = 90     # models load and the stream connects before the first frame is published
BACKOFF_START = 5
BACKOFF_MAX = 120
HEALTHY_AFTER = 60             # ran this long = the backoff starts over


def live_dir():
    return settings.MEDIA_ROOT / LIVE_DIR_NAME / "live"


def job_dir(job_id):
    return settings.MEDIA_ROOT / LIVE_DIR_NAME / f"job-{job_id}"


def has_road_edge(camera):
    """True when the camera has a usable no-parking area saved.

    A "disposal" entry never counts (core/zones.py) -- it is a different rule's
    area, and counting it launched parking on a camera with no road at all.

    Both shapes count. A ZONE is a closed polygon keyed by "type": "zone" and
    needs at least three points; a legacy EDGE is an open kerb line keyed
    left/right and needs two.

    This used to test the left/right keys ALONE. That was correct while the
    editor drew kerb lines, and silently wrong the moment it became zone-only:
    a camera with a perfectly good polygon answered False, so the live monitor
    launched watch_merged and quietly left parking out — no error, no log line,
    just three violations where four were configured.
    """
    return zones.has_road(camera.edges)


def _running_tests():
    """True when this process is a test runner.

    The recorder is a SUBPROCESS, so it connects to the real database and the real
    camera -- not the test ones. A test that reached ensure_recording() therefore
    started a genuine ffmpeg against the barangay's camera on whoever was running the
    suite, and left it running after the suite finished. That happened; this is the
    guard. Checked on argv rather than a settings flag because it has to hold for any
    caller, including one added later that nobody thought about.
    """
    first = [os.path.basename(a) for a in sys.argv[:2]]
    return "test" in sys.argv[:2] or any("pytest" in a for a in first)


class LiveMonitor:
    def __init__(self):
        self._lock = threading.RLock()
        self._want = False
        self._thread = None
        self._procs = {}
        self._started_at = None         # when the current run (not restart) began
        self._launched_at = None        # when the current process pair was launched
        self._restarts = 0
        self._warning = ""
        self._notice = ""
        self._job_ids = []
        self._last_error = ""
        self._rec_proc = None           # manage.py record_cctv (it owns the ffmpeg)
        self._rec_thread = None
        self._recording_wanted = False  # the tape's own switch, independent of _want

    # ---- control ---------------------------------------------------------------

    def start(self, user=None):
        """-> (ok, message)"""
        from django.conf import settings as django_settings

        from core.models import Camera

        # Same reason as DetectionJobViewSet: this Popens watch_merged, which
        # needs the GPU, the weights and the camera. Checked before the lock so
        # a hosted deployment answers immediately instead of half-starting.
        if not getattr(django_settings, "DETECTION_ENABLED", True):
            return False, ("This server does not run detectors. Start live "
                           "detection on the machine beside the camera.")
        with self._lock:
            if self._want:
                return True, "Detection is already running."
            self._reap_orphans()
            camera = Camera.objects.filter(code=CAMERA_CODE).first()
            if camera is None:
                return False, f"Camera {CAMERA_CODE} was not found."
            if not camera.resolved_stream_url:
                return False, ("The camera has no stream address configured. Save an RTSP URL "
                               "on Live Feeds, or set STREAM_URL in this machine's .env.")
            self._want = True
            self._started_at = timezone.now()
            self._restarts = 0
            self._warning = ""
            self._last_error = ""
            self._user_id = getattr(user, "pk", None)
            self._thread = threading.Thread(target=self._supervise, daemon=True, name="live-monitor")
            self._thread.start()
        # The tape is NOT started here. It runs on its own switch and its supervisor is
        # started with the server (core/apps.py), where it polls `recording_enabled`
        # every few seconds -- so turning recording on mid-shift is picked up without
        # anybody pressing Start, and pressing Start cannot spawn a recorder as a side
        # effect. That side effect was real: a test calling start() launched an ffmpeg
        # against the live camera.
        return True, "Starting detection."

    def stop(self):
        """Stop the DETECTORS. The tape keeps running.

        Deliberate: a traditional CCTV system records whether or not anything is
        analysing the picture, and detection is stopped for ordinary reasons -- tuning a
        threshold, freeing the GPU, re-drawing a zone. Footage missing from exactly
        those minutes is the archive failing at the one thing it is for. Recording is
        switched off in Settings, or by stopping LookOut."""
        with self._lock:
            self._want = False
            procs = list(self._procs.values())
        for p in procs:
            self._kill(p)
        return True, "Detection stopped."

    def shutdown(self):
        """Everything down, tape included: the web server is going away. Registered with
        atexit, and the only path that ends recording without an operator asking."""
        self._recording_wanted = False
        self.stop()
        self._stop_recording()

    def apply_zone(self, camera):
        """Make a just-saved no-parking zone take effect. -> outcome string.

        watch_parking reads the camera's zone ONCE, when it starts, so a saved
        change does nothing to a running process. Only the parking process is
        touched -- merged and disposal keep running, so smoking/drinking/holdup
        coverage has no gap. The old parking process is removed from `_procs`
        BEFORE it is killed, so the supervisor (which restarts everything when
        any tracked process exits) never sees it die.

        Outcomes: "not_running" (nothing to apply; the next start reads it),
        "restarting" (parking was running, now relaunching on the new zone),
        "started" (parking was absent for want of a zone and is now launched),
        "stopped" (the zone was cleared, so parking is switched off).
        """
        with self._lock:
            if not self._want or not self._procs:
                return "not_running"
            old = self._procs.pop("parking", None)
            had_old = old is not None
        ready = has_road_edge(camera) and bool(camera.resolved_stream_url)
        if not ready:
            self._swap_thread = threading.Thread(
                target=self._retire if old is not None else (lambda *_: None),
                args=(old,), daemon=True, name="parking-retire")
            self._swap_thread.start()
            return "stopped" if had_old else "not_running"
        self._swap_thread = threading.Thread(target=self._swap_parking, args=(camera, old),
                                             daemon=True, name="parking-swap")
        self._swap_thread.start()
        return "restarting" if had_old else "started"

    def _retire(self, proc):
        from core.models import DetectionJob
        self._kill(proc)
        try:
            DetectionJob.objects.filter(pid=proc.pid, status=DetectionJob.Status.RUNNING).update(
                status=DetectionJob.Status.CANCELLED, finished_at=timezone.now())
        except Exception:                                   # noqa: BLE001
            pass     # bookkeeping must never stop the relaunch that follows

    def apply_disposal_zone(self, camera):
        """Make a just-saved improper-disposal area take effect. -> outcome string.

        The same swap apply_zone does for parking, on the disposal process:
        watch_disposal reads the camera's area once at start, and only that
        process is replaced so the other detectors keep running. Outcomes match
        apply_zone's, except a cleared area does not stop disposal -- no area
        means "watch the whole frame", so it is relaunched ("restarting").
        """
        from core.models import SystemSettings
        with self._lock:
            if not self._want or not self._procs:
                return "not_running"
            old = self._procs.pop("disposal", None)
            had_old = old is not None
        ready = SystemSettings.load().disposal_enabled and bool(camera.resolved_stream_url)
        if not ready:
            self._swap_thread = threading.Thread(
                target=self._retire if old is not None else (lambda *_: None),
                args=(old,), daemon=True, name="disposal-retire")
            self._swap_thread.start()
            return "stopped" if had_old else "not_running"
        self._swap_thread = threading.Thread(target=self._swap, args=("disposal", camera, old),
                                             daemon=True, name="disposal-swap")
        self._swap_thread.start()
        return "restarting" if had_old else "started"

    def _swap_parking(self, camera, old):
        self._swap("parking", camera, old)

    def _swap(self, name, camera, old):
        from core.models import DetectionJob
        if old is not None:
            self._retire(old)
        with self._lock:
            if not self._want:
                return
        proc, job_id = self._spawn(name, camera)
        with self._lock:
            if not self._want:
                self._kill(proc)
                DetectionJob.objects.filter(pk=job_id).update(
                    status=DetectionJob.Status.CANCELLED, finished_at=timezone.now())
                return
            self._procs[name] = proc
            self._job_ids.append(job_id)

    def _reap_orphans(self):
        """Detector processes left behind by an earlier run of the web server (it restarts itself
        when code changes) would double up on the camera. End them and close their job rows.

        A recorder left by an earlier server is asked to stop here too (it has no job row, so
        the pid file is the only handle on it) -- two ffmpegs writing into one directory would
        interleave segments from two connections. Only one we do NOT own, though: our own
        recorder is the tape being written right now, and the pid file cannot tell the two
        apart. The request is left for the recording supervisor to follow up on, because
        waiting for a clean stop takes seconds and this runs on a request."""
        import signal
        from core import recording
        from core.models import DetectionJob
        ours = self._rec_proc is not None and self._rec_proc.poll() is None
        if not ours and recording.is_running(CAMERA_CODE):
            try:
                recording.request_stop(CAMERA_CODE)
            except Exception:                                    # noqa: BLE001
                pass
        for job in DetectionJob.objects.filter(status=DetectionJob.Status.RUNNING, camera__code=CAMERA_CODE):
            if job.pid:
                try:
                    os.kill(job.pid, signal.SIGTERM)
                except OSError:
                    pass
            job.status = DetectionJob.Status.CANCELLED
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "finished_at"])

    @staticmethod
    def _kill(proc):
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except OSError:
            pass

    # ---- the continuous tape -----------------------------------------------------

    def ensure_recording(self):
        """Make sure the tape's supervisor is running. Idempotent.

        Called from apps.ready() when the server starts, so the archive does not depend
        on anybody having pressed Start -- a barangay that never opens Live Feeds should
        still have footage of last Tuesday.
        """
        from django.conf import settings as django_settings
        if not getattr(django_settings, "DETECTION_ENABLED", True):
            return      # a hosted API has neither the camera nor the disk
        if _running_tests():
            return
        with self._lock:
            if self._rec_thread is not None and self._rec_thread.is_alive():
                return
            self._recording_wanted = True
            self._rec_thread = threading.Thread(target=self._supervise_recording, daemon=True,
                                                name="live-recorder")
            self._rec_thread.start()

    def _supervise_recording(self):
        """Keep `manage.py record_cctv` running for as long as the setting says to.

        Its own thread, not part of _supervise, and not bounded by the detectors'
        lifetime: the tape must survive a detector restart, a detector must not be
        restarted because a recording dropped, and stopping detection must not stop
        recording.

        Thin on purpose. The command does its own reconnecting, stall watchdog and
        retention sweeping -- it is closer to the camera and is the only thing that
        knows when a segment stopped growing. All this adds is the settings switch,
        re-read each pass, so turning recording off in the dashboard stops the tape
        within a few seconds and turning it on starts one, neither of them touching
        detection.
        """
        from core.models import Camera, SystemSettings
        while self._recording_wanted:
            try:
                wanted = SystemSettings.load().recording_enabled
            except Exception:                                    # noqa: BLE001
                wanted = False      # the database is unreachable; do not guess
            proc = self._rec_proc
            alive = proc is not None and proc.poll() is None
            if wanted and not alive:
                camera = Camera.objects.filter(code=CAMERA_CODE).first()
                if camera is not None and camera.resolved_stream_url:
                    # A recorder from a previous run of this server, or one still
                    # finalising its last segment, would be a second ffmpeg writing
                    # into the same directory. Waiting happens HERE, on a background
                    # thread, rather than in _reap_orphans, which a request calls.
                    from core import recording
                    if recording.is_running(CAMERA_CODE):
                        recording.terminate_running(CAMERA_CODE)
                    self._rec_proc = self._spawn_recorder(camera)
                    # Terminating a recorder takes seconds, and the server may have
                    # been shutting down for all of them. Without this check the last
                    # thing a dying process does is start an ffmpeg nobody will ever
                    # stop.
                    if not self._recording_wanted:
                        self._stop_recording()
                        break
            elif alive and not wanted:
                self._stop_recording()
            time.sleep(5.0)
        self._stop_recording()

    def _spawn_recorder(self, camera):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        log_dir = settings.MEDIA_ROOT / "uploads"
        os.makedirs(log_dir, exist_ok=True)
        log = open(log_dir / "live_recording.log", "w")
        try:
            return subprocess.Popen(
                [sys.executable, "manage.py", "record_cctv", "--camera", camera.code],
                cwd=str(settings.BASE_DIR), stdout=log, stderr=subprocess.STDOUT, env=env)
        finally:
            log.close()

    def _stop_recording(self):
        """End the recorder, and the ffmpeg underneath it.

        Through recording.terminate_running, which ASKS first (a flag file the recorder
        polls) and waits before killing anything. That order is the difference between
        the last ten minutes of footage existing and not: ffmpeg writes a segment's moov
        atom only when it closes the file, and a killed ffmpeg never closes it.

        Popen.terminate() is the fallback, not the method, because on Windows it is
        TerminateProcess -- the manage.py process gets no chance to run its own cleanup,
        so ffmpeg would be left running with the camera open, writing into the directory
        the next recorder is about to use.
        """
        from core import recording
        proc, self._rec_proc = self._rec_proc, None
        try:
            recording.terminate_running(CAMERA_CODE)
        except Exception:                                        # noqa: BLE001
            pass
        if proc is None:
            return
        try:
            proc.wait(timeout=20)       # it is finalising the open segment
        except subprocess.TimeoutExpired:
            self._kill(proc)

    def recording_status(self):
        """What the dashboard shows about the tape: whether it is recording now, and
        what the archive holds. Safe to call with no recordings directory and with the
        recorder never started."""
        from core import recording
        from core.models import SystemSettings
        cfg = SystemSettings.load()
        try:
            use = recording.usage(CAMERA_CODE)
        except (OSError, ValueError):
            use = {"segments": 0, "bytes": 0, "oldest": None, "newest": None,
                   "free_bytes": None}
        proc = self._rec_proc
        return {
            "enabled": cfg.recording_enabled,
            "running": (proc is not None and proc.poll() is None)
                       or recording.is_running(CAMERA_CODE),
            "mode": cfg.recording_mode,
            "segment_minutes": cfg.recording_segment_minutes,
            "retention_days": cfg.recording_retention_days,
            "max_gb": cfg.recording_max_gb,
            "segments": use["segments"],
            "bytes": use["bytes"],
            "free_bytes": use["free_bytes"],
            "oldest": use["oldest"].isoformat() if use["oldest"] else None,
            "newest": use["newest"].isoformat() if use["newest"] else None,
        }

    # ---- status ----------------------------------------------------------------

    def status(self):
        from core import debug_state
        from core.models import Camera
        camera = Camera.objects.filter(code=CAMERA_CODE).first()
        with self._lock:
            want, started, launched = self._want, self._started_at, self._launched_at
            alive = any(p.poll() is None for p in self._procs.values())
            warning, restarts, last_error = self._warning, self._restarts, self._last_error
        edge_ok = bool(camera) and has_road_edge(camera)
        notice = "" if edge_ok else "Draw the road edge for parking (parking is not being checked until you do)."
        if not want:
            return {"state": "stopped", "label": "Detection stopped", "since": None, "warning": "",
                    "notice": notice, "restarts": 0, "parking_ready": edge_ok,
                    "recording": self.recording_status()}
        age = debug_state.state_age(live_dir())
        now = time.time()
        in_grace = launched is not None and now - launched < STARTUP_GRACE_SECONDS
        if alive and age is not None and age < STATE_STALE_SECONDS:
            state, label = "running", "Detection running"
        elif alive and in_grace:
            state, label = "starting", "Starting detection…"
        elif restarts >= 1 or (alive and not in_grace):
            state, label = "unreachable", "Camera unreachable"
            warning = warning or "No video is arriving from the camera. LookOut keeps retrying."
        else:
            state, label = "starting", "Starting detection…"
        return {"state": state, "label": label, "since": started.isoformat() if started else None,
                "warning": warning, "notice": notice, "restarts": restarts, "parking_ready": edge_ok,
                "last_error": last_error, "recording": self.recording_status()}

    # ---- the supervisor ----------------------------------------------------------

    def _supervise(self):
        from core.models import Camera, DetectionJob
        backoff = BACKOFF_START
        while True:
            with self._lock:
                if not self._want:
                    break
            camera = Camera.objects.filter(code=CAMERA_CODE).first()
            if camera is None or not camera.resolved_stream_url:
                time.sleep(5)
                continue
            ran_since = time.time()
            self._launch(camera)
            # wait for either process to exit, or for stop()
            while True:
                time.sleep(1.0)
                with self._lock:
                    if not self._want:
                        break
                    exited = [name for name, p in self._procs.items() if p.poll() is not None]
                if exited:
                    break
            with self._lock:
                wanted = self._want
                codes = {n: p.poll() for n, p in self._procs.items()}
            # finish the job rows and stop what is still alive
            for p in list(self._procs.values()):
                self._kill(p)
            for jid in self._job_ids:
                DetectionJob.objects.filter(pk=jid, status=DetectionJob.Status.RUNNING).update(
                    status=DetectionJob.Status.CANCELLED if not wanted else DetectionJob.Status.FAILED,
                    finished_at=timezone.now())
            if not wanted:
                break
            if time.time() - ran_since >= HEALTHY_AFTER:
                backoff = BACKOFF_START
            with self._lock:
                self._restarts += 1
                self._last_error = "Detector stopped (exit codes: %s)" % ", ".join(
                    f"{n} {c}" for n, c in codes.items())
                self._warning = (f"The detector stopped and was restarted {self._restarts} time"
                                 f"{'' if self._restarts == 1 else 's'}. Retrying in {backoff} s.")
            waited = 0
            while waited < backoff:
                time.sleep(1.0)
                waited += 1
                with self._lock:
                    if not self._want:
                        return
            backoff = min(backoff * 2, BACKOFF_MAX)
        with self._lock:
            self._procs = {}

    def _spawn(self, name, camera, args=None, ldir=None, env=None):
        """Popen one detector and record its DetectionJob row -> (proc, job_id)."""
        from core.models import DetectionJob
        base = settings.BASE_DIR
        if env is None:
            env = os.environ.copy()
            env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
            env["PYTHONUNBUFFERED"] = "1"
        if args is None:
            args = ["watch_" + name, "--source", camera.resolved_stream_url, "--camera", camera.code]
        stream_url = camera.resolved_stream_url
        log_dir = settings.MEDIA_ROOT / "uploads"
        os.makedirs(log_dir, exist_ok=True)
        child_env = dict(env)
        if name == "merged":
            child_env["LOOKOUT_DEBUG_DIR"] = str(ldir or live_dir())
        log = open(log_dir / f"live_{name}.log", "w")
        try:
            proc = subprocess.Popen([sys.executable, "manage.py", *args], cwd=str(base),
                                    stdout=log, stderr=subprocess.STDOUT, env=child_env)
        finally:
            log.close()
        job = DetectionJob.objects.create(
            violation_type=name, source_filename=f"Live — {camera.name}", source_path=stream_url,
            camera=camera, status=DetectionJob.Status.RUNNING, pid=proc.pid,
            created_by_id=getattr(self, "_user_id", None))
        return proc, job.pk

    def _launch(self, camera):
        from core.models import DetectionJob
        base = settings.BASE_DIR
        env = os.environ.copy()
        env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        env["PYTHONUNBUFFERED"] = "1"
        ldir = live_dir()
        os.makedirs(ldir, exist_ok=True)
        for name in ("state.json", "frame.jpg"):          # stale files would look like a live stream
            try:
                os.remove(ldir / name)
            except OSError:
                pass
        log_dir = settings.MEDIA_ROOT / "uploads"
        os.makedirs(log_dir, exist_ok=True)

        # Resolved once: stream_url on the row if set, else settings.STREAM_URL from
        # this machine's .env (see Camera.resolved_stream_url).
        stream_url = camera.resolved_stream_url
        commands = {"merged": ["watch_merged", "--source", stream_url, "--camera", camera.code]}
        if has_road_edge(camera):
            commands["parking"] = ["watch_parking", "--source", stream_url, "--camera", camera.code]
        # Improper disposal runs as its OWN process rather than joining the
        # merged pass: it shares no model forward pass with the others (dual
        # background subtraction plus a zero-shot bag check, not the merged
        # Bottle/Cigarette/knife model), so there is nothing to fold in.
        #
        # Gated on the setting at LAUNCH time, not just inside the detector,
        # because the cost being avoided is loading a second model at all —
        # the in-detector `disposal_enabled` check would still have paid it.
        # The same reason has_road_edge gates parking above.
        from core.models import SystemSettings
        if SystemSettings.load().disposal_enabled:
            commands["disposal"] = ["watch_disposal", "--source", stream_url,
                                    "--camera", camera.code]
        procs, job_ids = {}, []
        for name, args in commands.items():
            procs[name], job_id = self._spawn(name, camera, args=args, ldir=ldir, env=env)
            job_ids.append(job_id)
        with self._lock:
            self._procs = procs
            self._job_ids = job_ids
            self._launched_at = time.time()


monitor = LiveMonitor()


import atexit
atexit.register(monitor.shutdown)  # the detectors AND the tape go down with the web server
