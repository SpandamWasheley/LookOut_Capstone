"""Vehicle detection for illegal-parking / obstruction testing.

Uses YOLOv8 (ultralytics, COCO-pretrained) to detect vehicles in a still
image and classify them as car / motorcycle / bus / truck / bicycle.

NOTE on "tricycle": the COCO dataset that stock YOLOv8 ships with has no
`tricycle` class. Philippine tricycles (a motorcycle + sidecar) are detected
as `motorcycle`. To distinguish real tricycles you would fine-tune YOLOv8 on a
labelled tricycle dataset and point MODEL_PATH at the resulting weights.
"""

import json

import cv2
import numpy as np

MODEL_PATH = "yolov8n.pt"  # auto-downloads (~6MB) on first use

# COCO class id -> our vehicle label
VEHICLE_CLASSES = {
    1: "bicycle",
    2: "car",
    3: "motorcycle",  # includes PH tricycles (see module note)
    5: "bus",
    7: "truck",
}

_model = None


def load_model():
    """Lazy-loads the YOLOv8 model (heavy import kept out of module load)."""
    global _model
    if _model is None:
        from ultralytics import YOLO

        _model = YOLO(MODEL_PATH)
    return _model


def detect_vehicles(frame, conf=0.35):
    """Returns a list of {label, conf, box:(x1,y1,x2,y2)} for detected vehicles."""
    model = load_model()
    results = model(frame, verbose=False)[0]
    out = []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        if cls_id not in VEHICLE_CLASSES:
            continue
        score = float(box.conf[0])
        if score < conf:
            continue
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        out.append({
            "label": VEHICLE_CLASSES[cls_id],
            "conf": score,
            "box": (x1, y1, x2, y2),
        })
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
