# LookOut detection settings snapshot — 2026-10-29

Taken read-only before accuracy alpha testing. Machine-readable twin: `settings_snapshot_2026-10-29.json`
(every module-level constant of the detection code, every `SystemSettings` column, camera rows, model hashes,
and a SHA-256 of each detection source file).

| | |
|---|---|
| Git commit | `37cbead52ab51c0faf9f49c179025a4dc4315600` (2026-10-28 21:15 +08) |
| Branch | `feat/v6.2-location-momentum` |
| Tracked files modified | **none** |
| Untracked (not detection code) | `alpha_eval/`, `docs/DETECTION_RULES_FOR_MANUSCRIPT.md`, this folder |
| Scoring spec | `v6.2.11` (`scoring.SCORING_SPEC_VERSION`) |
| ultralytics / torch | 8.4.67 / 2.6.0+cu124, CUDA available, NVIDIA GeForce RTX 4050 Laptop GPU (as seen by the interpreter that produced this snapshot — **UNCERTAIN** that `runserver` uses the same interpreter; see §5) |
| `LOOKOUT_*` env overrides set | none in the snapshot shell (they would override imgsz, momentum, confirm/cue-hold, model path) |

---

## 0. Values that differ between the Settings page and Run Detection

1. **Parking area thresholds are NOT the Settings-page values.** Run Detection sends its own "inside the road %" and "for N minutes" (UI defaults **50 %** and **5 min**, `RunDetectionPage.jsx:98-99`) and writes them onto the `CAM-PARKING-TEST` / `CAM-MERGED4-TEST` camera before launch (`views.py:1951-1957`). The live camera `CAM-SMOKE-01` has 35 % / 2 min. Whatever is typed in the Run Detection boxes is what the run uses. Only `parking_confidence` (35) comes from Settings.
2. **`parking_dwell` (60) and `parking_move_tolerance` (40) are used by nothing in Run Detection** (only `watch_all`, `watch_all.py:457-458`). Changing them on the Settings page has no effect on a parking run.
3. **Smoking-only run uses a different inference path than the Merged runs.** `Smoking` is launched with `--cascade` (person-crop pass, no tiling; `views.py:1976`). `Merged (All 3/4)` runs whole-frame + 2×2 tiles + 2× person-crop upscale **plus** the cigarette cascade. Same weights and the same Settings confidences, but a different set of passes, so a clip can score differently under "Smoking" vs "Merged".
4. **Time of day is not the Settings page's concern for uploads.** It comes from the "Recorded at" box (see §1.4). Without it, drinking's evening cue uses the machine's wall clock = the moment you press Start.
5. **Drinking "ordinance hours"** (`drinking_hours_enabled`, currently **0 = off**) would use the **wall clock even for a file** (`watch_drinking.py:714-720`). It is off, so no effect. If anyone switches it on, uploaded clips would be skipped outside the window. Keep it off for testing.
6. **Disposal on Run Detection with no area drawn does not clear the `CAM-DISPOSAL-TEST` area** left by a previous disposal run (the camera row is only rewritten when an area/threshold is sent, `views.py:1922`). No such camera row exists today, so the first disposal run is clean; later runs can inherit an old area.
7. Everything else (all thresholds, toggles, cooldowns, VLM settings, disposal parameters) is read from the Settings row, with no CLI overrides (Run Detection passes only `--source --camera [--cascade --start --end --clock]`).

---

## 1. What Run Detection actually uses (with evidence)

### 1.1 Start button → detector
`startStagedDetectionJob` (`lookout/src/api.js:536-565`) → `POST /api/detection-jobs/` → `DetectionJobViewSet.create` (`lookout_backend/core/views.py:1781`) → `subprocess.Popen([python, manage.py, <command>, --source <clip>, --camera CAM-<TYPE>-TEST, …])` (`views.py:2043-2049`). Command map `DETECTION_COMMANDS` (`views.py:1115-1136`):

