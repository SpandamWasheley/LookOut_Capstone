# LookOut sanity run — 4 extra clips (read-only)

## Summary

**2 of 4 passed (strict). 3 of 4 if hidden Monitoring rows are not counted against "no alert".**

| Clip | Enacted | Expected | Got | Verdict |
|---|---|---|---|---|
| Aug7_16 - Trim | Group drinking | 1 Drinking alert | **5 alerts**: Drinking group Possible (the expected one) + a 2nd Drinking Possible + Smoking **Likely** + 2 Holdup (Likely, Possible) | **FAIL** |
| Aug18_18 - NightNear | Smoking, night, near | 1 Smoking alert | 1 Smoking alert (Monitoring 16.6 s, Possible 16.7 s, Likely 42.3 s) | **PASS** |
| Holdup 2 | Knife shown | 1 weapon alert (knife seen ~0:39, ~0:45) | 1 Holdup/weapon alert (Monitoring 40.6 s, Likely 41.7 s, peak Likely); knife detected at 0:39 and 0:45 | **PASS** (plus 1 stray Drinking *Monitoring* row, hidden in the UI) |
| FH FriendHug | Nothing | No alert of any kind | No Possible/Likely. **1 Drinking *Monitoring* row** (bottle at 93.1 s, left open when the clip ended). Nothing visible in the UI. | **FAIL (strict)** — passes if Monitoring is ignored |

Main issues:
1. **Aug7_16 is a heavy false-positive clip.** Besides the expected group-drinking alert it produced a Smoking Likely (Cigarette class ~0.56 plus hand-to-mouth, while the VLM said "white cloth to mouth, no smoking item"), two Holdup alerts from the `knife` class on people holding bottles/objects in a frozen close pair, and a second Drinking Possible row for a second simultaneous gathering.
2. **A Monitoring row can survive the "flicker" rule at the end of a clip.** In FH, a 2.2 s bottle blip at 93.1–95.3 s is still in the DB because `_incident_gc` only runs per frame and the clip ended before the 3 s grace elapsed.
3. **Possible → Monitoring regression on re-open** (Aug18 at 30.4 s): a closed incident that is continued skips the "never go back to Monitoring" guard.
4. The raw model is very noisy on FH/Aug7 (FH: ~970 knife and ~960 cigarette detections above the engine floors in a clip with no knife and no smoking). The object gate, 2 s confirmation and the Monitoring-minimum rule are what keep FH quiet.
5. Without `--clock`, the drinking `time_band` cue (+5) reads the **wall clock of the machine** (it fired on every clip, FH included, because I ran at 17:00–18:00). It is only +0.05 but it is not footage time.

---

## 1. Version and method

- **Commit:** `716fa9a5286fbb34fff0113e43c3d53615d047ad` (branch `main`, "A question before anything is thrown away, and a frame big enough to draw on").
- **Uncommitted at run time** (results are for the working tree, not the commit alone):
  - Modified: `watch_drinking.py`, `watch_smoking.py`, `models.py`, `recognition.py`, `scoring.py`, `spec_settings.py`, and 7 test files (`tests_false_alarm_fixes`, `tests_incidents`, `tests_layer_e`, `tests_location_momentum`, `tests_scoring_v6`, `tests_v6210`, `tests_vape_size_gate`).
  - Untracked: `migrations/0078_drinking_group_duration_one_minute.py`, `tests_puff_only_off.py`, `REMINDERS.md`, plus eval folders. The `scoring.py` working-tree change includes `PUFF_ONLY_ENABLED = False`.
