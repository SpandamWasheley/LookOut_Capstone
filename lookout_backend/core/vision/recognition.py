"""Shared CV plumbing for the detection pipeline.

YOLOv8 (ultralytics) detects people and the violation objects (see MODEL_PATH);
YOLOv8-pose gives the keypoints the mouth anchor and the hand-to-mouth gesture
are built from. There is no facial recognition in LookOut.

No Django model access happens here — this module is pure CV plumbing so it
stays importable/testable independent of the management commands that use it.
"""

import json
import math
import os
import datetime
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np


def _hold_reps(ts, next_ts, playback_fps, max_hold_seconds):
    """How many times to repeat one buffered frame so real-time gaps between
    frames become real-time playback duration, capped so a detector stall
    can't freeze the clip on a single frame for longer than max_hold_seconds."""
    max_reps = max(1, round(max_hold_seconds * playback_fps))
    reps = max(1, round((next_ts - ts) * playback_fps))
    return min(reps, max_reps)


def draw_label(frame, text, x, y, color, scale=0.8, thickness=2, bg=(0, 0, 0)):
    """Draws one detection/track/cluster label with a filled background —
    the same convention ClipRecorder._stamp already uses for its timestamp
    overlay, just applied to per-box labels too — so text stays legible
    against a bright frame instead of the bare colored text every call site
    used to draw directly onto the image. Also clamps the origin so the
    label can't run off the frame's right edge, which a longer label (class
    name + confidence + dwell) makes much more likely than the short ones
    this used to draw.

    `(x, y)` is the text BASELINE origin, same convention cv2.putText itself
    uses and every call site already passes — typically `(x1, max(y1 - 8, 0))`,
    just above a box's top-left corner.
    """
    h, w = frame.shape[:2]
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    x = max(0, min(x, w - tw - 2))
    y = max(th + 2, y)
    cv2.rectangle(frame, (x - 2, y - th - 4), (x + tw + 2, y + baseline + 2), bg, -1)
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color,
               thickness, cv2.LINE_AA)


def draw_dashed_rect(frame, pt1, pt2, color, thickness=2, dash_len=8, gap_len=6):
    """Dashed-outline version of cv2.rectangle — marks a box as HISTORICAL
    (the track's last known detection, not a live one this frame) so an
    evidence clip can show why a track that crossed its dwell/alert
    threshold on a frame with no current detection still has a box
    justifying it, instead of drawing nothing that frame. cv2 has no native
    dashed-line primitive, hence drawing each side as short segments."""
    x1, y1 = pt1
    x2, y2 = pt2

    def dashed_line(p1, p2):
        (lx1, ly1), (lx2, ly2) = p1, p2
        length = max(1, int(math.hypot(lx2 - lx1, ly2 - ly1)))
        step = dash_len + gap_len
        for i in range(0, length, step):
            t0, t1 = i / length, min(i + dash_len, length) / length
            sx, sy = int(lx1 + (lx2 - lx1) * t0), int(ly1 + (ly2 - ly1) * t0)
            ex, ey = int(lx1 + (lx2 - lx1) * t1), int(ly1 + (ly2 - ly1) * t1)
            cv2.line(frame, (sx, sy), (ex, ey), color, thickness)

    dashed_line((x1, y1), (x2, y1))
    dashed_line((x2, y1), (x2, y2))
    dashed_line((x2, y2), (x1, y2))
    dashed_line((x1, y2), (x1, y1))


class ClipRecorder:
    """Rolling buffer of recent annotated frames, written out as an MP4 when a
    violation fires — so evidence is a ~N-second clip with the detection boxes
    drawn on it, not a single still.

    Each processed frame (with its boxes already drawn) is pushed via add();
    frames older than `seconds` are dropped, so the buffer always holds roughly
    the last N seconds leading up to the alert — the approach to the violation,
    which is the useful part.

    save() reconstructs real-time playback at a fixed FPS by holding each frame
    for its real duration, so the clip is ~N seconds long and plays in any
    browser even when the detector processed only a few frames per second (in
    which case it's choppy but still the full N seconds).
    """

    # Longest edge a buffered frame is kept at. Evidence clips are watched in a
    # browser panel a few hundred pixels wide, so holding 2560x1440 buys nothing
    # visible and costs 10.5 MB per frame: 30 seconds at 5 fps is ~1.6 GB for
    # ONE recorder, and merged mode runs three. That is what exhausted memory on
    # a 16 GB machine with the vision-language model also resident, and it
    # failed as a crash mid-run rather than as anything that named the cause.
    MAX_EDGE = 1280

    # A hard ceiling as well as the time window. The window alone assumes a
    # steady frame rate; a fast source or a stalled cutoff lets the deque grow
    # without bound, and running out of memory is a worse failure than a clip
    # with fewer frames in it.
    MAX_FRAMES = 450

    def __init__(self, seconds=10, playback_fps=12, label="", max_edge=None):
        self.seconds = seconds
        self.playback_fps = playback_fps
        self.label = label            # e.g. camera code, drawn next to the time
        self.max_edge = max_edge or self.MAX_EDGE
        self._buf = deque()  # (timestamp, annotated_frame)
        # Cap in seconds on how long a single buffered frame can be held during
        # playback. Without this, a detector stall (e.g. a slow far-mode tile
        # pass) between two buffered frames turns into a multi-second freeze on
        # ONE frame instead of a shorter, less misleading gap.
        self._max_hold_seconds = 2.0

    def add(self, frame, now):
        self._buf.append((now, self._fit(frame)))
        cutoff = now - self.seconds
        while self._buf and self._buf[0][0] < cutoff:
            self._buf.popleft()
        while len(self._buf) > self.MAX_FRAMES:
            self._buf.popleft()

    def _fit(self, frame):
        """A copy no larger than `max_edge` on its longest side.

        Always a copy: the capture loop reuses its buffer, so a stored reference
        would be overwritten within milliseconds and the clip would be a reel of
        whatever the camera is looking at now.
        """
        h, w = frame.shape[:2]
        if max(h, w) <= self.max_edge:
            return frame.copy()
        scale = self.max_edge / float(max(h, w))
        return cv2.resize(frame, (max(int(w * scale), 1), max(int(h * scale), 1)),
                          interpolation=cv2.INTER_AREA)

    def _stamp(self, frame, ts):
        """Burns the real capture date/time (system clock) into the frame — the
        evidentiary timestamp, starting at the clip's start and advancing per
        frame. Independent of the camera's own OSD clock, which may be wrong."""
        t = datetime.datetime.fromtimestamp(ts)
        text = t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 100000}"
        if self.label:
            text = f"{self.label}  {text}"
        h, w = frame.shape[:2]
        scale = max(0.5, min(1.0, w / 1280))
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        x, y = 10, 12 + th
        cv2.rectangle(frame, (x - 6, y - th - 8), (x + tw + 6, y + 8), (0, 0, 0), -1)
        cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
                    (0, 255, 0), 2, cv2.LINE_AA)

    def save(self, path):
        """Writes the buffered clip to `path` (MP4). Returns True on success."""
        if len(self._buf) < 2:
            return False
        frames = list(self._buf)
        h, w = frames[0][1].shape[:2]

        # Write to a temporary mp4v file (reliable, no codec DLL issues).
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            temp_path = tmp.name

        try:
            writer = cv2.VideoWriter(
                str(temp_path), cv2.VideoWriter_fourcc(*"mp4v"),
                self.playback_fps, (w, h),
            )
            if not writer.isOpened():
                return False
            for i, (ts, frame) in enumerate(frames):
                self._stamp(frame, ts)   # real date/time of THIS frame
                nxt = frames[i + 1][0] if i + 1 < len(frames) else ts + 1.0 / self.playback_fps
                reps = _hold_reps(ts, nxt, self.playback_fps, self._max_hold_seconds)
                for _ in range(reps):
                    writer.write(frame)
            writer.release()

            # Transcode to H.264 with ffmpeg for browser compatibility.
            # -movflags +faststart moves moov atom to front for streaming without
            # downloading the whole file. -crf 28 provides good quality/size tradeoff.
            return _run_ffmpeg([
                "-i", temp_path,
                "-vf", "scale=1280:-2",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "28",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(path),
            ])
        finally:
            # Clean up temp file.
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


