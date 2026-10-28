"""Trace the no-parking zone from a LIVE camera stream, in the background.

The geometry is zone_autodetect.trace(), unchanged. This module is only the glue
the Edge Zones editor needs around it: its own short-lived RTSP connection, a
wall-clock sampling cadence, a deadline, cancellation, and a job record the
browser can poll. It imports no Django and no torch -- `detect` is injected, as
in zone_autodetect.trace_clip -- so it is unit-tested with a stub detector.

Why a job and not one long request: a trace holds the camera for ~48 s, which is
longer than many tunnels/proxies keep an idle HTTP request open, and a request
cannot report progress or be cancelled. The browser starts it, polls it, and
may cancel it.

Why not zone_autodetect.sample_clip: that paces by frame COUNT using the
stream's reported fps (RTSP often reports none), has no deadline, and cannot be
interrupted. A live trace needs "a sample every 0.4 s of real time, stop at 90 s,
stop when told", so this reads forward with grab() and retrieves on the clock.
"""
import os
import threading
import time
import uuid

import cv2

from core.vision import zone_autodetect as autodetect

STRIDE_SECONDS = autodetect.SAMPLE_STRIDE_SECONDS      # 0.4 s
MAX_SAMPLES = autodetect.MAX_SAMPLES                   # 120 -> ~48 s
DEADLINE_SECONDS = 90.0        # the whole trace, connect + sampling + inference
OPEN_TIMEOUT_SECONDS = 15.0    # the stream must deliver a first frame by then
# Same transport options watch_parking opens its stream with, plus a socket
# timeout so an unreachable camera fails in seconds instead of hanging.
FFMPEG_OPTIONS = ("rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|"
                  "analyzeduration;500000|probesize;500000|stimeout;5000000")

# Reasons shown to the operator (also the `code` in the job record).
UNREACHABLE = "unreachable"
NO_FRAMES = "no_frames"
NO_VEHICLES = "no_vehicles"
NONE_MOVED = "none_moved"
TOO_FEW = "too_few_sightings"
DEGENERATE = "degenerate"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
ERROR = "error"


class Cancelled(Exception):
    pass


def _open(source):
    # The option string is read by OpenCV when a capture is opened. It is set
    # for this open only and restored, so it cannot leak into other code in the
    # server process.
    key = "OPENCV_FFMPEG_CAPTURE_OPTIONS"
    previous = os.environ.get(key)
    os.environ[key] = FFMPEG_OPTIONS
    try:
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:                                       # noqa: BLE001
        pass
    return cap


def classify_reason(report):
    """Failure code for a trace() report that produced no polygon."""
    if not report.get("detections"):
        return NO_VEHICLES
    if not report.get("moving_tracks"):
        return NONE_MOVED
    if report.get("ground_points", 0) < autodetect.MIN_TRAFFIC_POINTS:
        return TOO_FEW
    return DEGENERATE


def run_trace(source, detect, job, open_capture=_open, clock=time.monotonic):
    """Samples `source`, traces, and returns (points, report, frame_shape).

    Updates `job` (a dict with "progress"/"samples") as it goes and raises
    Cancelled when job["cancel"] is set. Raises TimeoutError/ConnectionError for
    the stream-level failures; the caller maps those to reasons.
    """
    started = clock()
    cap = open_capture(source)
    samples, first_shape = [], None
    try:
        if not cap.isOpened():
            raise ConnectionError("The camera stream could not be opened.")
        next_at = clock()
        last_frame_at = clock()
        while len(samples) < MAX_SAMPLES:
            if job.get("cancel"):
                raise Cancelled()
            now = clock()
            if now - started > DEADLINE_SECONDS:
                raise TimeoutError("The trace ran past its time limit.")
            if first_shape is None and now - started > OPEN_TIMEOUT_SECONDS:
                raise ConnectionError("The camera opened but sent no frames.")
            if now - last_frame_at > OPEN_TIMEOUT_SECONDS and samples:
                break                    # stream stalled mid-trace: use what we have
            if now < next_at:
                # Drain, so the next retrieved frame is current, not backlog.
                if not cap.grab():
                    if first_shape is None:
                        raise ConnectionError("The camera stream returned no frames.")
                    break
                last_frame_at = clock()
                continue
            ok, frame = cap.read()
            if not ok:
                if first_shape is None:
                    raise ConnectionError("The camera stream returned no frames.")
                break
            last_frame_at = clock()
            if first_shape is None:
                first_shape = frame.shape
            samples.append(detect(frame))
            job["samples"] = len(samples)
            job["progress"] = len(samples) / MAX_SAMPLES
            next_at = now + STRIDE_SECONDS
    finally:
        cap.release()

    if first_shape is None or not samples:
        raise ConnectionError("Could not read any frames from the camera.")
    points, report = autodetect.trace(samples, first_shape)
    report.pop("cloud", None)
    return points, report, first_shape


# ---- one job per camera -----------------------------------------------------

_lock = threading.Lock()
_jobs = {}          # camera pk -> job dict (the latest, running or finished)


def get(camera_pk):
    with _lock:
        job = _jobs.get(camera_pk)
        return public(job) if job else None


def public(job):
    return {k: v for k, v in job.items() if k not in ("cancel",)}


def start(camera_pk, source, detect, open_capture=_open, background=True):
    """-> (job, started). started is False when one is already running."""
    with _lock:
        existing = _jobs.get(camera_pk)
        if existing and existing["state"] == "running":
            return public(existing), False
        job = {"id": uuid.uuid4().hex, "state": "running", "progress": 0.0,
               "samples": 0, "max_samples": MAX_SAMPLES,
               "estimated_seconds": round(MAX_SAMPLES * STRIDE_SECONDS),
               "started_at": time.time(), "cancel": False}
        _jobs[camera_pk] = job

    def work():
        try:
            points, report, shape = run_trace(source, detect, job, open_capture)
            height, width = shape[:2]
            job["report"] = report
            if points is None:
                job["code"] = classify_reason(report)
                job["reason"] = report.get("reason", "No zone could be traced.")
                job["state"] = "failed"
            else:
                job["points"] = [[round(x, 1), round(y, 1)] for x, y in points]
                job["width"], job["height"] = width, height
                job["state"] = "done"
        except Cancelled:
            job.update(state="cancelled", code=CANCELLED, reason="Trace cancelled.")
        except TimeoutError as exc:
            job.update(state="failed", code=TIMEOUT, reason=str(exc))
        except ConnectionError as exc:
            code = NO_FRAMES if "frames" in str(exc) else UNREACHABLE
            job.update(state="failed", code=code, reason=str(exc))
        except Exception as exc:                            # noqa: BLE001
            job.update(state="failed", code=ERROR, reason=f"Could not trace a zone: {exc}")
        finally:
            job["finished_at"] = time.time()

    if background:
        threading.Thread(target=work, daemon=True, name=f"zone-trace-{camera_pk}").start()
    else:
        work()
    return public(job), True


def cancel(camera_pk):
    with _lock:
        job = _jobs.get(camera_pk)
        if job and job["state"] == "running":
            job["cancel"] = True
            return True
    return False


def reset():
    """Test hook."""
    with _lock:
        _jobs.clear()
