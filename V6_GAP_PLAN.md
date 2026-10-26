# v6 + momentum integration: gap plan (Part 1, read-only)

Sources: `docs/specs/LookOut_Scoring_Spec_v6.md` (v6), `docs/specs/LookOut_Object_Cue_Momentum_Spec.md` (M),
`SCORING_AUDIT_NOTES.md`. Code references are `file:line` at commit `0a0af40`.
Scale: the internal 0-1 scale is kept; 40 pts = 0.40, Possible = 0.55, Likely = 0.75, cap 1.00.

## A. Statuses, steps, safety rules (v6 sections 1-4)
| Spec | Current code | Verdict | Planned change |
|---|---|---|---|
| Statuses Monitoring / Possible / Likely (2) | `scoring.py:56-63` levels violation 0.75, warning 0.55, **watch 0.35**, none; `LEVEL_LABELS` `scoring.py:95` (watch = "Not shown") | DIFFERS | Add a `monitoring` level. Monitoring = object cue ON, not a score band. Drop the 0.35 watch band. `warning` / `violation` keep their stored names (labels are already Possible / Likely). |
| Monitoring stored, on a watchlist, no notification (2) | Alerts are written only when `score.alerting` (`watch_smoking.py:988`, `watch_drinking.py:1095,1230`); WATCH events are only counted in stats | MISSING | Write an Alert row when the cue turns ON (level `monitoring`); update that same row as the score rises or the object leaves. |
| Possible 55-74, Likely 75+ (2, 3 step 4) | `scoring.py:116 level_of` | MATCHES | none |
| Hysteresis: leave only 5 below the entry threshold; Monitoring ends when the object is gone and the score decays (2) | `scoring.py:140-159 level_with_hysteresis` exists (drop 0.05) but **no watcher uses it** (only `tests_layer_e.py:720`); every watcher alerts once per track and never re-levels | DIFFERS | Per-incident state machine that carries the previous level (reuse `level_with_hysteresis`, add monitoring), re-levels every processed frame and updates the Alert row. |
| Step 1: sum system indicators only, no AI points (3, 4) | `Score.__init__` `scoring.py:528-548` adds a capped `vlm_score` into the total; `theft.Evidence.rescore` adds E30-E34 | DIFFERS | Remove every `vlm_*` weight, `VLM_CAP`, the confidence scaling of points and the x0.25 `SCENE_MULTIPLIER` (`scoring.py:371,430`, `DRINKING_WEIGHTS`, `SMOKING_WEIGHTS`, `HOLDUP_VLM_WEIGHTS`). Official score = system indicators only. |
| Step 2: holdup time multiplier (7) | `scoring.py:178 MANILA_HOUR_BLOCKS` (0.76 0.80 0.56 1.25 1.31 1.36 1.09 0.87) | MATCHES | none; check it is applied to every holdup row including the knife (see D) |
| Step 3: cap 100 | `scoring.py:547` | MATCHES | add a test (110 -> 100) |
| Step 5: puff-only capped at Possible (3, 6) | `POSE_EXCEPTION` `scoring.py:355` keeps pose-only at NONE unless the AI "releases" it | DIFFERS | Puff-only: no gate; 3+ puffs = 55 = Possible, hard-capped at Possible, tagged. |
| Step 6 + safety rule: the AI never changes the official status (3, 4, 8) | the AI adds points and cuts x0.25 today | DIFFERS | Display-only suggested status (Part 4). |
| No object, no alert, except puff-only (4) | `Score.gate_open` `scoring.py:554` | MATCHES | keep the gate, drop the AI-release exception |

## B. Drinking (5)
| Spec | Current | Verdict | Change |
|---|---|---|---|
| Bottle 40 / 10+ min 15 / at mouth 15 / group 10 / evening 5 = 85 | `DRINKING_WEIGHTS` `scoring.py:251-265`, same numbers | MATCHES | remove the three `vlm_*` entries |
| A person in a gathering gets the group and duration points on the per-person path | per-person cues are only `bottle`, `at_mouth`, `time_band` (`watch_drinking.py:1080-1087`); gathering / duration exist only on the cluster path (`:1216`) | MISSING | In `_process_track`, look the person up in the `GroupTracker` clusters and add `gathering` / `gathering_duration` when they belong to one. |
| Behaviour points build silently; when the bottle appears the full score appears at once (examples) | The cluster path needs bottle evidence; the solo path scores only on the bottle | PARTLY | Keep gathering / duration points per track continuously so Possible can open the moment the bottle cue turns ON. |
| Bottle at mouth scores only with the bottle (5 note) | `CONDITIONAL_CUES` `scoring.py:465` | MATCHES | the "at mouth" string bug is already fixed (`31f38fa`) |
| No time multiplier; evening band 16:00-24:00 | `DRINKING_HIGH_BAND` `scoring.py:226` | MATCHES | none |
| A bottle on a table near a group must still count for the gathering path | `tracker.assign` (`tracking.py:800`) gives a bottle to a person only if its centre is inside that person's box + `ASSIGN_REACH`; a bottle on a table is not, so it becomes a **scene track**, and the gathering code collects evidence only from `not t.is_scene` members (`watch_drinking.py:~782`, same in `watch_merged._process_drinking_frame` and `watch_all._run_gathering`). So **today a bottle on a table is discarded** (solo path: "discarded: no person (scene)") and never reaches the cluster. | DIFFERS (bug) | `Cluster` gets a method that also claims scene-track bottles whose centre lies inside the cluster's bbox expanded by about one person-height; all three drinking loops use it. |