- **Pipeline:** Run Detection "Merged (All 3)" launches `manage.py watch_merged --source <clip> --camera <CAM-…-TEST>` (`core/views.py` `DETECTION_COMMANDS["merged"]`, `DetectionJobViewSet.create`). I ran exactly that command, in-process via `call_command`, with model `core/vision/merged_v2.pt` (classes Bottle / Cigarette / knife), ByteTrack person tracker, FAR 2x2 tiling, cascade + bottle crops on, pose model on cuda, AI checker ON (qwen3-vl via Ollama, as in the dashboard). I did not go through the HTTP endpoint or upload path.
- **No code, settings, thresholds or real DB values were changed.**
  - I accidentally touched `manage.py` once (a no-op `sed -i` that rewrote the file); it showed as modified and I reverted it with `git checkout`.
  - The run used `scratch_sanity/sanity_settings.py`, which points at a **copy** of `db.sqlite3` (`scratch_sanity/db_copy.sqlite3`) and a scratch `MEDIA_ROOT`. The real DB was verified untouched afterwards (max alert id 685, 370 rows, mtime unchanged).
  - `scratch_sanity/` is untracked and not committed.
- **Instrumentation (read-only wrappers in `scratch_sanity/run_instrumented.py`):** logs every `_incident_sync` call (video time, kind, level, score, cues), `_incident_discard`, and `PersonTracker.update`. The raw per-detection log is the command's own `--calibration-csv`. **Caveat:** per its help text this flag drops the merged model's inference floor to 0.01 for logging; each engine's own confidence floor is still applied downstream and alerting is documented as unaffected. I did not run a no-CSV control.
- **Timestamps:** `Alert.timestamp` is wall-clock processing time, not clip time. Every time below is **seconds into the clip**, taken from the event log. For the two Aug clips I also re-ran with `--clock` (Aug 7 16:00 / Aug 18 18:00, from the file names) so the alert timelines carry exact clip times; both runs agree on the alert set. The base (no-clock) run is the primary result; the `--clock` variant differs only by adding E20 (time-of-day) points to Holdup.
- "Frames processed" in the tracker log counts `PersonTracker.update` calls (one per engine), so divide by 3 for frames.

### Settings in use (SystemSettings row, unchanged)

| Setting | Value | Setting | Value |
|---|---|---|---|
| smoking_confidence / dwell | 30 / 3 | drinking_confidence / dwell | 35 / 8 |
| thief_confidence / dwell | 25 / 3 | drinking_min_group | 2 |
| drinking_group_duration | 60 s | drinking_start–end (hours_enabled) | 16:00–00:00 (0) |
| object_confirm_seconds | 2.0 | cue_hold_seconds | 5.0 |
| monitoring_min_seconds | 3.0 | alert_cooldown | 120 s |
| smoking_puff_count / window | 3 / 5 min | holdup_loiter_seconds | 20 |
| holdup_near_person_heights | 1.0 | vlm_model / holdup | qwen3-vl:2b / 4b |

**Status thresholds** (`core/vision/scoring.py:52-58`, `level_of`): object gate ON and score < 0.55 → **Monitoring**; ≥ **0.55 Possible** (`warning`); ≥ **0.75 Likely** (`violation`). Monitoring rows are quiet (`NOTIFY_LEVELS` = warning, violation).
**Weights:** drinking `bottle` 0.40 (gate), `gathering` 0.10, `gathering_duration` 0.15, `at_mouth` 0.15, `time_band` 0.05. Smoking `cigarette` 0.40 (gate), `gesture` 0.20, `puffs` 0.20, `puff_pattern` 0.15, `near_mouth` 0.05. Holdup (`theft.py:176`) `E14` knife 0.45 (gate), `E12` frozen pair 0.20, `E10` loiter 0.10, `E11E12` approach-then-freeze 0.10, `E20` time of day (needs a clock).

---

## 2. Aug7_16 - Trim (group drinking) — **FAIL**

96 s, 2560x1440. Expected 1 Drinking (group). Got 5 alert rows (base run; the `--clock` run gives the same set):

