# Accuracy Freeze — Alpha Testing Baseline

- **Frozen at:** 2026-10-30 18:31 +0800
- **Branch:** feat/v6.2-location-momentum
- **Model:** `lookout_backend/core/vision/merged_v2.pt`
- **merged_v2.pt SHA-256:** `8e9c77665b243ce8aebe9457ca2bc4f9fd11f09d0d88e406789d42172d2e8bbb`
- **Bottle crop pass:** ON (default). `watch_merged` only skips it with `--no-bottle-crops`; neither the Run Detection launcher (`views.py`) nor `monitor.py` passes that flag.

## SystemSettings (read-only snapshot, row id=1, updated_at 2026-10-10 01:15:04 UTC)

| Setting | Value |
|---|---|
| parking_enabled | True |
| parking_confidence | 35 |
| parking_dwell | 60 |
| parking_move_tolerance | 40 |
| smoking_enabled | True |
| smoking_confidence | 30 |
| smoking_dwell | 3 |
| thief_enabled | True |
| thief_confidence | 30 |
| thief_dwell | 3 |
| drinking_enabled | True |
| drinking_confidence | 35 |
| drinking_dwell | 8 |
| drinking_hours_enabled | False |
| drinking_start | 16:00:00 |
| drinking_end | 00:00:00 |
| drinking_min_group | 2 |
| drinking_group_duration | 600 |
| drinking_held_dwell | 24 |
| drinking_evidence_max_age | 12 |
| drinking_mouth_proximity | 3.0 |
| drinking_cooldown_center_dist | 1.5 |
| disposal_enabled | True |
| disposal_confidence | 15 |
| disposal_static_seconds | 5 |
| disposal_cooldown | 120 |
| disposal_cooldown_dist | 60 |
| disposal_ignore_movers | True |
| disposal_min_area | 0.08 |
| disposal_max_area | 8.0 |
| disposal_warmup_seconds | 5 |
| disposal_recheck_seconds | 10 |
| disposal_owner_radius | 80 |
| disposal_owner_away_seconds | 5 |
| disposal_owner_max_hold | 60 |
| disposal_suspect_confidence | 25 |
| disposal_collection_enabled | True |
| disposal_collection_start | 04:00:00 |
| disposal_collection_end | 07:00:00 |
| disposal_collection_days | 0,1,2,3,4,5 |
| vlm_enabled | True |
| vlm_endpoint | http://localhost:11434 |
| vlm_model | qwen3-vl:2b-instruct |
| vlm_timeout | 120 |
| vlm_frames | 8 |
| vlm_max_edge | 640 |
| vlm_async | True |
| object_confirm_seconds | 2.0 |
| cue_hold_seconds | 5.0 |
| monitoring_min_seconds | 3.0 |
| smoking_puff_count | 3 |
| smoking_puff_window_minutes | 5.0 |
| holdup_loiter_seconds | 20 |
| holdup_near_person_heights | 1.75 |
| vlm_model_holdup | qwen3-vl:4b-instruct |
| show_testing_tools | True |
| auto_start_detection | False |
| alert_cooldown | 120 |
| evidence_retention_days | 30 |
| evidence_auto_purge | False |
