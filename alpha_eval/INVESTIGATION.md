# INVESTIGATION — how LookOut's detection works (read-only findings)

Backend inspected: `C:\Users\User\OneDrive\Desktop\LookOut_v2\lookout_backend` (commit `e6af550…`, working tree has
unrelated uncommitted edits by the team; alpha_eval changes nothing there). `watch_merged.py` and `watch_merged_all.py`
both exist in `core/management/commands/`.

## 1. Structure of `watch_merged`

| Question | Answer |
|---|---|
| Engine to drive on a file | `watch_merged_all.Command` (subclass of `watch_merged.Command`). It runs smoking + drinking + holdup off ONE merged-model pass (`merged_v2.pt`) and adds **parking** as `EXTRA_ENGINES=("parking",)`. `watch_merged.Command` alone has no parking, so alpha_eval drives `watch_merged_all` (all four violation types in scope). |
| Per-frame loop | `watch_merged.py:_run` (L402-543): `reader.read()` → `now = reader.get(CAP_PROP_POS_MSEC)/1000` (L498, file sources) → `_process_frame` (L547) → `_extra_frame` (parking, `watch_merged_all.py:144`) then `_merged_pass` (L563). Every frame of a file is read; there is no frame skipping for files (`LatestFrameReader` is only used when `is_live`). |
| Where status changes | `core/management/commands/_incidents.py:IncidentMixin._incident_sync` (L424). First row: `create(level, …)` (L469). Later changes: `_incident_write` (L554) → `Alert.objects.filter(pk).update(level=…, peak_level=…)`. Levels: `scoring.MONITORING/WARNING/VIOLATION` = Monitoring/Possible/Likely (`core/vision/scoring.py` L36-61). |
| Where alerts are created | `Alert.objects.create(...)` in `_create_alert` of `watch_smoking.py:1040`, `watch_drinking.py:1236`, `watch_thief.py:1198`, `watch_parking.py:762`. Smoking/drinking/holdup are created via `_incident_sync(create=…)`; **parking creates one alert directly** from `ObstructionZone.update()` hits (`watch_parking.py:_run_zone` L583-616) and has no Monitoring/Possible stages (fires once, at the threshold). |
| AI checker | Armed in `_incident_sync` (L486-489), fired once when the event first reaches Possible/Likely (L540-543) → `_ai_trigger` (L235) → `ai_checker.check_async` (`core/vision/ai_checker.py:469`) which does `time.sleep(FRAMES_AFTER=1.0)` then crops `ring.window(t0)` and calls Ollama; result lands in `_incidents.store_ai_result` (L94) → `Alert.objects.filter(pk).update(ai={...})`. The verdict/suggested status are *derived* at read time by `ai_status.ai_context` / `suggest_status` (not stored). |
| Evidence | Image: `cv2.imwrite` in `_create_alert`; clips: `_save_clips` (`watch_smoking.py:968`) → `ClipRecorder.save` + `recognition.cut_raw_clip` (ffmpeg) into `MEDIA_ROOT/violations`. |
| SystemSettings | `SystemSettings.load()` (`models.py` ~L765, `get_or_create(pk=1)`); read at start (`_run` L430), **re-read every 5 wall-seconds** (L477), and by `ai_setup`. `apply_spec_settings` re-applies only if `updated_at` changed. |

## 2. Video time and every timer