| # | Type | Status path (clip s) | Start–end (s) | Peak | Indicators (points, when) |
|---|---|---|---|---|---|
| 1 | Smoking | Monitoring 10.7 → Possible 11.0 → **Likely 21.9** | 10.7–22.2 | 1.00 Likely | gesture +20 @10.0; cigarette +40 & near_mouth +5 @10.7 (capped to Monitoring: "cigarette confidence under 0.45"); puff_pattern +15 & puffs +20 @14.0; all five = 1.00 @21.9 |
| 2 | **Drinking (group) — the expected one** | Monitoring 28.4 → **Possible 28.9** | 28.4–94.9 | 0.70 Possible (@65.4) | bottle +40 @28.4 (0.45 with band +5); at_mouth +15 @28.9 → 0.60; gathering +10 @41.6 (0.55–0.70 later, "3 people together ~58 s") |
| 3 | Holdup | **Likely** straight away at 44.5; dips to Possible at 48.3 | 44.5–54.0 | 0.75 (0.85 with clock) | E14 +45, E10 +10, E12 +20, all @44.5 (E20 +10 in the clock run) |
| 4 | Holdup (2nd row) | Monitoring 52.6 → Possible 52.9 | 52.6–54.4 | 0.65 Possible (0.74 clock) | E14 +45, E12 +20 @52.6 |
| 5 | Drinking (2nd row) | **Possible** 81.4 | 81.4–85.2 | 0.55 | bottle +40, gathering +10, time_band +5 |

Also logged but **no row** (dropped as flickers under 3 s, or capped): several Drinking Monitoring blips (13.0, 14.1, 16.8 capped "solo Possible needs a Bottle held 3 s and a 1 s sip or 2 separate sips") and repeated Smoking Monitoring/Possible re-evaluations after 46 s that were suppressed (the first Smoking row holds the 120 s spot cooldown).

**Why it fails (from the logs):**
- **Smoking Likely (row 1)** — wrong violation type. The object gate fired on `Cigarette` detections (object confidence 0.557) and pose supplied hand-to-mouth + 2 puffs. The VLM reply stored on the row says "holding a white cloth to his mouth… no smoking item", but the checker is advisory and does not lower the level. Rules: `SMOKING_WEIGHTS` `core/vision/scoring.py:272`; Possible confidence floor `POSSIBLE_MIN_CONF = 0.45` `core/vision/location_momentum.py:104`; per-track cues in `watch_smoking.py` `_process_track` (~l.1041).
- **Holdup alerts (rows 3–4)** — wrong violation type. `knife` detections (505 above 0.25) on people holding bottles, plus E12 "frozen close pair" and E10 loitering. Rules: `theft.WEIGHTS` `core/vision/theft.py:176`, E12 in `theft.py` (~l.898), `watch_thief.py` `_process_track` (l.922), `scoring.HOLDUP_WEIGHTS` `scoring.py:335`.
- **Duplicate holdup (row 4)** — two different people (tracker IDs 7 and 11, both alive) each held a "knife" next to the same partner (ID 6). Incidents are keyed per `(kind, track id)`, so each gets its own row; `_incident_continue` only merges when the other track is gone (`_incidents.py:406`, `CONTINUE_MIN_GAP = 1.0`).
- **Second Drinking row (row 5)** — a second, simultaneous gathering (cluster 8 vs cluster 5) opened its own incident; the two were not judged the same subject (`_same_subject`, `_incidents.py:397`). I inferred this from the cluster keys, not from a dedicated log line.

### Object detections (≥ engine floor; floors: cigarette 0.30, bottle 0.35, knife 0.25; ranges merged at 3 s gaps)

**cigarette** (>= 0.30): 963 detections of 10250 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 0.0-95.9 | 963 | 0.30/0.50/0.88 |

**bottle** (>= 0.35): 2111 detections of 13569 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 0.0-20.9 | 119 | 0.35/0.46/0.85 |
| 24.9-95.9 | 1992 | 0.35/0.61/0.90 |

**knife** (>= 0.25): 505 detections of 6097 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 0.0-4.2 | 14 | 0.30/0.53/0.84 |
| 9.8-17.2 | 21 | 0.27/0.48/0.79 |
| 23.6-31.8 | 17 | 0.26/0.40/0.78 |
| 35.5-95.0 | 453 | 0.25/0.43/0.88 |

