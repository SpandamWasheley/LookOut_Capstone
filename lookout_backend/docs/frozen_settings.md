# Frozen settings: Smoking, Drinking, Knife (Dangerous Weapon)

Written from the code as it stands on 2026-11-13 (base commit `265a34d` plus the uncommitted `scoring.py` drinking cap, the
`BOTTLE_OVERLAP_VETO` in `location_momentum.py`, and the alpha run logger). Paths are under `lookout_backend/`. Nothing here
changes without a new entry and a re-score. Parking and improper disposal are out of scope for this freeze.

## 1. Status levels

| item | value | file:line |
|---|---|---|
| Possible (`SCORE_WARNING`) | **0.55** (score >= 55) | `core/vision/scoring.py:58` |
| Likely (`SCORE_VIOLATION`) | 0.75, **kept but unused** | `core/vision/scoring.py:57` |
| Likely switch (`LIKELY_ENABLED`) | **False**: any score >= 0.55 is Possible; the AI checker cannot suggest Likely either | `core/vision/scoring.py:64`, `core/vision/ai_status.py:116,195` |
| `clamp_level` (Likely -> Possible) | applied to every new decision and to a carried-over previous level | `core/vision/scoring.py:101` |
| Monitoring | gate cue present but score < 0.55 (quiet watchlist, notifies nobody) | `core/vision/scoring.py:108` (`level_of`) |
| Hysteresis (`HYSTERESIS_DROP`) | a status is left only 0.05 below the threshold that admitted it | `core/vision/scoring.py:68`, `:131` |
| Gate cue per detector (`GATE_CUES`) | drinking `bottle`, smoking `cigarette`, knife `E14`. **No gate cue = no event, nothing written** | `core/vision/scoring.py:364` |
| Notifying levels (`NOTIFY_LEVELS`) | Possible, Likely | `core/vision/scoring.py:88` |

## 2. Smoking

Total weight 1.00 (`SMOKING_WEIGHTS`, `core/vision/scoring.py:286`). Cues are cumulative: one puff = `gesture`; two or more =
`gesture` + `puffs`.

| indicator | plain words | weight | threshold | file:line |
|---|---|---|---|---|
| `cigarette` (GATE) | a Cigarette is held on this person, confirmed by object momentum | 0.40 | momentum ON 1.5 / OFF 0.4 / decay 0.9 / max 3.0; detection floor = Settings 30 % x class scale 1.0 | weight `scoring.py:287`; momentum `vision/momentum.py:28-31`; floor `watch_smoking.py:62-66,420` |
| `gesture` | one hand-to-mouth puff (completed cycle) | 0.20 | pose counter: wrist <= 0.9 shoulder-widths of the nose, held 0.7 s, then out past 1.0 | weight `scoring.py:288`; `vision/tracking.py:125,130,134` |
| `puffs` | did it again: 2 or more puffs | 0.20 | `puffs_cue >= 2` | weight `scoring.py:289`; cue `watch_smoking.py:1048` |
| `puff_pattern` | 3 or more puffs within 5 minutes | 0.15 | Settings `smoking_puff_count` 3 in `smoking_puff_window_minutes` 5.0 | weight `scoring.py:290`; cue `watch_smoking.py:1051` |
| `near_mouth` | the cigarette is at the mouth (needs the cigarette) | 0.05 | cigarette within 0.5 shoulder-widths of the mouth anchor (puff pairing uses 0.7), held `NEAR_MOUTH_HELD_BEFORE_CONFIRM` | weight `scoring.py:291`; `location_momentum.py:35,47`; cue `watch_smoking.py:1042-1045` |

Worked scores: cigarette only 0.40 (Monitoring); cigarette + near mouth 0.45; **cigarette + 1 puff (+ near mouth) 0.65 =
Possible**; cigarette + 2 puffs 0.85; + pattern 1.00 (capped at 1.00).

### Smoking caps (a cap lowers the status, never the score)

| cap | rule | file:line |
|---|---|---|
| no puff | Possible needs >= 1 puff in the last 10 s (`POSSIBLE_PUFF_WINDOW_SECONDS`, `POSSIBLE_MIN_PUFFS`), or a cigarette accepted at the mouth with momentum >= 1.0 (`POSSIBLE_MOUTH_MOMENTUM`) | `location_momentum.py:106-107,113`; `puff_cap` `:748` |
| cigarette confidence | the Cigarette box must be >= **0.45** (`POSSIBLE_MIN_CONF`); weaker boxes are not puff evidence | `location_momentum.py:112`; filter in `update_puffs` `:568` |
| cigarette "now" | a puff only raises the status while a cigarette was at a wrist or the mouth within 1.0 s (`MOUTH_ACCEPT_FRESH_SECONDS`); older puffs count in totals but cannot raise it | `location_momentum.py:117`; `cig_in_hand_or_mouth_now` `:715` |
| Likely | would need 2 puffs in 30 s (`LIKELY_PUFF_WINDOW_SECONDS`, `LIKELY_MIN_PUFFS`); inert while Likely is off | `location_momentum.py:108-109` |
| sticky | the caps only block the FIRST rise; a Possible event never falls back to Monitoring | `puff_cap` `location_momentum.py:748` |
| puff-only path | **off** (`PUFF_ONLY_ENABLED = False`): a cigarette is required | `scoring.py:409` |

