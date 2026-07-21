Custom detection models go here.

=============================================================================
 smoking.pt  —  cigarette / smoking detector  (REQUIRED for smoking detection)
=============================================================================

Stock YOLOv8 (yolov8n.pt) is trained on the COCO dataset, which has NO
cigarette or smoking class. So smoking detection needs a model trained
specifically for it. Put that model here and name it:

    models/smoking.pt

Once it exists, video_system.py and detect_image.py detect smoking
automatically. Without it, they simply skip smoking (no error).

-----------------------------------------------------------------------------
OPTION A — Download a ready-made model (fastest)
-----------------------------------------------------------------------------
1. Go to Roboflow Universe (https://universe.roboflow.com) and search
   "cigarette detection" or "smoking detection".
2. Pick a project, open its "Deploy"/"Download" page, and export the trained
   weights in the YOLOv8 (PyTorch .pt) format.
3. Copy the downloaded weights file here and rename it to  smoking.pt

-----------------------------------------------------------------------------
OPTION B — Train your own (best accuracy for your cameras)
-----------------------------------------------------------------------------
1. Download a cigarette/smoking dataset in YOLOv8 format from Roboflow
   (it comes with a data.yaml).
2. Train:
       yolo detect train data=path/to/data.yaml model=yolov8n.pt epochs=50 imgsz=640
3. Copy the best weights here:
       runs/detect/train/weights/best.pt  ->  models/smoking.pt

-----------------------------------------------------------------------------
Notes
-----------------------------------------------------------------------------
* The pipeline reads the model's own class names, so any label set works
  (e.g. classes ["cigarette"] or ["smoking","not-smoking"]).
* .pt files are git-ignored (see ../.gitignore), so your weights won't be
  committed — share them separately if a teammate needs them.
* To point at a model elsewhere without renaming, set an env var:
       SMOKING_MODEL=C:\path\to\weights.pt