### Tracking
16 distinct person IDs (PersonTracker), 30 raw ByteTrack IDs (max 1061), so the tracker consolidated some ByteTrack renames. Long-lived: ID 1 (0–43.7 s), 3 (0–24.7), **6 (23.0–95.9), 7 (25.1–95.9), 11 (42.0–95.9)**, 12 (56.3–88.8). No hand-over hit my heuristic (an ID ending and another starting nearby within 1.5 s). The knife holders are 7 and 11 (see the duplicate above). The smoker is ID 1.

---

## 3. Aug18_18 - NightNear (smoking) — **PASS**

70 s. One alert row: **Smoking**, peak Likely.

| Event | Clip s |
|---|---|
| Smoking row opened (Monitoring) | 16.6 |
| → Possible | 16.7 |
| back to Monitoring (re-open after a gap, "capped: no puff") | 30.4 |
| → Possible | 30.6 |
| → **Likely** | **42.3** |
| row last seen | 42.8 |

Indicators: gesture +20 @15.7 (no gate yet, status none); cigarette +40 & near_mouth +5 @16.6 → 0.65 (Monitoring, cap "cigarette confidence under 0.45", Possible 0.1 s later); puffs +20 & puff_pattern +15 @39.4 (momentarily object-off, so the 0.55 showed as none); all five = **1.00 Likely @42.3**. Object confidence of the cigarette: 0.83.
Other logged activity, no row: Drinking Monitoring blips at 19.5, 28.5, 62.2 (bottle 0.45); Holdup Monitoring 54.1–55.6 (E14 +45, E10 +10 = 0.55, capped "no frozen pair"). All dropped as flickers under 3 s. Nothing at Possible/Likely other than the smoker.

Observation: the status went Possible → Monitoring → Possible (30.4 s). That is the continued-after-gap path skipping the guard at `_incidents.py:539` (`if inc is not None and level == MONITORING and inc.peak in NOTIFY_LEVELS`): `inc` is `None` on that call because the key had been closed, so the guard never applies.

### Object detections

**cigarette** (>= 0.30): 590 detections of 4566 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 2.2-6.8 | 8 | 0.31/0.46/0.59 |
| 10.1-22.4 | 139 | 0.30/0.65/0.85 |
| 26.2-67.2 | 443 | 0.30/0.65/0.88 |

**bottle** (>= 0.35): 69 detections of 2890 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 2.5-2.9 | 2 | 0.37/0.38/0.40 |
| 15.1-48.6 | 54 | 0.35/0.53/0.81 |
| 54.9-63.3 | 13 | 0.38/0.52/0.80 |

**knife** (>= 0.25): 332 detections of 3743 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 4.2-10.2 | 9 | 0.25/0.37/0.49 |
| 14.5-20.9 | 17 | 0.26/0.35/0.88 |
| 26.0-70.3 | 306 | 0.25/0.40/0.84 |

### Tracking
23 PersonTracker IDs (30 raw ByteTrack). The smoker is ID 1 (4.9–69.6 s) throughout. 1 probable hand-over (ID 3 → 5 at 15.4 s, short-lived background people). No ID switch on the smoker.

---

## 4. Holdup 2 (knife) — **PASS**

48 s, 10 fps. One weapon alert (`theft`, shown as Holdup); the knife confirmed at ~0:40 and held through ~0:47.

| Event | Clip s |
|---|---|
| Holdup row opened, **Monitoring** (0.55 but capped: "no frozen pair") | 40.6 |
| **Likely** (0.85) | 41.7 |
| flaps Likely ↔ Monitoring (E12 lost/regained) | 46.4 / 46.7 / 46.8 / 47.4 / 47.6 |
| row last seen | 47.8 (final stored level Possible, **peak Likely**) |

Indicators: E14 knife +45 & E10 loiter +10 @40.6; E12 frozen pair +20 & E11E12 approach-freeze +10 @41.7 → 0.85. The second incident key (knife id 2) from 45.7 was **continued into the same row** (no duplicate). With no `--clock`, E20 is disabled ("no clip time").

