"""Shared CV plumbing for curfew face detection/recognition.

Pipeline: YOLOv8 (ultralytics) locates people in a frame, then insightface's
FaceAnalysis (ArcFace/buffalo_l) locates and recognizes a face within each
person crop, producing a 512-d embedding matched against `face_db.json`
(built by the `enroll_faces` management command).

No Django model access happens here — this module is pure CV plumbing so it
stays importable/testable independent of the management commands that use it.
"""

import json
import os
import datetime
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

import cv2
import numpy as np


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

    def __init__(self, seconds=10, playback_fps=12, label=""):
        self.seconds = seconds
        self.playback_fps = playback_fps
        self.label = label            # e.g. camera code, drawn next to the time
        self._buf = deque()  # (timestamp, annotated_frame)

    def add(self, frame, now):
        self._buf.append((now, frame.copy()))
        cutoff = now - self.seconds
        while self._buf and self._buf[0][0] < cutoff:
            self._buf.popleft()

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
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"),
            self.playback_fps, (w, h),
        )
        if not writer.isOpened():
            return False
        for i, (ts, frame) in enumerate(frames):
            self._stamp(frame, ts)   # real date/time of THIS frame
            # Hold each frame for its real duration so the clip runs ~real-time.
            nxt = frames[i + 1][0] if i + 1 < len(frames) else ts + 1.0 / self.playback_fps
            reps = max(1, round((nxt - ts) * self.playback_fps))
            for _ in range(reps):
                writer.write(frame)
        writer.release()
        return True


class LatestFrameReader:
    """Background thread that keeps only the newest frame from a live capture.

    The detectors run far slower than a camera delivers (often <1 FPS in far
    mode vs 15-25 FPS from the stream), so OpenCV's RTSP buffer fills with a
    backlog and cap.read() returns frames that are seconds old — the visible
    "delay". This drains the stream as fast as it arrives and overwrites, so the
    processing loop always gets a near-live frame; latency stays at ~one frame
    plus one inference instead of a growing backlog.

    Use for live sources only (RTSP / webcam). A video file should be read
    sequentially so no frames are skipped.
    """

    def __init__(self, cap):
        self.cap = cap
        self._lock = threading.Lock()
        self._frame = None
        self._stopped = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while not self._stopped:
            ok, f = self.cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self._lock:
                self._frame = f

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
FACE_DB_PATH = VISION_DIR / "face_db.json"

# Custom-trained smoking detector (cigarette/smoke/vape/smoking). Unlike the
# COCO yolov8n used for persons/vehicles, this is a separate fine-tuned model,
# so it loads its own weights. Override with the SMOKING_MODEL env var.
SMOKING_MODEL_PATH = Path(os.environ.get("SMOKING_MODEL", str(VISION_DIR / "smoking.pt")))

# Custom-trained thief/robbery detector (gun/knife/robbery activity/stealing).
# Same deal as the smoking model: separate fine-tuned weights, trained with
# detection_sandbox/train_thief.py. Override with the THIEF_MODEL env var.
THIEF_MODEL_PATH = Path(os.environ.get("THIEF_MODEL", str(VISION_DIR / "thief.pt")))

# Custom-trained public-drinking detector. NOTE: this model has a single class,
# "Red Horse" — it detects one beer BRAND, i.e. a product, not the act of
# drinking. The watcher's heuristics carry the gap between "a bottle is present"
# and "someone is drinking in public"; see watch_drinking.py. Override with the
# DRINKING_MODEL env var.
DRINKING_MODEL_PATH = Path(os.environ.get("DRINKING_MODEL", str(VISION_DIR / "drinking.pt")))

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

# insightface (ArcFace) cosine similarity for a genuine same-person match
# typically falls in ~0.35-0.70 (35-70 once scaled to a percent), unlike a
# percentage-intuition 0-100 scale. SystemSettings.curfew_confidence defaults
# to 75, which is stricter than that normal genuine-match range. Lower it
# (e.g. to ~40) in Django admin when testing, instead of treating 75 as a
# "75% sure" bar.

