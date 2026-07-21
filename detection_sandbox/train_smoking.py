"""Train the smoking-detection YOLOv8 model and install it into the pipeline.

Usage:
    python train_smoking.py                 # uses defaults below
    SMOKING_DATA=path/to/data.yaml python train_smoking.py

On completion it copies the best weights to  detection_sandbox/models/smoking.pt,
which is all the rest of the sandbox needs to enable smoking detection.

This machine is CPU-only, so training ~6k images is slow. Settings below favour
a usable model in reasonable time; bump imgsz/epochs on a GPU for best accuracy.
"""
import os
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_DATA = r"C:\Users\LENOVO THINKPAD T14s\Downloads\smoking dataset\merged\data.yaml"

DATA = os.environ.get("SMOKING_DATA", DEFAULT_DATA)
EPOCHS = int(os.environ.get("SMOKING_EPOCHS", "60"))
IMGSZ = int(os.environ.get("SMOKING_IMGSZ", "512"))
BATCH = int(os.environ.get("SMOKING_BATCH", "16"))
RUNS = BASE / "runs"
NAME = "smoking"


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
        dst = BASE / "models" / "smoking.pt"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(best), str(dst))
        print(f"\n[OK] Installed trained model -> {dst}")
    else:
        print(f"\n[WARN] best.pt not found at {best}")


if __name__ == "__main__":
    main()