**Knife detections ≥ 0.25, per frame (seconds: confidence):**
35.0: 0.31 · 37.0: 0.55 · 37.2: 0.40 · 37.3: 0.61 · 37.7: 0.49 · **39.8: 0.75 · 40.0: 0.63 · 40.1: 0.51 · 40.4: 0.67 · 41.0: 0.29** · **44.7: 0.62 · 44.8: 0.80 · 45.0: 0.79 · 45.1: 0.55 · 45.2: 0.81 · 45.3: 0.82 · 45.4: 0.57 · 45.5: 0.70 · 45.6: 0.73 · 45.9: 0.70** · 47.1: 0.27 · 47.8: 0.43. Earlier isolated: 21.1: 0.35, 23.2: 0.40.

**0:39 caught** (0.75 at 39.8 s, then 4 more through 41.0). **0:45 caught** (13 detections 44.7–45.9, up to 0.82). The row opens 0.8 s after the first confident knife (object_confirm_seconds 2.0 plus momentum). The early knife flickers (21–37 s) opened nothing.

**Stray row:** one **Drinking Monitoring** row (bottle +40 @10.0, +5 band; at_mouth +15 @12.4 → 0.60 but capped "solo Possible needs a Bottle held 3 s…"; re-seen at 21.0 and 32.2; last ~34 s). Never above Monitoring, hidden in the UI. A Smoking Monitoring at 7.5–10.2 (0.65, capped "no puff") was discarded as a flicker under 3 s. *(Which blips merged into the single drinking row is inferred from the event log.)*

### Object detections (ranges merged at 1 s gaps)

**cigarette** (>= 0.30): 81 detections of 2934 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 2.5-3.4 | 8 | 0.32/0.54/0.72 |
| 4.6-8.8 | 27 | 0.31/0.53/0.80 |
| 16.9-17.2 | 2 | 0.36/0.40/0.44 |
| 18.5-19.0 | 3 | 0.31/0.35/0.43 |
| 21.3-21.3 | 1 | 0.39/0.39/0.39 |
| 26.2-27.7 | 3 | 0.31/0.36/0.44 |
| 29.4-30.2 | 3 | 0.35/0.37/0.39 |
| 31.8-31.8 | 1 | 0.34/0.34/0.34 |
| 34.9-34.9 | 1 | 0.50/0.50/0.50 |
| 37.6-42.5 | 10 | 0.32/0.45/0.67 |
| 44.9-47.8 | 22 | 0.32/0.47/0.68 |

**bottle** (>= 0.35): 55 detections of 888 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 3.3-3.3 | 1 | 0.39/0.39/0.39 |
| 4.6-5.1 | 2 | 0.41/0.61/0.81 |
| 7.9-8.1 | 3 | 0.60/0.69/0.85 |
| 10.0-10.1 | 2 | 0.49/0.50/0.51 |
| 11.9-13.7 | 20 | 0.35/0.44/0.57 |
| 18.9-19.2 | 4 | 0.50/0.69/0.81 |
| 20.4-20.4 | 1 | 0.56/0.56/0.56 |
| 23.1-23.1 | 1 | 0.60/0.60/0.60 |
| 29.8-31.0 | 8 | 0.45/0.70/0.87 |
| 32.5-32.5 | 1 | 0.44/0.44/0.44 |
| 33.8-33.8 | 1 | 0.39/0.39/0.39 |
| 36.7-38.1 | 5 | 0.38/0.45/0.51 |
| 41.0-41.3 | 3 | 0.44/0.51/0.59 |
| 43.9-43.9 | 1 | 0.49/0.49/0.49 |
| 45.2-45.2 | 1 | 0.56/0.56/0.56 |
| 47.6-47.6 | 1 | 0.53/0.53/0.53 |

**knife** (>= 0.25): 27 detections of 260 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 21.1-21.1 | 1 | 0.35/0.35/0.35 |
| 23.2-23.2 | 1 | 0.40/0.40/0.40 |
| 35.0-35.0 | 1 | 0.31/0.31/0.31 |
| 37.0-37.7 | 4 | 0.40/0.51/0.61 |
| 39.8-41.0 | 5 | 0.29/0.57/0.75 |
| 44.7-45.9 | 13 | 0.26/0.62/0.82 |
| 47.1-47.8 | 2 | 0.27/0.35/0.43 |

