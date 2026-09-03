"""Vehicle detection for illegal-parking / obstruction testing.

Uses YOLOv8 (ultralytics, COCO-pretrained) to detect vehicles in a still
image and classify them as car / motorcycle / bus / truck / bicycle.

NOTE on "tricycle": the COCO dataset that stock YOLOv8 ships with has no
`tricycle` class. Philippine tricycles (a motorcycle + sidecar) are detected
as `motorcycle`. To distinguish real tricycles you would fine-tune YOLOv8 on a
labelled tricycle dataset and point MODEL_PATH at the resulting weights.
"""

import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np

MODEL_PATH = "yolov8n.pt"          # auto-downloads (~6MB) on first use
SEG_MODEL_PATH = "yolov8n-seg.pt"  # instance masks (~7MB), for exact footprints


def _load_backend_preprocess():
    """Imports the backend's low-light enhancement without pulling in Django.

    core/vision/preprocess.py is pure OpenCV by design, but importing it as
    `core.vision.preprocess` would execute the package __init__ chain. Loading
    the file directly keeps the sandbox independent of the Django app while
    still using ONE implementation of the enhancement rather than a copy that
    can drift.
    """
    path = (Path(__file__).resolve().parents[1] / "lookout_backend" / "core"
            / "vision" / "preprocess.py")
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("_lookout_preprocess", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preproc = _load_backend_preprocess()


def enhance(frame, sharpen=False):
    """Brightens and de-noises a dark frame; daytime frames pass through.

    Night footage is where vehicle detection fails first, and a missed vehicle
    breaks the dwell timer far more often than a mis-measured one does.
    """
    if preproc is None:
        return frame
    return preproc.preprocess(frame, mode="near", sharpen=sharpen)

# COCO class id -> our vehicle label
VEHICLE_CLASSES = {
    1: "bicycle",
    2: "car",
    3: "motorcycle",  # includes PH tricycles (see module note)
    5: "bus",
    7: "truck",
}

# PH TRICYCLE. COCO has no tricycle class, so a motorcycle with a sidecar is
# reported as `motorcycle` - and a sidecar roughly DOUBLES the width that sits
# on the ground. Left uncorrected the system would credit a tricycle with half
# the footpath it actually blocks, which matters because tricycles are the most
# common obstruction on a barangay street. A solo motorcycle viewed from behind
# is markedly taller than it is wide; add a sidecar and it becomes as wide as it
# is tall. That ratio is the only cue available from a stock COCO model.
TRICYCLE_MIN_ASPECT = 0.95    # width/height at or above this reads as a sidecar

_model = None
_seg_model = None


def load_model():
    """Lazy-loads the YOLOv8 model (heavy import kept out of module load)."""
    global _model
    if _model is None:
        from ultralytics import YOLO

        _model = YOLO(MODEL_PATH)
    return _model


def load_seg_model():
    """Lazy-loads the segmentation model used for exact ground footprints."""
    global _seg_model
    if _seg_model is None:
        from ultralytics import YOLO

        _seg_model = YOLO(SEG_MODEL_PATH)
    return _seg_model


def _refine_label(label, box):
    """Relabels a wide `motorcycle` as `tricycle` (see TRICYCLE_MIN_ASPECT)."""
    if label != "motorcycle":
        return label
    x1, y1, x2, y2 = box
    aspect = (x2 - x1) / max(y2 - y1, 1)
    return "tricycle" if aspect >= TRICYCLE_MIN_ASPECT else label


def detect_vehicles(frame, conf=0.35, masks=False):
    """Vehicles as {label, conf, box, mask}.

    `masks=True` switches to the segmentation model and adds a full-frame
    boolean mask per vehicle. That is what allows the obstruction rule to
    measure the vehicle's REAL ground contact instead of the bottom edge of its
    bounding box, which is always a little wider than the vehicle because the
    box stretches to whichever corners are widest in the image - often roof
    corners, and often at a different depth than the wheels.
    """
    model = load_seg_model() if masks else load_model()
    results = model(frame, verbose=False)[0]
    seg = results.masks.data.cpu().numpy() if (masks and results.masks is not None) else None
    h, w = frame.shape[:2]

    out = []
    for i, box in enumerate(results.boxes):
        cls_id = int(box.cls[0])
        if cls_id not in VEHICLE_CLASSES:
            continue
        score = float(box.conf[0])
        if score < conf:
            continue
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        item = {
            "label": _refine_label(VEHICLE_CLASSES[cls_id], (x1, y1, x2, y2)),
            "conf": score,
            "box": (x1, y1, x2, y2),
            "mask": None,
        }
        if seg is not None and i < len(seg):
            # Masks come back at the model's letterboxed resolution, so they are
            # resized to the frame before use; the rule indexes them in frame
            # coordinates.
            m = cv2.resize(seg[i], (w, h), interpolation=cv2.INTER_NEAREST)
            item["mask"] = (m > 0.5).astype(np.uint8)
        out.append(item)
    return out


def detect_persons(frame, conf=0.35):
    """Person boxes - used as corroboration that a footpath was really blocked."""
    results = load_model()(frame, verbose=False)[0]
    out = []
    for box in results.boxes:
        if int(box.cls[0]) != 0 or float(box.conf[0]) < conf:
            continue
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        out.append((x1, y1, x2, y2))
    return out


def load_zones(path):
    """Loads no-parking polygons. Format: [{"name": str, "points": [[x,y], ...]}]."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _box_center(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def vehicle_in_zone(box, polygon):
    """True if the vehicle box's center falls inside the restricted polygon."""
    poly = np.array(polygon, dtype=np.int32)
    cx, cy = _box_center(box)
    return cv2.pointPolygonTest(poly, (cx, cy), False) >= 0


def flag_violations(vehicles, zones):
    """Tags each vehicle inside a restricted zone with a 'violation' string."""
    if not zones:
        return vehicles
    for v in vehicles:
        for z in zones:
            if vehicle_in_zone(v["box"], z["points"]):
                v["violation"] = f"illegal parking: {z.get('name', 'zone')}"
                break
    return vehicles


def annotate(frame, vehicles, zones=None):
    """Draws restricted zones (red outline) and vehicle boxes onto the frame."""
    if zones:
        for z in zones:
            poly = np.array(z["points"], dtype=np.int32)
            cv2.polylines(frame, [poly], True, (0, 0, 255), 2)
            cv2.putText(frame, z.get("name", "zone"), tuple(poly[0]),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    for v in vehicles:
        x1, y1, x2, y2 = v["box"]
        color = (0, 0, 255) if v.get("violation") else (0, 200, 0)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        tag = f"{v['label']} {v['conf'] * 100:.0f}%"
        if v.get("violation"):
            tag += "  VIOLATION"
        cv2.putText(frame, tag, (x1, max(y1 - 8, 0)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    return frame