## C. Smoking (6) and the three paths
| Spec | Current | Verdict | Change |
|---|---|---|---|
| Item 40 (gate) | `SMOKING_WEIGHTS["cigarette"]` `scoring.py:283` | MATCHES | the cue now comes from momentum (Part 3) |
| 1 puff = 20, 2+ puffs = 20, 3+ puffs within 5 min = 15, **all from the pose gesture counter** | `puffs` is fed from cigarette-to-mouth **distance** (`tracking.update_puff` `tracking.py:386`, via `watch_smoking.py:507`) with `PUFF_MIN_CYCLES = 1` (`watch_smoking.py:98`); `gesture` (pose, 20 pts) exists (`tracking.py:414`) but pose is opt-in `--pose` (`watch_smoking.py:343`); there is no 3-in-5-min cue; the gesture window is 20 s (`tracking.py:81`) | DIFFERS | Cues: `gesture` = >= 1 pose gesture (20), `puffs` = >= 2 (20), new `puff_pattern` = >= 3 within 300 s (15). Widen the gesture history to 300 s. Pose becomes always-on: one full-frame pose call per processed frame, shared with the mouth anchor (replacing the per-person crop calls). Remove the object-distance puff counter. |
| Item at mouth 15 (only with the item) | `near_mouth` cue `watch_smoking.py:975`, but `_apply_mouth_rule` (`:465`) **hard-rejects** items beyond 2.5 face-widths | DIFFERS | Recall-plan item 0: keep every detection and set `near_mouth` only within `MOUTH_PROXIMITY`. |
| Path 1, cigarette detected: item = Monitoring; item + puff = 60; + at mouth = 75 | the arithmetic works once the cues above exist | MATCHES after the fix | tests |
| Path 2, one puff and no item: a high-resolution check on a close crop of hands and face for the next few seconds; found -> normal path, not found -> logged only | none (`detect_smoking_cascade` exists in `recognition.py` but a puff never triggers it) | MISSING | On the first gesture without an item, run the cigarette detector on a native-resolution crop of that person's head and hands for N seconds; log the puff either way. |
| Path 3, no item and 3+ puffs: 20 + 20 + 15 = 55, Possible, tag "No smoking item detected — based on hand movement only"; 1-2 puffs are logged only | `POSE_EXCEPTION` forces NONE unless the AI releases it | DIFFERS | New puff-only path as above; the tag is stored in `cues` and shown on the card. |
| No smoke-plume indicator; vapes have no class | none present | MATCHES | none |

## D. Holdup (7)
| Spec | Current | Verdict | Change |
|---|---|---|---|
| Knife 45 (E14), frozen pair 20 (E12), loitering 10 (E10) = 75 | `theft.WEIGHTS` `theft.py:139` | MATCHES | the "missing cues" comment (`theft.py:162-169`) is obsolete |
| The time multiplier applies to every row | Layer E path: `E20` multiplier. The **single-knife path** builds `Evidence("weapon", {"E14": 0.45}, {})` with **no multiplier** (`watch_thief.py:1026`) | DIFFERS | apply the Manila multiplier on both paths |
| At least 2 people (hard gate) | enforced only on the Layer E path (`watch_thief.py:1076`, `HOLDUP_MIN_PERSONS` `:191`); **not** on the single-knife path | DIFFERS | gate Possible / Likely on >= 2 people on every path |
| A knife alone always starts Monitoring | The single-knife path creates an Alert for any knife that survives vote + dwell, with `confidence = YOLO box score` (`watch_thief.py:1013`, `_create_alert(best_score, ...)`) and level `watch` (the Evidence band of 0.45) | DIFFERS | **Root cause of the baseline "knife-only alerts at 0.74-0.84":** the stored `confidence` is the YOLO box confidence, not the score, and the path never checks the band or the 2-person rule. Fix: level `monitoring`, score = 0.45 x multiplier, YOLO confidence kept only in `object_confidence`. |
| Knife filters: head/shoulder region, > 1/4 person height, near a wrist; must survive the vote and dwell windows | `_apply_weapon_region_rule` `watch_thief.py:516`, constants `:84-107` | MATCHES | keep the filters; vote / dwell are replaced by momentum (Part 3) |
| Worked examples (vendor 61 -> 88, night 26 -> 65) | follow from the arithmetic | MATCHES after the fix | tests |