### Tracking
Two PersonTracker IDs: **1 (0–47.8 s)** and **2 (34.2–47.8 s)**; raw ByteTrack IDs 1 and 230. No ID switch; the two people stay distinct, which is what E12 (pair frozen) needs.

---

## 5. FH FriendHug (no violation) — **FAIL (strict)**

95 s, 20 fps. No Possible/Likely and nothing shown in the UI, but **1 Drinking Monitoring row** exists.

| Event | Clip s |
|---|---|
| Drinking: bottle +40 (+5 band) → 0.45 **Monitoring**, key `drink 32` | 93.1 |
| clip ends (open incident never closed) | 95.3 |

Why it is a row: a Monitoring event shorter than `monitoring_min_seconds` (3.0) is dropped by `_incident_gc` (`_incidents.py:708`), but GC only runs per frame with a 3 s grace (`END_GRACE_SECONDS`); the stream ended 2.2 s in, so it was never evaluated. The same rule did remove two Holdup Monitoring blips (E14 +45 at 48.2 and 48.8 s, `people_near=False`, ~2.3 s).

**Did any gathering / drinking / weapon logic start?**
- **Gathering/cluster:** the group tracker created cluster tracks from 5.2 s on (keys cluster 1…14), but the `gathering` cue (+10) only fired at **91.3–92.0 s** (0.15 with band). It never reached Monitoring (no bottle gate) and `gathering_duration` (60 s) never fired. Closest: 0.15 vs 0.55 for Possible.
- **Drinking:** bottle 0.45 Monitoring at 93.1 s (max). Possible needs +0.10 (a second cue: gathering or at_mouth).
- **Weapon:** `knife` E14 +45 at 48.2–50.6 s → 0.45 Monitoring, no partner (capped, dropped). Two "holdup pattern" observations were printed in the run log without timestamps: "#3 and #7 stationary at arm's length 2 s+ after #3 loitered (E10 .10 + E12 .20 = 0.30, none)" and "#9 and #11 … (E12 .20 = 0.20, none)". So the hugging pair **did** score E12, but with no knife gate it produces nothing. If a knife flicker (E14 .45) had coincided with that frozen pair, 0.45 + 0.20 = 0.65 would be **Possible**.
- **Smoking:** max 0.20 (gesture only, no gate), no event.

Maxima: Drinking 0.45, Weapon 0.45, Smoking 0.20. Possible is 0.55.

### Object detections (this clip has none of these things; floors as above, ranges merged at 2 s gaps)

**cigarette** (>= 0.30): 960 detections of 8855 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 0.0-10.1 | 114 | 0.30/0.57/0.87 |
| 14.5-39.4 | 168 | 0.30/0.54/0.88 |
| 42.3-61.1 | 212 | 0.30/0.57/0.86 |
| 63.5-77.8 | 147 | 0.30/0.55/0.88 |
| 80.1-95.3 | 319 | 0.30/0.60/0.89 |

**bottle** (>= 0.35): 156 detections of 4130 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 0.8-0.8 | 1 | 0.48/0.48/0.48 |
| 5.5-5.5 | 1 | 0.65/0.65/0.65 |
| 11.1-11.1 | 1 | 0.54/0.54/0.54 |
| 16.4-18.5 | 4 | 0.37/0.44/0.55 |
| 22.0-25.7 | 9 | 0.37/0.53/0.67 |
| 29.0-34.1 | 10 | 0.36/0.45/0.68 |
| 36.2-39.5 | 13 | 0.36/0.47/0.78 |
| 41.6-44.9 | 15 | 0.40/0.59/0.77 |
| 47.2-51.1 | 11 | 0.37/0.49/0.72 |
| 55.7-55.7 | 1 | 0.62/0.62/0.62 |
| 70.0-74.5 | 9 | 0.36/0.50/0.69 |
| 80.5-84.0 | 16 | 0.36/0.50/0.72 |
| 87.4-94.8 | 65 | 0.35/0.56/0.85 |

