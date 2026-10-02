# Scoring / detection audit notes

Working notes, not a spec. Nothing here has been changed in code unless stated.

## Findings from the baseline run (merged_v2, insightface mouth anchor, 5 clips)

1. **Detection confidence floor.** `detect_*(conf=...)` never goes below 0.25, because `conf` is only used to filter the
   result afterwards; it is not passed to the model call, so ultralytics' default of 0.25 applies first.
   `detect_merged`'s default of 0.15 is therefore effectively 0.25. In `watch_merged` itself the requested floor is
   `min(smoking, drinking, thief confidence)` = 0.30 with current Settings, so production runs at 0.30; the 0.25 limit
   only bites for Settings below 25%, the `detect_merged` default, and `--calibration-csv` (which claims a 0.01 floor but
   is silently clipped to 0.25). Decide the intended floor, with the vote/dwell gate ("momentum") in mind.
2. **False knife alerts on non-holdup clips.** 3 knife-only alerts (cue `E14` only), scores 0.74-0.84, on `Aug18_18 - Trim2`,
   `Aug18_4 - DrinkingEveningFar3` and `Aug18_5 - DrinkingKabilangRoad1`. Per spec v6 a lone knife is 45 points
   (Monitoring only) and a holdup needs >= 2 people. Check: knife scoring, the 2-person gate, and the knife filters
   (head/shoulder region, > 1/4 person height, near-wrist).
3. **Drinking alerts with no cues.** Two `Bottle` alerts at 0.65 on `DrinkingKabilangRoad1` logged `cues: []`.
   They are Path B (gathering) alerts: `watch_drinking.py` calls `_create_alert(...)` for the gathering path **without
   `score_obj`**, so the cue list and the level are not passed through (and `Alert.level` is stored empty for them).
   Check what scores them and pass `score_obj` through.
4. **Level / status empty (`''`) on every logged alert.** Two causes: (a) the drinking gathering path above really does
   store no level; (b) for thief alerts it is a logging artefact only: the run-log hook reads `.level`, but a theft
   `Evidence` carries its band in `.band`. Smoking and drinking solo alerts do pass `score_obj`, so their level is set.
5. **Processing speed.** About 5-7.6 fps on 10 fps clips (slower than real time) on the dev laptop. Per frame the far
   path runs 1 whole-frame + 4 tile + about 2 person-crop model calls, plus person tracking and the mouth anchor. Note for
   performance work.
6. **insightface mouth anchor on the night-far smoking clip.** No face found on 151 of 184 attempts. Compare against the
   pose anchor in the after-run.
7. **Test clip coverage is weak.** Staged clips from the Hikvision camera position will be added to `TEST_CLIPS.md`.

## Findings from the "no smoking alert" diagnostic

8. **A lone cigarette cannot alert by design.** `cigarette` weighs 0.40; alerting needs a score >= 0.55 (`SCORE_WARNING`),
   0.35-0.55 is only WATCH. A cigarette held at the side stays WATCH; it needs `near_mouth` (+0.15 = exactly 0.55),
   `gesture` or `puffs` to alert. In the clips most cigarettes are held at the side.
9. **The vote / dwell gate is what blocks merged_v2.** A track only accrues dwell while a detection is no older than
   0.5 s (`ACCRUAL_STALE_SECONDS`) and >= 40% of the last 5 s of frames are positive (`VOTE_MIN_RATIO`), then needs 3 s of
   accrued time. merged_v2 detects a cigarette in too few frames to hold that (see the model comparison), so no track
   reached scoring (zero WATCH lines).
10. **merged_v2 vs smoking_v5, identical pipeline and settings** (`watch_smoking`, dry run, 3 smoking clips):
    detections >= 0.30 after NMS 102 / 214 / 199 (merged_v2) vs 406 / 485 / 484 (smoking_v5). smoking_v5 produced 2
    smoking alerts and 25 WATCH; merged_v2 produced none. smoking_v5 also fires on blurred vehicles, so its higher count
    is not all true positives.
11. **`_detect_far` ignores `NEAR_IMGSZ`.** Its whole-frame, tile and person-crop calls use ultralytics' default
    imgsz 640 (the near path uses 960). The whole-frame pass downscales 2560 px to 640 (4x); it finds almost nothing
    (7 of 250 detections >= 0.30 on one clip). Person crops (x2 upscale, then scaled to 640) find most of them.
12. **`watch_merged` refuses a model without Bottle and knife** (route-coverage check), so smoking_v5 cannot be tried
    in that command; compare via `watch_smoking` with `LOOKOUT_MODEL`.