## E. AI checker (8, 9)
| Spec | Current | Verdict | Change |
|---|---|---|---|
| Trigger: on entering Monitoring, and when puff-only reaches Possible | `_queue_context` runs only **after an alert is created** (`watch_smoking.py:1090`, `watch_drinking.py:1261`, `watch_thief.py`) | DIFFERS | trigger from the incident state machine |
| 8-12 frames bunched around the detection or puff | `FRAME_COUNT = 3`, spacing 1 s, plus 1 scene frame (`vlm.py:116-118`; setting `vlm_frames` default 3) | DIFFERS | frame buffer sampling around the trigger; update the setting defaults |
| Crops: smoking half body; drinking whole group and surroundings; holdup both people | one person box padded 40% (`vlm.py:122 CROP_PAD`, `verify_frame` ~`:1102`) | DIFFERS | per-violation crop builders |
| Shared instructions with `{system_note}`, observations first, v6 field lists | v3 `SYSTEM_PROMPT` `vlm.py:473` (no system_note); `DRINKING_SPEC` / `SMOKING_SPEC` / `HOLDUP_SPEC` `vlm.py:370-460` use the old fields (`group_appears_to_be_drinking_together`, `appears_to_be_a_holdup`, `person_appears_to_be_smoking`, ...) and a free-text `reason` | DIFFERS | rewrite the prompts and the JSON schema exactly as section 9; remove the old fields |
| Suggested status: a lookup, one step at most, HIGH confidence only, stricter going up (two agreeing answers for drinking / holdup), puff-only never above Possible; texts "Likely — AI agrees", "No change — X", "AI context unavailable" | none; today the AI adds points and cuts x0.25 (`scoring.py:371,430`) | MISSING | pure function `suggest_status(official, kind, answers, puff_only)`, recomputed on read when the official status changes |
| Invalid JSON -> "AI context unavailable", official status unchanged | `Verdict.ok` / `unavailable()` `vlm.py:282` | MATCHES (concept) | map to the card state; add a test |
| Local provider: no blur | `blur_required` `vlm.py:137` | MATCHES | done in `7e64347` |
| Model: Qwen3-VL (2B video via Transformers, or 4B frames via Ollama; "being finalised") | Ollama 4B frames only (`vlm.py:79-84`) | PARTLY | stay on Ollama frames; video / 2B left open |

### E2. AI checker details to build (decisions of the plan review)
- **Model.** Qwen3-VL through local Ollama, default 2B. Test 2B vs 4B on the same clips and report JSON-valid rate, the answers, seconds per call and GPU memory with detection running; keep the winner as the default (model name stays a Setting).
- **Async.** The call never blocks the detection loop.
- **Frames.** A rolling buffer of full-resolution frames (no drawn boxes). On trigger take 8-12 frames bunched around the detection / puff (denser close to the trigger).
- **Crops.** Per violation, following the tracked box from frame to frame: smoking = half body (head, shoulders, hands); drinking = whole group plus surroundings; holdup = both people. Crop from the full-resolution frame, then resize. Raw frames only: no boxes, labels or overlays.
- **Audit.** Save the exact frames sent with the alert, so an answer can be checked against what the model saw.

## F. Alert UI (2) and review
| Spec | Current | Verdict | Change |
|---|---|---|---|
| Status card: solid border, evidence checklist, no number, spec info text | `ViolationModal.jsx` ~`:982-1070` shows the level and a checklist, but the info text differs | PARTLY | rewrite to the spec wording |
| AI context card (dashed, AI icon, badge, observations <= 20 words, answered fields, "AI-generated · may be wrong") | one "AI checker said" line ~`:1039-1052`, `:1084-1111` | DIFFERS | new component |
| Status with AI context card (dashed, exact wording) | none | MISSING | new component |
| Review tag Pending / Verified / Dismissed replaces "Active"; Verified / Dismissed write `reviewed_valid` | `statusConfig` "Active" (`ViolationModal.jsx:13`, `AlertFeed.jsx:115`); `reviewed_valid`, `reviewed_by`, `reviewed_at` exist on `Alert` | PARTLY | derive the tag from `reviewed_valid` (null = Pending) and wire the Verified / Dismissed actions |
| No confidence x100 in AlertFeed | `AlertFeed.jsx:337`, `:495` show "% conf" | DIFFERS | remove |
| Monitoring as a quiet dashboard watchlist | none | MISSING | Overview watchlist panel; excluded from the notification list and the officer app |
| Officer app: only Possible / Likely notify; correct labels | the officer app polls `/alerts/` (no push); its level type lists `none / watch / warning / violation` (`officer_app/lib/api.ts:224`); the label comes from `level_label` | PARTLY | filter monitoring out and update the type |