**knife** (>= 0.25): 971 detections of 15330 raw
| range (s) | n | conf min/avg/max |
|---|---|---|
| 2.8-7.8 | 10 | 0.26/0.45/0.75 |
| 11.2-11.4 | 3 | 0.29/0.34/0.43 |
| 15.3-17.9 | 34 | 0.25/0.39/0.76 |
| 20.6-56.8 | 218 | 0.25/0.41/0.80 |
| 58.9-95.3 | 706 | 0.25/0.43/0.91 |

The raw model is clearly over-firing: ~960 cigarette and ~970 knife detections above floor in 95 s of two friends hugging.

### Tracking
35 PersonTracker IDs (61 raw ByteTrack, max 1383) for a handful of people. Many short-lived IDs (2–3 s) in the crowded scene (e.g. IDs 18–28 all start between 68–72 s). 1 probable hand-over (5 → 6 at 20.6 s). Long-lived: ID 3 (5.6–39.8), 9 (27.9–84.7), 7 (21.0–51.4), 26 (70.8–94.3). The hug pair (#3/#7, later #9/#11) kept separate IDs.

---

## 6. Logic still active in this pipeline (observation only)

- **Vape:** yes, as *object-puff* handling, not as a class. A Bottle box shorter than 10% of its person's height is treated as a vape: not a drink, not a sip (`core/vision/tracking.py:84` `VAPE_BOTTLE_MAX_FRAC`, `VAPE_PUFF_*`; `watch_merged.py` `holds_real_bottle` and the shared `vape_puffs`). There is no vape detection class (`SMOKING_CLASSES = {"cigarette"}`, `recognition.py:549`). **Smoking "puff-only" (no cigarette) is OFF**: `PUFF_ONLY_ENABLED = False` (`scoring.py:385`, uncommitted), consistent with puff-only 0.55 scores in the logs showing status *none*.
- **Gun:** **none.** No gun/firearm/pistol class or code. The model has Bottle / Cigarette / knife and the Holdup gate is `knife` (E14) only.
- **"Holdup":** active (`watch_thief` via the merged `knife` route, `theft.py`, `scoring.HOLDUP_WEIGHTS`). Legacy theft cues (E7–E9, E16–E19, E22) exist but are disabled unless `--enable-legacy-theft`.
- **Solo drinking:** active. `core/vision/solo_sip.py` gates a single person's Possible on a Bottle ≥ 0.5 held 3 s plus a 1 s sip (or 2 sips). Seen as "capped: solo Possible needs a Bottle held 3 s…" (Holdup 2 @12.4, Aug7 @16.8). Group drinking uses Path B (`GroupTracker`, `gathering`).
- **"Cigarette at the mouth":** active as the `near_mouth` cue, +0.05, counted only with a cigarette detected (`scoring.py:277`, `watch_smoking.py` `_near_mouth`, ~l.1105).

## 7. Did any Likely appear, and how?

Yes, for Smoking and Holdup; never for Drinking.
- **Smoking Likely** (Aug7 @21.9, Aug18 @42.3): cigarette +40 + gesture +20 + puffs +20 (+ puff_pattern +15 + near_mouth +5) = 1.00, with the Likely caps passing ("cigarette confidence ≥ 0.45", "≥ 2 puffs in 30 s" — `location_momentum.py:108-109`).
- **Holdup Likely** (Holdup 2 @41.7, Aug7 @44.5): E14 +45 + E12 +20 + E10 +10 = 0.75, or 0.85 with E11E12 (+10) or E20 (+10 under `--clock`).
- **Drinking** never exceeded Possible (max 0.70, Aug7 @65.4 s).

## 8. Files in `scratch_sanity/` (not committed)

`sanity_settings.py`, `run_instrumented.py`, `run_all.sh`, per-clip `events_*.jsonl` (score/tracker log), `det_*.csv` (raw detections), `log_*.txt`, analysis helpers (`analyze.py`, `dets_md.py`, `idsw.py`, `cuetl.py`, `maxes.py`, `dump_alerts.py`), `db_copy.sqlite3`, and a scratch `media/`. The `--clock` variants are tagged `aug7_clk` / `aug18_clk`.