## For later (Step 5): puff scoring source
Per scoring spec v6, the smoking puff points (1 puff 20, 2+ puffs 20, 3+ in 5 min 15) should come from the pose gesture
counter (hand to mouth), and "item at the mouth" (15) from object-to-mouth distance. Currently puffs are counted from
cigarette-to-mouth distance, so a puff-only path (no cigarette detected) cannot work. Also check that `scoring.py`
weights such as 0.15 / 0.20 match the spec points.

## Cigarette recall plan (merged_v2)
Smoking stays on merged_v2: no hybrid with smoking_v5. To do right after Steps 2-6 (pose swap, after-run, face removal,
`at_mouth` fix, notes, insightface delete), in this order (item 0 first):

0. **Mouth rule: hard reject -> `near_mouth` cue (+0.15). Do this FIRST, on its own, so the funnel test measures it
   separately.** Today `_apply_mouth_rule` *removes* a cigarette/vape detection that is more than `MOUTH_PROXIMITY`
   (2.5 face-widths) from the mouth, so a held-away cigarette never reaches scoring. With the pose anchor the rule now
   applies to far people insightface used to skip: on the baseline-vs-pose runs it cut 25 -> 29, 8 -> 16, 44 -> 99, 1 -> 30
   and 77 -> 114 detections (Aug14_3, Night-Far1, Trim2, Aug18_4, Kabilang), and about half of the events that previously had
   no anchor are now rejected. The crops show these are mostly real cigarettes held at the side. Instead: keep every
   detection, set the `near_mouth` cue only when the cigarette is within `MOUTH_PROXIMITY` of the mouth, so a lone cigarette
   (0.40) reaches scoring and shows as Monitoring per spec v6 (item g below).
b. **Native-resolution cascade pass for Cigarette in `watch_merged` / `watch_all`** (`detect_smoking_cascade`: person crops
   at native resolution). Measured on merged_v2: roughly 4x cheaper than the production far path (about 0.04 s vs 0.16 s per
   frame, so roughly 6 fps -> 5 fps) and as good or better on two of the three clips.
