"""Face recognition on a still image using insightface (ArcFace / buffalo_l).

Enrolls reference faces from face_db/*.jpg (the filename becomes the person's
name), caches the embeddings to face_db/_embeddings.json, then matches faces
found in a test image against them by cosine similarity.

Confidence scale gotcha (same as the main LookOut system): a genuine
same-person match typically scores ~35-70 on the 0-100 scale, NOT 90+. The
default threshold of 35 is calibrated to that scale.
"""

import json
from pathlib import Path

import cv2
import numpy as np

FACE_DIR = Path(__file__).resolve().parent / "face_db"
CACHE_PATH = FACE_DIR / "_embeddings.json"
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

_app = None


def load_face_app():
    """Lazy-loads insightface FaceAnalysis (buffalo_l, CPU). ~280MB first run."""
    global _app
    if _app is None:
        from insightface.app import FaceAnalysis

        _app = FaceAnalysis(name="buffalo_l")
        _app.prepare(ctx_id=-1, det_size=(320, 320))  # ctx_id=-1 -> CPU
    return _app


def _embed_best_face(img):
    app = load_face_app()
    faces = app.get(img)
    if not faces:
        return None
    return max(faces, key=lambda f: f.det_score).embedding


def enroll(rebuild=False):
    """Builds (or loads cached) embeddings for every image in face_db/."""
    FACE_DIR.mkdir(exist_ok=True)
    if CACHE_PATH.exists() and not rebuild:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    db = []
    for p in sorted(FACE_DIR.iterdir()):
        if p.suffix.lower() not in IMAGE_EXTS or p.name.startswith("_"):
            continue
        img = cv2.imread(str(p))
        if img is None:
            print(f"  ! could not read {p.name}, skipped")
            continue
        emb = _embed_best_face(img)
        if emb is None:
            print(f"  ! no face found in {p.name}, skipped")
            continue
        db.append({"name": p.stem, "embedding": emb.tolist()})
        print(f"  + enrolled {p.stem}")

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f)
    return db


def _match(embedding, db, threshold):
    if embedding is None or not db:
        return None, 0.0
    q = np.asarray(embedding, dtype=np.float32)
    q = q / (np.linalg.norm(q) or 1e-8)
    best_name, best_score = None, 0.0
    for e in db:
        v = np.asarray(e["embedding"], dtype=np.float32)
        v = v / (np.linalg.norm(v) or 1e-8)
        score = float(np.dot(q, v)) * 100
        if score > best_score:
            best_score, best_name = score, e["name"]
    if best_score >= threshold:
        return best_name, best_score
    return None, best_score


def recognize_faces_in_image(img, db, threshold=35.0):
    """Returns [{name, score, box:(x1,y1,x2,y2)}] for every face in the image."""
    app = load_face_app()
    results = []
    for f in app.get(img):
        name, score = _match(f.embedding, db, threshold)
        x1, y1, x2, y2 = (int(v) for v in f.bbox)
        results.append({
            "name": name or "Unknown",
            "score": score,
            "box": (x1, y1, x2, y2),
        })
    return results
