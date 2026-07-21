"""Cigarette / smoking detection for public-area monitoring.

IMPORTANT — this needs a CUSTOM-trained model. The stock YOLOv8 (COCO) model
used everywhere else in this sandbox has NO cigarette or smoking class, and
cigarettes are tiny objects, so they CANNOT be detected with it. You must supply
a model trained on a cigarette / smoking dataset.

Drop the weights here:  detection_sandbox/models/smoking.pt
(or override with the SMOKING_MODEL env var, or pass model_path=...)

Where to get one (see models/README.txt for the full guide):
  * Roboflow Universe has ready-made "cigarette" / "smoking" detection datasets
    with exported YOLOv8 weights you can download directly.
  * Or fine-tune it yourself:
        yolo detect train data=cigarette.yaml model=yolov8n.pt epochs=50 imgsz=640
    then copy runs/detect/train/weights/best.pt -> models/smoking.pt

The rest of the sandbox (video_system.py, detect_image.py) detects smoking
automatically once this file is present, and quietly skips it when it isn't.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_PATH = BASE_DIR / "models" / "smoking.pt"

_model = None
_loaded_path = None


def model_path():
    """Resolved path to the smoking weights (SMOKING_MODEL env var wins)."""
    return Path(os.environ.get("SMOKING_MODEL", str(DEFAULT_MODEL_PATH)))


def is_available():
    """True if a smoking model file is present — callers use this to skip cleanly."""
    return model_path().exists()


def load_model():
    global _model, _loaded_path
    p = model_path()
    if not p.exists():
        raise FileNotFoundError(
            f"Smoking model not found at {p}. See models/README.txt for how to "
            "obtain or train one — stock YOLOv8 cannot detect cigarettes."
        )
    if _model is None or _loaded_path != str(p):
        from ultralytics import YOLO

        _model = YOLO(str(p))
        _loaded_path = str(p)
    return _model


def detect_smoking(frame, conf=0.35):
    """Returns [{label, conf, box:(x1,y1,x2,y2)}] for cigarette / smoking detections.

    Uses whatever class names the custom model was trained with (e.g. "cigarette",
    "smoking"), so it adapts to any Roboflow/YOLO smoking model you drop in.
    """
    model = load_model()
    results = model(frame, verbose=False)[0]
    names = results.names  # id -> class name, from the trained model
    out = []
    for box in results.boxes:
        score = float(box.conf[0])
        if score < conf:
            continue
        cls_id = int(box.cls[0])
        label = names[cls_id] if cls_id in names else "smoking"
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        out.append({"label": label, "conf": score, "box": (x1, y1, x2, y2)})
    return out
