"""Opt-in inference timing for the watch_* commands (``--timing``).

Nothing here changes what is detected or scored. ``enable()`` wraps a handful of
functions with stopwatches AT RUNTIME (monkeypatching), so the detection and
scoring code is untouched and a normal run (no ``--timing``) never imports this
module's patches. Everything is recorded in memory and written to a JSON file by
``finalize()``.

What is measured
  per frame   total time, and the time spent in each stage
              yolo_merged    merged_v2 whole-frame + tiles + person crops
              yolo_cascade   native-resolution person-crop pass for Cigarette
              person_track   yolov8n person detection + ByteTrack (model.track)
              bytetrack      the tracker's own update(), a part of person_track
              pose_full      yolov8n-pose on the whole frame (gesture, knife wrist)
              pose_crop      yolov8n-pose on one person crop (mouth anchor)
              vehicle_track  yolov8n vehicle detection + ByteTrack (parking)
              evidence       writing evidence (JPEG, annotated + raw clip, ffmpeg)
  per call    Qwen (Ollama) chat latency, end-to-end worker time, how long the
              check_async() call itself blocked the frame loop, thread name
  per event   first YOLO detection of the object, then the first time the event
              reached Monitoring / Possible / Likely, in video time and wall time
  per run     torch peak GPU memory (allocated and reserved)

Stage times are wall-clock around the call, with a CUDA synchronize before and
after so queued GPU work is counted where it was issued.
"""
import json
import threading
import time
from contextlib import contextmanager

ENABLED = False
_S = None                      # run state, created by enable()
_LOCK = threading.Lock()

# Class label (lower case) -> the incident-key prefix that engine uses.
_KEY_PREFIX = {"cigarette": "smoke", "bottle": "drink", "knife": "knife"}


def _sync():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.synchronize()
    except Exception:                                           # noqa: BLE001
        pass


class _State:
    def __init__(self, meta):
        self.meta = dict(meta)
        self.frames = []            # one dict per processed frame
        self.cur = None             # the frame being processed
        self.loose = {}             # stage samples recorded outside a frame
        self.first_det = {}         # (prefix, track_id) -> {video, wall}
        self.events = []            # incident status events
        self.seen_levels = set()
        self.qwen = []              # one dict per Qwen chat call
        self.checks = []            # one dict per check() (worker time)
        self.triggers = []          # one dict per check_async() call
        self.threads = []
        self.started_wall = time.time()


@contextmanager
def stage(name):
    """Time a block into the current frame under `name` (summed if repeated)."""
    if not ENABLED:
        yield
        return
    _sync()
    t0 = time.perf_counter()
    try:
        yield
    finally:
        _sync()
        ms = (time.perf_counter() - t0) * 1000.0
        cur = _S.cur
        if cur is not None:
            cur["stages"][name] = cur["stages"].get(name, 0.0) + ms
        else:
            _S.loose.setdefault(name, []).append(ms)


def _wrap(owner, attr, name):
    """Replace owner.attr with a version that times the call as stage `name`."""
    original = getattr(owner, attr)
    if getattr(original, "_timing_wrapped", False):
        return

    def timed(*args, **kwargs):
        with stage(name):
            return original(*args, **kwargs)

    timed._timing_wrapped = True
    timed.__wrapped__ = original
    setattr(owner, attr, timed)


def _wrap_tracker_update(model_attr_owner):
    """Time the ByteTrack association step on its own. ultralytics creates the
    tracker on the first model.track() call (predictor.trackers[0]); patch its
    update() the first time we see it."""
    try:
        from core.vision import recognition
        model = recognition.load_yolo()
        trackers = getattr(getattr(model, "predictor", None), "trackers", None)
        if not trackers:
            return
        for tr in trackers:
            if getattr(tr.update, "_timing_wrapped", False):
                continue
            original = tr.update

            def timed(*a, _o=original, **k):
                with stage("bytetrack"):
                    return _o(*a, **k)

            timed._timing_wrapped = True
            tr.update = timed
    except Exception:                                           # noqa: BLE001
        pass