### Smoking puff definition (puff fix)

| item | value | file:line |
|---|---|---|
| puff needs a completed hand-to-mouth cycle for every pose reach | True | `location_momentum.py:74` (`PUFF_NEEDS_COMPLETED_GESTURE`) |
| zoomed-pose reach also needs the cycle (slack 0.5 s) | True | `location_momentum.py:68-69` |
| one puff per person per moment | 2.0 s | `location_momentum.py:77` |
| reach <-> cigarette pairing window | 5.0 s either order | `location_momentum.py:46` |
| minimum gap between reaches | 1.0 s | `location_momentum.py:50` |
| far-person object puff | on, only where pose cannot measure the person | `location_momentum.py:54` |

## 3. Drinking

Total weight 0.85 (`DRINKING_WEIGHTS`, `core/vision/scoring.py:278`). Drinking is a **group** violation (one alert per gathering).

| indicator | plain words | weight | threshold | file:line |
|---|---|---|---|---|
| `bottle` (GATE) | a Bottle linked to a group member's hand/mouth, or stationary on the table inside the group area | 0.40 | momentum ON 1.5 / OFF 0.4 / hold 1.0 s; detection floor Settings 35 % | weight `scoring.py:279`; `momentum.py:28-31,66`; `location_momentum.py:20,26` |
| `gathering` | 2 or more people together and stationary | 0.10 | `drinking_min_group` 2; group stationary >= 5 s (window `min(held, 30 s)`) | weight `scoring.py:280`; `watch_drinking.py:949-952` |
| `gathering_duration` | the group has stayed together for the long-stay time | 0.15 | `drinking_group_duration` **60 s**, stationary over that whole time | weight `scoring.py:281`; `watch_drinking.py:954-956` |
| `at_mouth` | the bottle is raised to a mouth (needs the bottle) | 0.15 | posture "at-mouth"; mouth proximity 3.0 (Settings `drinking_mouth_proximity`) | weight `scoring.py:282`; `watch_drinking.py:1022-1025` |
| `time_band` | evening 16:00-24:00 | 0.05 | only when `drinking_hours_enabled`; band start/end in Settings | weight `scoring.py:283`; `watch_drinking.py:931-934,1033`; `models.py:718-719` |

Gathering is a **second gate** (`DRINKING_GATHERING_GATE = True`, `scoring.py:403`): without a `gathering` cue the status is held
at Monitoring (`scoring.py:506`) and the **score** is held at 0.54 (`scoring.py:511-512`, uncommitted); `raw_score` keeps the true sum. A lone
drinker can therefore never reach Possible. Bottle + gathering = 0.50 (Monitoring); Possible needs one more cue
(`at_mouth` or `gathering_duration`).

### Drinking group formation

| item | value | file:line |
|---|---|---|
| people within this many person-heights cluster | 1.8 | `vision/tracking.py:181` (`GROUP_CLUSTER_DIST`) |
| a member may be absent this long before the group drops | 3.0 s gap / 3.0 s presence grace | `tracking.py:190,196` |
| a dissolved group can be revived within | 15 s, match dist 2.0 | `tracking.py:211,214` |
| group cooldown | Settings `alert_cooldown` 120 s; same-place centre distance 1.5 | `models.py:982,764` |

## 4. Knife (Dangerous Weapon, code `theft`)

Total weight 0.60 (`HOLDUP_WEIGHTS` `scoring.py:352`, mirrored by `theft.WEIGHTS` `core/vision/theft.py:172`). The knife and how
steadily it was seen; nothing else scores it.

| indicator | plain words | weight | threshold | file:line |
|---|---|---|---|---|
| `E14` (GATE) | a knife box on a person that passes the head, size and wrist rules | 0.40 | model confidence >= **0.50** (`KNIFE_MIN_CONF`, wins over Settings 25 %); knife within **0.22** person-heights of a confident wrist (keypoint conf >= 0.5); not in the top 15 % of the box; box not taller than 25 % of the person | weight `scoring.py:353`, `theft.py:176`; `watch_thief.py:82,86,113,124`; rule `watch_thief.py:516-585`, `_confident_wrists` `:152` |
| `momentum` | the knife persisted | 0.20 | seen in >= **2 of the last 5** processed frames, remembered 3.0 s; knife momentum holds 1.0 s, confirms 0.5 s | weight `scoring.py:354`, `theft.py:177`; `watch_thief.py:121,125,130`; `momentum.py:60,73` |
| `E12`, `E10`, `E11E12` | pair frozen / loiterer / approach: **retired**, kept at 0.0 | 0.00 | none | `theft.py:181-183` |

