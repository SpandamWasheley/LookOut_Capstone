# Detection Sandbox

A standalone image-testing playground for the LookOut project. It runs two
detectors on a **still image** (no camera / no Django needed) and saves an
annotated copy:

1. **Vehicle recognition** (illegal parking / obstruction) — YOLOv8 detects and
   classifies vehicles: `car`, `motorcycle`, `bus`, `truck`, `bicycle`.
2. **Face recognition** — insightface (ArcFace / buffalo_l) matches faces
   against reference photos you drop in `face_db/`.

This folder is intentionally independent of `lookout_backend/` — it shares no
code, so you can experiment freely without touching the main system.

## Setup

```bash
cd detection_sandbox
python -m venv venv           # optional; the repo-root venv also works
pip install -r requirements.txt
```

First run auto-downloads the models: YOLOv8 weights (`yolov8n.pt`, ~6MB) and the
insightface `buffalo_l` pack (~280MB). This is a one-time, slow download.

## Usage

Run everything on one image:

```bash
python detect_image.py --image sample_images/street.jpg
```

Add no-parking / obstruction **zones** (any vehicle whose center falls inside a
zone is flagged as a violation):

```bash
python detect_image.py --image sample_images/street.jpg --zones zones.example.json
```

Run only one detector:

```bash
python detect_image.py --image portrait.jpg --no-vehicles      # faces only
python detect_image.py --image street.jpg  --no-faces          # vehicles only
```

Results print to the console and an annotated image is written to `output/`.

### Batch mode (a whole folder at once)

Process every image in a folder in one run — the face database is enrolled once
and reused across all images, and a summary CSV is written alongside the
annotated copies:

```bash
python detect_batch.py                                    # processes sample_images/
python detect_batch.py --folder my_photos --zones zones.json
python detect_batch.py --no-faces                         # vehicles only
```

Output: `output/<name>_annotated.jpg` per image, plus `output/batch_summary.csv`
(counts of vehicles, violations, faces, and known faces per image).

### Drawing zones by clicking (no hand-typed coordinates)

Open an image and click to place zone points instead of editing JSON by hand:

```bash
python draw_zones.py --image sample_images/street.jpg --out zones.json
```

Controls (focus the image window): **left click** = add point, **n** = finish
this zone / start a new one, **z** = undo last point, **s** = save & exit,
**q**/**Esc** = quit. The saved `zones.json` plugs straight into `--zones` above.

### Color key
| Color | Meaning |
|-------|---------|
| Green box | vehicle (OK) |
| Red box + zone outline | vehicle inside a no-parking zone |
| Blue box | known / recognized face |
| Orange box | unknown face |

## Face recognition

Drop one clear front-facing photo per person into `face_db/` — the filename
becomes the person's name (e.g. `Juan_DelaCruz.jpg`). Faces are enrolled and
cached on first run; pass `--rebuild-faces` after changing the photos.

> **Confidence scale note:** an insightface genuine same-person match usually
> scores ~35–70 on the 0–100 scale, **not** 90+. The default match threshold is
> 35 — don't treat it as a "35% sure" bar.

## Defining zones

`zones.example.json` is a list of polygons in **pixel coordinates** of your
image:

```json
[
  { "name": "No Parking - Main Rd", "points": [[50, 300], [600, 300], [600, 470], [50, 470]] }
]
```

Adjust the points to match the restricted areas in your own photo.

## The "tricycle" caveat

Stock YOLOv8 is trained on the COCO dataset, which has **no `tricycle` class**.
Philippine tricycles (motorcycle + sidecar) are detected as `motorcycle`. To
recognize tricycles as their own class you must fine-tune YOLOv8 on a labelled
tricycle dataset and point `MODEL_PATH` in `vehicle_detection.py` at the new
weights.

## Files

| File | Purpose |
|------|---------|
| `detect_image.py` | CLI entry point — runs both detectors on one image |
| `detect_batch.py` | runs both detectors over every image in a folder + writes a summary CSV |
| `draw_zones.py` | interactive tool: click on an image to draw no-parking zones |
| `vehicle_detection.py` | YOLOv8 vehicle detection + zone/violation logic |
| `face_recognition_test.py` | insightface enrollment + matching |
| `zones.example.json` | sample no-parking polygons |
| `face_db/` | reference face photos (one per person) |
| `sample_images/` | put your test images here |
| `output/` | annotated results are written here |