# How much context a RAW evidence clip carries around the alert-firing moment.
# Kept short of the annotated buffer's window on purpose — this is cut from the
# original source at full frame rate/resolution, so it doesn't need to be long
# to be useful, and shorter keeps the -c copy cut fast.
RAW_CLIP_PRE_SECONDS = 15
RAW_CLIP_POST_SECONDS = 2


def _run_ffmpeg(args, timeout=300):
    """Runs ffmpeg with `args` (excluding the binary name/-y). Returns True on
    success; logs and returns False on any failure — evidence capture must
    never crash a watcher over a transcode/cut problem."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            print(f"ffmpeg failed: {result.stderr}")
            return False
        return True
    except FileNotFoundError:
        print("ffmpeg not found — cannot produce evidence clip")
        return False
    except subprocess.TimeoutExpired:
        print(f"ffmpeg timeout (>{timeout}s)")
        return False


def cut_raw_clip(source_path, start_sec, duration_sec, out_path):
    """Cuts [start_sec, start_sec + duration_sec) directly out of a seekable
    source video file with -c copy (stream copy, no re-encode) — full source
    frame rate and resolution, unlike the annotated clip, which is
    reconstructed from the detector's own much sparser processed-frame buffer.

    File sources only: -ss before -i seeks the input directly (fast, but
    keyframe-snapped rather than frame-exact — fine for evidence context).
    This only works for a real file/seekable stream, not a live RTSP feed,
    which can't be seeked backwards this way.
    """
    return _run_ffmpeg([
        "-ss", f"{max(0.0, start_sec):.3f}",
        "-i", str(source_path),
        "-t", f"{duration_sec:.3f}",
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ], timeout=60)


class RawFrameRecorder:
    """Rolling buffer of RAW (unannotated) frames for a LIVE source, for cutting
    a raw evidence clip when there's no seekable file to pull from instead (see
    cut_raw_clip for the file-source path).

    Frames are JPEG-encoded rather than held as raw arrays to keep memory
    bounded: a raw 2560x1440x3 frame is ~11MB, so a 15s window at 15fps held as
    arrays would be ~2.5GB continuously. JPEG brings that down to roughly
    200KB/frame (~45MB for the same window). Encode cost is paid once per frame
    on capture; decode only happens if save() is actually called.

    A continuous segment recorder was considered as a source for this instead
    of an in-process buffer, and rejected: cv2.VideoWriter-based mp4 muxing
    doesn't finalize the moov atom until the segment rolls over and closes (the
    same class of problem fixed for the annotated clip's own mp4v output), so
    the currently-open segment — which is always the one covering "right now" —
    isn't safely readable by a second process. This buffer sidesteps that, and
    is why no continuous recorder is needed for evidence capture.
    """

    def __init__(self, seconds=RAW_CLIP_PRE_SECONDS + RAW_CLIP_POST_SECONDS,
                 quality=85, playback_fps=12):
        self.seconds = seconds
        self.quality = quality
        self.playback_fps = playback_fps
        self._max_hold_seconds = 2.0
        self._buf = deque()  # (timestamp, jpeg_bytes)

    def add(self, frame, now):
        ok, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if ok:
            self._buf.append((now, enc.tobytes()))
        cutoff = now - self.seconds
        while self._buf and self._buf[0][0] < cutoff:
            self._buf.popleft()

    def save(self, path):
        """Writes the buffered clip to `path` (MP4). Returns True on success."""
        if len(self._buf) < 2:
            return False
        frames = list(self._buf)
        first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
        if first is None:
            return False
        h, w = first.shape[:2]

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            temp_path = tmp.name

        try:
            writer = cv2.VideoWriter(
                str(temp_path), cv2.VideoWriter_fourcc(*"mp4v"),
                self.playback_fps, (w, h),
            )
            if not writer.isOpened():
                return False
            for i, (ts, enc) in enumerate(frames):
                frame = cv2.imdecode(np.frombuffer(enc, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                nxt = frames[i + 1][0] if i + 1 < len(frames) else ts + 1.0 / self.playback_fps
                reps = _hold_reps(ts, nxt, self.playback_fps, self._max_hold_seconds)
                for _ in range(reps):
                    writer.write(frame)
            writer.release()

            return _run_ffmpeg([
                "-i", temp_path,
                "-vf", "scale=1280:-2",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "28",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(path),
            ])
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


class LatestFrameReader:
    """Background thread that keeps only the newest frame from a live capture.

    The detectors run far slower than a camera delivers (often <1 FPS in far
    mode vs 15-25 FPS from the stream), so OpenCV's RTSP buffer fills with a
    backlog and cap.read() returns frames that are seconds old — the visible
    "delay". This drains the stream as fast as it arrives and overwrites, so the
    processing loop always gets a near-live frame; latency stays at ~one frame
    plus one inference instead of a growing backlog.

    Also reconnects: a dropped RTSP connection otherwise leaves cap.read()
    returning False forever — the process stays alive, burning CPU, silently
    producing zero detections. After max_consecutive_failures failed reads
    (the point past which a stream is treated as "really gone"),
    this releases the dead capture and calls open_fn() in a retry loop until a
    fresh one opens, logging every attempt and the eventual recovery via `log`.
    Pass open_fn=None to opt out and keep the old non-reconnecting behavior.

    Use for live sources only (RTSP / webcam). A video file should be read
    sequentially so no frames are skipped.
    """

    def __init__(self, cap, open_fn=None, log=None,
                 max_consecutive_failures=30, reconnect_wait=3.0):
        self.cap = cap
        self._open_fn = open_fn
        self._log = log or (lambda msg: None)
        self._max_consecutive_failures = max_consecutive_failures
        self._reconnect_wait = reconnect_wait
        self._lock = threading.Lock()
        self._frame = None
        self._stopped = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        fails = 0
        while not self._stopped:
            ok, f = self.cap.read()
            if not ok:
                fails += 1
                if self._open_fn is not None and fails >= self._max_consecutive_failures:
                    self._reconnect(fails)
                    fails = 0
                else:
                    time.sleep(0.01)
                continue
            if fails:
                self._log(f"Live source recovered after {fails} failed read(s).")
            fails = 0
            with self._lock:
                self._frame = f

    def _reconnect(self, fails):
        self._log(f"Live source stopped responding after {fails} consecutive "
                   "failed reads — reconnecting...")
        try:
            self.cap.release()
        except Exception:
            pass
        attempt = 0
        while not self._stopped:
            attempt += 1
            new_cap = self._open_fn()
            if new_cap is not None and new_cap.isOpened():
                self.cap = new_cap
                self._log(f"Reconnected to live source (attempt {attempt}).")
                return
            self._log(f"Reconnect attempt {attempt} failed — retrying in "
                       f"{self._reconnect_wait:.0f}s.")
            # Sleep in small increments so stop() doesn't have to wait out a
            # full reconnect_wait to interrupt a stuck retry loop.
            for _ in range(int(self._reconnect_wait / 0.1)):
                if self._stopped:
                    return
                time.sleep(0.1)

    def read(self):
        with self._lock:
            if self._frame is None:
                return False, None
            # Copy so debug drawing on the returned frame can't mutate the array
            # the reader thread still holds as "latest".
            return True, self._frame.copy()

    def stop(self):
        self._stopped = True
        self._t.join(timeout=1)
        self.cap.release()

VISION_DIR = Path(__file__).resolve().parent

# LookOut uses a single merged model (merged_v2: Bottle, Cigarette, knife) for all
# violations. Vapes have no class (puff-only path). Holdup detects knives only.
#
# Override with the LOOKOUT_MODEL env var. The per-violation names below are
# aliases of this one path, kept so callers and error messages keep working;
# each detector keeps only its own classes (see SMOKING_CLASSES etc.).
#
# Labels come from the model's own names dict (see _smoking_boxes_from_result),
# so class index/order is irrelevant -- only the name strings matter, matched
# case-insensitively (Bottle / Cigarette / knife).
MODEL_PATH = Path(os.environ.get("LOOKOUT_MODEL", str(VISION_DIR / "merged_v2.pt")))
SMOKING_MODEL_PATH = THIEF_MODEL_PATH = DRINKING_MODEL_PATH = MERGED_MODEL_PATH = MODEL_PATH

SMOKING_CLASSES = {"cigarette", "vape"}
DRINKING_CLASSES = {"bottle"}
THIEF_CLASSES = {"knife"}


def _only(dets, classes):
    """Keeps detections whose label is in `classes` (case-insensitive)."""
    return [d for d in dets if str(d[5]).lower() in classes]

PERSON_CLASS_ID = 0  # COCO class id for "person"

# COCO drinking-vessel classes. The custom drinking model recognises exactly one
# brand, so anything poured into a glass — or any other brand of bottle — is
# invisible to it. These come free from the same stock yolov8n already being run
# for person detection, extending coverage to any vessel at no extra inference
# cost. They say nothing about CONTENTS (a water bottle looks the same), so
# callers must treat them as a weaker signal than a branded detection.
VESSEL_CLASS_IDS = {
    39: "bottle",
    40: "wine glass",
    41: "cup",
}

# COCO class ids for the vehicle types relevant to illegal-parking /
# obstruction detection. yolov8n.pt is trained on COCO, so these come for
# free from the same model already used for person detection.
VEHICLE_CLASS_IDS = {
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}

# COCO "carriable" classes — the personal property Layer E watches for a change
# of custody (rules E4, E9, E21, E22). Like the vessel and vehicle sets above,
# these are a different class filter on the person pass that already runs every
# frame, so they cost no extra inference. Custody transfer is the single most
# specific theft cue available without training a new model.
CARRIABLE_CLASS_IDS = {
    24: "backpack",
    26: "handbag",
    28: "suitcase",
}

# Two-wheelers only, per the Layer E scope limitation: at the planned mounting
# geometry (~6m elevation, 2.8mm lens) a four-wheeled vehicle does not fit
# usably in frame at detection range, so carnapping is not claimed for cars.
TWO_WHEELER_LABELS = {"motorcycle", "bicycle"}
BICYCLE_CLASS_ID = 1

# --- Phase 0: configurable inference resolution --------------------------
# YOLO downscales every frame to `imgsz` before inference (default 640), so a
# person or object far from the camera can lose all detail even on a 1440p main
# stream. Raising imgsz keeps distant subjects resolvable — but it is quadratic
# in cost, so it is only affordable on a GPU. The plan targets 960 near / 1280
# cascade on an RTX 4050; on a CPU-only build those are seconds per frame, so we
# default to 640 and let the caller raise it. Must be a multiple of 32.
#
# Override per run with the LOOKOUT_IMGSZ env var, or per call via the imgsz
# argument now threaded through the person/smoking/thief detectors.
def _gpu_available():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


# GPU-aware default: 960 where a CUDA GPU is present (per the plan), 640 on CPU
# (where 960 would drop near mode well under 10 FPS — the plan's own fallback).
NEAR_IMGSZ = int(os.environ.get("LOOKOUT_IMGSZ", 960 if _gpu_available() else 640))
CASCADE_IMGSZ = int(os.environ.get("LOOKOUT_CASCADE_IMGSZ", 1280))
# The far path (whole frame + tiles + person crops) used to call the model with
# ultralytics' default imgsz of 640 while the near path used NEAR_IMGSZ (960 on a
# GPU), so a 2560 px frame was shrunk 4x before the model ever saw it. Both paths
# now use the same size. Override with LOOKOUT_FAR_IMGSZ (e.g. 640 to trade
# recall for speed).
FAR_IMGSZ = int(os.environ.get("LOOKOUT_FAR_IMGSZ", NEAR_IMGSZ))

_yolo_model = None
_merged_model = None
_pose_model = None

# COCO 17-keypoint indices used by the hand-to-mouth gesture detector.
KP_NOSE = 0
KP_LSHOULDER, KP_RSHOULDER = 5, 6
KP_LWRIST, KP_RWRIST = 9, 10
KP_MIN_CONF = 0.3   # a keypoint below this confidence is treated as unknown


def load_yolo():
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO

        _yolo_model = YOLO("yolov8n.pt")
    return _yolo_model


def load_pose():
    """Lazy-loads YOLOv8-pose (person keypoints). Auto-downloads yolov8n-pose.pt
    on first use, like the plain detector. Prints the device it ends up on once,
    so a run log shows whether pose is on the GPU and in half precision."""
    global _pose_model
    if _pose_model is None:
        from ultralytics import YOLO

        _pose_model = YOLO("yolov8n-pose.pt")
    return _pose_model


_pose_logged = False


def _log_pose_device_once():
    """Prints where pose inference REALLY runs. Must be called after the first
    prediction: ultralytics only moves the model to the GPU (and applies half
    precision) when it first predicts, so the device at load time is misleading."""
    global _pose_logged
    if _pose_logged:
        return
    _pose_logged = True
    m = load_pose()
    try:
        pred = m.predictor
        print(f"[pose] YOLOv8n-pose running on {pred.device}, "
              f"{'FP16' if pred.args.half else 'FP32'}", flush=True)
    except Exception:
        print("[pose] YOLOv8n-pose device unknown", flush=True)


def detect_pose(frame, conf=0.4, imgsz=None):
    """Returns [(box, keypoints)] per person, where keypoints is a 17x3 array of
    (x, y, confidence). Used for the pose-based hand-to-mouth smoking gesture,
    which works at distances where the cigarette itself is too small to detect —
    a wrist and nose stay resolvable long after an 85mm cigarette becomes a
    couple of pixels."""
    model = load_pose()
    results = model(frame, verbose=False, imgsz=imgsz or NEAR_IMGSZ)[0]
    people = []
    if results.keypoints is None or results.boxes is None:
        return people
    kpts = results.keypoints.data.cpu().numpy()   # (N, 17, 3)
    boxes = results.boxes
    for i in range(len(kpts)):
        if float(boxes.conf[i]) < conf:
            continue
        box = tuple(int(v) for v in boxes.xyxy[i].tolist())
        people.append((box, kpts[i]))
    return people


def hand_to_mouth_ratio(kpts):
    """Distance of the CLOSEST wrist to the nose, normalised by shoulder width,
    or None if the needed keypoints aren't confidently visible.

    Normalising by shoulder width makes the measure distance-invariant: the same
    gesture reads the same at 3m and 12m, since both the wrist-nose gap and the
    shoulders shrink together. Low ratio = hand at the face; high = hand down.
    """
    nose = kpts[KP_NOSE]
    ls, rs = kpts[KP_LSHOULDER], kpts[KP_RSHOULDER]
    if nose[2] < KP_MIN_CONF or ls[2] < KP_MIN_CONF or rs[2] < KP_MIN_CONF:
        return None
    shoulder_w = ((ls[0] - rs[0]) ** 2 + (ls[1] - rs[1]) ** 2) ** 0.5
    if shoulder_w < 1:
        return None
    best = None
    for w in (kpts[KP_LWRIST], kpts[KP_RWRIST]):
        if w[2] < KP_MIN_CONF:
            continue
        d = ((w[0] - nose[0]) ** 2 + (w[1] - nose[1]) ** 2) ** 0.5 / shoulder_w
        if best is None or d < best:
            best = d
    return best


def smoking_model_available():
    """True if the custom smoking weights are present (callers skip cleanly if not)."""
    return SMOKING_MODEL_PATH.exists()


def load_smoking_model():
    """Smoking uses the shared merged model; see MODEL_PATH."""
    return load_merged_model()


def detect_persons(frame, conf=0.5, imgsz=None):
    """Returns a list of (x1, y1, x2, y2, conf) boxes for detected people.

    `imgsz` is the inference resolution (default NEAR_IMGSZ). Raising it keeps
    distant people resolvable at GPU cost; see the NEAR_IMGSZ note.
    """
    model = load_yolo()
    results = model(frame, verbose=False, imgsz=imgsz or NEAR_IMGSZ)[0]
    boxes = []
    for box in results.boxes:
        if int(box.cls[0]) != PERSON_CLASS_ID:
            continue
        if float(box.conf[0]) < conf:
            continue
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        boxes.append((x1, y1, x2, y2, float(box.conf[0])))
    return boxes


# --- pose-based mouth anchor ---------------------------------------------------
#
# Locates a person's mouth from YOLOv8-pose keypoints alone (no face detector),
# returning (x, y, face_width) in full-frame
# coordinates, or None when it can't be told. "Face width" is a PROXY built
# from whichever of ear-to-ear / eye-to-eye / shoulder-to-shoulder keypoints are
# confident, scaled so it matches the width the rules were tuned in
# (MOUTH_PROXIMITY / drinking_mouth_proximity are in face-widths). The scale
# factors come from detection_sandbox/mouth_calibration.py run on the test clips
# (1,547 person boxes, 5 clips, fitted against insightface's face width, since
# removed): medians of that width / pose width, and of (mouth - nose) in
# face-widths.
POSE_HALF = _gpu_available()  # FP16 inference on CUDA (ultralytics rejects half on CPU)
MOUTH_MISS_CACHE_SECONDS = 0.5   # after "no mouth anchor", don't re-run pose for this long (per track)
POSE_CROP_PAD = 0.10          # extra margin around the person box before pose
POSE_CROP_IMGSZ = 640         # pose inference size on that crop
POSE_EAR_TO_FACE = 0.92       # face width = ear-to-ear distance x this
POSE_EYE_TO_FACE = 2.51       # ... or eye-to-eye distance x this
POSE_SHOULDER_TO_FACE = 0.55  # ... or shoulder-to-shoulder distance x this
POSE_MOUTH_DY = 0.29          # mouth sits this many face-widths below the nose

KP_LEYE, KP_REYE, KP_LEAR, KP_REAR = 1, 2, 3, 4


def pose_on_box(frame, box):
    """Runs YOLOv8-pose on ONE person's padded box and returns the (17, 3)
    keypoints, in FULL-FRAME coordinates, of the pose that belongs to that
    person -- or None. Only the box is processed, so it costs one small pose
    pass per queried person, not a full-frame one."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box[:4])
    bw, bh = max(x2 - x1, 1), max(y2 - y1, 1)
    cx1 = max(int(x1 - bw * POSE_CROP_PAD), 0)
    cy1 = max(int(y1 - bh * POSE_CROP_PAD), 0)
    cx2 = min(int(x2 + bw * POSE_CROP_PAD), w)
    cy2 = min(int(y2 + bh * POSE_CROP_PAD), h)
    crop = frame[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return None
    res = load_pose()(crop, verbose=False, imgsz=POSE_CROP_IMGSZ, half=POSE_HALF)[0]
    _log_pose_device_once()
    if res.keypoints is None or res.boxes is None or len(res.boxes) == 0:
        return None
    kpts = res.keypoints.data.cpu().numpy()
    best, best_score = None, 0.0
    for i in range(len(kpts)):
        bx1, by1, bx2, by2 = (float(v) for v in res.boxes.xyxy[i].tolist())
        mx, my = (bx1 + bx2) / 2 + cx1, (by1 + by2) / 2 + cy1
        if not (x1 <= mx <= x2 and y1 <= my <= y2):
            continue                      # a different person caught in the margin
        score = float(res.boxes.conf[i]) * (bx2 - bx1) * (by2 - by1)
        if score > best_score:
            best, best_score = i, score
    if best is None:
        return None
    out = kpts[best].copy()
    out[:, 0] += cx1
    out[:, 1] += cy1
    return out


def _kp_dist(kpts, a, b):
    """Pixel distance between two keypoints, or None unless both are confident."""
    if kpts[a][2] < KP_MIN_CONF or kpts[b][2] < KP_MIN_CONF:
        return None
    return float(((kpts[a][0] - kpts[b][0]) ** 2 + (kpts[a][1] - kpts[b][1]) ** 2) ** 0.5)


def pose_face_metrics(kpts):
    """Raw widths (pixels) the face-width proxy is built from: ear-to-ear,
    eye-to-eye, shoulder-to-shoulder. Each is None when its keypoints aren't
    confident. Exposed so the calibration script can fit the scale factors."""
    return {
        "ear": _kp_dist(kpts, KP_LEAR, KP_REAR),
        "eye": _kp_dist(kpts, KP_LEYE, KP_REYE),
        "shoulder": _kp_dist(kpts, KP_LSHOULDER, KP_RSHOULDER),
    }


def pose_face_width(kpts):
    """(face_width_proxy, source) using the first confident of ear -> eye ->
    shoulder, or (None, None)."""
    m = pose_face_metrics(kpts)
    for src, factor in (("ear", POSE_EAR_TO_FACE), ("eye", POSE_EYE_TO_FACE),
                        ("shoulder", POSE_SHOULDER_TO_FACE)):
        if m[src]:
            return m[src] * factor, src
    return None, None


def find_mouth_pose(frame, box, with_source=False):
    """Locates the mouth from pose keypoints: the mouth is the nose plus a small
    downward offset, and the face width is the proxy from pose_face_width().

    Returns (x, y, face_width) in FULL-FRAME coordinates, or None when the nose
    or every width keypoint is unconfident. None means UNKNOWN, not "no face" --
    callers must keep the detection rather than reject it: a person facing away,
    or far down the street, simply has no confident nose at CCTV distance.
    With with_source=True returns (x, y, face_width, source) instead.
    """
    kpts = pose_on_box(frame, box)
    if kpts is None or kpts[KP_NOSE][2] < KP_MIN_CONF:
        return None
    face_w, source = pose_face_width(kpts)
    if face_w is None:
        return None
    mx = float(kpts[KP_NOSE][0])
    my = float(kpts[KP_NOSE][1]) + POSE_MOUTH_DY * face_w
    return (mx, my, face_w, source) if with_source else (mx, my, face_w)


def log_mouth(**fields):
    """Appends one JSON line to $LOOKOUT_MOUTH_LOG when that variable is set;
    a no-op otherwise. Used to compare mouth distances before/after the swap."""
    path = os.environ.get("LOOKOUT_MOUTH_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(fields, default=float) + chr(10))


def detect_persons_tracked(frame, conf=0.5, tracker="bytetrack.yaml", imgsz=None):
    """detect_persons, but with ultralytics' built-in multi-object tracker.

    Returns (boxes, ids) where ids[i] is a stable integer identity for boxes[i]
    (or None if the tracker won't commit to one yet). ultralytics ships
    ByteTrack and BoT-SORT, which add a Kalman motion model and proper
    occlusion handling that the greedy matcher in tracking.py cannot do —
    notably keeping two people apart when their paths cross, where greedy
    IoU/proximity swaps their identities (and so swaps their dwell timers).

    Caveats, which is why the watchers default to the greedy matcher: this
    keeps state between calls (`persist=True`), so it assumes it is being fed
    consecutive frames of ONE stream, and its motion model assumes roughly
    regular intervals — at far mode's ~1 FPS the predictions get poor. Benchmark
    on your own footage before switching a camera over to it.
    """
    model = load_yolo()
    # persist=True (ByteTrack state) is unaffected by imgsz — it only changes the
    # detection resolution, not the tracker association (Phase 0.3 verified).
    results = model.track(frame, verbose=False, persist=True, tracker=tracker,
                          imgsz=imgsz or NEAR_IMGSZ)[0]
    boxes, ids = [], []
    for box in results.boxes:
        if int(box.cls[0]) != PERSON_CLASS_ID:
            continue
        if float(box.conf[0]) < conf:
            continue
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        boxes.append((x1, y1, x2, y2, float(box.conf[0])))
        ids.append(int(box.id[0]) if box.id is not None else None)
    return boxes, ids


def detect_persons_and_vessels(frame, conf=0.5, vessel_conf=0.35):
    """One YOLO pass returning (person_boxes, vessel_boxes).

    The person pass runs every frame anyway as the tracking anchor, so pulling
    the COCO vessel classes out of the SAME result costs no extra inference —
    it is a different class filter on work already paid for.

    Vessels come back as (x1,y1,x2,y2,conf,label) like the custom detectors, so
    they can be merged into the same downstream pipeline. `vessel_conf` is
    separate because a vessel is only ever a weak, contents-unknown signal.
    """
    model = load_yolo()
    results = model(frame, verbose=False)[0]
    persons, vessels = [], []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        score = float(box.conf[0])
        if cls_id == PERSON_CLASS_ID:
            if score < conf:
                continue
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
            persons.append((x1, y1, x2, y2, score))
        elif cls_id in VESSEL_CLASS_IDS:
            if score < vessel_conf:
                continue
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
            vessels.append((x1, y1, x2, y2, score, VESSEL_CLASS_IDS[cls_id]))
    return persons, vessels


def _scene_from_result(results, conf, obj_conf):
    """Splits one YOLO result into (persons, ids, carriables, vehicles).

    Layer E needs people, the bags they carry and the vehicles they park, all in
    the same coordinate frame and all on the same frame. Pulling three class
    filters out of ONE result is the difference between one inference per frame
    and three — on CPU that is the difference between the module being usable
    and not.
    """
    persons, ids, carriables, vehicles = [], [], [], []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        score = float(box.conf[0])
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        if cls_id == PERSON_CLASS_ID:
            if score < conf:
                continue
            persons.append((x1, y1, x2, y2, score))
            # box.id exists only on model.track() results, not model() ones.
            tid = getattr(box, "id", None)
            ids.append(int(tid[0]) if tid is not None else None)
        elif cls_id in CARRIABLE_CLASS_IDS:
            if score < obj_conf:
                continue
            carriables.append((x1, y1, x2, y2, score, CARRIABLE_CLASS_IDS[cls_id]))
        elif cls_id in VEHICLE_CLASS_IDS or cls_id == BICYCLE_CLASS_ID:
            if score < obj_conf:
                continue
            label = "bicycle" if cls_id == BICYCLE_CLASS_ID else VEHICLE_CLASS_IDS[cls_id]
            vehicles.append((x1, y1, x2, y2, score, label))
    return persons, ids, carriables, vehicles


def detect_scene(frame, conf=0.5, obj_conf=0.35, imgsz=None):
    """One YOLO pass returning (persons, ids, carriables, vehicles) for Layer E.

    `ids` is a list of None here — the plain detector has no identities. Use
    detect_scene_tracked when the caller wants ByteTrack/BoT-SORT ids, which E26
    (identity-switch guard) is written against.
    """
    model = load_yolo()
    results = model(frame, verbose=False, imgsz=imgsz or NEAR_IMGSZ)[0]
    return _scene_from_result(results, conf, obj_conf)


def detect_scene_tracked(frame, conf=0.5, obj_conf=0.35,
                         tracker="bytetrack.yaml", imgsz=None):
    """detect_scene, but with ultralytics' multi-object tracker supplying ids.

    Same caveats as detect_persons_tracked: it keeps state between calls, so it
    assumes consecutive frames of one stream and roughly regular intervals.
    """
    model = load_yolo()
    results = model.track(frame, verbose=False, persist=True, tracker=tracker,
                          imgsz=imgsz or NEAR_IMGSZ)[0]
    return _scene_from_result(results, conf, obj_conf)


def _vehicle_boxes_from_result(results, conf, offset=(0, 0)):
    """Pulls (x1,y1,x2,y2,conf,label) vehicle tuples out of a YOLO result.

    Only the COCO vehicle classes (car/motorcycle/bus/truck) are kept; `offset`
    shifts every box by (ox, oy) so detections found inside a tile come back in
    full-frame coordinates.
    """
    ox, oy = offset
    boxes = []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        if cls_id not in VEHICLE_CLASS_IDS:
            continue
        if float(box.conf[0]) < conf:
            continue
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        boxes.append((x1 + ox, y1 + oy, x2 + ox, y2 + oy,
                      float(box.conf[0]), VEHICLE_CLASS_IDS[cls_id]))
    return boxes


# YOLOv8's default imgsz is 640; a parking/street frame is high-resolution
# (e.g. 4080x3072) and a parked car down the block shrinks to a handful of
# pixels once the whole frame is squished to 640 — so the default silently
# misses exactly the far vehicles this detector exists to catch. 1280 keeps
# them big enough to detect while still being one fast pass.
VEHICLE_IMGSZ = 1280


def detect_vehicles(frame, conf=0.4, imgsz=VEHICLE_IMGSZ):
    """Returns a list of (x1, y1, x2, y2, conf, label) boxes for detected vehicles.

    `label` is the COCO vehicle class name (car/motorcycle/bus/truck). Reuses
    the same YOLOv8 model as detect_persons — no extra weights to download.
    Runs at `imgsz` (default 1280, not YOLO's 640) so distant vehicles survive
    the downscale; at true CCTV range use detect_vehicles_far.
    """
    model = load_yolo()
    results = model(frame, verbose=False, imgsz=imgsz)[0]
    return _vehicle_boxes_from_result(results, conf)


def detect_vehicles_far(frame, conf=0.4, tiles=(2, 2), overlap=0.2,
                        imgsz=VEHICLE_IMGSZ):
    """Long-range vehicle detection for CCTV footage — SAHI-style tiling.

    The frame is split into `tiles` (rows, cols) with `overlap`, and the YOLO
    detector runs on each tile at native resolution, so a distant vehicle that
    would vanish in a single downscaled pass still lands on enough pixels.
    Returns (x1,y1,x2,y2,conf,label) tuples in full-frame coordinates,
    de-duplicated with NMS. Slower than detect_vehicles (many passes per frame),
    which is fine for a stationary parking camera where high FPS isn't needed.
    """
    model = load_yolo()
    boxes = []

    # Whole-frame pass always runs, so far mode is a strict superset of
    # detect_vehicles — see _detect_far for why tiles alone can lose objects.
    results = model(frame, verbose=False, imgsz=imgsz)[0]
    boxes.extend(_vehicle_boxes_from_result(results, conf))

    rows, cols = tiles
    if rows > 1 or cols > 1:
        for tile, (ox, oy) in _iter_tiles(frame, rows, cols, overlap):
            if tile.size == 0:
                continue
            results = model(tile, verbose=False, imgsz=imgsz)[0]
            boxes.extend(_vehicle_boxes_from_result(results, conf, offset=(ox, oy)))
    return _nms(boxes)


def _smoking_boxes_from_result(results, conf, offset=(0, 0)):
    """Pulls (x1,y1,x2,y2,conf,label) tuples out of a YOLO result.

    `offset` shifts every box by (ox, oy) so detections found inside a tile or a
    crop come back in full-frame coordinates. `label` is the model's own class
    name (cigarette/smoke/vape/smoking), so this adapts to any smoking dataset.
    """
    ox, oy = offset
    names = results.names  # id -> class name, from the trained model

    # Oriented-bounding-box models (task="obb", e.g. a yolov8s-obb fine-tune)
    # leave `.boxes` as None and put rotated boxes under `.obb`. Their `.xyxy` is
    # the axis-aligned box enclosing the rotated one — exactly what the rest of
    # the pipeline expects — and each item exposes the same .xyxy/.conf/.cls
    # interface, so both model types flow through unchanged from here.
    source = results.boxes if results.boxes is not None else getattr(results, "obb", None)
    if source is None:
        return []

    boxes = []
    for box in source:
        score = float(box.conf[0])
        if score < conf:
            continue
        cls_id = int(box.cls[0])
        label = names[cls_id] if cls_id in names else "smoking"
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        boxes.append((x1 + ox, y1 + oy, x2 + ox, y2 + oy, score, label))
    return boxes


def detect_smoking(frame, conf=0.3, imgsz=None):
    """Single-pass smoking detection over the whole frame (the fast path).

    Requires SMOKING_MODEL_PATH weights. Good for close cameras; at CCTV
    distance a cigarette is only a few pixels once the frame is downscaled to
    the model's imgsz, so use detect_smoking_far there instead. `imgsz` raises
    the inference resolution (default NEAR_IMGSZ).
    """
    model = load_smoking_model()
    results = model(frame, verbose=False, imgsz=imgsz or NEAR_IMGSZ, conf=conf)[0]
    return _only(_smoking_boxes_from_result(results, conf), SMOKING_CLASSES)


def _iter_tiles(frame, rows, cols, overlap):
    """Yields (tile, (x_offset, y_offset)) sub-images covering the frame.

    Tiles overlap by `overlap` (fraction of tile size) so a cigarette straddling
    a seam isn't cut in half. Running the detector on each tile at native
    resolution — SAHI-style — keeps small far-away objects big enough to detect,
    instead of losing them when the whole frame is shrunk to the model's imgsz.
    """
    h, w = frame.shape[:2]
    tile_h, tile_w = h // rows, w // cols
    pad_y, pad_x = int(tile_h * overlap), int(tile_w * overlap)
    for r in range(rows):
        for c in range(cols):
            y0 = max(r * tile_h - pad_y, 0)
            x0 = max(c * tile_w - pad_x, 0)
            y1 = min((r + 1) * tile_h + pad_y, h)
            x1 = min((c + 1) * tile_w + pad_x, w)
            yield frame[y0:y1, x0:x1], (x0, y0)


def _iou(a, b):
    """Intersection-over-union of two (x1,y1,x2,y2,...) boxes."""
    ax1, ay1, ax2, ay2 = a[:4]
    bx1, by1, bx2, by2 = b[:4]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(ix2 - ix1, 0), max(iy2 - iy1, 0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / float(area_a + area_b - inter)


def _nms(boxes, iou_thresh=0.5):
    """Greedy non-max suppression over (x1,y1,x2,y2,conf,label) boxes.

    The tiling and person-crop passes overlap, so the same cigarette can be
    found more than once; this collapses the duplicates, keeping the highest
    confidence for each real object.
    """
    kept = []
    for b in sorted(boxes, key=lambda x: x[4], reverse=True):
        if all(_iou(b, k) < iou_thresh for k in kept):
            kept.append(b)
    return kept


def _detect_far(model, frame, conf, tiles, overlap, person_boxes, upscale, imgsz=None):
    """Long-range detection cascade for CCTV footage, merged from two
    resolution-preserving passes so a distant small object (a few pixels on the
    full frame) still lands on enough pixels to detect:

      1. Tiling (SAHI-style): the frame is split into `tiles` (rows, cols) with
         `overlap`, and the detector runs on each tile at native resolution.
      2. Person-crop upscale: each person box (pass the output of detect_persons
         as `person_boxes` to avoid re-running YOLO here) is cropped, enlarged
         `upscale`x, and run through the detector — spending resolution only
         where a person actually is.

    Model-agnostic: works for any custom detector (smoking, thief, ...) since
    labels come from the model's own class names. Returns (x1,y1,x2,y2,conf,
    label) tuples in full-frame coordinates, de-duplicated with NMS. Slower
    than a single pass (many inference passes per frame), which is fine for
    CCTV where high FPS isn't needed.
    """
    boxes = []
    imgsz = imgsz or FAR_IMGSZ

    # The whole-frame pass ALWAYS runs, tiles or not. A tile is a zoomed-in
    # fragment with the surrounding context cropped away, and the model was
    # trained on whole scenes — so tiling alone can score *worse* than the plain
    # pass on a modest-resolution frame (a 640x640 test frame scores gun 0.77
    # whole-frame and nothing at all once cut into 320x320 quarters). Running
    # both and merging keeps far mode a strict superset of near mode: it can
    # only ever add recall, never trade it away.
    results = model(frame, verbose=False, imgsz=imgsz, conf=conf)[0]
    boxes.extend(_smoking_boxes_from_result(results, conf))

    rows, cols = tiles
    if rows > 1 or cols > 1:
        for tile, (ox, oy) in _iter_tiles(frame, rows, cols, overlap):
            if tile.size == 0:
                continue
            results = model(tile, verbose=False, imgsz=imgsz, conf=conf)[0]
            boxes.extend(_smoking_boxes_from_result(results, conf, offset=(ox, oy)))

    scale = upscale or 1.0
    for pb in (person_boxes or []):
        px1, py1, px2, py2 = (int(v) for v in pb[:4])
        px1, py1 = max(px1, 0), max(py1, 0)
        crop = frame[py1:py2, px1:px2]
        if crop.size == 0:
            continue
        if scale != 1.0:
            crop = cv2.resize(crop, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_CUBIC)
        results = model(crop, verbose=False, imgsz=imgsz, conf=conf)[0]
        for (x1, y1, x2, y2, score, label) in _smoking_boxes_from_result(results, conf):
            # map the (possibly upscaled) crop-space box back to full-frame coords
            boxes.append((
                int(x1 / scale) + px1, int(y1 / scale) + py1,
                int(x2 / scale) + px1, int(y2 / scale) + py1,
                score, label,
            ))

    return _nms(boxes)


def detect_smoking_far(frame, conf=0.3, tiles=(2, 2), overlap=0.2,
                       person_boxes=None, upscale=2.0):
    """Long-range smoking detection — see _detect_far for how the cascade works."""
    return _only(_detect_far(load_smoking_model(), frame, conf, tiles, overlap,
                             person_boxes, upscale), SMOKING_CLASSES)


def detect_on_person_crops(model, frame, person_boxes, conf, pad=0.35,
                           crop_imgsz=640, min_person_h=0):
    """Cascade crop at native resolution: run a small-object model on each
    person's crop taken from the FULL-RES frame, not the downscaled one.

    The reasoning (pixels-on-target): YOLO squashes a 2688-wide frame to imgsz
    640, so an 18px cigarette becomes ~4px and vanishes. But a person at 10m is
    still ~350px tall. Crop that person from the NATIVE frame and feed the crop
    (which now fills the model's 640 input) and the same cigarette is 30-50px —
    free super-resolution, exactly where the object cue lives. No tiling, so it
    is much cheaper than the full far cascade: one inference per person.

    `frame` MUST be the native-resolution frame (use the main stream). `pad`
    widens each person box (a raised hand/cigarette sticks outside the body box).
    `min_person_h` skips tiny far persons whose crop still wouldn't help. Returns
    (x1,y1,x2,y2,conf,label) in full-frame coords, de-duplicated with NMS.
    """
    h, w = frame.shape[:2]
    boxes = []
    for pb in (person_boxes or []):
        px1, py1, px2, py2 = (int(v) for v in pb[:4])
        if (py2 - py1) < min_person_h:
            continue
        pw, ph = px2 - px1, py2 - py1
        x1 = max(px1 - int(pw * pad), 0)
        y1 = max(py1 - int(ph * pad), 0)
        x2 = min(px2 + int(pw * pad), w)
        y2 = min(py2 + int(ph * pad), h)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        results = model(crop, verbose=False, imgsz=crop_imgsz, conf=conf)[0]
        for (bx1, by1, bx2, by2, score, label) in _smoking_boxes_from_result(results, conf):
            boxes.append((bx1 + x1, by1 + y1, bx2 + x1, by2 + y1, score, label))
    return _nms(boxes)


def detect_smoking_cascade(frame, person_boxes, conf=0.3, pad=0.35, crop_imgsz=640):
    """Smoking detection by native-res person crops — see detect_on_person_crops.
    Frame must be native resolution (main stream) for the pixels to be there."""
    return _only(detect_on_person_crops(load_smoking_model(), frame, person_boxes,
                                        conf, pad, crop_imgsz), SMOKING_CLASSES)


def thief_model_available():
    """True if the custom thief weights are present (callers skip cleanly if not)."""
    return THIEF_MODEL_PATH.exists()


def load_thief_model():
    """Holdup uses the shared merged model; see MODEL_PATH."""
    return load_merged_model()


def detect_thief(frame, conf=0.3, imgsz=None):
    """Single-pass thief/robbery detection over the whole frame (the fast path).

    Returns the same (x1,y1,x2,y2,conf,label) tuples as detect_smoking; labels
    come from the shared model, filtered to knife. A knife is larger than a
    cigarette, so this covers more range than detect_smoking does — but at
    real CCTV distance it still shrinks to a few pixels; use detect_thief_far there. `imgsz`
    raises the inference resolution (default NEAR_IMGSZ).
    """
    model = load_thief_model()
    results = model(frame, verbose=False, imgsz=imgsz or NEAR_IMGSZ, conf=conf)[0]
    return _only(_smoking_boxes_from_result(results, conf), THIEF_CLASSES)


def detect_thief_far(frame, conf=0.3, tiles=(2, 2), overlap=0.2,
                     person_boxes=None, upscale=2.0):
    """Long-range thief/robbery detection — see _detect_far for the cascade.
    Helps the small handheld knife class at CCTV range."""
    return _only(_detect_far(load_thief_model(), frame, conf, tiles, overlap,
                             person_boxes, upscale), THIEF_CLASSES)


def drinking_model_available():
    """True if the custom drinking weights are present (callers skip cleanly if not)."""
    return DRINKING_MODEL_PATH.exists()


def load_drinking_model():
    """Drinking uses the shared merged model; see MODEL_PATH."""
    return load_merged_model()


def detect_drinking(frame, conf=0.35):
    """Single-pass public-drinking detection over the whole frame (the fast path).

    Returns the same (x1,y1,x2,y2,conf,label) tuples as detect_smoking. A bottle
    is a larger, higher-contrast object than a cigarette, so this covers more
    range — but at true CCTV distance use detect_drinking_far.
    """
    model = load_drinking_model()
    results = model(frame, verbose=False, imgsz=NEAR_IMGSZ, conf=conf)[0]
    return _only(_smoking_boxes_from_result(results, conf), DRINKING_CLASSES)


def detect_drinking_far(frame, conf=0.35, tiles=(2, 2), overlap=0.2,
                        person_boxes=None, upscale=2.0):
    """Long-range public-drinking detection — see _detect_far for the cascade."""
    return _only(_detect_far(load_drinking_model(), frame, conf, tiles, overlap,
                             person_boxes, upscale), DRINKING_CLASSES)


def merged_model_available():
    """True if the merged (Bottle/Cigarette/knife) weights are present."""
    return MERGED_MODEL_PATH.exists()


def load_merged_model():
    """Lazy-loads the merged multi-class detector. Raises if the weights are
    missing. See MERGED_MODEL_PATH for how classes route to rule engines."""
    global _merged_model
    if _merged_model is None:
        if not MERGED_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Merged model not found at {MERGED_MODEL_PATH}. Copy the "
                "trained best.pt there, or set the MERGED_MODEL env var."
            )
        from ultralytics import YOLO

        _merged_model = YOLO(str(MERGED_MODEL_PATH))
    return _merged_model


def detect_merged(frame, conf=0.15, imgsz=None):
    """Single-pass merged detection over the whole frame (the fast path).

    `conf` should be the LOWEST of the three engines' configured confidences
    (watch_merged does this) — each engine's own class floor is applied
    downstream, after routing by label, so this pass must not pre-filter a
    detection away before the engine that actually owns its class gets a look.
    Returns the same (x1,y1,x2,y2,conf,label) tuples as detect_smoking/
    detect_thief/detect_drinking; labels come from the merged model's own
    class names.
    """
    model = load_merged_model()
    results = model(frame, verbose=False, imgsz=imgsz or NEAR_IMGSZ, conf=conf)[0]
    return _smoking_boxes_from_result(results, conf)


def detect_merged_far(frame, conf=0.15, tiles=(2, 2), overlap=0.2,
                      person_boxes=None, upscale=2.0):
    """Long-range merged detection — see _detect_far for how the cascade works."""
    return _detect_far(load_merged_model(), frame, conf, tiles, overlap,
                       person_boxes, upscale)


def load_image(path):
    """Decodes an image file from disk into an OpenCV BGR array, or None on failure."""
    return cv2.imread(str(path))