| Run Detection choice | Command | Notes |
|---|---|---|
| Smoking | `watch_smoking --cascade` | `views.py:1976`; `--cascade` ⇒ `far=False` (`watch_smoking.py:324-325`) |
| Drinking | `watch_drinking` | far tiling default on |
| Holdup | `watch_thief` | |
| Parking | `watch_parking` | auto-traces a zone if none drawn (`watch_parking.py:252,361`) |
| Disposal | `watch_disposal` | separate pipeline |
| Merged (All 3) | `watch_merged` | smoking + drinking + holdup from one `merged_v2` pass; cascade on, far on (`watch_merged.py:244-247`) |
| Merged (All 4) | `watch_merged_all` | + parking; **refuses to start without a drawn area** (`views.py:1894`) |

### 1.2 Thresholds: DB vs CLI vs code
No threshold CLI flags are passed. Each detector calls `SystemSettings.load()` at start and every 5 s (`SETTINGS_REFRESH_SECONDS = 5`, e.g. `watch_smoking.py:715-779`), so **editing the Settings page during a run changes that run**. Code constants (§2) are the fixed part; spec timings (confirm 2.0 s, cue hold 5.0 s, monitoring min 3.0 s, puff count/window, loiter, etc.) are also DB columns applied by `apply_spec_settings` (`_incidents.py:195-216`; env `LOOKOUT_CONFIRM` / `LOOKOUT_CUE_HOLD` would override — none set).

### 1.3 Time handling for uploaded clips — wall-clock bug is **fixed**
Every detector uses the video position for a file: `reader.get(cv2.CAP_PROP_POS_MSEC)/1000` — smoking `watch_smoking.py:794`, drinking `watch_drinking.py:734`, thief `watch_thief.py:789`, parking `watch_parking.py:657`, merged `watch_merged.py:498`, disposal `watch_disposal.py:379`. Dwell, vote, cooldown, dedup, clip cutting and the trim all use it. Wall clock is used only to re-read Settings, and for the one disabled exception in §0.5. The one residual: the AI checker runs asynchronously on real time (display only).