c. **Fix the 640 vs 960 `imgsz` mismatch** for the whole-frame, tile and person-crop calls in `_detect_far` (near path uses
   `NEAR_IMGSZ`, far path uses ultralytics' default 640).
d. **Pass `conf` explicitly to the model call** so floors below 0.25 work (see finding 1).
e. **Momentum object cue** from the Object Cue Accumulation spec, per (track_id, class), configurable
   `DECAY` / `ON` / `OFF` / `MAX` with defaults 0.90 / 1.5 / 0.4 / 3.0, replacing the 40% / 5 s vote and the 3 s dwell gate.
   Processing runs at about 6 fps, so momentum updates once per *processed* frame (not per source frame).
f. **Tune momentum on the 3 smoking clips.** Report hit rate, confidence and momentum traces; try `DECAY` 0.90 vs 0.95 (and
   the `ON` threshold if needed). Pick values where real held cigarettes turn ON and stay ON while the vehicle / motorcycle
   false hits do not.
g. **A cigarette alone (0.40) must show as Monitoring** per spec v6 (today the 0.35-0.55 band is WATCH and not stored).
h. **Re-run `detection_sandbox/funnel_run.py` on the 3 clips**: before vs after the fixes.
i. **After the defense:** fine-tune merged_v2 with more Cigarette examples at CCTV height.

## Round 2 findings from the Part 3 smoke test (not tuned; for Round 2)

Smoke test: Trim2 and the Holdup clip, `--clock`, `watch_merged`, DB copy. Evidence crops saved in
`detection_sandbox/output/audit_crops/`.

**a. Trim2 smoking Possible, "cigarette seen, 0 puffs" (alert 398).** Cues were `cigarette 0.40` +
`near_mouth 0.15` = 0.55, exactly the Possible cutoff. The Cigarette object cue was ON (momentum peaked at the
3.0 cap, detection 76%) and the item was within the mouth distance, so the "item at the mouth" point was earned.
No puff had been counted yet (gesture 0). Likely a real smoker (man at the curb, hand at the face), but it shows
that object + near-mouth alone reaches Possible with a single sustained detection. Round 2: check against
ground truth on more smoking clips before deciding whether the near_mouth point needs a stricter distance.

**b. Holdup clip smoking Possible/Likely (alert 402).** False positive. The "Cigarette" (50%) is a yellow phone
case held to the ear of a woman walking past (crop: `holdup_clip_smoking_FP_cigarette.jpg`). Cues were
`cigarette 0.40` + `gesture 0.20` (one hand-to-face movement counted as a puff) = 0.60 Possible, later 0.75
(Likely) when near_mouth was added. A phone call is exactly the case the AI checker's
`hand_to_mouth_activity = other_activity` is for (display only), but the official score is unchanged by it.
Round 2: the merged model fires on phones; consider a hard-negative set (phones at the ear) for retraining, and
whether a gesture counted while the item never moves to the mouth region should earn the puff point.

**c. Trim2 drinking gathering counted a passing motorbike rider (alert 400, "3 persons").** The row's cues were
only `bottle 0.40` + `time_band 0.05` = Monitoring. The gathering cue was NOT earned, so the rider did not add
points, but the description counts three people because cluster membership is just proximity
(`GROUP_CLUSTER_DIST` = 1.8 box-widths) of any person box, with no per-member movement check. "Stationary" is
defined on the CLUSTER, not its members: `Cluster.stationary(now, window)` requires the union box centre to
stay within `STATIONARY_DIST` = 0.6 mean box-widths over the window (>= 5 s, up to 30 s for the `gathering`
cue; the full group-duration window for `gathering_duration`). A moving member shifts the union box and fails
the check, which is why no gathering points were earned here. The Monitoring row came from a Bottle detection
(a carried bag) on a walking pedestrian, which spec v6 allows (a bottle carried past is Monitoring). Round 2:
count only members that are themselves roughly stationary when reporting the group size, and review whether a
moving person should be allowed to create a gathering row at all.

## AI checker model choice (Part 4)

Benchmarked on the real crop path (smoker at curb, the passing-bike "gathering", the holdup knife), GPU 6 GB:

| Config | Valid JSON | s/call (alone) | s/call (detection running) | Memory |
|---|---|---|---|---|
| 2B-instruct, 8 frames, ctx 10240 | 3/3 | 8.8 | 11.3 | 100% GPU, 2.8 GB |
| 2B-instruct, 12 frames, ctx 16384 | 3/3 | 13.8 | n/a | 100% GPU, 3.4 GB |
| 4B-instruct, 8 frames, ctx 10240 | 3/3 | 26.0 | 20.8 | 23% CPU / 77% GPU (spills), 4.8 GB |

Chosen: 2B-instruct, 8 frames, ctx 10240. Each image costs about 1,100 tokens regardless of crop size, so 12
frames need ctx 16384 and 12 frames at ctx 8192 fails with HTTP 400. Upscaling the small smoker crop to 512 px
changed nothing useful (left off). The 2B model misses the knife confrontation (said "unlikely", the 4B said
"likely"); the cost is only a missing upward suggestion, since the AI never changes the official status.

## Holdup AI accuracy test (before Part 5)

Holdup clip (staged knife confrontation), same crop path:

| Config | Answer | s/call |
|---|---|---|
| 2B-instruct, 8 frames | unlikely / other_activity | 3.6 warm (15 cold) |
| 2B-instruct, 12 frames (context 17408) | unlikely / other_activity, high | 4.1 warm (20 cold) |
| 4B-instruct, 8 frames | likely / confrontation, object pointed, high | 16.9 warm, 52-56 on a model switch |

More frames did not fix the 2B. With detection running (Holdup clip, `watch_merged --dry-run`) while the AI
calls alternated 2B -> 4B -> 4B -> 2B -> 4B continuously: nothing crashed or blocked, but the clip took
191 s against 156 s and 152 s alone (about +23% for the minutes the AI was busy). Only one model fits the
6 GB GPU well beside detection, so each switch costs a reload (about 50 s for the 4B). That is a worst case
(back-to-back calls); a real holdup is rare and the call is asynchronous, so only the AI card waits.

Decision: per-violation AI model. Smoking and drinking use the 2B, holdup uses the 4B (`vlm_model_holdup`,
Settings > System > AI checker; blank = use the main model). Limitation to state plainly: during a holdup
check detection may slow by roughly a fifth for under a minute, and the holdup AI card appears later than the
others.

## Part 5 notes

- The old ordinance-hours gate shared the `drinking_start/end` columns with the spec's evening band. Migration
  0049 moves rows whose gate is off to the spec band (16:00-24:00) and the Settings toggle was removed; the
  dormant gate code in `watch_drinking` is still there (default off) and should be deleted in Round 2.
- `drinking_group_duration` on the live DB is 25 s (a testing value). It shows as "changed" next to the
  spec default of 10 minutes; use "Reset to spec defaults" before measurement runs.