def enable(meta=None):
    """Turn timing on and install the wrappers. Call once, before the run."""
    global ENABLED, _S
    if ENABLED:
        return
    _S = _State(meta or {})
    ENABLED = True
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:                                           # noqa: BLE001
        pass

    from core.vision import ai_checker, recognition

    _wrap(recognition, "detect_merged_far", "yolo_merged")
    _wrap(recognition, "detect_merged", "yolo_merged")
    _wrap(recognition, "detect_smoking_cascade", "yolo_cascade")
    _wrap(recognition, "detect_pose", "pose_full")
    _wrap(recognition, "pose_on_box", "pose_crop")
    _wrap(recognition, "detect_vehicles_tracked", "vehicle_track")

    original_persons = recognition.detect_persons_tracked

    def persons(*a, **k):
        with stage("person_track"):
            out = original_persons(*a, **k)
        _wrap_tracker_update(None)       # patch the tracker once it exists
        return out

    persons._timing_wrapped = True
    recognition.detect_persons_tracked = persons

    original_vehicles = recognition.detect_vehicles_tracked

    def vehicles(*a, **k):
        out = original_vehicles(*a, **k)
        _wrap_tracker_update(None)
        return out

    vehicles._timing_wrapped = True
    recognition.detect_vehicles_tracked = vehicles

    # ---- Qwen / Ollama ----
    original_chat = ai_checker.OllamaClient.chat

    def chat(self, kind, note, images_jpeg):
        w0, p0 = time.time(), time.perf_counter()
        ok = True
        try:
            return original_chat(self, kind, note, images_jpeg)
        except Exception:
            ok = False
            raise
        finally:
            with _LOCK:
                _S.qwen.append({
                    "model": self.model, "kind": kind, "images": len(images_jpeg),
                    "wall_start": w0, "wall_end": time.time(),
                    "seconds": time.perf_counter() - p0, "ok": ok,
                    "thread": threading.current_thread().name,
                })

    ai_checker.OllamaClient.chat = chat

    original_check = ai_checker.check

    def check(client, kind, note, images_jpeg, frame_files=None):
        p0 = time.perf_counter()
        result = original_check(client, kind, note, images_jpeg, frame_files=frame_files)
        with _LOCK:
            _S.checks.append({
                "model": client.model, "kind": kind, "images": len(images_jpeg),
                "seconds": time.perf_counter() - p0,
                "valid_reply": bool(getattr(result, "reply", None)),
                "error": getattr(result, "error", ""),
            })
        return result

    ai_checker.check = check

    original_async = ai_checker.check_async

    def check_async(client, ring, kind, keys, t0, note, on_done, **kw):
        p0 = time.perf_counter()
        thread = original_async(client, ring, kind, keys, t0, note, on_done, **kw)
        with _LOCK:
            _S.triggers.append({
                "model": client.model, "kind": kind, "video_t": t0,
                "wall": time.time(), "caller_blocked_ms": (time.perf_counter() - p0) * 1000.0,
                "synchronous": bool(kw.get("wait")),
                "caller_thread": threading.current_thread().name,
            })
            if thread is not None:
                _S.threads.append(thread)
        return thread

    ai_checker.check_async = check_async


# ---- frame bracketing ------------------------------------------------------

def frame_begin(video_t, index):
    _S.cur = {"i": index, "video_t": video_t, "wall": time.time(), "stages": {}}
    _S._t0 = time.perf_counter()


def frame_end():
    cur = _S.cur
    if cur is None:
        return
    _sync()
    cur["total_ms"] = (time.perf_counter() - _S._t0) * 1000.0
    _S.frames.append(cur)
    _S.cur = None


def install_frame_hook(cls, method, after=None):
    """Wrap cls.method(self, frame, now, ...) so each call is one timed frame.
    `now` is the second positional argument after the frame (video position for
    a file). `after(self)` may return a dict stored with the frame (outside the
    stopwatch's own cost it is trivial)."""
    original = getattr(cls, method)
    if getattr(original, "_timing_wrapped", False):
        return

    def wrapped(self, frame, now, *a, **k):
        frame_begin(now, len(_S.frames))
        try:
            return original(self, frame, now, *a, **k)
        finally:
            if after is not None and _S.cur is not None:
                try:
                    _S.cur["extra"] = after(self)
                except Exception:                               # noqa: BLE001
                    pass
            frame_end()

    wrapped._timing_wrapped = True
    setattr(cls, method, wrapped)


