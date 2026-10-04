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
"""
import os
import subprocess
import sys
import threading
import time

from django.conf import settings
from django.utils import timezone

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

    Both shapes count. A ZONE is a closed polygon keyed by "type": "zone" and
    needs at least three points; a legacy EDGE is an open kerb line keyed
    left/right and needs two.

    This used to test the left/right keys ALONE. That was correct while the
    editor drew kerb lines, and silently wrong the moment it became zone-only:
    a camera with a perfectly good polygon answered False, so the live monitor
    launched watch_merged and quietly left parking out — no error, no log line,
    just three violations where four were configured.
    """
    edges = camera.edges or {}
    if not isinstance(edges, dict):
        return False
    for spec in edges.values():
        if not isinstance(spec, dict):
            continue
        points = spec.get("points") or []
        minimum = 3 if spec.get("type") == "zone" else 2
        if len(points) >= minimum:
            return True
    return False


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
            if not camera.stream_url:
                return False, "The camera has no stream address configured."
            self._want = True
            self._started_at = timezone.now()
            self._restarts = 0
            self._warning = ""
            self._last_error = ""
            self._user_id = getattr(user, "pk", None)
            self._thread = threading.Thread(target=self._supervise, daemon=True, name="live-monitor")
            self._thread.start()
            return True, "Starting detection."

    def stop(self):
        with self._lock:
            self._want = False
            procs = list(self._procs.values())
        for p in procs:
            self._kill(p)
        return True, "Detection stopped."

    @staticmethod
    def _reap_orphans():
        """Detector processes left behind by an earlier run of the web server (it restarts itself
        when code changes) would double up on the camera. End them and close their job rows."""
        import signal
        from core.models import DetectionJob
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
                    "notice": notice, "restarts": 0, "parking_ready": edge_ok}
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
                "last_error": last_error}

    # ---- the supervisor ----------------------------------------------------------

    def _supervise(self):
        from core.models import Camera, DetectionJob
        backoff = BACKOFF_START
        while True:
            with self._lock:
                if not self._want:
                    break
            camera = Camera.objects.filter(code=CAMERA_CODE).first()
            if camera is None or not camera.stream_url:
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

        commands = {"merged": ["watch_merged", "--source", camera.stream_url, "--camera", camera.code]}
        if has_road_edge(camera):
            commands["parking"] = ["watch_parking", "--source", camera.stream_url, "--camera", camera.code]
        procs, job_ids = {}, []
        for name, args in commands.items():
            child_env = dict(env)
            if name == "merged":
                child_env["LOOKOUT_DEBUG_DIR"] = str(ldir)
            log = open(log_dir / f"live_{name}.log", "w")
            try:
                procs[name] = subprocess.Popen([sys.executable, "manage.py", *args], cwd=str(base),
                                               stdout=log, stderr=subprocess.STDOUT, env=child_env)
            finally:
                log.close()
            job = DetectionJob.objects.create(
                violation_type=name, source_filename=f"Live — {camera.name}", source_path=camera.stream_url,
                camera=camera, status=DetectionJob.Status.RUNNING, pid=procs[name].pid,
                created_by_id=getattr(self, "_user_id", None))
            job_ids.append(job.pk)
        with self._lock:
            self._procs = procs
            self._job_ids = job_ids
            self._launched_at = time.time()


monitor = LiveMonitor()


import atexit
atexit.register(monitor.stop)      # the detectors go down with the web server
