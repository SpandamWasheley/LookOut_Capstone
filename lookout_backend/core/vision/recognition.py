"""Shared CV plumbing for curfew face detection/recognition.

Pipeline: YOLOv8 (ultralytics) locates people in a frame, then insightface's
FaceAnalysis (ArcFace/buffalo_l) locates and recognizes a face within each
person crop, producing a 512-d embedding matched against `face_db.json`
(built by the `enroll_faces` management command).

No Django model access happens here — this module is pure CV plumbing so it
stays importable/testable independent of the management commands that use it.
"""

import json
import urllib.request
from pathlib import Path

import cv2
import numpy as np

VISION_DIR = Path(__file__).resolve().parent
FACE_DB_PATH = VISION_DIR / "face_db.json"

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


def load_yolo():
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO

        _yolo_model = YOLO("yolov8n.pt")
    return _yolo_model


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