* The frame clock is `cap.get(cv2.CAP_PROP_POS_MSEC)/1000` (`watch_merged.py:498`), copied to `cmd._video_pos_sec` (L511).
  Every rule layer receives that `now` (momentum, dwell/confirm `object_confirm_seconds`, `cue_hold`, END_GRACE 3 s,
  CONTINUE 15 s, drinking `group_duration`, cooldowns, parking `ObstructionZone.update(…, now_ts)`, tracker expiry).
  LookOut already uses **content time for files**; the wall clock matters only in these places:
  1. `watch_merged.py:431/477` settings refresh (`time.time()`); harmless once `SystemSettings.load` is deterministic.
  2. `ai_checker.py:478` `time.sleep(FRAMES_AFTER)` and `:406/418` `time.monotonic()` (latency only). **Async thread, wall-time dependent**: the frames Qwen sees depend on how many frames were processed during the sleep → not repeatable. Handled by the deferred-check scheduler (see DECISIONS D6).
  3. `watch_thief.py:1017` `_clock_now` → wall clock Manila time **when no `--clock` is given** → holdup time-block multiplier (night) would depend on when the run is processed. Same for the drinking evening band (`scoring.DRINKING_HIGH_BAND`). Handled by passing `--clock` (D5).
  4. `datetime.now()` / `timezone.now()` in filenames and Alert timestamps only — no logic.
* The pure-vision modules (`tracking.py`, `theft.py`, `momentum.py`, `obstruction_zone.py`, `scoring.py`) use no wall clock at all.

## 3. Internal names

Violation `ViolationType.code`: `smoking`, `drinking`, `theft` (= **Holdup**), `parking` (= Parking Obstruction). Engine names in
`watch_merged`: `smoking`/`drinking`/`thief`. Event identity: the `Alert` row (pk) — one row per incident, keyed in memory by
`(kind, track_id)` (`("track", id)` or `("cluster", id)`); a track-id switch continues the same Alert (`_incident_continue` L367).

## 4. Bounding boxes

`_incident_sync(key, score, now, …, box=…)` receives the person (smoking/holdup) or cluster box on every call; parking hits carry
`hit["box"]` and `hit["track_id"]`. alpha_eval wraps `_incident_sync` to remember `(key, box, now)` for the Alert writes that happen inside it.

## 5. Async AI checker on files

Real thread (`daemon=False`) per call; waits `FRAMES_AFTER` wall seconds. Pending calls finish after the loop ends (process joins
non-daemon threads). alpha_eval replaces scheduling only (not the check itself): the call is deferred until video time ≥ t0+FRAMES_AFTER (or EOF), then the original `check_async(wait=True)` runs it, so the frame ring holds the same frames every time.

## 6. Parking polygon

`Camera.edges` / `edges_width` / `edges_height` (JSON polygon in pixels) of the `--camera` record, else `--zone` JSON
(`draw_zone`). **The installed camera `CAM-SMOKE-01` has `edges={}` in the DB** (only the test camera `CAM-MERGED4-TEST` has one), and
`core/vision/zones/obstruction_zone.json` does not exist. So alpha_eval uses its own zone file `alpha_eval/parking_zone.json` (seeded
from `lookout_backend/zone_mine.json`, a normalised polygon drawn on the 2560×1440 installed view) via `--zone`. See D8.

## 7. Threshold settings

* Parking: not a SystemSettings field. `--alert-score` (seconds) overrides `camera.obstruction_minutes*60` (`watch_parking._load_zone` L~245). alpha_eval passes it from the Set.
* Drinking group duration: `SystemSettings.drinking_group_duration` (default 600), read from `cfg` each frame in `watch_merged._process_drinking_frame` (L728). alpha_eval overrides it **in memory** on the loaded object.
* Others used (all captured in each run's snapshot): `{smoking,drinking,thief,parking}_{enabled,confidence,dwell}`, `drinking_min_group`,
  `drinking_held_dwell`, `drinking_evidence_max_age`, `drinking_mouth_proximity`, `drinking_cooldown_center_dist`, `alert_cooldown`,
  `object_confirm_seconds`, `cue_hold_seconds`, `monitoring_min_seconds`, `smoking_puff_*`, `holdup_*`, `vlm_*`.

## 8. Things that would have written to LookOut's DB

`Alert.objects.create/update/delete`, `Camera/ViolationType.get_or_create` (SELECT only when the rows exist; they do). alpha_eval swaps
the `Alert` name in the five modules for an in-memory store, opens the DB `mode=ro`, and still installs the `execute_wrapper` write block as a safety net.
