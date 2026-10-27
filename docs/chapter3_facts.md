# Chapter 3 fact sheet (read from code, configs, runs and datasets)

Compiled 2026-10-12. Read-only pass; nothing was run, trained or modified. "NOT FOUND" means the fact is not in any file I was allowed to read. Where two sources disagree, both are listed.

**Path aliases used in brackets**

| Alias | Folder |
|---|---|
| `CAP` | `C:\Users\User\OneDrive\Desktop\CAPSTONE` (not LookOut_Capstone) |
| `V2` | `C:\Users\User\OneDrive\Desktop\LookOut_v2` |
| `BE` | `V2\lookout_backend\core` (the Django app) |
| `VIS` | `V2\lookout_backend\core\vision` |
| `CMD` | `V2\lookout_backend\core\management\commands` |

Not read, as instructed: LookOut_Capstone, Models_Train, LookOut_DEV_VERSION. The only command run in Models_Train was the initial `cd`/`dir` check.

---

## A. Custom detection model (merged_v2)

### A.1 Classes and IDs
| ID | Class | Source |
|---|---|---|
| 0 | Bottle | [`CAP\Merged_Crops_v2\data.yaml`] `names: ['Bottle','Cigarette','knife']`, `nc: 3` |
| 1 | Cigarette | same; also embedded in the deployed weights `V2\lookout_backend\core\vision\merged_v2.pt` (read with `torch.load`: `{0:'Bottle',1:'Cigarette',2:'knife'}`) |
| 2 | knife | same |

Routing in the live code is by class name, not index [`CMD\watch_merged.py` `ROUTE_ENGINE = {"cigarette":"smoking","bottle":"drinking","knife":"thief"}`]. There is no vape class and no gun class in merged_v2 [`VIS\recognition.py` comment above `MODEL_PATH`; `V2\docs\specs\holdup_scoring_v2.md` section 1].

### A.2 Dataset

**Which dataset is merged_v2's.** `args.yaml` says `data: ...\CAPSTONE\Merged_Crops_v2\data.yaml` [`V2\args.yaml`; `CAP\runs\detect\merged_v2\args.yaml`]. The two args files are identical apart from line endings (checked with `diff` after stripping CR). `V2\results.csv` is byte-identical to `CAP\runs\detect\merged_v2\results.csv` (`cmp`). The deployed `merged_v2.pt` has the same MD5 (`8e14d8aa9dd1adffcf863f7b0221e3b7`) as `CAP\runs\detect\merged_v2\weights\best.pt`. So the match is certain.

**Source.** Roboflow project `angeles-mathew-tuble / lookout_violation_objectdetection`, version 2 "All_Violations_v2", license CC BY 4.0, exported 2026-09-28 07:22 GMT [`CAP\Merged_Fullres_v2\README.roboflow.txt`, `data.yaml`]. Image file names follow the pattern `Aug14_13_0163_jpg.rf.<hash>.jpg`, `Sept-…`, `…_ParkOnly`, `…TrimMorningCigFar`, `VideoProject… Trim_f…`, and sampled images are all 2560×1440 [`CAP\Merged_Fullres_v2\train\images`]. This suggests frames cut from the team's own recorded clips. No file states the footage source in words, so "own footage" is an inference. No other source (web or public dataset) is named for v2.

**Stage 1: full-resolution export** (`CAP\Merged_Fullres_v2`). 3,716 images [README.roboflow.txt "The dataset includes 3716 images"; counts below sum to 3,716]. Pre-processing: auto-orient only. Augmentation: none ("No image augmentation techniques were applied"). Split 70/20/10 (2,602 / 742 / 372).

| Split | Images | Bottle (0) | Cigarette (1) | knife (2) | Empty-label (negative) images |
|---|---|---|---|---|---|
| train | 2,602 | 2,512 | 753 | 937 | 488 |
| valid | 742 | 614 | 230 | 279 | 152 |
| test | 372 | 320 | 120 | 144 | 65 |
| **total** | **3,716** | **3,446** | **1,103** | **1,360** | **705** |

Counted by reading every `.txt` in `CAP\Merged_Fullres_v2\{train,valid,test}\labels` (non-empty lines, first token = class id).

**Stage 2: person-sized crops used for training** (`CAP\Merged_Crops_v2`). Every crop is 256×384 px.

| Split | Crops | Bottle (0) | Cigarette (1) | knife (2) | Empty-label (negative) crops |
|---|---|---|---|---|---|
| train | 10,055 | 9,680 | 3,896 | 2,809 | 976 |
| valid | 2,905 | 2,244 | 1,190 | 841 | 304 |
| test | 1,482 | 1,274 | 656 | 432 | 130 |
| **total** | **14,442** | **13,198** | **5,742** | **4,082** | **1,410** |

Counted from `CAP\Merged_Crops_v2\{train,valid,test}\labels`. Negatives = 2 crops from each of the 705 unannotated full-res images (488×2=976, 152×2=304, 65×2=130; matches `NEGATIVE_CROPS_PER_IMAGE = 2`).