### 1.4 Time-of-day rules for uploaded clips
Source: `core/vision/clock.py` — with a "Recorded at" the clock = `recorded_at + video position`; without it the wall clock (when you ran it).
- **How to set it:** Run Detection page → "Recorded at" datetime box (`RunDetectionPage.jsx:494`), local Manila time of the *first frame of the clip* (if you trim, still enter the clip's own start; the clock is `start + position` in the file, `--start` seeks but position stays absolute). It is sent as `--clock "YYYY-MM-DD HH:MM"` (`views.py:1997-2009`) and **only** for Holdup, Drinking, Merged, Merged-4, Disposal (`RunDetectionPage.jsx:242`). Smoking and Parking have no time-of-day rule.
- **Holdup** (E20 points 1–10 by 3-hour Manila block, `scoring.MANILA_HOUR_BLOCKS`): with no Recorded at → E20 **OFF** (0 pts) and a warning (`watch_thief.py:1028-1033`).
- **Drinking** evening band 16:00–24:00 (5 pts): with no Recorded at → **upload-time wall clock** (`clock.py:36-40`). *Fill it in for every drinking/merged clip.*
- **Disposal** collection window 04:00–07:00 Mon–Sat: with no Recorded at → rule **skipped** with one warning (`watch_disposal.py:756-769`).

### 1.5 Trim
Respected. `trim_start`/`trim_end` → `--start/--end` (`views.py:1993-1996`); every watcher (`smoking, drinking, thief, parking, merged, disposal`) seeks to `start` and stops when the video position ≥ `end` (`core/vision/trim.py:63-79`).

### 1.6 AI checker
ON if `vlm_enabled=1` and Ollama answers (`_incidents.py:55-83`). Ollama is up at `localhost:11434` with `qwen3-vl:2b-instruct` and `qwen3-vl:4b-instruct` pulled (digests in JSON). It is **display-only**: `store_ai_result` only updates `Alert.ai`; it never touches score, status or level (`_incidents.py:94-103, 218`). Smoking/drinking use `vlm_model` (2b); holdup uses `vlm_model_holdup` (4b).

### 1.7 Parking zone
- A drawn polygon (or the **Trace zone** button's suggestion, which the operator accepts on the canvas) is written to `CAM-PARKING-TEST.edges` on **every Start**; with none drawn the field is reset to `{}` (`views.py:1936-1943`), so the previous run's zone is never inherited. pct/minutes are overwritten too.
- With `{}` the detector auto-traces from the clip itself (`watch_parking.py:361,412-418`): vehicle conf 0.4, `TRACE_IMGSZ 640`, ≤120 samples every 0.4 s, tracks must be seen ≥3 times and move ≥1.0 box-widths, ≥12 traffic points. The traced zone is **not saved** on a `-TEST` camera (`watch_parking.py:453`).
- Merged (All 4) never auto-traces; it needs a drawn area.
- Current DB: no `CAM-PARKING-TEST` row yet. `CAM-MERGED4-TEST` holds a leftover zone (9 points, 2560×1440, 25 %, 1.0 min) that the next Merged-4 Start overwrites.

---

## 2. Current values

Legend: **DB** = SystemSettings column (Settings page), **CODE** = module constant, **CLI** = command default.

### 2.1 Models
| Role | File | Bytes | SHA-256 |
|---|---|---|---|
| merged_v2 (cigarette / bottle / knife) — `recognition.MODEL_PATH` | `lookout_backend/core/vision/merged_v2.pt` | 6,235,555 | `8e9c77665b243ce8aebe9457ca2bc4f9fd11f09d0d88e406789d42172d2e8bbb` |
| Person + vehicle + carriables (yolov8n COCO) | `lookout_backend/core/vision/yolov8n.pt` | 6,549,796 | `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36` |
| Pose (yolov8n-pose) | `lookout_backend/yolov8n-pose.pt` | 6,832,633 | `c6fa93dd1ee4a2c18c900a45c1d864a1c6f7aba75d84f91648a30b7fb641d212` |
| YOLO-World (disposal) | `yolov8s-worldv2.pt` (`disposal.DEFAULT_WORLD_MODEL`, auto-download) | **UNCERTAIN** | **Not on disk yet.** It downloads on the first disposal run. Hash it after that run (§4) and add it here. Prompt cache dir `core/vision/world_cache` does not exist yet. |
| Qwen (AI checker) | Ollama `qwen3-vl:2b-instruct` | 1,930,039,258 | digest `ff304a477a01ad3771ecdabc5edbd40da7070efff96f58a553a29c30aa920c8c` |
| | Ollama `qwen3-vl:4b-instruct` | 3,335,885,370 | digest `ef33995bb2ac37689e49c1a15cd627354d05a3c21d1f4bdcd6a5c83825349c1f` |

Where the pose and person weights load from (relative names `yolov8n.pt` / `yolov8n-pose.pt`) may resolve against the working directory (`lookout_backend/`, which also has `yolov8n.pt` byte-identical to the vision copy). Legacy `smoking.pt`, `smoking_v5.pt`, `drinking.pt`, `thief.pt` exist but **all detectors point at `merged_v2.pt`** (`recognition.py:546-547`); hashes are in the JSON.

### 2.2 Detection confidences
| Item | Value | Source |
|---|---|---|
| Smoking confidence (cigarette/vape) | **30 %** | DB `smoking_confidence` |
| Drinking confidence (bottle) | **35 %** | DB `drinking_confidence` |
| Holdup confidence (knife) | **30 %** | DB `thief_confidence` |
| Parking confidence (vehicle) | **35 %** | DB `parking_confidence` |
| Disposal confirm (YOLO-World bag score) | **15 %** (Likely) | DB `disposal_confidence` |
| Disposal suspect-person conf | 25 % | DB `disposal_suspect_confidence` |
| Class-floor scales | cigarette/vape/smoking ×1.0, smoke/vapor ×1.5, knife ×1.0 (dwell scale knife 1.0, ratio 0.25, stale 6.0) | CODE `watch_smoking.CLASS_POLICY`, `watch_thief.CLASS_POLICY` |
| Merged model inference floor | min of the active engines' confidences = 30 % | `watch_merged.py:581` |
| Person detection conf | 0.5 | CODE `recognition.detect_persons(_tracked)` default |
| Pose detection conf / keypoint min conf | 0.4 / 0.3 | CODE `detect_pose`, `KP_MIN_CONF` |
| Vehicle detection conf | 0.4 | CODE `detect_vehicles_tracked` default (parking passes cfg 35 % via `_run_zone`) |
| Knife spatial gate | head exclusion 0.15, max height 0.25 of frame, max wrist distance 0.22; persist 2 hits in 5 frames, memory 3.0 s | CODE `watch_thief.py` |
| Drinking mouth proximity | 3.0 face widths (DB); smoking mouth proximity 2.5 (CODE) | DB `drinking_mouth_proximity`, `watch_smoking.MOUTH_PROXIMITY` |

### 2.3 Small-object inference (CUDA present)
`NEAR_IMGSZ = 960` (640 if no CUDA), `FAR_IMGSZ = 960`, `CASCADE_IMGSZ = 1280` (defined; watchers pass `crop_imgsz=NEAR_IMGSZ`), `VEHICLE_IMGSZ = 1280`, `POSE_CROP_IMGSZ = 640`, pose crop pad 0.10. Far mode: tiles **2×2**, overlap **0.2**, person-crop upscale **2.0×** (`_detect_far`), merged with `_nms`. Cascade: person-crop pad **0.35**. Smoking run = cascade only (no tiles); Merged = whole frame + tiles + person crops + cascade. NMS IoU: see `recognition._nms` (**UNCERTAIN** — not extracted; hash of `recognition.py` is in the JSON). Preprocess/sharpen: off (no `--preprocess`).

### 2.4 Trackers
- ByteTrack (ultralytics 8.4.67 default `bytetrack.yaml`, sha in JSON): `track_high_thresh 0.25`, `track_low_thresh 0.1`, `new_track_thresh 0.25`, `track_buffer 30` frames, `match_thresh 0.8`, `fuse_score True`. Both `--tracker bytetrack` defaults.
- Person track layer (`tracking.py`): `TRACK_MATCH_IOU 0.3`, `TRACK_MATCH_DIST 0.8`, `TRACK_MAX_GAP 2.0 s`, tombstone `10 s` / match dist `1.2`, `ASSIGN_REACH 0.45`, `SCENE_MATCH_DIST 1.5`, `SCENE_MAX_GAP 2.0`, `STATIONARY_DIST 0.6`, `ACCRUAL_STALE_SECONDS 0.5`, vote window 5.0 s / ratio 0.4 / min frames 2, group cluster dist 1.8 / match 1.2 / max gap 3.0 / presence grace 3.0 / tombstone 15 s. Full list: JSON `code_constants["vision/tracking.py"]`.

### 2.5 Object momentum
`decay 0.90`, `on 1.5`, `off 0.4`, `max 3.0`, `hold 0.0`; **knife hold 1.0 s**, `KNIFE_CONFIRM_SECONDS 0.5` (`momentum.py`, `_incidents._momentum_book`). Confirmation after cue ON: `object_confirm_seconds` **2.0 s** (DB). Location-aware momentum master switch `True`; gated classes cigarette, bottle; `CLOSE_SHOULDER_FRAC 0.5`, `JUMP_HEIGHT_FRAC 0.25`, `JUMP_MEMORY_SECONDS 2.0`, `CONF_FULL_AT 0.6`, `KNIFE_PRESENT_SECONDS 2.0`, etc. (77 constants in JSON `vision/location_momentum.py`).

### 2.6 Scoring engine (all three person-based violations)
Status thresholds: **Monitoring** = object cue on, score < 0.55; **Possible (warning) ≥ 0.55**; **Likely (violation) ≥ 0.75**; hysteresis drop 0.05 (`scoring.py:49-54`). Points and thresholds are fixed by spec, not settings.

| Violation | Indicator | Points |
|---|---|---|
| Smoking (total 1.10, capped 1.00) | cigarette (GATE) 0.40; gesture/1 puff 0.20; 2+ puffs 0.20; 3+ puffs in window 0.15; near-mouth 0.15 (needs cigarette) | |
| Drinking (total 0.85) | bottle (GATE) 0.40; gathering 2+ 0.10; stayed ≥ duration 0.15; at-mouth 0.15 (needs bottle); evening band 0.05 | |
| Holdup (total 0.75 + E20) | E14 weapon present (GATE) 0.45; E12 two people frozen close 0.20; E10 loitering first 0.10; E20 time-of-day 1–10 points added (peak 15–18 h = 10, quietest 06–09 h = 4), counted only after E10/E12 corroborate | |

Puff caps: Possible needs ≥1 puff in 10 s; Likely needs ≥2 puffs in 30 s; no-puff cap otherwise (`location_momentum.py`). Holdup rule caps: Possible needs a frozen pair, etc. (full constants in JSON).

Rule distances/times, with current values:
- **Smoking:** `smoking_puff_count 3`, `smoking_puff_window_minutes 5.0` (DB); `cue_hold_seconds 5.0` (DB); `smoking_dwell 3 s` (DB); mouth proximity 2.5 face widths; `PUFF_MIN_CYCLES 1`; `PUFF_RAISED_RATIO 1.5`, `PUFF_LOWERED_RATIO 3.0`; `HAND_RAISED_RATIO 0.7`, `HAND_LOWERED_RATIO 1.3`; `PUFF_PAIR_WINDOW_SECONDS 5.0`; `PUFF_MIN_GAP_SECONDS 1.0`; `PUFF_CLOSE_SHOULDER_FRAC 0.7`; `PUFF_ELBOW_SHOULDER_FRAC 1.1`; far-puff head zone 0.22, body zone 0.30–0.70, pair 4.0 s, gap 2.0 s; walking-blocks-puff OFF.
- **Drinking:** `drinking_dwell 8 s`, `drinking_held_dwell 24 s`, `drinking_group_duration 600 s`, `drinking_min_group 2`, evening band `16:00–00:00`, `drinking_cooldown_center_dist 1.5`, `drinking_evidence_max_age 12`, `drinking_mouth_proximity 3.0`, hours gate `drinking_hours_enabled 0` (DB); group cluster dist 1.8 persons; `STATIONARY_HEIGHT_FRAC 0.10`, `GROUP_AREA_EXPAND 1.2`.
- **Holdup:** `holdup_loiter_seconds 20`, `holdup_near_person_heights 1.75` (DB); `FREEZE_SECONDS 2.0`, `FREEZE_REACH 1.0`, `FREEZE_SPEED 0.3`, `WEAPON_REACH 0.5`, `E12_E14_LEAD/TRAIL 3.0 s`, `CLOSE_FROM 3.0`, `CLOSE_TO 1.0`, `CLOSE_SECONDS 3.0`, `E11_E12_WINDOW 5.0`, `LOITER_MEMORY 60 s`, relink 3.0 s / 0.5 heights, `CLOSE_STAY_SECONDS 2.0`, pass-through rule (moving > 0.3, before 1.0 s, separate 2.0 s), `HOLDUP_MIN_PERSONS 2`, night = 22:00–05:00 (E16 only), shadow/fake-person filter on; crowd soft/hard 6/10.

### 2.7 Parking
| Value | Current | Source |
|---|---|---|
| Share of vehicle footprint inside zone to start counting | **Run Detection box (UI default 50 %)**; live camera 35 % | camera `obstruction_pct` ← Run Detection |
| Release share (hysteresis) | 0.10 below enter (`FRACTION_HYSTERESIS`) | CODE |
| Stopped test | centre drift ≤ 0.15 box-widths over 3.0 s | CODE `STATIONARY_DIST/WINDOW` |
| Time limit | **Run Detection box (UI default 5 min)**; live camera 2 min (`ALERT_SCORE 120 s` code default) | camera `obstruction_minutes` |
| Moving weight | 0.0 (only stopped time counts) | CLI default `--moving-weight` |
| Drain while detected outside zone | 2.0 s per s | `DECAY_PER_SEC` |
| Drain while undetected | 0.5 s per s after grace | `MISSING_DECAY_PER_SEC` |
| Presence grace | 2.0 s | `PRESENCE_GRACE` |
| Max dt / forget / adopt | 2.0 s / 30 s / 1.2 widths, IoU ≥ 0.55 | CODE |
| Cooldown | 120 s, centre dist 1.5 widths | CODE `COOLDOWN_SECONDS`, DB `alert_cooldown` is separate |
| Footprint samples | 21 | CODE |
| Settings page values not used by Run Detection | `parking_dwell 60`, `parking_move_tolerance 40` | DB |

### 2.8 Abandoned waste (disposal)
Code constants (`disposal.py`) vs the DB columns that override them at run time:
| Value | Run uses | Source |
|---|---|---|
| Processing width | 960 px | CODE `PROC_WIDTH` |
| Warm-up | **5 s** | DB `disposal_warmup_seconds` |
| Warm-up / long / short MOG2 rates | 0.05 / 0.0005 / 0.02 | CODE |
| Foreground threshold | 200 | CODE |
| Static time | **5 s** | DB `disposal_static_seconds` (code default 2.0 s) |
| Size limits | **0.08 % – 8.0 %** of frame | DB `disposal_min_area` / `disposal_max_area` |
| Match dist / grace | 40 px / 2.0 s | CODE |
| Owner radius | **80 px** | DB |
| Owner away / max hold | **5 s / 60 s** | DB (code defaults 3.0 / 3.0) |
| Ignore movers | on | DB |
| Re-check rejected | **10 s** | DB |
| YOLO-World prompts (garbage) | plastic bag, garbage bag, trash bag, pile of trash, sack, cardboard box, rice sack, styrofoam, pile of plastic bottles | CODE `PROMPTS` |
| Reject prompts | person, dog, cat, motorcycle, car, bicycle, chair, trash can, garbage bin, bucket, potted plant | CODE `REJECT` |
| Confirm / weak predict floor | **0.15** (DB 15 %) / 0.05 | DB / CODE `WEAK_PREDICT_CONF` |
| Garbage-hit combining | noisy-OR, group IoU 0.5 | CODE |
| Cooldown | **120 s**, distance **60 px** | DB `disposal_cooldown`, `disposal_cooldown_dist` |
| Occlusion hold / pin max / presence recheck / miss limit | 20 s / 1800 s / 15 s / 2 | CODE |
| Collection window | **on, 04:00–07:00, Mon–Sat (days 0–5)** | DB |
| Suspect person look-back | 3.0 s before / 1.0 s after, ≤ 24 frames | CODE |

### 2.9 Dedup, cooldown, minimum event, evidence
- Alert cooldown (smoking/drinking/holdup): `alert_cooldown` **120 s** (DB). Spatial cooldown distance 1.5 box heights (smoking `COOLDOWN_CENTER_DIST`), holdup `COOLDOWN_IOU 0.3`.
- Same event continued on the same Alert row if the subject reappears within `CONTINUE_SECONDS 15`, `CONTINUE_DIST 0.8`, size ratio 0.45–2.2 (`_incidents.py:32-35`); end grace 3.0 s; row update throttle 5.0 s; status event gap 5.0 s; clip refresh 15 s.
- Minimum event length: `monitoring_min_seconds` **3.0 s** (a Monitoring event shorter than this that never rose is dropped).
- Evidence clip: annotated `ClipRecorder` 30 s; raw clip from the file = **15 s before + 15 s after** the alert (`RAW_CLIP_PRE/POST_SECONDS = 15`); disposal cuts 5 s before the drop. `evidence_retention_days 30`, `evidence_auto_purge 0`.
- AI checker: 8 frames, edge 640, 5.0 s before / 1.0 s after trigger, timeout 120 s, async on, endpoint `http://localhost:11434`.

### 2.10 Settings-page toggles (current)
`smoking_enabled 1`, `drinking_enabled 1`, `thief_enabled 1`, `parking_enabled 1`, `disposal_enabled 1`, `drinking_hours_enabled 0`, `disposal_collection_enabled 1`, `disposal_ignore_movers 1`, `vlm_enabled 1`, `vlm_async 1`, `evidence_auto_purge 0`, `auto_start_detection 0`, `show_testing_tools 1`. A disabled detector makes the run loop idle without creating alerts, so confirm all five are enabled before each session (they are now). All remaining SystemSettings columns are in the JSON under `db_systemsettings` (last saved `2026-10-10 01:15:04`).

---

## 3. Things I could not confirm (UNCERTAIN)
- `yolov8s-worldv2.pt` hash and size (not downloaded yet).
- Which Python interpreter `runserver` uses, and whether it sees CUDA. If it does not, `NEAR_IMGSZ/FAR_IMGSZ` fall to **640** and results change materially. Run `python -c "import torch;print(torch.cuda.is_available())"` with the same interpreter as `runserver`.
- NMS IoU inside `recognition._nms` (not extracted; covered by the source hash).
- Whether ultralytics resolves `yolov8n.pt` / `yolov8n-pose.pt` from `core/vision/` or from `lookout_backend/` at run time (both `yolov8n.pt` copies are identical).

## 4. Re-check before each testing session
Ask Claude Code, from the repo root:

> Compare the current detection values against `docs/accuracy_testing/settings_snapshot_2026-10-29.json`. Read-only. Check: git commit and tracked-file changes, SHA-256 of the three model files and every file listed in `source_file_sha256`, every column in `db_systemsettings`, the `db_cameras` rows for `CAM-*-TEST`, the Ollama model digests, and the module constants in `code_constants`. List every difference, and nothing else.

Cheap manual checks: `git status --short` (no ` M` lines) and `git rev-parse HEAD` = `37cbead…`; Settings page unchanged since `updated_at 2026-10-10 01:15:04` (the `updated_at` of the settings row, which moves on every save).
After the first disposal run, add the `yolov8s-worldv2.pt` hash to the JSON and re-snapshot.

## 5. Things the system changes by itself during a run
- **Start overwrites `CAM-PARKING-TEST` / `CAM-MERGED4-TEST`:** edges, frame size, inside-% and minutes, every time (`views.py:1922-1957`). The previous clip's zone and thresholds do not carry over; type them again per clip.
- **Disposal area carry-over** (§0.6): `CAM-DISPOSAL-TEST.edges` is not cleared when a disposal run starts with no area drawn.
- `CAM-<TYPE>-TEST` camera rows and ViolationType rows are created if missing.
- Settings are re-read every 5 s, so a Settings edit mid-run takes effect.
- A parking auto-trace is never saved on a `-TEST` camera, so it does not persist.
- Nothing writes to SystemSettings from a detector.
- Alerts, evidence files (`media/violations/`), the staged clip and its `.log` are created per run; `evidence_auto_purge` is off, so nothing is purged.
