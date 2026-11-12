# Frozen settings: drinking group logic

Settings that were fixed for the drinking component test and for the results chapter. Nothing here changes without a new
entry and a re-score (`drinking_eval/filter_in_lookout.py`).

## Long stay

| setting | value | where |
|---|---|---|
| `SystemSettings.drinking_group_duration` | **60 s** | settings row (`core_systemsettings`) and code defaults (`models.py`, `vision/spec_settings.py` `SPEC_DEFAULTS`, migration `0078_drinking_group_duration_one_minute`) |
| API floor (`spec_settings.LIMITS`) | 60 s minimum, 7200 s maximum | `vision/spec_settings.py` |

The manuscript's Table 10 still says 10 minutes (600 s); the committed history before this change also had 600 s.

## Passer-by filter (leaves riders and walkers out of group formation)

| setting | value |
|---|---|
| `SystemSettings.drinking_passerby_filter` (Detection Settings > Drinking) | **ON** by default (`models.py`, `SPEC_DEFAULTS`, migration `0079_systemsettings_drinking_passerby_filter`). Off restores the old behaviour: anyone near a member joins the cluster. |

Thresholds are code constants in `core/vision/tracking.py`, not settings:

| constant | value | meaning |
|---|---|---|
| `PASSERBY_RIDER_OVERLAP` | 0.35 | rider: overlap area / the smaller of (person box, motorcycle box) areas |
| `PASSERBY_RIDER_FRAC` | 0.50 | rider: share of the recent window the box must overlap a motorcycle |
| `PASSERBY_RIDER_WINDOW_S` | 1.0 s | rider: the recent window (judge time about 0.3 s, since 3 frames are enough) |
| `PASSERBY_RIDER_MIN_SAMPLES` | 3 frames | rider: minimum frames in the window |
| `PASSERBY_MIN_AGE_S` | 1.0 s | walker: a track younger than this is never excluded (judge time about 1 s) |
| `PASSERBY_STILL_S` | 5.0 s | a track still for this long is "settled": never removed as a walker **or as a rider** (the rider fix) |
| `PASSERBY_WALK_WINDOW_S` | 2.0 s | walker: window the movement is measured over |
| `PASSERBY_WALK_MIN_NET` | 0.6 box-heights | walker: minimum net displacement |
| `PASSERBY_WALK_STRAIGHT` | 0.6 | walker: net displacement / path length |
| `MOTORCYCLE_CLASS_ID` / `MOTORCYCLE_MIN_CONF` (`vision/recognition.py`) | 3 (COCO motorcycle) / 0.3 | which detections count as a motorcycle; a tricycle is usually detected as one |

Where it runs: `watch_merged` (`_process_drinking_frame`), between `PersonTracker.update` and `GroupTracker.update`,
read from the same person-tracker pass (no extra inference). `watch_all` and the standalone `watch_drinking` do not apply it:
they do not see motorcycles.


## Smoking puff definition (proposed, not committed; `core/vision/location_momentum.py`)

Proposal pending approval. No existing value changed.

| change | value | note |
|---|---|---|
| Puff needs a completed hand-to-mouth cycle for every pose reach (`PUFF_NEEDS_COMPLETED_GESTURE`) | boolean, True | the cycle is `Track.update_gesture`: enter <= 0.9 shoulder-widths held 0.7 s, exit >= 1.0 (`tracking.HAND_RAISED_RATIO` / `GESTURE_APEX_HOLD_SECONDS` / `HAND_LOWERED_RATIO`, unchanged); the manuscript's puff definition |
| One puff per person per moment (`PUFF_ONE_PER_MOMENT_SECONDS`) | **2.0 s** (new constant, value from the brief) | both wrists / the object path within 2 s count once |
| Box that confirms a puff or shows a cigarette at hand or mouth | >= `POSSIBLE_MIN_CONF` = **0.45** (existing) | reading of "same as the object gate"; open question |
| Status tied to the present (`cig_in_hand_or_mouth_now`, `puff_cap`) | **10 s** (`POSSIBLE_PUFF_WINDOW_SECONDS`) and **1.0 s** (`MOUTH_ACCEPT_FRESH_SECONDS`), both existing | banked puffs count in totals but cannot raise the status |
| Puff-only path | unchanged (`PUFF_ONLY_ENABLED = False`) | |