**Other dataset versions found (not merged_v2's).**

| Dataset | Folder | Images train/valid/test | Instances (class 0 / 1 / 2) train | Note |
|---|---|---|---|---|
| Merged v1 full-res | `CAP\Merged_Fullres` (Roboflow v1, exported 2026-09-01, 2,666 images) | 1,866 / 533 / 267 | 2,387 / 647 / 539 | pre-processing auto-orient only, no augmentation |
| Merged v1 crops | `CAP\Merged_Crops` | 6,152 / 1,766 / 896 | 8,878 / 2,059 / 1,608 | used by merged_v1; 216/70/28 negatives |
| Smoking full-res | `CAP\Smoking_Fullres` (Roboflow `smoking_finetuning-zmbnj` v4, 2026-08-19, 1,198 images) | 839 / 240 / 119 | Cigarette 607 (Vape defined in yaml, 0 instances) | |
| Smoking crops | `CAP\Smoking_Crops` | 2,091 / 600 / 300 | Cigarette 1,823 | used by smoking_v5_crops and smoking_v6 |
| Drinking full-res | `CAP\Drinking_Fullres` (Roboflow `drinking_dataset` v1, 2026-08-20, 637 images) | 447 / 127 / 63 | Bottle 1,631 | |
| Drinking crops | `CAP\Drinking_Crops` | 4,928 / 1,443 / 674 | Bottle 21,570 | used by drinking_v1 |

Other folders exist (`Smoking_Crops_v5_backup`, `Smoking_Fullres_v5_backup`, `Smoking_Finetune`, `Smoking Detection_FINALE`, `Waste Detection`, `Person_Detection_testing`, `Testing_Trials_Models`, `Trials`) and were not counted. They are not tied to merged_v2.

### A.3 Preprocessing pipeline (full-res to crops)
Script: `CAP\build_crops_merged_v2.py`. Run on the Roboflow full-res export (`SRC_DIR = ...\Merged_Fullres_v2`, `OUT_DIR = ...\Merged_Crops_v2`). Training script: `CAP\finetune_crops_merged_v2.py`.

- Frames are extracted at full resolution (2560×1440, Roboflow "Resize: NONE"). The extraction from video into frames is not in these scripts; Roboflow naming suggests it was done earlier. NOT FOUND as a script.
- Crop window `CROP_W = 256`, `CROP_H = 384` (original-frame pixels). It is resized up to 640 by YOLO at training (scale 640/256 = 2.5 on width used by the script's size report).
- Crops per annotated box (`CROPS_PER_BOX`): Bottle 1, Cigarette 5, knife 3 (default for other classes 3).
- `MAX_CROPS_PER_IMAGE = 20`.
- `NEGATIVE_CROPS_PER_IMAGE = 2`: random 256×384 crops from images with no annotations (the "hard negatives": white trousers, parked cars, taillights, per the script comment).
- Crop centre is the object centre plus random jitter up to ±0.25 of the crop size (`JITTER = 0.25`), clamped inside the frame.
- Every other box is kept in the crop only if at least 50% of its area is inside the crop; coordinates are re-normalised.
- `SEED = 42`. Split folders (train/valid/test) are inherited from the Roboflow 70/20/10 split, so crops from one source image stay in one split.
- No other filtering. Source: `CAP\build_crops_merged_v2.py`.
- v1 differences (`CAP\build_crops_merged.py`): Cigarette 3 crops per box, 1 negative per image.

### A.4 Augmentation
- Roboflow export: none [`CAP\Merged_Fullres_v2\README.roboflow.txt`].
- Ultralytics training-time (all from `V2\args.yaml` / `CAP\runs\detect\merged_v2\args.yaml`): mosaic 1.0 (off for the last `close_mosaic: 10` epochs), fliplr 0.5, flipud 0.0, hsv_h 0.015, hsv_s 0.7, hsv_v 0.4, translate 0.1, scale 0.5, degrees 0, shear 0, perspective 0, mixup 0, cutmix 0, copy_paste 0, erasing 0.4, `auto_augment: randaugment`.
- Dataset-level: random crop offset (jitter ±25%) in A.3.

### A.5 Training configuration [`V2\args.yaml`]
| Item | Value |
|---|---|
| Base weights | `CAP\runs\detect\smoking_v6\weights\best.pt` (a fine-tuned model, not stock COCO). Head rebuilt because class count changed (3 vs the old set). [`CAP\finetune_crops_merged_v2.py` docstring; `ALLOW_HEAD_REBUILD = True`] |
| Model size | YOLOv8n (nano). Deployed weights have 3,011,433 parameters (`torch.load` of `merged_v2.pt`). Lineage: smoking_v3 started from `yolov8n.pt` [`CAP\Smoking Detection_FINALE\runs\detect\smoking_v3\args.yaml`]. |
| Epochs set / run | 80 set; 71 rows in `results.csv` (patience 15 stopped it; best epoch 56) |
| imgsz / batch | 640 / 32 |
| Optimizer | SGD (explicit), lr0 0.01, lrf 0.01, momentum 0.937, weight_decay 0.0005, warmup_epochs 3, cos_lr false |
| Patience / seed | 15 / 42 (deterministic true) |
| Device / workers | `device: '0'` (GPU) / 2; amp true |
| Total training time | `args.yaml` has `time: null`. `results.csv` cumulative `time` column: 7,997.73 s at epoch 71 (about 2 h 13 min); 6,258.04 s at best epoch 56. |
| Ultralytics version | 8.4.67 [`merged_v2.pt` checkpoint field `version`; checkpoint date 2026-09-28T17:57] |

### A.6 Final metrics [`V2\results.csv` = `CAP\runs\detect\merged_v2\results.csv`]
Validation set: `valid/images` of `Merged_Crops_v2` (2,905 crops). Metrics are box (B), computed by Ultralytics each epoch.

| | Precision | Recall | mAP@50 | mAP@50-95 |
|---|---|---|---|---|
| **Best epoch 56** (`best.pt`; selected by fitness = mAP@50-95) | 0.90375 | 0.85288 | 0.89056 | 0.54798 |
| Last epoch 71 | 0.91484 | 0.84058 | 0.88895 | 0.54118 |
| Best single values over the run | P 0.91763 (ep 62) | R 0.86014 (ep 57) | mAP50 0.89354 (ep 57) | 0.54798 (ep 56) |

- Best-epoch row at ep 56 also: train box/cls/dfl loss 0.86729 / 0.50312 / 0.88885; val box/cls/dfl loss 1.32533 / 0.67911 / 1.07403. The same numbers are stored in the deployed checkpoint (`train_metrics`).
- F1: no numeric F1 is logged. A plot exists (`CAP\runs\detect\merged_v2\BoxF1_curve.png`). Derived from the best-epoch P and R: F1 = 2PR/(P+R) = **0.878** (my arithmetic, not a logged value).
- **Per-class precision / recall / mAP: NOT FOUND.** `results.csv` only holds overall numbers. Per-class values would need a validation run, which was out of scope. Plots exist but no text: `confusion_matrix.png`, `BoxPR_curve.png`, `BoxP_curve.png`, `BoxR_curve.png` in `CAP\runs\detect\merged_v2`. `val*` folders under `CAP\runs\detect` contain images only.
- **Test split metrics: NOT FOUND** (no log of evaluating `test/images`).
- Live-pipeline accuracy (alerts versus ground truth on clips) is not in these files.

### A.7 Earlier versions (progression)
All from `CAP\runs\detect\<run>\{args.yaml,results.csv}`; best epoch = highest mAP@50-95. The validation sets differ per run (different datasets), so rows are **not directly comparable**.

| Run | Started from | Data (`CAP\…`) | Classes | Epochs run (best) | Time (s, cumulative) | P | R | mAP50 | mAP50-95 |
|---|---|---|---|---|---|---|---|---|---|
| smoking_v3 | yolov8n.pt | `Smoking Detection_FINALE\data.yaml` (50 epochs, optimizer auto) | not read | 50 (47) | not read | 0.92053 | 0.68234 | 0.78033 | 0.45639 |
| smoking_v4_finetune-2 | smoking_v3 | `Smoking_Finetune` (full-res frames) | | 40 (32) | 1,080 | 0.14197 | 0.05882 | 0.02438 | 0.00994 |
| smoking_v5_crops | smoking_v3 | `Smoking_Crops` | Cigarette (Vape defined) | 40 (36) | 1,408 | 0.91691 | 0.91285 | 0.95467 | 0.53357 |
| **smoking_v6** | smoking_v5_crops | `Smoking_Crops` | Cigarette (Vape defined) | 40 (28) | 1,136 | 0.94542 | 0.90118 | 0.93974 | 0.51779 |
| **drinking_v1** | yolov8n.pt (`C:\Users\User\.yolo\models\yolov8n.pt`) | `Drinking_Crops` | Bottle | 42 (27) | 2,263 | 0.92571 | 0.86863 | 0.92576 | 0.64575 |
| **merged_v1** | smoking_v6 | `Merged_Crops` | Bottle, Cigarette, knife | 57 (42) | 5,130 | 0.89121 | 0.79413 | 0.85384 | 0.51425 |
| **merged_v2** | smoking_v6 | `Merged_Crops_v2` | Bottle, Cigarette, knife | 71 (56) | 7,998 | 0.90375 | 0.85288 | 0.89056 | 0.54798 |

- smoking_v4_finetune-2 failed because the images were full 2560 px frames shrunk to 640 (the cigarette becomes ~3 px) [`CAP\build_crops_merged_v2.py` docstring]. This motivated the crop approach.
- smoking_v3 and smoking_v4 per-run times were not read. smoking_v3's best row is from `CAP\Smoking Detection_FINALE\runs\detect\smoking_v3\results.csv`.
- merged_v1 to merged_v2 change: more data (2,666 to 3,716 full-res images), Cigarette crops per box 3 to 5, negatives 1 to 2 per image, recall 0.794 to 0.853, mAP50 0.854 to 0.891.
- Common settings across these runs (from args.yaml): imgsz 640, batch 32, mosaic 1.0, fliplr 0.5, patience 15. smoking_v5/v6 used SGD lr0 0.001, 40 epochs; merged and drinking runs used SGD lr0 0.01, 80 epochs.

---

## B. Pretrained models used without retraining

| Purpose | File / name | Loaded at | Detail |
|---|---|---|---|
| Person and vehicle detector (COCO) | `yolov8n.pt` (also `V2\lookout_backend\yolov8n.pt` and `VIS\yolov8n.pt` on disk) | `VIS\recognition.py` `load_yolo()` → `YOLO("yolov8n.pt")` (relative name, resolved from the working directory, which is `lookout_backend`) | Person = COCO class 0 (`PERSON_CLASS_ID`), conf ≥ 0.5, `imgsz` = `NEAR_IMGSZ` (960 with a CUDA GPU, 640 on CPU, env `LOOKOUT_IMGSZ`). Vehicles = COCO ids 2 car, 3 motorcycle, 5 bus, 7 truck (`VEHICLE_CLASS_IDS`), `imgsz` 1280 (`VEHICLE_IMGSZ`), conf 0.40 default but parking passes `parking_confidence/100` (0.35). Other COCO sets defined: bottle/wine glass/cup (39/40/41), backpack/handbag/suitcase (24/26/28), bicycle (1). Used for Layer E only in legacy code that is not fed in `watch_merged`. |
| Pose | `yolov8n-pose.pt` (`V2\lookout_backend\yolov8n-pose.pt`) | `VIS\recognition.py` `load_pose()` → `YOLO("yolov8n-pose.pt")` | COCO-17 keypoints used: nose (0), shoulders (5, 6), wrists (9, 10); the mouth anchor also uses eyes and ears. Keypoint confidence floor 0.3 (`KP_MIN_CONF`); whole-frame pose person conf 0.4 (`detect_pose`); mouth anchor runs pose on a person crop (pad 0.10, imgsz 640, FP16 on GPU) (`pose_on_box`, `POSE_CROP_*`). Uses: (1) hand-to-mouth gesture `hand_to_mouth_ratio`; (2) mouth position for "item at the mouth" and "bottle at the mouth"; (3) wrist position for the knife-near-hand rule. |
| Custom detector | `VIS\merged_v2.pt` | `VIS\recognition.py` `MODEL_PATH` (env `LOOKOUT_MODEL` overrides), `load_merged_model()` | Not pretrained-only; see A. All four per-violation path names (`SMOKING_MODEL_PATH`, `THIEF_…`, `DRINKING_…`, `MERGED_…`) are aliases of this one file. |
| Vision-language model (VLM) | `qwen3-vl:2b-instruct` (smoking, drinking) and `qwen3-vl:4b-instruct` (holdup) | `BE\models.py` `SystemSettings.vlm_model` / `vlm_model_holdup` defaults; `VIS\ai_checker.py` `DEFAULT_MODEL`; built in `CMD\_incidents.py` `ai_setup()` | Served by **Ollama** (`/api/chat`), default `http://localhost:11434`; `VLM_ENDPOINT` in the environment overrides (used with an ngrok tunnel in the hosted layout) [`BE\models.py` `resolved_vlm_endpoint`; `V2\lookout_backend\.env.example`]. Call: temperature 0, `num_predict` 400, `think: false`, reply forced to a JSON schema, `num_ctx` = images×1200 + 2500 tokens rounded up to 1024 (`context_for`). Frames: 8 (`vlm_frames`), longest edge 640 px (`vlm_max_edge`), timeout 120 s (holdup client at least 180 s). Asynchronous by default (`vlm_async` True). The model sizes are the code defaults; whether those tags are actually pulled on the machine is NOT FOUND. |
| **Trigger** | | `CMD\_incidents.py` `_incident_sync` | One call per event, at the moment the event first reaches Possible or Likely, never for Monitoring (a puff-only smoking row opens at Possible, so it triggers at creation). It adds no points and never changes the status; it feeds the AI card and a suggested status [`VIS\ai_checker.py` docstring, `VIS\ai_status.py`]. |

Unused weights present in `VIS`: `drinking.pt`, `smoking.pt`, `smoking_v5.pt`, `thief.pt`. Nothing loads them; they appear only in error messages and comments (grep of `*.py`). `CAP` also contains `yolo26n.pt` and `yolov8n.pt` (not used by the deployed code).

---

## C. Inference pipeline (in the order the code runs it)

Live entry: the web "start" button runs `core\monitor.py`, which launches two subprocesses against the camera RTSP URL: `manage.py watch_merged` (smoking + drinking + holdup) and, only if a road edge is saved for the camera, `manage.py watch_parking` [`BE\monitor.py` `_launch`]. A supervisor restarts them with 5 s back-off up to 120 s. Uploaded-video runs use `DETECTION_COMMANDS` in `BE\views.py` (`watch_smoking`, `watch_drinking`, `watch_thief`, `watch_parking`, `watch_merged`, or `watch_merged_all` = merged + parking in one process).

1. **Frame source.** `cv2.VideoCapture(url, CAP_FFMPEG)` with `rtsp_transport;tcp`, `fflags;nobuffer`, buffer size 1 [`CMD\watch_merged.py` `_open`]. Live sources go through `LatestFrameReader`, a thread that keeps only the newest frame and reconnects after 30 failed reads (3 s wait) [`VIS\recognition.py`]. Files are read sequentially (no skipping). The camera is one Hikvision DS-2CD1047G2, code `CAM-SMOKE-01` [`CMD\watch_merged.py`; `V2\CLAUDE.md`]. Stream resolution: frames are 2560×1440 in the dataset; the actual RTSP stream setting is NOT FOUND in code.
2. **Frame rate.** No fixed frame skipping; the loop processes the latest available frame as fast as inference allows. Code comments state about 4–6 processed fps live, 10 fps for the test clips [`VIS\momentum.py` docstring; `VIS\ai_checker.py` `RING_MIN_GAP`]. A measured live FPS is NOT FOUND. A warning prints under 1.5 FPS. All timing rules use a content clock (video position for files, wall clock for live).
3. **Pre-processing.** None in `watch_merged` (the brightness/CLAHE enhancement in `VIS\preprocess.py` is bypassed: `preprocess=False`).
4. **Person pass (once per frame, shared).** `detect_persons_tracked`: yolov8n, conf 0.5, `NEAR_IMGSZ`, `model.track(persist=True, tracker="bytetrack.yaml")` [`VIS\recognition.py`]. Tracker YAML is Ultralytics' built-in (not in the repo). Installed 8.4.67 defaults: `track_high_thresh 0.25`, `track_low_thresh 0.1`, `new_track_thresh 0.25`, `track_buffer 30`, `match_thresh 0.8`, `fuse_score True` [`…\ultralytics\cfg\trackers\bytetrack.yaml`]. `--tracker` choices are greedy / bytetrack / botsort; merged default is bytetrack, parking default bytetrack. `PersonTracker` then maps those IDs to its own tracks (missing-gap 2 s, tombstone 10 s) [`VIS\tracking.py`: `TRACK_MAX_GAP 2.0`, `TOMBSTONE_SECONDS 10.0`, `TRACK_MATCH_IOU 0.3`].
5. **Object detection with merged_v2** (`detect_merged_far`, default; `--fast` = one whole-frame pass):
   - whole-frame pass at `FAR_IMGSZ` (= `NEAR_IMGSZ`, 960 on GPU);
   - 2×2 tiles (`--tiles 2x2`), overlap 0.2 of tile size on each side;
   - each person box cropped and upscaled ×2 (bicubic) and run through the model;
   - all merged with greedy NMS at IoU 0.5 (`_nms`). [`VIS\recognition.py` `_detect_far`, `_iter_tiles`]
   - Extra "cascade" for Cigarette (on by default, `--no-cascade` turns it off): each person box padded 35% on every side, cropped from the native frame, run at `crop_imgsz = NEAR_IMGSZ`, conf = `smoking_confidence/100`, NMS 0.5 [`detect_on_person_crops`, `detect_smoking_cascade`].
   - Ultralytics' own NMS IoU (0.7 default) is not overridden anywhere in `core\vision\recognition.py`.
6. **Confidence thresholds.** The merged model's floor = the lowest enabled engine's setting. Defaults in `BE\models.py` `SystemSettings` (editable in Settings; the stored database value may differ and was not read): smoking 30%, drinking 35%, holdup (thief) 30%, parking 35%. Class floors: Cigarette ×1.0, knife ×1.0 [`CMD\watch_smoking.py` `CLASS_POLICY`; `CMD\watch_thief.py` `CLASS_POLICY`]. Person 0.5, pose person 0.4, keypoint 0.3, vehicle default 0.4 / parking 0.35.
7. **Routing and linking to a person.** Detections are routed by name to the three engines [`CMD\watch_merged.py` `_merged_pass`]. `PersonTracker.assign` gives a detection to the person whose box, widened by 45% of the box width on all sides (`ASSIGN_REACH = 0.45`), contains the detection centre (smallest box wins). Unclaimed detections become "scene" tracks, which smoking/holdup discard; a scene bottle can still support a drinking group [`VIS\tracking.py`].
8. **Knife filters (holdup).** Pose runs only on frames with a knife. A knife box is dropped if its centre is in the top 15% of the person box (`KNIFE_HEAD_EXCLUSION_FRAC`), taller than 25% of the person's height (`KNIFE_MAX_HEIGHT_FRAC`), has no confident wrist, or is farther than 0.22 person-heights from the nearest wrist (`KNIFE_MAX_WRIST_DIST_FRAC`) [`CMD\watch_thief.py`]. Then it must appear in at least 2 of the last 5 frames (a burst counts for 3 s).
9. **Object cue = momentum**, per (track, class): `momentum = momentum × 0.90 + confidence`, capped at 3.0; ON at ≥ 1.5, OFF below 0.4 [`VIS\momentum.py` `MomentumConfig(decay 0.90, on 1.5, off 0.4, max 3.0)`]. The cue must also stay ON for `object_confirm_seconds` (default 2.0 s) before it counts (knife: 0.5 s and a 1.0 s hold before decay). Momentum slots are dropped as soon as the track is gone. `LOOKOUT_MOMENTUM` env can override the numbers.
10. **Scoring and status** per the rules in D; hysteresis keeps Possible until the score drops 5 points below 55 and Likely until 5 below 75 [`VIS\scoring.py`].
11. **Events (one Alert row per incident)** [`CMD\_incidents.py`]:
    - *Created* when the object cue is ON (status Monitoring), or straight at Possible/Likely. Blocked if an alert was already raised near that spot within `alert_cooldown` (default 120 s; same-place test = centre within 1.5 box-sizes for smoking/drinking, IoU ≥ 0.3 for holdup).
    - *Updated* when the level changes or at most every 5 s (`ROW_UPDATE_SECONDS`): level, confidence (score), `last_seen_at`, description, `peak_level` (never lowered), cue vector, timeline entry on a rise or after 5 s.
    - *Ended* when no update for 3 s (`END_GRACE_SECONDS`); the row stays, `last_seen_at` is stamped.
    - *Continued* under the same row if the same subject (centre within 0.8 box-sizes, similar height) reappears within 15 s or after a tracker id change.
    - *Deleted*: a Monitoring event shorter than `monitoring_min_seconds` (3 s) that never rose is removed: row, image and AI frames (`_incident_discard`).
12. **Evidence.** On creation: one full annotated frame JPEG (`cv2.imwrite`, original frame size) named `<timestamp>_<type>_<label>.jpg` in `media\violations`. Monitoring gets the image only. Once the event is Possible/Likely: an annotated clip and a raw clip, re-written every 15 s while it continues and when it ends [`_incident_clip`, `CLIP_REFRESH_SECONDS`].
    - Annotated clip: rolling buffer of the last 30 s (`ClipRecorder(seconds=30)`), frames capped at 1280 px long edge and 450 frames, date/time burned in, written with OpenCV mp4v at 12 fps then transcoded by ffmpeg to H.264 (`libx264`, preset veryfast, CRF 28, yuv420p, `scale=1280:-2`, `+faststart`); a processed frame is held for its real duration, at most 2 s [`VIS\recognition.py` `ClipRecorder.save`].
    - Raw clip: for a file, cut with `ffmpeg -c copy` from 15 s before to 2 s after the alert; for live, saved from a rolling raw-frame buffer (`RAW_CLIP_PRE_SECONDS 15`, `RAW_CLIP_POST_SECONDS 2`).
13. **Database write** (`core_alert`, `BE\models.py` `Alert`): code `ALT-####`, type, status `active`, camera, timestamp (live: now; uploaded clip with "Recorded at": that time + position in the clip, Asia/Manila), `confidence` (final score 0–1), `object_confidence` (YOLO box), `description`, `image_url`, `video_url`, `raw_video_url`, `level`, `peak_level`, `last_seen_at`, `cues` (JSON: fired cues, weights, multipliers, suppressed, raw score, settings snapshot, momentum snapshot, time source), `ai` (JSON, written later by the worker thread), `timeline` (JSON list, max 60 events), `reviewed_valid/by/at`. The detector process writes straight to the same database the API reads. Evidence files are published to Cloudinary or S3-compatible storage if configured (`BE\media.py`), otherwise served from local `MEDIA_ROOT`.
14. **Reaching web and mobile.** There is no WebSocket and no push service. The web dashboard (`lookout\src\AlertFeed.jsx`, `useLiveOverviewData`) and the officer app (`officer_app\context\AssignmentContext.tsx`) poll `/api/alerts/` every 4 000 ms. The list endpoint hides `monitoring` rows unless `?level=monitoring` or `?include_monitoring=1` [`BE\views.py` `AlertViewSet.get_queryset`], so only Possible and Likely (and parking, which has an empty level) reach the normal lists. Officers see their assignments after a dispatcher assigns or they `accept` (`POST /api/alerts/{id}/accept/`). `expo-notifications` is not in `officer_app\package.json`.
15. **Parking path** (`watch_parking` or the merged-all extra engine): vehicles from yolov8n at 1280, tracked with bytetrack, judged by `ObstructionZone` (details in D.4). One alert per stay.

---

## D. Heuristic engine

Points are on a 0–100 scale (stored 0–1). Status: Monitoring = object cue ON and score < 55; Possible ≥ 55; Likely ≥ 75; cap 100. Hysteresis: Possible/Likely are left only when the score is 5 points below the threshold that admitted them. Possible and Likely notify; Monitoring is stored but silent [`VIS\scoring.py`: `SCORE_WARNING 0.55`, `SCORE_VIOLATION 0.75`, `HYSTERESIS_DROP 0.05`]. The points and the 55/75 cut-offs are not user-configurable [`VIS\spec_settings.py` docstring]. Alert statuses stored in the database: `warning` = Possible, `violation` = Likely.

**Settings the user can change (Settings page, admin only) and their defaults** [`VIS\spec_settings.py` `SPEC_DEFAULTS`, `LIMITS`; `BE\models.py`]:

| Setting | Default | Range |
|---|---|---|
| Object confirm time `object_confirm_seconds` | 2.0 s | 0.5–10 |
| Behaviour hold `cue_hold_seconds` | 5.0 s | 0–30 |
| Monitoring minimum `monitoring_min_seconds` | 3.0 s | 0–60 |
| Drinking: stay time `drinking_group_duration` | 600 s | 60–7,200 |
| Drinking: min group `drinking_min_group` | 2 | 2–20 |
| Drinking: evening band `drinking_start`/`drinking_end` | 16:00–00:00 | any |
| Smoking: puff count `smoking_puff_count` | 3 | 2–10 |
| Smoking: puff window `smoking_puff_window_minutes` | 5.0 min | 1–30 |
| Holdup: lingering `holdup_loiter_seconds` | 20 s | 5–300 |
| Holdup: second person distance `holdup_near_person_heights` | 1.75 holder-heights | 0.5–4 |
| Confidence per violation (`smoking_confidence`, `drinking_confidence`, `thief_confidence`, `parking_confidence`) | 30 / 35 / 30 / 35 % | slider 10–90 |
| Drinking bottle-to-mouth `drinking_mouth_proximity` | 3.0 face-widths | slider 1.5–5 |
| Drinking same-spot radius `drinking_cooldown_center_dist` | 1.5 | slider 0.5–3 |
| Alert cooldown `alert_cooldown` | 120 s | slider 30–600 |
| Evidence retention `evidence_retention_days` / auto-purge | 30 days / off | slider 7–90 |
| Enable per violation, auto-start detection (off), show testing tools (off) | | |

Sources for the slider ranges: `V2\lookout\src\SystemConfig.jsx`. `drinking_evidence_max_age` (12 s) exists in the model but no slider was seen for it.

### D.1 Smoking [`VIS\scoring.py` `SMOKING_WEIGHTS`; `CMD\watch_smoking.py` `_process_track`]
| Indicator (code name) | Points | Exact condition |
|---|---|---|
| Smoking item (`cigarette`) — GATE | 40 | Cigarette momentum cue ON for ≥ confirm time (2 s). |
| Hand to mouth, 1 puff (`gesture`) | 20 | ≥ 1 completed pose gesture in the window. A gesture = wrist-to-nose distance ≤ 0.7 shoulder-widths ("at face") then ≥ 1.3 ("down") [`VIS\tracking.py` `HAND_RAISED_RATIO`, `HAND_LOWERED_RATIO`]. |
| Did it again (`puffs`) | 20 | ≥ 2 gestures in the window. |
| Repeated pattern (`puff_pattern`) | 15 | ≥ `smoking_puff_count` (3) gestures within `smoking_puff_window_minutes` (5 min = 300 s). |
| Item at mouth (`near_mouth`) | 15 | Needs the item. Item centre within 2.5 face-widths of the pose-derived mouth (`MOUTH_PROXIMITY = 2.5`), measured within the last 1.0 s (`MOUTH_FRESH_SECONDS`); then held for the cue-hold time (5 s). |
| Total | 110, capped at 100 | |

- Puff-only path (no item): needs the pattern cue (≥ 3 puffs); 20+20+15 = 55 → Possible, capped at Possible, tagged "No smoking item detected — based on hand movement only". Fewer than 3 puffs without the item are logged only [`VIS\scoring.py` `PUFF_ONLY_*`].
- High-resolution check: for 4 s (`HIRES_SECONDS`) after a new puff, the cigarette detector runs on the top 60% of that person's box at native resolution (`HIRES_BOX_FRACTION 0.6`).
- Examples from the weights: item 40 = Monitoring; item+gesture 60 = Possible; item+gesture+near_mouth 75 = Likely.
- Dedupe: alert cooldown 120 s with centre-distance 1.5 box-sizes (`COOLDOWN_CENTER_DIST`).
- **Disabled/inert in code:** `--require-puff` (object-based puff-cycle rule, `PUFF_RAISED_RATIO 1.5`, `PUFF_LOWERED_RATIO 3.0`; opt-in flag, default false); `POSE_EVERY_N_FRAMES = 1`; `MOUTH_CUE_MAX_AGE` is marked "no longer used". `CLASS_POLICY` still has `smoke`/`vapor` entries (×1.5 conf, ×2 dwell) though merged_v2 has no such class. `smoking_dwell` setting is passed into `_process_track` but not read there.

### D.2 Drinking [`VIS\scoring.py` `DRINKING_WEIGHTS`; `CMD\watch_drinking.py`]
| Indicator | Points | Condition |
|---|---|---|
| Bottle (`bottle`) — GATE | 40 | Bottle momentum cue ON for ≥ 2 s. A group's bottle may be one in a member's hand or a scene bottle within 0.75× the group's height of it. |
| Group of 2+ (`gathering`) | 10 | Cluster of ≥ `drinking_min_group` (2) people, grouped when closer than 1.8 box-sizes (`GROUP_CLUSTER_DIST`), stationary (centre drift ≤ 0.6 box-widths) over `min(duration_held, 30 s)`, which must be ≥ 5 s. Needs no bottle to accrue, but is only shown once the bottle cue is ON. |
| Stayed 10+ minutes (`gathering_duration`) | 15 | Cluster held ≥ `drinking_group_duration` (600 s) and stationary for that long. |
| Bottle at mouth (`at_mouth`) | 15 | Needs the bottle. Bottle centre within `face-width × drinking_mouth_proximity` (3.0) of the pose mouth; held 5 s after last seen. |
| Evening (`time_band`) | 5 | Clock inside `drinking_start`–`drinking_end` (16:00–24:00). A scored cue, not a gate. |
| Total | 85 | |

- A group of ≥ min_group is ONE event for the whole cluster (the members do not each get a row). Cluster memory: lost gatherings revive for 15 s (`GROUP_TOMBSTONE_SECONDS`); a drop below min group is tolerated for 3 s.
- Cooldown: 120 s, same-spot radius `drinking_cooldown_center_dist` 1.5.
- Inert settings: `drinking_dwell` (8), `drinking_held_dwell` (24) are passed in but not read in the scoring path; `drinking_hours_enabled` (default False) is a legacy hard gate that still exists in `watch_drinking.py` (lines 666, 716) but is off by default.

### D.3 Holdup [`VIS\theft.py` `WEIGHTS`, `Evidence`; `CMD\watch_thief.py` `_knife_evidence`; spec `V2\docs\specs\holdup_scoring_v2.md`]
| Code | Indicator | Points | Condition |
|---|---|---|---|
| E14 | Knife near a hand — GATE | 45 | Knife passes the filters in C.8; cue ON ≥ 0.5 s; seen in ≥ 2 of last 5 frames. Alone it is Monitoring. |
| E12 | Pair frozen close together | 20 | Two people within arm's length (`FREEZE_REACH 1.0`) and speed < 0.3 (`FREEZE_SPEED`) for ≥ 2 s (`FREEZE_SECONDS`), AND the knife seen during the freeze, up to 3 s before it began or 3 s after it ended; held 5 s after it breaks. |
| E11→E12 | Approach then freeze | 10 | Pair closed from > 3 heights to < 1 height within 3 s, and the freeze began within 5 s of that. E11 alone is worth 0. |
| E10 | Loitering, remembered | 10 | Holder was on screen ≥ `holdup_loiter_seconds` (20 s) with path efficiency < 0.30 over 20 s at some moment in the last 60 s (stamp copied if the track id changes within 3 s and 0.5 heights). |
| E20 | Time of day | +4 to +10 | Added points, `round(10 × p_block / 0.170)`, from the Manila 3-hour table (Robielos & Duran 2020): 00–03 → 6, 03–06 → 6, 06–09 → 4, 09–12 → 9, 12–15 → 10, 15–18 → 10, 18–21 → 8, 21–24 → 6. Counted only with E10 or E12; never moves the score into a higher band. |

- Leaving Monitoring needs all of: E14; a second person within `holdup_near_person_heights` (1.75 holder-heights); and E10 or E12. Otherwise the status stays Monitoring (`holdup_capped`).
- Base = E14+E12+E11E12+E10 (max 85); E20 rule: base ≥ 75 → base+E20; 55 ≤ base < 75 → min(base+E20, 74); base < 55 → min(base+E20, 54).
- Expected: E14 only 45 Monitoring; +E10 55 Possible; +E12 65 Possible; +E12+E10 75 Likely.
- Cooldown: 120 s, IoU ≥ 0.3 with an earlier alert box.
- **Mismatch inside the code:** `VIS\scoring.py` `HOLDUP_WEIGHTS` lists only E14/E12/E10 (total 0.75) and a multiplier table `MANILA_HOUR_BLOCKS`; the live path uses `theft.WEIGHTS` with E11E12 and E20 as added points. `HOLDUP_WEIGHTS` is not used by the live knife path.
- **Present but not active:** snatch (E6–E9), carnapping (E15–E19), property (E21–E22) rules remain in `theft.py` with weights, and `E13` is retired. Their evidence cannot create alerts: `_handle_evidence` only stores E12/E10 cues, and `watch_merged` passes empty carriable and vehicle lists. The `--enable-legacy-theft` flag named in comments was not found in `watch_thief.py`.

### D.4 Parking [`VIS\obstruction_zone.py`; `CMD\watch_parking.py`; `V2\docs\specs\parking_defaults.md`]
No points and no Monitoring/Possible/Likely rows; one alert when the timer fills.
- Zone: a polygon drawn per camera (`Camera.edges`, edited in the web "Edge Zones" editor, or `draw_zone` command). Merged-all and live monitor refuse/skip parking if none is drawn.
- Overlap: the share of the vehicle's ground contact line (bottom edge, 21 sample points) inside the polygon must be ≥ `Camera.obstruction_pct` (default 35%; code `ENTER_FRACTION 0.35`), with 10-point hysteresis (disengage 10 points lower).
- Stopped rule: centre drift ≤ 0.15 box-widths over a 3 s window (`STATIONARY_DIST`, `STATIONARY_WINDOW`). Only stopped time banks 1 s per second; moving weight 0.
- Time limit: `Camera.obstruction_minutes` default 2.0 min (120 s; `ALERT_SCORE 120.0`).
- Draining: detected but outside the zone loses 2.0 s per second (`DECAY_PER_SEC`); undetected loses 0.5 s per second after a 2 s grace (`MISSING_DECAY_PER_SEC`, `PRESENCE_GRACE`); state forgotten after 30 s (`FORGET_AFTER`).
- ID handover: a new track id inherits a lost vehicle's banked seconds if the vehicle is stopped, its box overlaps the old one by IoU ≥ 0.55 and the old id was seen recently (`ADOPT_MIN_IOU 0.55`, `ADOPT_DIST 1.2`, `_absorb_lost`).
- Cooldown: 120 s and centre distance 1.5 box-widths.
- Only COCO car, motorcycle, bus, truck count; vehicle conf 0.35 (parking_confidence), imgsz 1280.
- Alert: written with an empty `level`; status `active`; description "Obstruction: <label> stopped in zone <n>s".
- User-configurable: parking confidence, enable; per-camera zone, "Inside the road %" and minutes in the zone editor. Not used by this rule: `parking_dwell` (60) and `parking_move_tolerance` (40) exist in Settings and are read only by `watch_all.py`.

---

## E. Functional requirements (what exists)

| Feature | Exists? | Implemented in |
|---|---|---|
| Authentication | JWT (`djangorestframework-simplejwt`): login returns access (30 min) + refresh (12 h, env `REFRESH_TOKEN_HOURS`); refresh tokens rotate and are blacklisted after use; logout blacklists the refresh token; login throttled 10/min | `V2\lookout_backend\lookout_backend\settings.py` (`SIMPLE_JWT`), `BE\views.py` (`LoginView`, `logout`), `BE\throttling.py` |
| Roles | `admin`, `dispatcher`, `officer`, `both` (officer & dispatcher) | `BE\models.py` `User.Role` |
| Web access (client-side gate) | Web login rejects `officer`; mobile login accepts only `officer` or `both`. The server itself does not block an officer token from other endpoints. | `V2\lookout\src\api.js`, `V2\officer_app\lib\api.ts` |
| Web pages by role | admin: dashboard, cameras, alerts, records, violators log, run detection (only if testing tools on), officers, settings, about. dispatcher: dashboard, cameras, alerts, records, violators log, about. both: dashboard, cameras, alerts, records, about. officer (if ever logged in on web): cameras, alerts, records, about | `V2\lookout\src\admin_dashboard.jsx` `ROLE_PAGES` |
| Server permissions | Default `IsAuthenticated`. Admin only: settings, monitor start/stop/state, detection jobs, uploads, register personnel, dispatcher list/delete, camera snapshot-edge frame. Admin write / others read: zones, violation types, cameras, officers. Any logged-in user: read and update alerts (assign, dismiss, resolve) and delete them (no permission class on `AlertViewSet`), create citations. A citation can be edited only by its filing officer while its alert is open. | `BE\views.py`, `BE\permissions.py` |
| Alerts CRUD | Create: detectors only (no API form). Read: list/detail with filters `status`, `type`, `camera`. Update: status, officers_assigned, notes. Delete: allowed by the viewset for any logged-in user (whether a UI button calls it was not checked). `accept` action for officers | `BE\views.py` `AlertViewSet` |
| Citations ("slips") | Create, read, update (limited as above). No delete. Filing a citation resolves its alert. Idempotent through `client_uuid` | `BE\views.py` `CitationViewSet`, `BE\models.py` `Citation` |
| Violators | Read-only list; fuzzy search; merge two records (`merge`). Created implicitly when a citation names a person. No update/delete endpoint | `BE\views.py` `ViolatorViewSet` |
| Cameras | Admin CRUD, snapshot proxy, road-edge frame upload; "-TEST" cameras are hidden from lists | `BE\views.py` `CameraViewSet` |
| Personnel | Officers: admin CRUD (delete also deletes the linked user). Dispatchers: list and delete. Registration with email OTP: `register_personnel`. | `BE\views.py` `OfficerViewSet`, `DispatcherViewSet`, `register_personnel` |
| Settings | Admin read/update, reset-to-spec-defaults per violation | `BE\views.py` `SystemSettingsView`, `reset_spec_defaults` |
| Users | Created with personnel registration; password change and forgot-password OTP; `must_change_password` flag | `BE\views.py` |
| Real-time processing | Live RTSP via the start button (supervised subprocesses); live processing view fed by `LOOKOUT_DEBUG_DIR`. Upload-a-video job: chunked, resumable upload (5 MiB chunks, 1 GB max, .mp4/.mkv/.avi), optional trim and "Recorded at" clock, run as a subprocess; alerts go to a `<CODE>-TEST` camera. Both admin only. | `BE\monitor.py`, `BE\views.py` (`UploadSessionViewSet`, `DetectionJobViewSet`), `V2\lookout\src\RunDetectionPage.jsx`, `chunkedUpload.js` |
| Notifications | Polling every 4 s from web and mobile; Possible and Likely (and parking) listed, Monitoring only on request. No WebSocket, no Expo/FCM push, no SMS, no email alert. | see C.14 |
| Email | Only one-time verification codes (registration, forgot password) through the Brevo HTTP API; code valid 10 minutes | `BE\mail.py`, `BE\views.py` (`CODE_EXPIRY_MINUTES`) |
| Dashboard / analytics | Overview page: four tiles (Pending Review, Assigned, Alerts Today, Total Alerts), live camera grid, recent violations feed. API `GET /api/dashboard/stats/` returns counts by status, alerts by type for 7 days, a weekly trend, cameras online, officers on duty. The web client does not draw charts: `recharts` is in `package.json` but no file imports it. | `V2\lookout\src\admin_dashboard.jsx`, `BE\views.py` `dashboard_stats` |
| Filters / records | Alerts page: filters and "Include Monitoring"; Records page lists closed (resolved/dismissed) alerts with status filter and search; Violators log with search, merge and violation-type filter | `V2\lookout\src\AlertFeed.jsx`, `RecordsPage.jsx`, `ResidentLog.jsx` |
| Exports | No CSV or PDF export found (no export/CSV/PDF/print code in `lookout\src`). Only "Save clip" (download of an evidence video) in `ViolationModal.jsx`. | grep of `V2\lookout\src` |
| Officer app | Login, assignments (active), history, profile, assignment detail with accept and citation filing/editing; tokens in `expo-secure-store`; polls every 4 s | `V2\officer_app\app`, `context\AssignmentContext.tsx`, `lib\api.ts` |
| Integrations | Ollama (Qwen3-VL); ngrok tunnels for Ollama and the camera (`V2\lookout_backend\ngrok.example.yml`, `VLM_ENDPOINT`, `STREAM_URL`); Render (API: `V2\render.yaml`); DigitalOcean app spec (`V2\.do\app.yaml`); Vercel/Netlify for the dashboard and Expo EAS for the app (`V2\DEPLOYMENT.md`, `eas.json`); PostgreSQL (`DATABASE_URL`) or SQLite (`USE_SQLITE`); Cloudinary or S3 for evidence; Brevo for email; ffmpeg. |
| SMS / Semaphore | **Not active.** No SMS code in `lookout_backend` or the clients; the only trace is an unused `SEMAPHORE_API_KEY` secret placeholder in `V2\.do\app.yaml` line 72. |
| Security | Passwords: Django default hasher (PBKDF2-SHA256; `PASSWORD_HASHERS` not overridden in `settings.py`); case-insensitive username backend that hashes on a miss to equalise timing (`BE\auth_backends.py`); Django password validators. Rate limits on login, OTP and password reset (`BE\throttling.py`). Roles as above. Access token kept only in a JS variable, refresh token in `sessionStorage` (web) or `expo-secure-store` (mobile). CORS allow-list only. |
| Audit / timeline | `Alert.timeline` (status changes, assigned, dismissed, resolved, reopened, with the actor), `reviewed_by`, `reviewed_at`, `reviewed_valid`; every alert stores the cue vector and the settings snapshot that produced it. No separate audit-log table. | `BE\timeline.py`, `BE\views.py` `perform_update` |
| Evidence retention | `evidence_retention_days` default 30; `purge_old_evidence` deletes images and clips and blanks their URLs, keeps the Alert rows; auto-purge default OFF; nothing runs it unless scheduled. | `BE\management\commands\purge_old_evidence.py`, `BE\models.py` |
| Data minimisation | No face or biometric processing. `recognition.py` states "There is no facial recognition in LookOut"; the Settings About page says the same. Mouth position comes from pose keypoints, not a face model. No insightface/ArcFace/embedding code remains (only comments and a test name). A frame is not blurred before the VLM (per spec) but stays on the local Ollama host. | `VIS\recognition.py`, `V2\lookout\src\AboutPage.jsx` |

---

## F. Hardware and software versions

| Item | Value | Source |
|---|---|---|
| GPU used for training and testing | NVIDIA GeForce RTX 4050 Laptop GPU, 6,141 MiB, driver 591.86 | `nvidia-smi` on this machine (read-only query) |
| PyTorch | 2.6.0+cu124 (installed here); CUDA 12.4 (`torch.version.cuda`) | `importlib.metadata` on this machine |
| Ultralytics | 8.4.67 (used for training: checkpoint `version`; installed here: 8.4.67). Not pinned in `requirements-detection*.txt`. | `merged_v2.pt`; pip metadata |
| torchvision / numpy / OpenCV / lap | 0.21.0+cu124 / 2.1.3 / 4.13.0.92 / 0.5.13 (installed here) | pip metadata |
| Python | `runtime.txt` and `.python-version` say 3.12.7; the Anaconda Python on this machine is 3.13.5 | `V2\lookout_backend\runtime.txt`, `.python-version` |
| Django | 6.0.6; DRF 3.17.1; simplejwt 5.5.1; django-cors-headers 4.9.0; django-filter 25.2; gunicorn 23.0.0; psycopg 3.2.13; whitenoise 6.8.2 | `V2\lookout_backend\requirements.txt` |
| Web dashboard | React ^19.2.6, react-router-dom ^7.17.0, Vite ^8.0.12, Tailwind ^4.3.0, recharts ^2.9.0 (unused) | `V2\lookout\package.json` |
| Officer app | Expo ~54.0.37, expo-router ~6.0.24, React Native 0.81.5, React 19.1.0, expo-secure-store ~15.0.8, expo-updates ~29.0.20 | `V2\officer_app\package.json` |
| CPU / RAM of the deployment PC | NOT FOUND (the memory comment mentions a "16 GB machine") | `VIS\recognition.py` `ClipRecorder.MAX_EDGE` comment |
| Camera | Hikvision DS-2CD1047G2 (name in code); mounting about 6 m, 2.8 mm lens is stated in a comment | `CMD\watch_merged.py`; `VIS\recognition.py` `TWO_WHEELER_LABELS` comment |

---

## G. Mismatches (docs versus code, and leftovers)

**Docs/specs that disagree with the code**
1. **Holdup time of day.** Spec v6 section 7 applies a multiplier (×0.56 to ×1.36) [`V2\docs\specs\LookOut_Scoring_Spec_v6.md`]; code adds 4–10 points (E20) and only with E10/E12; `holdup_scoring_v2.md` documents the code and says it supersedes spec section 7 [`VIS\theft.py`, `VIS\scoring.py`]. Spec v6 also does not list E11→E12 (+10).
2. **Holdup requirements.** Spec v6: "at least two people". Code: a second person within 1.75 holder-heights AND E10 or E12.
3. **Freeze values.** Written spec 3 s and speed 0.2; code 2 s and 0.3 (`theft.py` comments say so; `holdup_scoring_v2.md` explains).
4. **When the AI checker is called.** Spec v6 section 8: when the event enters Monitoring. Code: once, at first Possible/Likely [`CMD\_incidents.py`].
5. **VLM model.** Spec v6: "being finalised: 2B with video input through Transformers, or 4B with frames through Ollama". Code: Ollama, 2B-instruct (smoking/drinking) and 4B-instruct (holdup).
6. **Parking.** `LookOut_Scoring_Spec_v6.md` section 10 and `V2\LookOut_Scoring_Spec.md`: ≥ 50% past the edge for 5 minutes (Ordinance 601). Code defaults: 35% for 2 minutes [`parking_defaults.md` documents this on purpose]. The spec also says "no indicators"; the live view does show Monitoring/Possible/Likely for parking from progress, but the stored alert has no level.
7. **Knife confirmation.** Spec v6 text: the knife "must survive the vote and dwell windows". Code uses momentum + 0.5 s confirm + 2-of-5 persistence; the old vote/dwell gate is gone.
8. **`V2\docs\V6_GAP_PLAN.md`** cites `vlm.py`, `FRAME_COUNT = 3`, etc. `vlm.py` no longer exists in `core\vision`; the plan is stale.
9. **`V2\CLAUDE.md`** says SQLite; `settings.py` defaults to PostgreSQL (`DATABASE_URL` or `DB_*`) and uses SQLite only with `USE_SQLITE=True`. It also lists `watch_all` as the way to run all four; the live monitor actually runs `watch_merged` + `watch_parking`, and `watch_merged.py` documents `watch_all.py` as having bugs.
10. **`V2\DEPLOYMENT.md`** says the detector libraries include `insightface` and `onnxruntime`; they are not in any requirements file or imported.
11. **`requirements-detection.txt`** still lists `google-genai` and describes a Gemini Flash verifier "core/vision/vlm.py"; the code uses Ollama (no `google.genai` import found in `core`).
12. **`VIS\scoring.py` `HOLDUP_WEIGHTS`** (E14/E12/E10 only) differs from the live `theft.WEIGHTS` (adds E11E12, E20).
13. Settings listed but unused by the new scoring: `smoking_dwell`, `thief_dwell`, `drinking_dwell`, `drinking_held_dwell`, `parking_dwell`, `parking_move_tolerance`.
14. No document states per-class metrics for merged_v2, so none can be quoted from files (see A.6).

**Leftover code for removed features**
- *Facial recognition:* none left functionally. Mentions only in comments (`watch_smoking.py`/`watch_drinking.py` "insightface was ~150–400 ms", `recognition.py` calibration comment), a re-ID note in `theft.py` E19, and the stale docs in G.10. `V2\SYSTEM_FLOWCHART.md` (line 326) and `V2\FLOWCHART_COMBINED.md` (line 388) still draw InsightFace/ArcFace boxes.
- *SMS/Semaphore:* `V2\.do\app.yaml` line 72 `SEMAPHORE_API_KEY`. No code.
- *Curfew / noise / waste:* settings columns removed (migration `0045_remove_unused_settings_columns.py`); no `watch_curfew` command file; leftover comments in `BE\models.py`, `BE\views.py` line 1040 and four `watch_*` files; `CAP\Waste Detection` folder exists (not used by the app).
- *Snatching / carnapping / unattended property:* still coded in `VIS\theft.py` (E6–E9, E15–E19, E21–E22, `AnchorStore`, `PairState` snatch logic) and `VIS\recognition.py` (`CARRIABLE_CLASS_IDS`, `TWO_WHEELER_LABELS`) and tested in `BE\tests_layer_e.py`; inert in the live pipeline (C/D.3). The word "snatch/carnapping" appears in `ai_checker.kind_for` (mapped to holdup).
- *Old standalone models:* `drinking.pt`, `smoking.pt`, `smoking_v5.pt`, `thief.pt` in `VIS`; commands `watch_smoking`, `watch_drinking`, `watch_thief`, `watch_all`, `watch_smoking_pose` still exist but now load the same merged model.
- *Unused front-end dependency:* `recharts`.
- *Empty/duplicate data:* `CAP\runs\detect\val`, `val-2` are empty; `smoking_v4_finetune` has no results file.