def install_evidence_hook(cls, method):
    """Time an evidence writer (e.g. _save_clips) as stage 'evidence'."""
    original = getattr(cls, method, None)
    if original is None or getattr(original, "_timing_wrapped", False):
        return

    def wrapped(self, *a, **k):
        with stage("evidence"):
            return original(self, *a, **k)

    wrapped._timing_wrapped = True
    setattr(cls, method, wrapped)


def install_parking_alert_hook(cls):
    """Record when a parking alert is created (video time, wall time)."""
    original = cls._create_alert

    def create_alert(self, score, label, frame, description, now=None):
        _S.events.append({"key": ["parking", label], "level": "alert",
                          "video_t": now, "wall": time.time(), "detail": description})
        return original(self, score, label, frame, description, now=now)

    create_alert._timing_wrapped = getattr(original, "_timing_wrapped", False)
    cls._create_alert = create_alert


def zone_snapshot(cmd):
    """Parking: the most seconds any vehicle has banked so far, and how many are watched."""
    states = list(cmd.zone.states.values())
    return {"max_banked_s": max((st.score for st in states), default=0.0),
            "watched": len(states),
            "stopped": sum(1 for st in states if st.stationary and st.inside)}


# ---- event timing ----------------------------------------------------------

def install_incident_hooks():
    """Record when an object is first detected and when each event first reaches
    each status. IncidentMixin methods are wrapped; their logic is unchanged."""
    from core.management.commands import _incidents
    mixin = _incidents.IncidentMixin

    original_cue = mixin._object_cue

    def object_cue(self, track, dets, now_ts):
        if dets:
            for d in dets:
                prefix = _KEY_PREFIX.get(str(d[5]).lower())
                if prefix is not None:
                    _S.first_det.setdefault((prefix, track.id),
                                            {"video": now_ts, "wall": time.time()})
        return original_cue(self, track, dets, now_ts)

    mixin._object_cue = object_cue

    original_sync = mixin._incident_sync

    def incident_sync(self, key, score, now, **kw):
        out = original_sync(self, key, score, now, **kw)
        level = getattr(score, "level", None) or getattr(score, "band", None)
        if level and level != "none":
            inc = self._incident_book().get(key)
            if inc is not None and getattr(inc, "announced", False) and (key, level) not in _S.seen_levels:
                _S.seen_levels.add((key, level))
                _S.events.append({"key": [str(key[0]), key[1]], "level": level,
                                  "video_t": now, "wall": time.time()})
        return out

    mixin._incident_sync = incident_sync


# ---- output ----------------------------------------------------------------

def finalize(path):
    """Wait for in-flight Qwen threads, then write the JSON report."""
    global ENABLED
    if not ENABLED:
        return
    deadline = time.time() + 600
    for thread in list(_S.threads):
        thread.join(timeout=max(0.0, deadline - time.time()))
    ENABLED = False
    vram = {}
    try:
        import torch
        if torch.cuda.is_available():
            vram = {"max_allocated_mib": torch.cuda.max_memory_allocated() / 2**20,
                    "max_reserved_mib": torch.cuda.max_memory_reserved() / 2**20,
                    "device": torch.cuda.get_device_name(0)}
    except Exception:                                           # noqa: BLE001
        pass
    first = {f"{k[0]}:{k[1]}": v for k, v in _S.first_det.items()}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"meta": _S.meta, "frames": _S.frames, "events": _S.events,
                   "first_detection": first, "qwen_chat": _S.qwen,
                   "qwen_check": _S.checks, "qwen_trigger": _S.triggers,
                   "loose_stages_ms": _S.loose, "vram": vram,
                   "wall_start": _S.started_wall, "wall_end": time.time()}, fh)
    return path
