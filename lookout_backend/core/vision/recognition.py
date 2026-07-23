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
import urllib.request
from pathlib import Path

import cv2
import numpy as np

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

PERSON_CLASS_ID = 0  # COCO class id for "person"

# COCO class ids for the vehicle types relevant to illegal-parking /
# obstruction detection. yolov8n.pt is trained on COCO, so these come for
# free from the same model already used for person detection.
VEHICLE_CLASS_IDS = {
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}

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


def load_yolo():
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO

        _yolo_model = YOLO("yolov8n.pt")
    return _yolo_model


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


def detect_persons(frame, conf=0.5):
    """Returns a list of (x1, y1, x2, y2, conf) boxes for detected people."""
    model = load_yolo()
    results = model(frame, verbose=False)[0]
    boxes = []
    for box in results.boxes:
        if int(box.cls[0]) != PERSON_CLASS_ID:
            continue
        if float(box.conf[0]) < conf:
            continue
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        boxes.append((x1, y1, x2, y2, float(box.conf[0])))
    return boxes


def detect_vehicles(frame, conf=0.4):
    """Returns a list of (x1, y1, x2, y2, conf, label) boxes for detected vehicles.

    `label` is the COCO vehicle class name (car/motorcycle/bus/truck). Reuses
    the same YOLOv8 model as detect_persons — no extra weights to download.
    """
    model = load_yolo()
    results = model(frame, verbose=False)[0]
    boxes = []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        if cls_id not in VEHICLE_CLASS_IDS:
            continue
        if float(box.conf[0]) < conf:
            continue
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        boxes.append((x1, y1, x2, y2, float(box.conf[0]), VEHICLE_CLASS_IDS[cls_id]))
    return boxes


def _smoking_boxes_from_result(results, conf, offset=(0, 0)):
    """Pulls (x1,y1,x2,y2,conf,label) tuples out of a YOLO result.

    `offset` shifts every box by (ox, oy) so detections found inside a tile or a
    crop come back in full-frame coordinates. `label` is the model's own class
    name (cigarette/smoke/vape/smoking), so this adapts to any smoking dataset.
    """
    ox, oy = offset
    names = results.names  # id -> class name, from the trained model
    boxes = []
    for box in results.boxes:
        score = float(box.conf[0])
        if score < conf:
            continue
        cls_id = int(box.cls[0])
        label = names[cls_id] if cls_id in names else "smoking"
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        boxes.append((x1 + ox, y1 + oy, x2 + ox, y2 + oy, score, label))
    return boxes


def detect_smoking(frame, conf=0.3):
    """Single-pass smoking detection over the whole frame (the fast path).

    Requires SMOKING_MODEL_PATH weights. Good for close cameras; at CCTV
    distance a cigarette is only a few pixels once the frame is downscaled to
    the model's imgsz, so use detect_smoking_far there instead.
    """
    model = load_smoking_model()
    results = model(frame, verbose=False)[0]
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

    rows, cols = tiles
    if rows > 1 or cols > 1:
        for tile, (ox, oy) in _iter_tiles(frame, rows, cols, overlap):
            if tile.size == 0:
                continue
            results = model(tile, verbose=False)[0]
            boxes.extend(_smoking_boxes_from_result(results, conf, offset=(ox, oy)))
    else:
        results = model(frame, verbose=False)[0]
        boxes.extend(_smoking_boxes_from_result(results, conf))

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


def detect_thief(frame, conf=0.3):
    """Single-pass thief/robbery detection over the whole frame (the fast path).

    Returns the same (x1,y1,x2,y2,conf,label) tuples as detect_smoking; labels
    come from the trained model (gun/knife/robbery activity/stealing). Guns,
    knives and whole-body actions are far larger than a cigarette, so this
    covers more range than detect_smoking does — but at real CCTV distance a
    handgun still shrinks to a few pixels; use detect_thief_far there.
    """
    model = load_thief_model()
    results = model(frame, verbose=False)[0]
    return _smoking_boxes_from_result(results, conf)


def detect_thief_far(frame, conf=0.3, tiles=(2, 2), overlap=0.2,
                     person_boxes=None, upscale=2.0):
    """Long-range thief/robbery detection — see _detect_far for the cascade.
    Mainly helps the small handheld classes (gun/knife); the whole-body classes
    (robbery activity/stealing) usually don't need it."""
    return _detect_far(load_thief_model(), frame, conf, tiles, overlap,
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