_yolo_model = None
_face_app = None
_smoking_model = None
_thief_model = None
_drinking_model = None
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
    on first use, like the plain detector."""
    global _pose_model
    if _pose_model is None:
        from ultralytics import YOLO

        _pose_model = YOLO("yolov8n-pose.pt")
    return _pose_model


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
    """Lazy-loads the custom smoking detector. Raises if the weights are missing —
    stock YOLOv8 (COCO) has no cigarette/smoking class, so this model is required."""
    global _smoking_model
    if _smoking_model is None:
        if not SMOKING_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Smoking model not found at {SMOKING_MODEL_PATH}. Train one with "
                "detection_sandbox/train_smoking.py and copy best.pt here, or set "
                "the SMOKING_MODEL env var."
            )
        from ultralytics import YOLO

        _smoking_model = YOLO(str(SMOKING_MODEL_PATH))
    return _smoking_model


def load_face_app():
    """Lazy-loads insightface's FaceAnalysis (buffalo_l pack, CPU).

    Model weights (~280MB) auto-download on first use to
    ~/.insightface/models/buffalo_l — no manual download step needed, but
    the first run will be slow while that completes.
    """
    global _face_app
    if _face_app is None:
        from insightface.app import FaceAnalysis

        _face_app = FaceAnalysis(name="buffalo_l")
        _face_app.prepare(ctx_id=-1, det_size=(320, 320))  # ctx_id=-1 -> CPU
    return _face_app


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


def find_mouth(frame, box):
    """Locates the mouth of the most prominent face inside a person box.

    Returns (x, y, face_width) in FULL-FRAME coordinates, or None when no face
    could be found. Reuses the same insightface FaceAnalysis the curfew pipeline
    already loads, so this adds no new model or download.

    None is common and expected — a person facing away, or standing far enough
    down the street that their face is a handful of pixels, yields no detection.
    Callers must read None as "unknown", never as "no face is present", or a
    mouth-proximity rule built on this would silently disable itself at exactly
    the CCTV distances the far-mode cascade exists to cover.
    """
    app = load_face_app()
    x1, y1, x2, y2 = (int(v) for v in box[:4])
    x1, y1 = max(x1, 0), max(y1, 0)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    faces = app.get(crop)
    if not faces:
        return None

    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    fx1, fy1, fx2, fy2 = (float(v) for v in face.bbox)
    kps = getattr(face, "kps", None)
    if kps is not None and len(kps) >= 5:
        # insightface's 5-point landmarks: 0/1 eyes, 2 nose, 3/4 mouth corners.
        mx = (float(kps[3][0]) + float(kps[4][0])) / 2
        my = (float(kps[3][1]) + float(kps[4][1])) / 2
    else:
        # No landmarks: approximate the mouth at three-quarters down the face box.
        mx, my = (fx1 + fx2) / 2, fy1 + (fy2 - fy1) * 0.75
    return (mx + x1, my + y1, fx2 - fx1)


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
    results = model(frame, verbose=False, imgsz=imgsz or NEAR_IMGSZ)[0]
    return _smoking_boxes_from_result(results, conf)


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


def _detect_far(model, frame, conf, tiles, overlap, person_boxes, upscale):
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

    # The whole-frame pass ALWAYS runs, tiles or not. A tile is a zoomed-in
    # fragment with the surrounding context cropped away, and the model was
    # trained on whole scenes — so tiling alone can score *worse* than the plain
    # pass on a modest-resolution frame (a 640x640 test frame scores gun 0.77
    # whole-frame and nothing at all once cut into 320x320 quarters). Running
    # both and merging keeps far mode a strict superset of near mode: it can
    # only ever add recall, never trade it away.
    results = model(frame, verbose=False)[0]
    boxes.extend(_smoking_boxes_from_result(results, conf))

    rows, cols = tiles
    if rows > 1 or cols > 1:
        for tile, (ox, oy) in _iter_tiles(frame, rows, cols, overlap):
            if tile.size == 0:
                continue
            results = model(tile, verbose=False)[0]
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
        results = model(crop, verbose=False)[0]
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
    return _detect_far(load_smoking_model(), frame, conf, tiles, overlap,
                       person_boxes, upscale)


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
        results = model(crop, verbose=False, imgsz=crop_imgsz)[0]
        for (bx1, by1, bx2, by2, score, label) in _smoking_boxes_from_result(results, conf):
            boxes.append((bx1 + x1, by1 + y1, bx2 + x1, by2 + y1, score, label))
    return _nms(boxes)


def detect_smoking_cascade(frame, person_boxes, conf=0.3, pad=0.35, crop_imgsz=640):
    """Smoking detection by native-res person crops — see detect_on_person_crops.
    Frame must be native resolution (main stream) for the pixels to be there."""
    return detect_on_person_crops(load_smoking_model(), frame, person_boxes,
                                  conf, pad, crop_imgsz)


def thief_model_available():
    """True if the custom thief weights are present (callers skip cleanly if not)."""
    return THIEF_MODEL_PATH.exists()


def load_thief_model():
    """Lazy-loads the custom thief/robbery detector. Raises if the weights are
    missing — stock YOLOv8 (COCO) has no gun/knife/robbery class, so this model
    is required."""
    global _thief_model
    if _thief_model is None:
        if not THIEF_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Thief model not found at {THIEF_MODEL_PATH}. Train one with "
                "detection_sandbox/train_thief.py and copy best.pt here, or set "
                "the THIEF_MODEL env var."
            )
        from ultralytics import YOLO

        _thief_model = YOLO(str(THIEF_MODEL_PATH))
    return _thief_model


def detect_thief(frame, conf=0.3, imgsz=None):
    """Single-pass thief/robbery detection over the whole frame (the fast path).

    Returns the same (x1,y1,x2,y2,conf,label) tuples as detect_smoking; labels
    come from the trained model (gun/knife/robbery activity/stealing). Guns,
    knives and whole-body actions are far larger than a cigarette, so this
    covers more range than detect_smoking does — but at real CCTV distance a
    handgun still shrinks to a few pixels; use detect_thief_far there. `imgsz`
    raises the inference resolution (default NEAR_IMGSZ).
    """
    model = load_thief_model()
    results = model(frame, verbose=False, imgsz=imgsz or NEAR_IMGSZ)[0]
    return _smoking_boxes_from_result(results, conf)


def detect_thief_far(frame, conf=0.3, tiles=(2, 2), overlap=0.2,
                     person_boxes=None, upscale=2.0):
    """Long-range thief/robbery detection — see _detect_far for the cascade.
    Mainly helps the small handheld classes (gun/knife); the whole-body classes
    (robbery activity/stealing) usually don't need it."""
    return _detect_far(load_thief_model(), frame, conf, tiles, overlap,
                       person_boxes, upscale)