Knife alone 0.40 (Monitoring); knife + persistence **0.60 = Possible**, no second person needed. The old "needs a second person and a
frozen pair" cap is retired. `WEAPON_REACH` 0.5 (`theft.py:97`) is the knife-to-person distance used by the standalone
weapon evidence; the Run Detection path uses the wrist rule above. There is no wrist fallback: with no confident wrist the knife is rejected.

## 5. Unscored guards (they stop an event; they add no points)

| guard | rule | file:line |
|---|---|---|
| bottle overlap veto (smoking) | a "Cigarette" box overlapping (IoU >= 0.3) a Bottle the drinking engine linked in the last 10 s is dropped from puff evidence. Measured on Aug7 and Aug18: changes nothing | `location_momentum.py:116` (`BOTTLE_OVERLAP_VETO`), filter `:595-598`; `watch_smoking.py:656` |
| bottle stand-down (smoking) | the gesture counter stands down for a person with a confident Bottle (>= 0.5) continuously >= 3.0 s within 10 s | `watch_smoking.py:117-121,617`, `watch_merged.py:78` |
| class flip | the same spot seen as a different class within 5 frames (IoU > 0.3) is not counted | `location_momentum.py:41-42,227` |
| jump | a hit that moved > 0.25 x person height in 2.0 s is a jump, not a held object | `location_momentum.py:38-39` |
| location gate | cigarette/bottle momentum only rises for a detection at a wrist or the mouth (0.5 shoulder-widths); a Bottle also counts in the upper 60 % of the body for drinking | `location_momentum.py:19,26,35` |
| walking | a person walking does not get puffs counted | `location_momentum.py:56-62` (`WALK_*`) |
| passer-by filter (drinking) | riders and walkers are left out of group formation: rider = >= 0.35 overlap with a motorcycle for >= 0.5 of the last 1.0 s (>= 3 frames); walker = net move >= 0.6 box-heights over 2.0 s, straightness >= 0.6; a track younger than 1.0 s is never removed; a track still for 5.0 s is never removed | `vision/tracking.py:1102-1110,1125,1134`; applied `watch_merged.py` (`passers`, Settings `drinking_passerby_filter` ON); motorcycle conf >= 0.3 `recognition.py` (`MOTORCYCLE_MIN_CONF`) |
| vape-size gate | a Bottle under 10 % of the person is not a held bottle | `tracking.py:84` (`VAPE_BOTTLE_MAX_FRAC`) |
| knife jump / class flip | same rules as the cigarette (0.25 height, 2.0 s, 5 frames) | `location_momentum.py:128-130` |
| cooldown | one alert per place within `alert_cooldown` | `models.py:982` |

## 6. Detection Settings (live database, `core_systemsettings`, read 2026-11-13)

| setting | value | | setting | value |
|---|---|---|---|---|
| `smoking_enabled` | on | | `drinking_enabled` | on |
| `smoking_confidence` | 30 % | | `drinking_confidence` | 35 % |
| `smoking_dwell` | 3 s | | `drinking_dwell` | 8 s |
| `smoking_puff_count` | 3 | | `drinking_min_group` | 2 |
| `smoking_puff_window_minutes` | 5.0 | | `drinking_group_duration` | **60 s** |
| `thief_enabled` | on | | `drinking_passerby_filter` | **on** |
| `thief_confidence` | 25 % (knife floor is 0.50 in code) | | `drinking_mouth_proximity` | 3.0 |
| `thief_dwell` | 3 s | | `drinking_held_dwell` | 24 s |
| `alert_cooldown` | 120 s | | `drinking_evidence_max_age` | 12 s |
| `drinking_hours_enabled` | off | | `drinking_cooldown_center_dist` | 1.5 |

Defaults live in `core/models.py:667-982` and `core/vision/spec_settings.py` (`SPEC_DEFAULTS`; API floor for the long stay is 60 s,
`LIMITS`). Settings are re-read every few seconds by the detectors.

## 7. Model

| item | value | file:line |
|---|---|---|
| detector model | `core/vision/merged_v2.pt` (classes Bottle, Cigarette, knife), one pass for all three; env `LOOKOUT_MODEL` overrides | `core/vision/recognition.py:635-636` |
| class routing | cigarette -> smoking, bottle -> drinking, knife -> thief | `core/management/commands/watch_merged.py:86` |
| pose model | `yolov8n-pose.pt` (wrist, nose and shoulder keypoints; keypoint conf >= 0.5) | `recognition.py` (`GESTURE_KP_MIN_CONF`, `KP_LWRIST`/`KP_RWRIST`) |
| person tracker | ByteTrack, far mode 2x2 tiles | `watch_merged.py` (`--tracker`, `--tiles`) |

## 8. Run log (alpha testing; logging only)

Each Run Detection run on a recorded clip writes `alpha_logs/<clip>_<timestamp>.json` (header with the values above, one sample per
second per person or group, the alerts filed); `python scripts/export_alpha.py` turns them into `alpha_logs/alpha_summary.csv`.
Code: `core/vision/run_log.py`. Switch off with `LOOKOUT_RUN_LOG=0`. It reads detection state and never changes it.
