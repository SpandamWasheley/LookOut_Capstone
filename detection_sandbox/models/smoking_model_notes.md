# smoking.pt — model provenance & notes

**Installed:** `detection_sandbox/models/smoking.pt` (2026-07-21)

## What it is
YOLOv8n detector, 4 classes: `cigarette`, `smoke`, `vape`, `smoking`.
Auto-loaded by `smoking_detection.py`; consumed by `detect_image.py` and `video_system.py`
(they call `smoke.is_available()` / `smoke.detect_smoking()`).

## Training
- Script: `detection_sandbox/train_smoking.py` (env: `SMOKING_EPOCHS`, `SMOKING_IMGSZ`, `SMOKING_BATCH`, `SMOKING_DATA`)
- Data: merged from two Roboflow sets via `merge_smoking.py` →
  `Downloads/smoking dataset/merged` (6058 train / 519 val / 220 test).
  `face` and `Person` classes were dropped on purpose (they'd cause false smoking alerts;
  person detection is handled by the main COCO model).
- This build: **FAST config** (imgsz 320, 15 epochs) on CPU — a rough demo-grade model.

## Accuracy (val, mAP50)
- smoking **0.70** (best), cigarette 0.45, smoke 0.28, vape 0.09 (only 6 val instances — unreliable).

## To improve
Retrain at imgsz 640, ~60 epochs (Colab GPU ~15 min). Zip already prepared at
`Downloads/smoking_merged.zip`. Drop the resulting `best.pt` here as `smoking.pt`.