### F2. Settings (Part 5): adjustable indicator timings and conditions
| Item | Current | Verdict | Change |
|---|---|---|---|
| Object confirmation time (~2 s) | `smoking_dwell` 3, `drinking_dwell` 8, `thief_dwell` 3 (`models.py` SystemSettings) | DIFFERS | one "object confirmation" per violation, spec default ~2 s (momentum ON/OFF in Part 3) |
| Drinking: stay 10 min, minimum group 2, evening band | `drinking_group_duration` 600, `drinking_min_group` 2, `drinking_start/end` 16:00-00:00 | MATCHES (fields exist) | surface them under one "indicator conditions" block |
| Smoking puff window (3 puffs in 5 min) | hard-coded `GESTURE_WINDOW_SECONDS` 20 s | MISSING | settings `puff_count` / `puff_window_seconds`, defaults 3 / 300 |
| Holdup loitering time | `LOITER_SECONDS = 20` (`theft.py:61`) | MISSING | setting, default from the spec |
| Holdup "nearby person" distance | none | MISSING | setting, default 1.75 person-heights (range about 1.5-2), also read by Part 2 |
| Reset to spec defaults per violation | none | MISSING | button in Settings per violation panel |
| Points and the 55 / 75 cut-offs | module constants | MATCHES | **not editable** (stay in code) |
| Active settings logged with each alert | not logged | MISSING | `cues["settings"]` snapshot on every Alert |
| Footage clock for uploaded clips (`--clock "YYYY-MM-DD HH:MM"`) | CLI option built in Part 3 (`core/vision/clock.py`); Run Detection does not pass it yet | PARTLY | Part 5: a "footage start time" field in the Run Detection upload form that passes `--clock` to the job |

## G. Momentum spec (M)
| Item | Current | Verdict | Change |
|---|---|---|---|
| `momentum = momentum*DECAY + conf`, cap, ON/OFF hysteresis, defaults 0.90 / 1.5 / 0.4 / 3.0, exposed as config | none; the gate is the 5 s / 40% vote + 0.5 s staleness + 3 s dwell (`tracking.py:65-93`, `present` / `accruing` `:513-553`) | MISSING | new `ObjectMomentum` per (track_id, class); config as module constants per class, overridable in `SystemSettings` if wanted |
| Placement between tracking and scoring; the cue feeds scoring | vote / dwell live in each `_process_track` | DIFFERS | replace vote / dwell with the cue |
| Slot created on the first confidence > 0, destroyed when the track is lost; no momentum across ids | n/a | MISSING | cleanup in the tracker's drop path |
| Update per **processed** frame (4-6 fps) | n/a | note | DECAY is per processed frame, so its memory in seconds depends on fps; document it |
| Log the momentum with each alert | `Alert.cues` JSON | MISSING | `cues["momentum"]` |
| Tests: a flicker holds the cue; an object leaving turns it OFF | n/a | MISSING | unit tests, then clips |

## H. Migrations needed
1. `Alert.level` choices gain `monitoring` (choices only, no schema change) plus a **data migration**: existing `watch` rows -> `monitoring`.
2. `Alert.last_seen_at` (DateTimeField, null) so the watchlist can tell an active Monitoring event from a finished one.
3. No migration for the AI fields: answers, observations, the badge inputs, `puff_only` and `momentum` live in `Alert.cues` (JSON); the suggested status is computed on read. (The old `vlm_verdict / vlm_confidence / vlm_reason` columns stay, unused, for a later cleanup.)
DB backup before each; migration files shown before applying.

## I. Decisions needed before Part 2
1. **Knife and people (decided).** The second person must be *near* the knife holder (about 1.5-2 person-heights, configurable); knife alone or nobody nearby stays Monitoring. (Original question:)
   **Knife and people.** v6's own vendor example reaches Possible (45 x 1.36 = 61) with the knife alone, while section 7 says a holdup needs >= 2 people. I read it as: >= 2 people in frame is the gate for Possible / Likely; with fewer people the event is Monitoring only. So a knife with 2 people at 09:00-18:00 can be Possible without a freeze.
2. **Existing `watch` rows** become `monitoring` (they are object-detected, stored events).
3. **Pose always on** for smoking. Cost: one full-frame pose call per processed frame, replacing the per-person crops. FPS measured interleaved in Part 3.
4. **Monitoring evidence.** Write the evidence image on entry and (re)write the clip when the event rises or ends, so Monitoring does not produce a clip per passer-by.