def drinking_model_available():
    """True if the custom drinking weights are present (callers skip cleanly if not)."""
    return DRINKING_MODEL_PATH.exists()


def load_drinking_model():
    """Lazy-loads the custom public-drinking detector. Raises if the weights are
    missing — stock YOLOv8 (COCO) has a `bottle` class but not a brand/alcohol
    class, so this model is required."""
    global _drinking_model
    if _drinking_model is None:
        if not DRINKING_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Drinking model not found at {DRINKING_MODEL_PATH}. Train one "
                "and copy best.pt here, or set the DRINKING_MODEL env var."
            )
        from ultralytics import YOLO

        _drinking_model = YOLO(str(DRINKING_MODEL_PATH))
    return _drinking_model


def detect_drinking(frame, conf=0.35):
    """Single-pass public-drinking detection over the whole frame (the fast path).

    Returns the same (x1,y1,x2,y2,conf,label) tuples as detect_smoking. A bottle
    is a larger, higher-contrast object than a cigarette, so this covers more
    range — but at true CCTV distance use detect_drinking_far.
    """
    model = load_drinking_model()
    results = model(frame, verbose=False)[0]
    return _smoking_boxes_from_result(results, conf)


def detect_drinking_far(frame, conf=0.35, tiles=(2, 2), overlap=0.2,
                        person_boxes=None, upscale=2.0):
    """Long-range public-drinking detection — see _detect_far for the cascade."""
    return _detect_far(load_drinking_model(), frame, conf, tiles, overlap,
                       person_boxes, upscale)


def load_image(path):
    """Decodes an image file from disk into an OpenCV BGR array, or None on failure."""
    return cv2.imread(str(path))


def compute_face_embedding(crop):
    """Detects the best face in `crop` and returns its 512-d embedding, or None."""
    if crop is None or crop.size == 0:
        return None

    app = load_face_app()
    faces = app.get(crop)
    if not faces:
        return None

    best_face = max(faces, key=lambda f: f.det_score)
    return best_face.embedding


def precompute_face_db(face_db):
    """Adds a cached, L2-normalized embedding to each entry for fast repeated matching.

    Without this, match_embedding() would recompute np.linalg.norm for every
    enrolled face on every single call (every detected person, every frame) —
    wasted work, since the enrolled embeddings never change between calls.
    Returns a new list; does not mutate `face_db` or affect save_face_db
    (the original plain-list "embedding" field is preserved alongside it).
    """
    precomputed = []
    for entry in face_db:
        vec = np.asarray(entry["embedding"], dtype=np.float32)
        norm = np.linalg.norm(vec) or 1e-8
        precomputed.append({**entry, "_normalized": vec / norm})
    return precomputed


def match_embedding(embedding, face_db, threshold_pct):
    """Returns (best_matching_entry_or_None, score_pct) against a precomputed
    face_db (see precompute_face_db — each entry needs a cached "_normalized" vector).
    """
    if embedding is None or not face_db:
        return None, 0.0

    query = np.asarray(embedding, dtype=np.float32)
    query_norm = np.linalg.norm(query) or 1e-8
    query_normalized = query / query_norm

    best_entry = None
    best_score = 0.0
    for entry in face_db:
        score = float(np.dot(query_normalized, entry["_normalized"]))
        if score > best_score:
            best_score = score
            best_entry = entry

    score_pct = best_score * 100
    if best_entry is not None and score_pct >= threshold_pct:
        return best_entry, score_pct
    return None, score_pct


def load_face_db():
    if not FACE_DB_PATH.exists():
        return []
    with open(FACE_DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_face_db(entries):
    FACE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FACE_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def fetch_image_as_array(url, timeout=10):
    """Downloads an image URL and decodes it into an OpenCV BGR array, or None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        arr = np.frombuffer(data, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None
