"""Train the thief/robbery-detection YOLOv8 model and install it into the pipeline.

Usage:
    python train_thief.py                 # uses defaults below
    THIEF_DATA=path/to/data.yaml python train_thief.py

On completion it copies the best weights to  detection_sandbox/models/thief.pt.
Copy that to lookout_backend/core/vision/thief.pt (or set the THIEF_MODEL env
var) to enable watch_thief.

Classes: gun, knife, robbery activity, stealing (cleaned Roboflow export — the
No gun_* decoy classes and null images were stripped by the cleanup pass).

This machine is CPU-only; defaults favour a usable model in reasonable time
(~3.5k train images). Bump imgsz/epochs on a GPU for best accuracy.
"""
import os
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_DATA = r"C:\Users\LENOVO THINKPAD T14s\Downloads\datasetfor theif.v1i.yolov8\data_lookout.yaml"

DATA = os.environ.get("THIEF_DATA", DEFAULT_DATA)
EPOCHS = int(os.environ.get("THIEF_EPOCHS", "20"))
IMGSZ = int(os.environ.get("THIEF_IMGSZ", "416"))
BATCH = int(os.environ.get("THIEF_BATCH", "16"))
RUNS = BASE / "runs"
NAME = "thief"


def main():
    from ultralytics import YOLO

    base_weights = BASE / "yolov8n.pt"
    model = YOLO(str(base_weights) if base_weights.exists() else "yolov8n.pt")

    model.train(
        data=DATA,
        epochs=EPOCHS,
        imgsz=IMGSZ,
        batch=BATCH,
        device="cpu",
        workers=4,
        patience=12,          # early-stop if no val improvement
        project=str(RUNS),
        name=NAME,
        exist_ok=True,
        verbose=True,
    )

    best = RUNS / NAME / "weights" / "best.pt"
    if best.exists():
        dst = BASE / "models" / "thief.pt"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(best), str(dst))
        print(f"\n[OK] Installed trained model -> {dst}")
    else:
        print(f"\n[WARN] best.pt not found at {best}")


if __name__ == "__main__":
    main()
