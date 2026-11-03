# CHANGES: smoking false alarms, holdup ghost partner, evidence-clip labels

Branch: `feat/v6.2-location-momentum` (not `feat/single-camera`: that branch is an ancestor with none of the v6.2 code these fixes modify; chosen with you). Nothing pushed.

## A. Smoking: stop pose-only false alarms

- **Stricter keypoints for the gesture counter only.** Nose and the wrist used must be at least 0.5; an unknown frame neither raises nor lowers the hand state. New `recognition.gesture_ratio` ([recognition.py:732](core/vision/recognition.py#L732)); constants `GESTURE_KP_MIN_CONF`, `GESTURE_EYE_MIN_CONF`, `GESTURE_MIN_PERSON_HEIGHT_PX`, `GESTURE_MIN_SHOULDER_PX` at [recognition.py:639-643](core/vision/recognition.py#L639). The general `KP_MIN_CONF` (0.3) is unchanged because the mouth anchor and the puff gate also use it.
- **Facing check.** Nose under 0.5, or neither eye at 0.4 or better = facing away: no gesture scoring that frame ([recognition.py:732](core/vision/recognition.py#L732)).
- **Minimum size.** No gesture scoring when the person box is under 120 px tall or the shoulders under 25 px wide (native resolution). Same function.
- **Apex hold.** The hand counts as "at the face" only after the ratio stays at or under 0.7 for 0.6 s of video time; no upper limit, so a 2-4 s vape draw passes ([tracking.py:89](core/vision/tracking.py#L89); logic in `update_gesture`, [tracking.py:449](core/vision/tracking.py#L449)). Two at-face samples more than 1.5 s apart restart the timer ([tracking.py:92](core/vision/tracking.py#L92)). Dipping into the 0.7-1.3 band before the hold is met also restarts it.
- **Puff-only cap.** With no smoking item ever seen on the person, hand movement alone is held at Monitoring, never Possible. `Score(puff_only_max=...)` ([scoring.py:331](core/vision/scoring.py#L331), [scoring.py:379](core/vision/scoring.py#L379)); per-track flag `smoke_item_seen` ([watch_smoking.py:943](core/management/commands/watch_smoking.py#L943)); switch `PUFF_ONLY_NEEDS_ITEM_FOR_POSSIBLE` ([watch_smoking.py:117](core/management/commands/watch_smoking.py#L117)). Card text: "capped: no smoking item seen".
- **Drinking stand-down.** If the drinking engine associated a Bottle with the same ByteTrack id (or the same box under greedy tracking) in the last 10 s, that person's gesture cycles are not counted and their half-finished raise is discarded. `BOTTLE_STANDDOWN_SECONDS` / `BOTTLE_STANDDOWN_IOU` ([watch_smoking.py:118](core/management/commands/watch_smoking.py#L118)), `_bottle_standdown` ([watch_smoking.py:598](core/management/commands/watch_smoking.py#L598)). Smoking runs before drinking in a frame, so it reads previous frames' associations, shared through `bottle_seen` ([watch_merged.py:277](core/management/commands/watch_merged.py#L277), [watch_merged.py:650](core/management/commands/watch_merged.py#L650); cap `BOTTLE_SEEN_MAX`, [watch_merged.py:78](core/management/commands/watch_merged.py#L78)). `watch_all` (its own older loop) does not feed it, so it has no stand-down.
- **Wiring:** the gates are called from `_feed_pose` ([watch_smoking.py:527](core/management/commands/watch_smoking.py#L527)). Every skip shows in `--stats` as "gesture skipped: <reason>".
- **Settings window: not changed in code.** Recommendation: lower `smoking_puff_window_minutes` from 5 to about 1.5 (90 s). Every counted cycle is now a held raise, so the long window is the remaining source of "three touches in five minutes". Keep `smoking_puff_count` at 3 unless you want vape-only coverage back (see D).

## B. Holdup: "2 people" must mean 2 real people

- **Where the partner was picked:** E12 pairs are built in `TheftEngine._pair_evidence` ([theft.py:1150](core/vision/theft.py#L1150)); the knife holder's second person in `watch_thief._knife_evidence` / `_valid_partner` ([watch_thief.py:1085](core/management/commands/watch_thief.py#L1085)). Both used every live track, including lost ones kept for up to 2 s.
- **Logging:** each candidate partner is logged once per change of verdict to `$LOOKOUT_MOUTH_LOG` as `kind: "partner"` (holder, partner id, track age, frames missed, seconds since last real detection, box sizes, confidence, distance) and counted in `--stats` as "holdup partner: <verdict>".
- **Valid partner** (`theft.partner_problem`, [theft.py:264](core/vision/theft.py#L264); constants [theft.py:125-129](core/vision/theft.py#L125)): matched by the person detector THIS frame, confidence at least 0.4, box height at least 0.5 x the holder's, and stayed within 0.6 box widths for 2 s (this is what excludes a passing rider). A track seen for under 2 s cannot prove it is standing.
- **Frozen = real detections only:** pairs are built only from tracks matched this frame ([theft.py:1150](core/vision/theft.py#L1150)); a pair that loses a half drops its freeze at once (the 5 s cue-hold still runs, from the last REAL frame). `holdup_pair_cues` takes the holder and requires a valid partner ([theft.py:1447](core/vision/theft.py#L1447)).
- **Cap:** the existing rule is now fed honest input: no valid partner = `people_near` false = Monitoring at most. An incident already at Possible/Likely keeps its level (existing "sticky" rule), but it can only have got there with a valid partner.
- **Knife confidence:** `KNIFE_MIN_CONF = 0.50` ([watch_thief.py:124](core/management/commands/watch_thief.py#L124), applied [watch_thief.py:509](core/management/commands/watch_thief.py#L509)); a higher Settings value still wins.
- **Knife on phones:** I found no phone in the clips I looked at. On SF LolliNear1 the knife class fired 406 times on the woman (ID 10). I looked at one frame (t=54 s, conf 0.575): it is her lollipop stick, hands behind her back. Across the clip, 194 of 488 knife samples are under 0.5 and are now dropped; the rest are not (245 of the 406 on ID 10 are 0.5 or more), so the floor alone does not fix this; the partner rule does. I did not measure a phone rate.

## C. Evidence clip: who triggered it

- **Label and box:** the triggering person is boxed thick (5 px) in the status colour (Monitoring grey, Possible orange, Likely red) with `ID 10 - Smoking - Possible` above it ([_incidents.py:467](core/management/commands/_incidents.py#L467); constants [_incidents.py:40-50](core/management/commands/_incidents.py#L40)). OpenCV's fonts have no middle dot, so the separator is a hyphen. Drawn in `_incident_sync` ([_incidents.py:508](core/management/commands/_incidents.py#L508)) before the frame reaches the clip buffer and the alert image, so both carry it. Drinking-group incidents show "ID G<n>".
- **Other people:** the existing thin grey person boxes stay; object boxes unchanged.
- **Holdup partner:** boxed thinner in cyan and labelled `partner` ([_incidents.py:467](core/management/commands/_incidents.py#L467); passed from [watch_thief.py:1010](core/management/commands/watch_thief.py#L1010)).
- **Stored on the alert, no migration:** `Alert.cues` is already a JSON field. It now carries `cues["trigger"] = {track_id, violation, cues:[{name, points}], partner_track_id}` ([_incidents.py:457](core/management/commands/_incidents.py#L457); written at [_incidents.py:539](core/management/commands/_incidents.py#L539) and on every update). Older alerts have none and show nothing.
- **Alert detail page:** "Triggered by: ID 34 - gesture 20, ..." under the clip controls (`TriggeredBy`, [ViolationModal.jsx:1152](../lookout/src/ViolationModal.jsx#L1152), used at [ViolationModal.jsx:327](../lookout/src/ViolationModal.jsx#L327)). Not viewed in a browser; checked with eslint only (the two errors it reports are in old code at lines 82 and 457).
- **Known rough edge:** a person with two incidents open at once (e.g. holdup and drinking) gets two labels in the same spot.

## D. Verification (dry run, `watch_merged --dry-run --stats`, same settings before and after)

Clip: `Accuracy\Smoking\SF LolliNear1.mkv` (76 s). I could not see your screenshots, so I used the clip that produced today's holdup alert 276. It has no 03:44 (it is 76 s long). The IDs match your description (1 and 22 far; 10 the woman).

| Track | Old highest status | New highest status |
|---|---|---|
| Smoking ID 1 (far) | Possible (puff-only, 0.55) | none |
| Smoking ID 22 (far) | Possible (puff-only, 0.55) | none |
| Smoking ID 10 (woman) | Likely (1.00) | Monitoring (0.75, capped: no puff) |
| Holdup ID 10 (woman) | Likely (0.75: knife + E10 + E12) | Monitoring (knife only) |
| Holdup ID 3 | Monitoring | Monitoring |
| Holdup ID 20 | Monitoring | none |

Partner verdicts across that whole run (new code): not detected this frame 115, too small 86, moving 12, seen too briefly 7, valid 9.

**Vape coverage got worse on the positive clip I tried (`HIde\S VapeNightNear.mkv`):** old = Possible (puff-only) on person #3; new = no smoking alert. Why:
- Person #3 made ONE real held draw (25.9-27.1 s, 1.2 s). The old counter reached "3 puffs" using that draw plus three sub-0.1 s wobbles of the ratio (19.7, 20.1, 28.8 s). The new rules count only the real draw, so 1 is under 3.
- The drinking stand-down also suppressed that one real draw: the merged model tagged the vape device as `Bottle` (183 of 329 feed samples flagged). Without the stand-down the new counter finds 1 cycle; with it, 0.
- The pose-quality filters were NOT the cause (40 of 42 frames with the hand at the face pass them).
- I tested only this one vape clip with before/after; the other clips in `HIde` were not run.
- Decision for you: if vape coverage matters more than the stand-down, make the stand-down need a higher Bottle confidence or several seconds of Bottle, or skip it for small Bottle boxes. The item-5 cap also means a vape-only person can no longer reach Possible unless a cigarette-class item is seen on them.

**Evidence clip render:** ran `HIde\S 2NearFar.mkv` against a throw-away database and media folder (not the real DB). It produced one Possible drinking alert with an annotated clip; the record carries `trigger` (ID 34, Drinking, its cues). In the rendered clip (frame 318) the person is boxed thick in grey with "ID 34 - Holdup - Monitoring" above it; the alert still image shows "ID 34 - Drinking - Monitoring". I did not see an orange or red trigger box in the frames I sampled (only grey), so those two colours are untested visually.

Reproduce: `LOOKOUT_MOUTH_LOG=run.jsonl python manage.py watch_merged --source <clip> --dry-run --stats` (set `KMP_DUPLICATE_LIB_OK=TRUE` on this machine). Raw before/after logs are in the session scratchpad, not in the repo.

## E. Strong-gesture exception, tighter drinking stand-down, stacked labels

- Strong-gesture exception to the "no item seen, max Monitoring" cap. Constants `STRONG_GESTURE_MIN_BOX_PX` = 250, `STRONG_GESTURE_MIN_HOLD_S` = 1.5, `STRONG_GESTURE_MIN_DRAWS` = 2, `STRONG_GESTURE_WINDOW_S` = 90 ([tracking.py:98-101](core/vision/tracking.py#L98)).
- A draw is a hold at ratio <= 0.7 of at least 1.5 s of video time, every sample on a box >= 250 px tall, ended by the hand dropping. The facing check already gates every ratio, so a back-turned frame is never a sample. Logic in `update_gesture` ([tracking.py:459](core/vision/tracking.py#L459)); `strong_gesture()` ([tracking.py:501](core/vision/tracking.py#L501)) is true when 2 draws fall within 90 s of each other.
- Box height is checked in `_count_gesture` ([watch_smoking.py:625](core/management/commands/watch_smoking.py#L625)); the cap is lifted in `_process_track` ([watch_smoking.py:990-996](core/management/commands/watch_smoking.py#L990)).
- Tag "Hand movement only — close range, sustained draws": `PUFF_ONLY_STRONG_TAG` ([scoring.py:300](core/vision/scoring.py#L300)), `Score(strong_gesture=...)` ([scoring.py:334](core/vision/scoring.py#L334), [scoring.py:363](core/vision/scoring.py#L363)).
- This only lifts the cap. Puff-only still needs the `puff_pattern` cue (Settings puff count, default 3 cycles), so a Possible needs 3 cycles of which 2 are strong draws.
- Drinking stand-down now needs a Bottle at conf >= 0.5 ([watch_smoking.py:119](core/management/commands/watch_smoking.py#L119), filter applied at [watch_merged.py:649](core/management/commands/watch_merged.py#L649)) on that ext_id for >= 2 s in total within the last 10 s ([watch_smoking.py:120](core/management/commands/watch_smoking.py#L120), `_bottle_standdown` [watch_smoking.py:601](core/management/commands/watch_smoking.py#L601)). Each sample credits at most 1 s ([watch_smoking.py:121](core/management/commands/watch_smoking.py#L121)), so a gap does not count as holding it.
- Evidence-clip labels for the same frame no longer overlap: a label that would collide with one already drawn moves up (down at the top edge) by one line height. `_stack_label_y` ([_incidents.py:467](core/management/commands/_incidents.py#L467)), used by `_draw_trigger` ([_incidents.py:498](core/management/commands/_incidents.py#L498), called at [_incidents.py:529](core/management/commands/_incidents.py#L529)). Not viewed in a rendered clip.
- Tests: `StrongGesture` class and two new stand-down tests in [tests_false_alarm_fixes.py](core/tests_false_alarm_fixes.py); existing stand-down tests now seed 3 s of Bottle samples. 130 tests pass.

## F. Replay: old vs current vs this change (`watch_merged --dry-run`, all 10 clips in Accuracy\Smoking and HIde\)

- old = HEAD (37cbead). cur = working tree before section E. new = with section E. Same settings and database, three runs in parallel on one GPU, so tracker ids can differ between runs.
- Vape clips: S VapeNightNear, S1 NearVape2, Vape Far Night. The rest I treated as non-vape. I did not check which clips are truly positive; SF FanFar and SF LolliNear1 look like false-alarm clips from their names.
- "no item" = highest incident had no cigarette cue (hand movement only).

| Clip | Vape | Smoking old / cur / new | Drinking alerts old / cur / new | Holdup old / cur / new |
|---|---|---|---|---|
| S1 NearVape2 | yes | Likely #5 / Monitoring #5, #46 / Possible #46 | 0 / 0 / 0 | Likely / Monitoring / Monitoring |
| SD 1 FarNear | | Possible #3, #8 (no item) / none / none | 2 Monitoring / same / same | Likely / Possible / Possible |
| SD NearFar | | Possible #29 (no item) / Monitoring #8 / Monitoring #8 | 1 Monitoring / same / same | Likely / Monitoring / Monitoring |
| SF FanFar | | Possible #15, #20 (no item) / none / none | 0 / 0 / 0 | Monitoring / Monitoring / Monitoring |
| SF LolliNear1 | | Likely #10 / Monitoring #10 / Monitoring #10 | 0 / 0 / 0 | Likely / Monitoring / Monitoring |
| S 2NearFar | | Possible #7, #34 (no item) / Monitoring #7 / Monitoring #7 | 1 Possible / same / same | Monitoring / Monitoring / Monitoring |
| S 2NearFar(1) | | Likely #18 / Possible #18 / Likely #18 | 0 / 0 / 0 | Possible / Monitoring / Monitoring |
| S FarWalk | | Possible #1, #2, #3 (no item) / none / none | 0 / 0 / 0 | Monitoring / Monitoring / Monitoring |
| S VapeNightNear | yes | Possible #3 (no item) / none / none | 0 / 0 / 0 | Monitoring / Monitoring / Monitoring |
| Vape Far Night | yes | Possible #3 (no item) / none / none | 0 / 0 / 0 | Monitoring / Monitoring / Monitoring |

- No far or back-turned person reaches Possible in the new run. Every Possible/Likely in "new" has a cigarette cue (S1 NearVape2 #46, S 2NearFar(1) #18). In the old run, 9 far/no-item people reached Possible across SD 1 FarNear, SD NearFar, SF FanFar, S 2NearFar, S FarWalk and the two night vape clips.
- The strong-gesture exception did not fire in any clip, so it recovers none of the three vape clips' hand-only alerts. S VapeNightNear and Vape Far Night give no smoking alert under cur or new. S1 NearVape2 comes back as Possible #46 under new (Monitoring under cur); I did not verify whether the stand-down change or run-to-run variation caused that.
- The new bottle stand-down changed no drinking result: drinking alerts are identical in cur and new. Drinking is identical across all three versions.
- S 2NearFar(1) #18 is Likely under new and Possible under cur, with the same code path except the stand-down and strong-gesture change; I did not trace it, so I cannot say which change caused it.
- I did not log person box heights per gesture, so I cannot say whether any vape draw reached 250 px. If you want that, log box height at each draw.
- Raw logs are in the session scratchpad, not in the repo.

## G. Cigarette-only Possible, and the false "near mouth" (ALT-0194, ALT-0196)

- **Cause of the false near mouth.** Both alerts are the same track (ID 15), Possible = cigarette 40 + near mouth 15, no gesture cue. `_apply_mouth_cue` ([watch_smoking.py:460](core/management/commands/watch_smoking.py#L460)) measures the cigarette-to-mouth distance on the RAW detections, before the location gate, within 2.5 face-widths (`MOUTH_PROXIMITY`). The location gate (`evaluate_track`, [location_momentum.py:388](core/vision/location_momentum.py#L388)) is a different, tighter test (0.7 shoulder-widths of a wrist or the mouth keypoint) and was the one that rejected the hit as `[off-anchor]`. `_near_mouth` ([watch_smoking.py:1026](core/management/commands/watch_smoking.py#L1026)) never looked at the gate's verdict, so a rejected hit still stamped `last_mouth_seen`, and the 5 s hold kept it.
- **Fix.** `near_mouth` now also needs the gate to have ACCEPTED a cigarette on the mouth anchor within 1 s: `accepted_at_mouth` ([location_momentum.py:685](core/vision/location_momentum.py#L685)), used in `_near_mouth` ([watch_smoking.py:1036-1043](core/management/commands/watch_smoking.py#L1036)). `loc.last_hit` is written only for accepted hits, so off-anchor, jump, flip and no-keypoint rejections never count. Counted in `--stats` as "near mouth withheld: not accepted on the mouth anchor". I could not replay the exact ALT footage (the alerts' clips are on Cloudinary and I did not download them), so this is confirmed by reading the code and by unit tests, not by a replay of those two alerts.
- **Possible rule.** Cigarette confidence >= 0.45 AND (a puff in the last 10 s OR a cigarette accepted on the mouth anchor with momentum >= 1.0); otherwise Monitoring at most. `POSSIBLE_MIN_CONF`, `POSSIBLE_MOUTH_MOMENTUM` ([location_momentum.py:97-98](core/vision/location_momentum.py#L97)); rule in `puff_cap` ([location_momentum.py:693](core/vision/location_momentum.py#L693)); the call passes the cue's confidence and momentum ([watch_smoking.py:986-987](core/management/commands/watch_smoking.py#L986)). Card text: "capped: cigarette confidence under 0.45", or the existing "capped: no puff". Likely is unchanged (still 2 puffs in 30 s). An incident already at Possible/Likely is not pushed back (existing sticky rule).
- **Decision for you:** by "puff cycle" I used the existing puff (wrist reaches the mouth with a cigarette, or the far-range object puff), because that is what the old no-puff cap counted. It is not the pose gesture cycle. If you meant the gesture cycle, it is a one-line change. Note ALT-0194/0196 reached Possible under the old puff-only rule with no gesture cue, so a puff was counted there some other way; with the near-mouth fix they would have scored 40 only (Monitoring).
- Tests: `PossibleEvidenceRule` and `NearMouthNeedsAnAcceptedHit` in [tests_false_alarm_fixes.py](core/tests_false_alarm_fixes.py). Full suite: 1059 tests pass.

## H. Night light check (report only, no fix)

How often the Cigarette class fires on bright red/orange blobs (tail lights). Method: one frame every 0.5 s, person detector + the far smoking detector at conf >= 0.25, Cigarette detections only. "Blob" = at least 15 % of the box's pixels are saturated red/orange (hue <= 25 or >= 170, S >= 120, V >= 200). Script and raw rows are in the session scratchpad.

| Clip | Frames | Cigarette dets >= 0.25 | >= 0.45 | On a red/orange blob | Blob and >= 0.45 | Blob not on a person |
|---|---|---|---|---|---|---|
| S VapeNightNear | 79 | 60 | 31 | 0 | 0 | 0 |
| Vape Far Night | 157 | 71 | 12 | 1 | 0 | 1 |
| DSingle NearNight | 68 | 85 | 54 | 1 | 1 | 1 |

- Two hits total across 216 detections. Vape Far Night t=72.0 s: conf 0.44, box 105x44 px, 53 % red/orange, outside every person box. DSingle NearNight t=5.0 s: conf 0.48, box 34x16 px, 24 % red/orange, outside every person box.
- Neither can reach Possible on its own now: a cigarette off every person is a scene detection and is discarded, and the first is under 0.45.
- These three clips are the only night footage I checked; there is no vehicle traffic in the sampled frames I measured, so this says the rate is low here, not that tail lights are safe. The colour test is also crude (a lit cigarette tip is itself orange); I did not look at the two frames.

## I. Drinking: person-crop Bottle pass

- **Report.** `watch_merged` runs merged_v2 on the full frame at `NEAR_IMGSZ` ([recognition.py:619](core/vision/recognition.py#L619)), 960 with CUDA, else 640. Default (far) mode goes through `detect_merged_far` ([recognition.py:1464](core/vision/recognition.py#L1464)) -> `_detect_far` ([recognition.py:1240](core/vision/recognition.py#L1240)): whole frame + 2x2 tiles + every person box cropped and upscaled 2x, all at `FAR_IMGSZ` (= 960), all classes including Bottle. `--fast` is the whole frame only.
- **Hi-res check.** Drinking has no equivalent of smoking's `_hires_check` ([watch_smoking.py:505](core/management/commands/watch_smoking.py#L505)) and no Bottle-specific crop. The smoking cascade ([watch_merged.py, "Cigarette recall"](core/management/commands/watch_merged.py#L602)) keeps Cigarette/Vape only. The far-mode crops in `_detect_far` do already see Bottle, but they are unpadded, take every person, and are not native-res crops fed at 640.
- **New pass.** `detect_bottles_on_person_crops` ([recognition.py:1354](core/vision/recognition.py#L1354)) reuses `detect_on_person_crops` ([recognition.py:1308](core/vision/recognition.py#L1308)), which gained a `box_fraction` argument (default 1.0, no change for smoking). Crops come from the native frame: top 75 % of the person box + 20 % padding, run at imgsz 640, Bottle kept, boxes mapped back to frame coordinates, NMS-merged with the full-frame detections ([watch_merged.py:615](core/management/commands/watch_merged.py#L615)).
- **Constants** at [recognition.py:629-632](core/vision/recognition.py#L629): `BOTTLE_CROP_PAD` 0.20, `BOTTLE_CROP_BOX_FRACTION` 0.75, `BOTTLE_CROP_IMGSZ` 640, `BOTTLE_CROP_MAX` 4 (largest persons first).
- **Zone limit.** `watch_merged` has no zone filter (`cmd.zones = []`, [watch_merged.py:352](core/management/commands/watch_merged.py#L352)), so there is nothing to test against; only the max-4-crops cap protects fps. Not wired to a zone.
- **Switch.** On by default for the drinking engine; `--no-bottle-crops` turns it off ([watch_merged.py:158](core/management/commands/watch_merged.py#L158)).

Measured on `Accuracy\Drinking\D Near 1.mkv` (2560x1440, 1507 frames, every 3rd frame = 503 sampled, RTX GPU, raw merged_v2 detections at conf 0.15 with no engine floors or rules, so these are detections, not alerts). Time is detect-only (person detection excluded), not full pipeline fps. Single run, not repeated.

| Variant | Bottle frames | Bottle dets | Cigarette frames | Cigarette dets | s/frame | fps |
|---|---|---|---|---|---|---|
| a0 whole frame, imgsz 960 (`--fast`) | 27/503 | 35 | 138 | 174 | 0.036 | 27.9 |
| a current default (whole + 2x2 tiles + 2x person crops, 960) | 425/503 | 1024 | 413 | 850 | 0.373 | 2.7 |
| b whole frame, imgsz 1920 | 255/503 | 497 | 477 | 643 | 0.073 | 13.7 |
| c current + bottle crop pass | 449/503 | 1279 | 413 | 849 | 0.470 | 2.1 |
| d `--fast` + bottle crop pass | 258/503 | 371 | 136 | 156 | 0.137 | 7.3 |

- The clip has no smoking, so every Cigarette detection is a false positive at the raw 0.15 floor. The crop pass adds none (c vs a: 849 vs 850; d vs a0: 156 vs 174). Imgsz 1920 raises Cigarette frames from 138 to 477 over the 960 whole-frame pass.
- Over today's default (a), the crop pass adds 24 Bottle frames (425 -> 449) for +0.10 s/frame (2.7 -> 2.1 fps). Over `--fast` (a0 -> d) it adds 231 Bottle frames (27 -> 258) at 7.3 fps.
- Bottle detections here are not checked against ground truth (I did not label which frames show a bottle), so "more frames" is more recall or more false positives; it does not say which. Raw Bottle in 425 of 503 frames under the default suggests the low floor is passing a lot; the drinking engine's own confidence and posture rules sit after this.
- Not run: the full `watch_merged --dry-run` alert comparison, so I cannot say whether the extra detections change any drinking alert.
- Measuring script is in the session scratchpad, not the repo.

## J. Dry runs: two vape clips, all drinking clips, and the Vape class (nothing in the code or thresholds was changed)

Setup
- Branch `feat/v6.2-location-momentum`, working tree as it stands (HEAD 37cbead plus the uncommitted changes in sections A to I). Nothing pushed, no code or threshold touched by this work; the only repo change is this file.
- Command: `watch_merged --dry-run --stats` (default far mode, bytetrack, cascade on), one clip at a time, nothing else on the GPU. No Alert rows written.
- Settings read from the database at run time: smoking confidence 30, dwell 3, puff count 3, puff window 5.0 min; drinking confidence 35, dwell 8, held dwell 24, min group 2, group duration 600, hours band off; holdup confidence 30 (the 0.50 knife floor still wins, [watch_thief.py:124](core/management/commands/watch_thief.py#L124)); alert cooldown 120; monitoring_min_seconds 3.0.
- Logging was done by a wrapper script in the session scratchpad that wraps `_feed_pose`, `Track.update_gesture`, `Track.reset_hand`, `_incident_sync`, `_debug_note`, `_apply_engine_floor` and each engine's `_process_track` and only writes to a log; it is not in the repo. The wrapper re-derives each frame's gesture ratio with the same `gesture_ratio` call on the same keypoints, so the pose columns match what the counter saw.
- Times are video seconds (`CAP_PROP_POS_MSEC`). "Box h" is the person box height in native pixels (2560x1440 clips). Track ids are this run's `PersonTracker` ids; they are not the same ids across engines.
- Both vape clips were run twice (once before I added the per-frame score log, once after): same holds, same levels both times.
- I have no ground-truth labels. I looked at one frame per vape run (25.5 s in Vape Far Night, 26.3 s in S VapeNightNear), described below.

### J1. A: vape dry runs

Strong-draw conditions (all must hold; code in `update_gesture` [tracking.py:459](core/vision/tracking.py#L459), `_count_gesture` [watch_smoking.py:618](core/management/commands/watch_smoking.py#L618), `strong_gesture` [tracking.py:501](core/vision/tracking.py#L501)):
- 1 Apex: ratio <= 0.7 for 0.6 s ([tracking.py:89](core/vision/tracking.py#L89)).
- 2 Held for at least 1.5 s ([tracking.py:99](core/vision/tracking.py#L99)).
- 3 Every sample on a box at least 250 px tall ([tracking.py:98](core/vision/tracking.py#L98)).
- 4 Facing check and size check pass (an unknown frame does not end the hold, but a gap over 1.5 s restarts it). Nose >= 0.5, one eye >= 0.4, box >= 120 px, shoulders >= 25 px ([recognition.py:645-648](core/vision/recognition.py#L645), [recognition.py:738](core/vision/recognition.py#L738)).
- 5 Ends with the hand dropping (ratio >= 1.3).
- 6 Not wiped by the Bottle stand-down (`reset_hand`, [watch_smoking.py:620](core/management/commands/watch_smoking.py#L620)).
- 7 Two such draws within 90 s ([tracking.py:100-101](core/vision/tracking.py#L100)).
- 8 Even then, it only lifts the "no item seen" cap; Possible still needs `puff_pattern` (3 cycles, Settings puff count).

**Vape Far Night.mkv (78.4 s)**
- Highest smoking status: none on all 25 smoking tracks. No Monitoring, Possible or Likely. The per-frame score log shows no smoking cue (not even `gesture`) on any track at any frame.
- Cigarette class: track 16 had the cigarette slot ON in 47 of 64 samples (max conf 0.52) and still scored no cue; the other tracks' cigarette samples (max 0.66 to 0.86) never turned a slot ON. I did not trace why track 16 produced no cue.
- Only one hold reached the 0.6 s apex: **track 11, 23.60 s, held 3.86 s, box h 374-577 px, of its 78 pose samples 72 scored, 5 failed the facing check and 1 had unsure keypoints (unknown samples do not break a hold), keypoints (median nose / best eye / best wrist) 0.76 / 0.69 / 0.98.** Conditions 1, 2, 3, 4 pass. **Blocked by condition 6**: at about 27.5 s the Bottle stand-down fired (flag on from 27.6 to 29.4 s in my log; the wrist ratio was 1.0, still in the 0.7-1.3 band, not dropped yet) and `reset_hand` wiped a hold already 3.86 s old, so no cycle and no strong draw were recorded. Even without the reset it was one draw, so condition 7 (two draws) and condition 8 (three cycles) would still have blocked.
- What that hold is: the frame at 25.5 s shows this person with a hand on the cap and a lit phone in the other hand. I cannot tell from one frame whether it is a vape draw, so this clip may have no real strong draw on track 11.
- The person at a pole at 22.7 s (track 3, box h 111-358, my guess at the vaper; not confirmed) was never scored: of 1229 pose samples, 692 were "small person" (691 of them with the box at or over 120 px, so the **25 px shoulder-width limit**, 546 of them on the zoomed pose), 207 facing away, 17 keypoints unsure, 313 scored. Only 5 scored samples were at ratio <= 0.7, in three holds of 0.0 to 0.04 s (best ratio 0.60). I did not log shoulder widths, so I cannot say how narrow they were.
- Vape class detections: 0 (see J3).

All holds in the clip:

| Track | Start (s) | Held (s) | Ended by | Reached apex (>=0.6 s) | Box h min-max (px) | Facing fails in hold | Unknown (other) | Nose/eyes/wrist conf (median) | Counted cycle | Strong draw | Blocker |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 | 14.95 | 0.0 | dropped | no | 228-228 | 0/1 | 0 | 0.75/0.79/0.84 | no | no | hold 0.0 s < 0.6 s apex (dropped) |
| 3 | 22.71 | 0.04 | band-restart | no | 196-197 | 0/2 | 0 | 0.98/0.99/0.86 | no | no | hold 0.04 s < 0.6 s apex (band-restart) |
| 3 | 56.0 | 0.04 | dropped | no | 203-203 | 0/2 | 0 | 0.97/0.96/0.98 | no | no | hold 0.04 s < 0.6 s apex (dropped) |
| 4 | 7.66 | 0.04 | dropped | no | 322-322 | 0/2 | 0 | 0.85/0.74/0.84 | no | no | hold 0.04 s < 0.6 s apex (dropped) |
| 9 | 9.51 | 0.0 | dropped | no | 496-496 | 0/1 | 0 | 0.63/0.68/0.93 | no | no | hold 0.0 s < 0.6 s apex (dropped) |
| 11 | 22.81 | 0.0 | band-restart | no | 334-334 | 0/1 | 0 | 0.66/0.48/0.95 | no | no | hold 0.0 s < 0.6 s apex (band-restart) |
| 11 | 22.92 | 0.03 | band-restart | no | 336-339 | 0/2 | 0 | 0.90/0.83/0.97 | no | no | hold 0.03 s < 0.6 s apex (band-restart) |
| 11 | 23.09 | 0.27 | band-restart | no | 340-391 | 0/7 | 0 | 0.89/0.81/0.97 | no | no | hold 0.27 s < 0.6 s apex (band-restart) |
| 11 | 23.46 | 0.04 | band-restart | no | 386-387 | 0/2 | 0 | 0.91/0.84/0.97 | no | no | hold 0.04 s < 0.6 s apex (band-restart) |
| 11 | 23.6 | 3.86 | bottle-standdown-reset | yes | 374-577 | 5/78 | 1 | 0.76/0.69/0.98 | no | no | reset by Bottle stand-down |
| 13 | 25.46 | 0.0 | dropped | no | 404-404 | 0/1 | 0 | 0.94/0.91/0.90 | no | no | hold 0.0 s < 0.6 s apex (dropped) |
| 14 | 28.96 | 0.07 | band-restart | no | 363-472 | 0/2 | 0 | 0.63/0.71/0.95 | no | no | hold 0.07 s < 0.6 s apex (band-restart) |

**S VapeNightNear.mkv (39.2 s)**
- Highest smoking status: none on all 9 smoking tracks. The only smoking cue anywhere is `gesture` on track 3 (max score 0.20), which is below Monitoring because the puff-only path needs 3 cycles (got 1) and the cigarette cue was never confirmed (cigarette samples on track 3: 189, max conf 0.81, slot ON in 10, no cigarette cue).
- One hold reached the apex: **track 3, 25.92 s, held 1.19 s (ends 27.11 s), box h 426-436 px, facing check passed 25 of 25, keypoints 0.91 / 0.86 / 0.96, ended by the hand dropping, counted as 1 gesture cycle.** Conditions 1, 3, 4, 5, 6 pass. **Blocked by condition 2** (1.19 s is under 1.5 s), so it is not a strong draw. It was also the only draw in the clip (condition 7) and one cycle is under the Settings puff count of 3.
- The Bottle stand-down was active on track 3 from 28.29 to 33.14 s (98 frames; Bottle detections at conf >= 0.5 on that person, I did not look at what they were). It overlaps only 2 samples at ratio <= 0.7 (28.75 and 28.79 s, no hold started), so it did not block the 25.92 s draw here.
- The frame at 26.3 s shows this person with a vape at the mouth.

All holds in the clip:

| Track | Start (s) | Held (s) | Ended by | Reached apex (>=0.6 s) | Box h min-max (px) | Facing fails in hold | Unknown (other) | Nose/eyes/wrist conf (median) | Counted cycle | Strong draw | Blocker |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 | 19.75 | 0.0 | band-restart | no | 408-408 | 0/1 | 0 | 0.85/0.84/0.96 | no | no | hold 0.0 s < 0.6 s apex (band-restart) |
| 3 | 20.05 | 0.1 | band-restart | no | 410-414 | 0/3 | 0 | 0.89/0.83/0.95 | no | no | hold 0.1 s < 0.6 s apex (band-restart) |
| 3 | 25.69 | 0.06 | band-restart | no | 442-448 | 0/2 | 0 | 0.95/0.93/0.97 | no | no | hold 0.06 s < 0.6 s apex (band-restart) |
| 3 | 25.92 | 1.19 | dropped | yes | 426-436 | 0/25 | 0 | 0.91/0.86/0.96 | yes | no | held 1.19 s < 1.5 s |
| 3 | 27.85 | 0.05 | band-restart | no | 425-425 | 0/2 | 0 | 0.89/0.87/0.96 | no | no | hold 0.05 s < 0.6 s apex (band-restart) |
| 3 | 34.99 | 0.0 | band-restart | no | 426-426 | 0/1 | 0 | 0.97/0.96/0.96 | no | no | hold 0.0 s < 0.6 s apex (band-restart) |
| 3 | 35.39 | 0.0 | dropped | no | 443-443 | 0/1 | 0 | 0.95/0.93/0.96 | no | no | hold 0.0 s < 0.6 s apex (dropped) |
| 4 | 16.05 | 0.04 | band-restart | no | 290-290 | 0/2 | 0 | 0.64/0.61/0.82 | no | no | hold 0.04 s < 0.6 s apex (band-restart) |

Summary of what blocked each real draw
- Vape Far Night, track 11 (3.86 s): condition 6, Bottle stand-down reset the hold before the hand dropped.
- S VapeNightNear, track 3 (1.19 s): condition 2, hold under 1.5 s.
- Every other hold in both clips ended under 0.6 s (condition 1).
- Neither clip has two qualifying draws, so condition 7 blocks the strong-gesture exception in both regardless of the above.

### J2. B: drinking clips, bottle crop pass ON vs OFF

- Clips: every file under `Accuracy\Drinking` (2 files) and `Accuracy\Drinking\Hide` (3 files). ON = default; OFF = `--no-bottle-crops` ([watch_merged.py:158](core/management/commands/watch_merged.py#L158), used at [watch_merged.py:616](core/management/commands/watch_merged.py#L616)). 10 runs, one at a time.
- "Solo" = drinking person incident (Path A), "group" = gathering incident (Path B). First alert = first time the status reached Possible; "never" = only Monitoring or nothing. "Possible+ keys" = distinct solo or group incidents that reached Possible or Likely in the run.
- Wrong-type = smoking or holdup incidents in a drinking clip, highest status and the cues that produced it. "ON the drinker" = same bytetrack id as the best drinking incident. Holdup shows as `knife`.
- "Detections on drinker" = merged-model detections (after the 0.30 inference floor, before per-engine floors) whose centre falls in the box of the best drinking incident's bytetrack id(s), summed over all frames: B = Bottle, K = knife, C = Cigarette. The second pair counts only conf >= 0.50. For a group incident it covers every member, so group numbers include the whole cluster.
- The best drinking incident = highest status, ties broken by score.

| Clip | Crops | Drinking solo: highest | Solo first alert | Drinking group: highest | Group first alert | Wrong-type alerts (highest) | Drinker ids | Detections on drinker, floor: Bottle / knife / Cigarette | same, conf >= 0.50 |
|---|---|---|---|---|---|---|---|---|---|
| D Far | ON | Possible (t=26.7s, id 11); Possible+ keys: 1 | 26.7s | none (t=0.2s, id 4); Possible+ keys: 0 | never | smoke Monitoring (t=7.89s, id 5, cues cigarette); knife Monitoring (t=5.75s, id 7, cues E14) | 1485 | B 16 / K 4 / C 4 | B 6 / K 0 / C 3 |
| D Far | OFF | Monitoring (t=22.29s, id 11); Possible+ keys: 0 | never | none (t=0.2s, id 4); Possible+ keys: 0 | never | smoke Monitoring (t=7.89s, id 5, cues cigarette); knife Monitoring (t=5.75s, id 7, cues E14) | 1485 | B 13 / K 4 / C 4 | B 4 / K 0 / C 3 |
| D Near 1 | ON | Monitoring (t=47.49s, id 18); Possible+ keys: 0 | never | Monitoring (t=45.35s, id 2); Possible+ keys: 0 | never | knife Monitoring (t=3.89s, id 3, cues E14, ON the drinker) | 1542,1813,2228,2606,2619,2731,2841,2911,311,389,551,8 | B 626 / K 361 / C 424 | B 350 / K 174 / C 149 |
| D Near 1 | OFF | Monitoring (t=47.49s, id 18); Possible+ keys: 0 | never | Monitoring (t=45.35s, id 2); Possible+ keys: 0 | never | knife Monitoring (t=3.89s, id 3, cues E14, ON the drinker) | 1542,1813,2228,2606,2619,2731,2841,2911,311,389,551,8 | B 534 / K 363 / C 426 | B 293 / K 174 / C 150 |
| Hide/DSingle NearNight | ON | Possible (t=27.8s, id 6); Possible+ keys: 1 | 27.8s | none (t=0.0s, id 1); Possible+ keys: 0 | never | smoke Possible (t=26.84s, id 15, cues cigarette,near_mouth); knife Monitoring (t=8.4s, id 6, cues E14, ON the drinker) | 121 | B 226 / K 196 / C 124 | B 180 / K 128 / C 58 |
| Hide/DSingle NearNight | OFF | Monitoring (t=27.9s, id 6); Possible+ keys: 0 | never | none (t=0.0s, id 1); Possible+ keys: 0 | never | smoke Possible (t=26.84s, id 15, cues cigarette,near_mouth); knife Monitoring (t=8.4s, id 6, cues E14, ON the drinker) | 121 | B 150 / K 196 / C 124 | B 95 / K 128 / C 58 |
| Hide/SD FarNear | ON | Possible (t=69.12s, id 23); Possible+ keys: 1 | 69.12s | Possible (t=71.33s, id 12); Possible+ keys: 1 | 71.33s | smoke Possible (t=52.07s, id 5, cues gesture,puff_pattern,puffs); knife Monitoring (t=8.94s, id 4, cues E14) | 644 | B 363 / K 49 / C 463 | B 162 / K 8 / C 180 |
| Hide/SD FarNear | OFF | Possible (t=69.12s, id 23); Possible+ keys: 1 | 69.12s | Possible (t=71.33s, id 12); Possible+ keys: 1 | 71.33s | smoke Possible (t=52.07s, id 5, cues gesture,puff_pattern,puffs); knife Monitoring (t=8.94s, id 4, cues E14) | 644 | B 308 / K 49 / C 464 | B 132 / K 8 / C 180 |
| Hide/D NearFar | ON | Possible (t=63.53s, id 30); Possible+ keys: 3 | 63.53s | Possible (t=152.08s, id 47); Possible+ keys: 4 | 152.08s | smoke Possible (t=108.29s, id 38, cues gesture,puff_pattern,puffs, ON the drinker); knife Monitoring (t=0.64s, id 2, cues E14) | 4190,4546,4742,4766,4806,4976,4981,5835,5880,6125,6155,6196,6233,6337,6340,6447,6493,6534,6568,6941,6955,7304,7306,7675,7676,7700,7732,7739,7763,7774,7779 | B 1007 / K 252 / C 288 | B 728 / K 120 / C 115 |
| Hide/D NearFar | OFF | Possible (t=203.57s, id 165); Possible+ keys: 1 | 203.57s | Possible (t=58.5s, id 8); Possible+ keys: 7 | 58.5s | smoke Possible (t=108.13s, id 38, cues gesture,puff_pattern,puffs, ON the drinker); knife Monitoring (t=0.64s, id 2, cues E14) | 4190,4546,4742,4766,4806,4976,5835,5880,6125,6155,6196,6233,6337,6340,6447,6493,6534,6568,6941,6955,7304,7306,7675,7676,7700,7732,7739,7763,7774,7779 | B 818 / K 248 / C 289 | B 543 / K 118 / C 116 |

Reading it
- Crops ON gave a Possible that OFF did not on two clips: D Far (Possible at 26.7 s ON; Monitoring only, at 22.29 s, OFF) and DSingle NearNight (Possible at 27.8 s ON; Monitoring only, at 27.9 s, OFF). Same drinker id in both runs of D Far.
- D Near 1 and SD FarNear: status and first-alert time are identical ON and OFF. The crops add Bottle detections on the drinker (D Near 1 626 vs 534; SD FarNear 363 vs 308) and nothing else changes.
- D NearFar (312 s, a long multi-event clip) differs both ways: ON first alerts are solo 63.53 s and group 152.08 s (3 solo and 4 group incidents reached Possible+); OFF first alerts are group 58.5 s and solo 203.57 s (1 solo and 7 group incidents reached Possible+). So the crop pass moved the first solo alert 140 s earlier and the first group alert 94 s later in this single pair of runs; I did not look into why, and with one run each I cannot separate that from run-to-run variation in tracking and clustering. Drinker detections: Bottle 1007 ON vs 818 OFF.
- Each configuration ran once. I did not repeat an identical configuration, so I cannot say how much of a one-alert difference is run-to-run variation (tracker ids and cluster timing can shift).
- Wrong-type alerts: no holdup incident above Monitoring in any run. Smoking reached Possible in three clips, with the same result ON and OFF where I could compare: DSingle NearNight id 15 at 26.84 s (cues cigarette + near_mouth, not the drinker), SD FarNear id 5 at 52.07 s (gesture + puffs + puff_pattern, no cigarette cue; that track had 184 Cigarette detections, so the item-seen rule lifted the no-item cap; I did not trace which of its holds counted), D NearFar (ids 38 at 108.29 s ON / 108.13 s OFF and 107 at 209.43 s in both, cues gesture + puffs + puff_pattern; id 38 is the drinker; same result ON and OFF). Smoking Monitoring on D Far (id 5) and holdup Monitoring on D Far, D Near 1 (on the drinker), DSingle NearNight (on the drinker), SD FarNear and D NearFar (id 2 at 0.64 s, cue E14).
- Bottle vs knife vs Cigarette on the drinker: Cigarette and knife detections are common on drinkers (D Near 1: B 626 / K 361 / C 424; SD FarNear: B 363 / K 49 / C 463; D NearFar ON: B 1007 / K 252 / C 288), so the merged model confuses the bottle with the other two classes; the wrong-type rules (momentum, location gate, 0.50 knife floor, partner rule) are what kept them at Monitoring. Without labels I cannot tell which of these detections sit on a real bottle.

### J3. C: is there a "Vape" class?

- **merged_v2.pt, the live model**: classes `Bottle`, `Cigarette`, `knife`. No Vape. It is the model every detector uses: `MODEL_PATH` defaults to it ([recognition.py:546](core/vision/recognition.py#L546)) and `SMOKING_MODEL_PATH`, `THIEF_MODEL_PATH`, `DRINKING_MODEL_PATH` and `MERGED_MODEL_PATH` are aliases of that path ([recognition.py:547](core/vision/recognition.py#L547)), unless `LOOKOUT_MODEL` is set (not set in `.env` or `render.yaml`). The comment says it: "Vapes have no class (puff-only path)" ([recognition.py:537](core/vision/recognition.py#L537)).
- **smoking.pt** (22.7 MB, in `core/vision/`): classes `Cigarette`, `Vape`, `Vapor`.
- **smoking_v5.pt** (6.1 MB, in `core/vision/`): classes `Cigarette`, `Vape`.
- Neither smoking.pt nor smoking_v5.pt is loaded by any code path: no `.py` file under `lookout_backend` opens them (the only mentions are an error string, [watch_smoking.py:326](core/management/commands/watch_smoking.py#L326), and a comment, [views.py:1973](core/views.py#L1973)). `drinking.pt` (Bottle only) and `thief.pt` (gun, knife, robbery activity, stealing) are likewise unused.
- Code that mentions vape but cannot fire on merged_v2: `SMOKING_CLASSES = {"cigarette", "vape"}` filter used by `detect_smoking`, `detect_smoking_far` and `detect_smoking_cascade` ([recognition.py:549](core/vision/recognition.py#L549), cascade at [recognition.py:1347](core/vision/recognition.py#L1347)), the `vape` class policy ([watch_smoking.py:65](core/management/commands/watch_smoking.py#L65)), `MOUTH_ANCHORED_CLASSES` ([watch_smoking.py:83](core/management/commands/watch_smoking.py#L83)) and the AI checker's wording ([ai_checker.py:113](core/vision/ai_checker.py#L113)). So the smoking hi-res cascade can only return `Cigarette` today.
- In both vape runs the momentum log recorded 0 detections with a Vape label. A vape is seen only when the model calls it Cigarette (or Bottle; see the stand-down in J1).

## K. S 2NearFar: vanishing smoking alert, vaper alerted as drinking (investigation, no detection code changed)

Clip `Accuracy\Smoking\HIde\S 2NearFar.mkv` (2560x1440, 20 fps, 2010 frames, 100.5 s). Evidence is the real alerts your run left in `db.sqlite3` (read-only) plus my own dry runs through the `alpha_eval` worker (real `watch_merged_all`, in-memory alert store, nothing written to the database), crops ON and crops OFF, with a hook that logs every detection each engine track receives. **Caveat that matters:** the track ids in your run (smoking 7, drinking 34) are not the ids in mine (smoking 12, drinking 35), and my dry runs did **not** reproduce the drinking Possible on the far person (see K2). Everything about your run's drinking alert comes from its stored row, not from my reruns.

### K1. Bug: smoking alert (ID 7) appeared, vanished, tab went blank

- **It is not a crash.** The alert is ALT-0208 (smoking, track 7, camera CAM-MERGED-TEST). Its row still exists: `level=monitoring`, `peak_level=warning`, `status=active`. Its timeline reads Possible at 13:47:26, Monitoring at 13:52:47. It was not deleted, closed, merged or hidden as a test alert.
- **Why it left the tab:** the list excludes any alert whose CURRENT level is Monitoring ([views.py:1021](core/views.py#L1021), docstring [views.py:1004-1014](core/views.py#L1004)). Possible fell to Monitoring, so the next 4 s poll dropped it. The drinking alert (ALT-0210) did not exist until 13:53:13, 26 s later, so for those 26 s the list was empty: that is the blank tab.
- **Why it fell to Monitoring:** a frame with no scoring cue sets the incident level to NONE without touching the row ([_incidents.py:521-526](core/management/commands/_incidents.py#L521)). The "sticky" puff cap only holds while `previous_level` is warning or violation ([location_momentum.py:704-710](core/vision/location_momentum.py#L704)), and `previous_level` is now NONE. The next frame that scores is treated as a first rise, gets "capped: no puff", and `_incident_write` lowers the stored level ([_incidents.py:639-665](core/management/commands/_incidents.py#L639)). ALT-0208's stored cues end at score 0.55, `capped: "capped: no puff"`, level monitoring.
- **Same sequence in both dry runs:** smoking id 12 reaches Possible (cigarette + near_mouth, 0.55) at 41.15 s for ONE frame, none at 41.19, then Monitoring (cigarette only, 0.40) at 46.79 s. ON and OFF are identical except OFF reaches Possible again at 46.79.
- **Not the cause:** `_incident_discard` deletes only a Monitoring-peak flicker shorter than `monitoring_min_seconds` ([_incidents.py:431-444](core/management/commands/_incidents.py#L431), called from the gc at [_incidents.py:711-717](core/management/commands/_incidents.py#L711)); ALT-0208 peaked at Possible. `_incident_continue` merges only the same kind of incident ([_incidents.py:400-428](core/management/commands/_incidents.py#L400)); the drinking row is a different kind. "-TEST" cameras are not filtered out of the alert list.
- **Render crash:** I found no payload shape that throws. `cues.trigger.cues` is always a list ([debug_view.py:161-165](core/vision/debug_view.py#L161)), and every `cues` field the dashboard reads is optional-chained. But the dashboard had no error boundary at all, so ANY throw would have blanked the whole app.
- **Fixed (UI only, commit "Fix: alerts tab crash / vanishing alert"):** `ErrorBoundary` ([ErrorBoundary.jsx:5](../lookout/src/components/ErrorBoundary.jsx#L5)) around every alert card ([AlertFeed.jsx:208](../lookout/src/AlertFeed.jsx#L208)) and the alert modal ([AlertFeed.jsx:762](../lookout/src/AlertFeed.jsx#L762)); `TriggeredBy` tolerates a non-array `cues` ([ViolationModal.jsx:1156](../lookout/src/ViolationModal.jsx#L1156)); corrected the comment at [AlertFeed.jsx:530](../lookout/src/AlertFeed.jsx#L530), which claimed the server filters on peak level (it filters on current level).
- **Not changed, your call:** the vanishing itself. Options: list on `peak_level` (reverses the deliberate rule in the `get_queryset` docstring), or stop resetting `previous_level` on a NONE frame (detection code).
- **Separate finding:** ALT-0210 and ALT-0207 store `object_confidence = 1.0`. When the alert is written on a frame with no Bottle detection, `last_conf` is 0 and the fallback is `peak / 3.0` ([_incidents.py:358](core/management/commands/_incidents.py#L358)), where `peak` is the MOMENTUM peak (capped at 3.0), not a confidence. The same alert's `cues.object_confidence` says 0.427.

### K2. Dry run, crops ON vs OFF (per track, after each engine's own confidence floor; "raw" = model output on that ByteTrack id)

| Person | Class | Crops ON, engine level | Crops OFF, engine level | Raw ON / OFF |
|---|---|---|---|---|
| Near smoker (engine id 12) | Cigarette | n=732, max 0.90, mean 0.71, >=0.50: 624 | n=558, max 0.90, mean 0.68, >=0.50: 451 | 2351 / 2291 |
| | Bottle | n=1, max 0.35 | n=1, max 0.35 | 96 / 96 (max 0.21) |
| | knife | n=72, max 0.88, >=0.50: 72 | n=89, max 0.88, >=0.50: 89 | 992 / 1031 |
| Far vaper (engine id 35) | Cigarette | n=76, max 0.73, mean 0.45, >=0.50: 23 | n=24, max 0.73, mean 0.48, >=0.50: 10 | 473 / 437 |
| | Bottle | n=128, max 0.85, mean 0.55, >=0.50: 72 | n=80, max 0.85, mean 0.57, >=0.50: 51 | 959 / 936 |
| | knife | n=14, max 0.74, >=0.50: 14 | n=14, max 0.74, >=0.50: 14 | 3872 / 3873 |

- The crops add Bottle and Cigarette detections (far vaper Bottle 128 vs 80, Cigarette 76 vs 24; near smoker Cigarette 732 vs 558) and change no status that matters. The only status differences between ON and OFF: OFF reaches smoking Possible a second time (46.79 s), and ON has a one-frame drinking Monitoring at 67.34 s (bottle + time band, 0.45).
- The near smoker's "knife" detections (72-89, all >=0.50) are the cigarette being read as a knife; they gave holdup Monitoring only (knife 12: E14 0.45 at 13.89 s, E10+E14 0.55 from 36.79 s). Nothing above Monitoring.
- **Drinking timeline of your run (ALT-0210, drinking id 34, stored row):** Monitoring at 13:53:13.80, Possible at 13:53:16.61. Cues at Possible: bottle 0.40 + at_mouth 0.15 + time_band 0.05 = 0.60; posture `at-mouth`; momentum snapshot cue_on, `on_since` 41.09 s of video. Wall times are processing time (the clip is "processed"), not video time.
- **Drinking timeline of my dry runs (ON):** drinking id 35: none (0.05, time band) until Monitoring at 67.34 s (bottle + time band, 0.45), none again at 67.54 s. No Possible in ON or OFF. The far person had Bottle detections at 0.36-0.85 from 40.95 s, but the bottle cue only turned ON at 42.55 s and the detections thinned out before the 2 s confirmation. Your run's cue was ON from 41.09 s, so the pose-based location gate ([location_momentum.py:352](core/vision/location_momentum.py#L352)) accepted frames in your run that it rejected in mine. I inferred that from the momentum log (detections present, cue OFF); I did not log the gate's reason. Run-to-run variation on this person is large.
- **Why Monitoring went to Possible almost immediately:** Monitoring is the bottle cue itself being confirmed (momentum >= 1.5 and ON for `object_confirm_seconds` = 2.0 s: [momentum.py:104-112](core/vision/momentum.py#L104), [_incidents.py:353](core/management/commands/_incidents.py#L353)). That alone scores bottle 0.40 + time band 0.05 = 0.45. Possible is 0.55 ([scoring.py:50](core/vision/scoring.py#L50)), and the only thing missing is `at_mouth` (+0.15, [scoring.py:260](core/vision/scoring.py#L260)). `at_mouth` is one frame with any Bottle box within `drinking_mouth_proximity` = 3.0 face widths of the pose mouth anchor ([watch_drinking.py:503-533](core/management/commands/watch_drinking.py#L503), setting default [models.py:659](core/models.py#L659)), then held for `cue_hold_seconds` = 5 ([watch_drinking.py:1019](core/management/commands/watch_drinking.py#L1019)). 3 face widths reaches the chest, and a vape draw puts the "bottle" at the face, so it fires on the first frame. Ungated momentum replay on my data confirms the bottle cue at 43.1 s, which equals your stored `on_since` 41.09 + 2.0 s.
- **A real drinker looks the same, on purpose.** D Far (dry run): Monitoring 22.39 s, Possible 24.50 s (2.1 s later), same cues.
- **Why no smoking alert for the vaper (three reasons, plus one that makes it worse):**
  - The live model has no Vape class ([recognition.py:537](core/vision/recognition.py#L537); J3 above). Cigarette on the far person: 24 detections (10 >= 0.50) with crops OFF, 76 (23) ON, over 70 s. The cigarette cue never turned ON, and there is no `smoke` incident for id 35 in either run.
  - The Bottle stand-down discards the person's hand-to-mouth cycles once a Bottle >= 0.50 has been on them for 2 s in total within 10 s ([watch_smoking.py:118-120](core/management/commands/watch_smoking.py#L118), [:601-623](core/management/commands/watch_smoking.py#L601); fed from [watch_merged.py:659-663](core/management/commands/watch_merged.py#L659)). Replaying the log, it is active for this person on 34.1-37.2 s, 41.0-58.3 s, 65.0-68.9 s and 78.1-92.0 s, among shorter windows (crops OFF), and on 34.1-94.7 s with two short gaps (crops ON). The vape draw at ~41-43.5 s falls inside it.
  - Puff-only with no smoking item ever seen is held at Monitoring ([watch_smoking.py:117](core/management/commands/watch_smoking.py#L117), [:995](core/management/commands/watch_smoking.py#L995)).
  - The same Bottle detections feed the drinking engine, which has no look-alike rule to stop them.
- **Why the vaper got a drinking alert instead:** the model calls the vape "Bottle" (0.36-0.85, median 0.62 from 40-50 s). Bottle cue, at-mouth cue and the evening band are all drinking needs.

### K3. ID switches for track 7's person (the near smoker; AI reply on ALT-0208: woman in a light blue top holding a cigarette)

- Your run: ALT-0208 is one row on ID 7 from creation to demotion, with no continuation event. I cannot see the ids before the alert from the database.
- My dry run, same person (engine id 12): two engine-id handovers. ID 3 for 11 frames at 7.3-7.8 s (the person entering; the holdup Monitoring flicker at 7.94 s is on id 3), then ID 12 from 10.3 s to 51.4 s, then ID 39 for 7 frames at 46.4-46.6 s (a duplicate box on the same person, 0.2 s before the second smoking Monitoring at 46.79 s). ID 12 sat on one raw ByteTrack id (202) the whole time. Identical ON and OFF.
- The far vaper: engine id 35 from 32.5 to 91.9 s, but ByteTrack gave it three raw ids (2140, 2419, 4592), plus short duplicate engine ids 36 (1 frame at 33.3 s) and 60 (4 frames at 79.1 s). The engine's own matching (not ByteTrack) is what kept id 35.

### K4. Proposed drinking rule changes (NOT applied)

Measured on the clips I could rerun. The earlier J2 runs and mine do not agree on three of the five clips (DSingle NearNight and SD FarNear reached Possible in J2 and not in my reruns; D Near 1 stayed Monitoring in both, but my rerun also put a smoking Possible on the drinker, id 8, at 70.45 s), so those two cannot be re-measured here. D NearFar (312 s, ~80 min at this speed) was not rerun; its group incidents (Path B) cannot be replayed from per-person logs at all.

| Rule | Vaper (S 2NearFar, your run) | D Far drinker (true positive in J2 and my rerun) | Other clips |
|---|---|---|---|
| Bottle conf >= 0.50 for Possible (Monitoring stays at the 0.35 floor) | **No effect.** Bottle cue still confirms at 43.1 s; 72 of 128 detections are >= 0.50 | Cue confirms 24.2 s instead of 22.2 s (+2.0 s); Possible kept | D Near 1: confirmed-ON time 6.6 s -> 3.2 s. DSingle NearNight: 1.2 s of confirmed cue at 0.35 -> never confirmed at 0.50 (would likely drop its J2 Possible). SD FarNear: 0.1 s -> 0.0 s |
| Bottle cue ON continuously >= 3 s (sustained on the same person) | **Blocks the first draw:** its confirmed cue lasts 1.0-1.3 s per episode (OFF/ON) | Kept: longest confirmed run 5.5 s. In the cue stream Possible moves 24.50 -> 25.39 s (+0.9 s) | D Near 1 longest run 2.9 s (it only reached Monitoring anyway) |
| >= 1 sip | Already how Possible works (one at-mouth frame): no change | No change (2 sips seen: 24.5 s, 37.85 s) | |
| >= 2 sips | Probably not blocked: a second burst of Bottle detections at 47.65-49.6 s; I cannot confirm its posture because my runs never got the cue ON | **Possible moves 24.50 -> 37.85 s (+13.4 s)** | |
| 5 s sustained + 2 sips | | **Loses the true drinker** (Possible never reached) | |

- Net, on what I could measure: a bottle-confidence floor does not touch the vaper; "sustained >= 3 s" is the only one of your three that separates the vaper from a real drinker here (1.3 s vs 5.5 s), at a cost of under 1 s on D Far; "2 sips" costs a real drinker 13 s and does not clearly stop the vaper. One vaper and one drinker is two data points: treat the 1.3 s vs 5.5 s gap as a lead, not a threshold.
- **A fourth rule worth testing: Bottle box size against the person.** Long side of the Bottle box over the person's box height, median: vaper 0.08 (n=28); D Far drinker 0.145; DSingle drinker 0.165; SD FarNear drinker 0.197; D Near 1 drinker 0.102 (bottle held sideways; its short side is 0.061). A cut near 0.095 separates these five, but D Near 1 sits right at the line and the sample is five persons.
- Not simulated: gathering (Path B) incidents, which fold member bottles through `location_momentum.cluster_confidence`; and the pose gate, which I cannot replay offline.

### K5. How the numbers were produced

- Driver and analysis scripts live in the session scratchpad, not in the repo. The driver reuses `alpha_eval/worker.py` unchanged and wraps `IncidentMixin._incident_sync` and each engine's `_process_track` to log per-frame scores and detections. `--calibration-csv` supplied the raw model output; crops OFF is `--no-bottle-crops --no-cascade`. The runs went 2-3 at a time on the one GPU, ~1-2 fps each.
- Ids differ between runs (your 7/34, mine 12/35), and the pose gate makes borderline Bottle cues flip between runs, so one dry run per configuration cannot say how often your drinking Possible happens. I did not repeat an identical configuration.
- Branch `feat/v6.2-location-momentum`; nothing pushed. The only code change is the dashboard commit in K1.

## L. Fix: alert level reset, peak-level listing, confidence (follows section K)

- **Level reset:** a cue-less frame no longer resets `inc.level` ([_incidents.py:523](core/management/commands/_incidents.py#L523)). The stored level now falls only through `Score`'s own drop-out rule (score under threshold minus `HYSTERESIS_DROP`, [scoring.py:54](core/vision/scoring.py#L54)). Tests: [tests_incidents.py:544](core/tests_incidents.py#L544) `LevelSurvivesACuelessFrameTests` (ALT-0208: Possible, one cue-less frame, same cues without a puff, must stay Possible; also a run of 19 cue-less frames, and that a real score drop still lowers the row but keeps the peak). The ALT-0208 test and the confidence test FAIL on the old code (checked by stashing `_incidents.py`).
- **Peak-level listing:** no migration, `Alert.peak_level` already exists and only ever rises ([_incidents.py:645](core/management/commands/_incidents.py#L645)). The Violations list and the Monitoring watchlist now split on it ([views.py:1020](core/views.py#L1020)); a row with no peak recorded falls back to its current level. This reverses the earlier "split on current level" rule; [tests_peak_level.py:115](core/tests_peak_level.py#L115) was rewritten to match (the partition test still passes: every alert is in exactly one list).
- **Card:** current status plus "peaked at Likely/Possible" when they differ ([AlertFeed.jsx:209](../lookout/src/AlertFeed.jsx#L209), used at [:274](../lookout/src/AlertFeed.jsx#L274) and [:314](../lookout/src/AlertFeed.jsx#L314); the alert modal's Status card at [ViolationModal.jsx:957](../lookout/src/ViolationModal.jsx#L957)). The officer app is untouched; it will now receive faded alerts that were previously hidden.
- **Confidence:** `object_confidence` is now this frame's detection, else the most recent real one (`Slot.last_detected_conf`, [momentum.py:77](core/vision/momentum.py#L77), [:96](core/vision/momentum.py#L96); used at [_incidents.py:360](core/management/commands/_incidents.py#L360)). Before, a frame with no detection fell back to `peak / 3`, which stored 1.0 on ALT-0210 and ALT-0207. Existing rows keep their old value.
- **Full suite:** `python manage.py test`, 1071 tests, OK.
- **Re-run of S 2NearFar (one dry run, crops ON):** the smoking alert does **NOT** stay Possible, and the reason is not the reset. Smoking id 12 reaches Possible at 41.15 s (cigarette + near_mouth, 0.55), has a cue-less frame at 41.19 s (level now kept, no cap), and at 46.79 s scores cigarette only = 0.40, below the 0.50 floor, so it drops to Monitoring by the normal rule (alert E0003 status change 46.787 s). `peak_level` stays Possible, so under the new listing the alert stays in the Violations tab labelled "Monitoring, peaked at Possible". The old failure (a capped 0.55 flipping to Monitoring) is gone; this one is a sustained 0.40 score. If you want Possible to survive a few seconds of lower score too, that needs a time-based hold on the drop (for example `cue_hold_seconds`) in `Score`/`_incident_sync`; I did not add one because you specified the existing hysteresis.
- Run-to-run note: ids and the drinking path are as in K (smoking 12, no drinking Possible on the far person in this run).

## M. Gesture counter, bottle stand-down, repeated-draw rule (merged_v2.pt only, no Qwen/VLM in the runs)

What changed
- Hand down is now 1.0 shoulder-widths: `HAND_LOWERED_RATIO` 1.3 -> 1.0, core/vision/tracking.py:85. Raised stays 0.7 (tracking.py:84).
- Apex hold is now 1.0 s: `GESTURE_APEX_HOLD_SECONDS` 0.6 -> 1.0, tracking.py:89.
- Bottle stand-down needs a CONTINUOUS run: Bottle conf >= 0.5 on the same person for >= 3.0 s, samples no more than 1.0 s apart (`BOTTLE_STANDDOWN_MIN_RUN_SECONDS`, watch_smoking.py:120; logic in `_bottle_standdown`, watch_smoking.py:601-621). It replaced the old "2 s IN TOTAL within 10 s", so scattered flickers no longer add up.
- Repeated-draw rule reuses the existing strong-gesture path (tracking.py:94-103, `strong_gesture` 502): 3 draws (was 2), each held >= 1.0 s (was 1.5), within 120 s (was 90), on a person box >= 150 px (was 250; far vaper boxes measured 147-255 px, median ~200).
- Facing check: unchanged. A sample with no usable face/pose gives no ratio, so it never reaches the draw counter (recognition.gesture_ratio).
- Tag text is now "Repeated draws — no smoking item detected" (core/vision/scoring.py:300).
- Item-less cap: the draw rule sets `puff_only_max` to Possible (watch_smoking.py:995-997), so `PUFF_ONLY_NEEDS_ITEM_FOR_POSSIBLE` does not block it. Never Likely: puff-only is clamped to `puff_only_max` even with a Likely carried over (scoring.py:380-384). Tested in tests_false_alarm_fixes.py (`test_repeated_draws_reach_possible_without_a_cigarette_and_never_likely`).
- Tests updated for the new timings (tests_false_alarm_fixes.py, tests_v6210.py) and added for the stand-down (1.3 s vape misread, scattered flickers, exactly 3 s).

Dry runs (crops on, VLM off, one at a time)
- Vape Far Night.mkv: 0 smoking alerts. The far vaper (track 9, box 147-255 px) has 56 samples at ratio <= 1.0 but only 12 at <= 0.7, never held for 1.0 s, so 0 gestures and 0 draws. Only Monitoring rows: knife (theft) x2. A different person (track 11, 581 px) holds a hand at the cap for 4.6 s: that is 1 draw, not 3.
- S VapeNightNear.mkv: 0 smoking alerts. Near vaper (track 5, 407-529 px): at-face runs <= 0.7 last only 0.9 s and 0.7 s, so 0 draws. His ratio jitters 0.4-1.1 during each draw.
- S 2NearFar.mkv: 1 smoking alert, the near smoker (track 12), Possible at 41.15 s via the cigarette path, back to Monitoring at 46.79 s (same as section L). Far vaper (track 35, 134-316 px): 0 draws, no smoking alert, only a 3 s drinking Monitoring row (67.3 s) and knife Monitoring rows. The Bottle stand-down never fired in any of the three runs.
- Result: the repeated-draw rule fires on none of the three vape clips, so the vapers are still missed. The new constants are stricter, not looser.

Why, and what the data says (offline replay of the logged ratios, scratchpad only)
- Pose ratio at these ranges is noisy: during a draw it bounces 0.4-1.1, so the hold timer keeps restarting. Samples are also sparse (gaps of 2-10 s when the pose is lost).
- With raised 0.9 and a 3-sample median, the replay finds 2 draws on the near vaper (21.4 s, 27.2 s), 2 on the 2NearFar vaper (36.8 s, 88.6 s), 0 on the Far Night vaper. Even then no clip reaches 3 draws; two draws is the most any vaper has.
- Not applied: raising 0.7 -> 0.9, a median filter, or lowering to 2 draws. Each changes what you asked for. They are the next thing to try.

Duplicate-file check
- Only one "Vape Far Night.mkv" exists (Accuracy\Smoking\, 69,888,187 bytes). There is no copy in HIde\, so nothing to compare.

## N. A Possible / Likely alert is never shown as Monitoring (follows sections L and M)

- "peaked at Possible" was the display half of section L (core/AlertFeed.jsx:209, ViolationModal.jsx:957). It is display-only and has no part in alert creation or notification. It showed because an alert that reached Possible could fall back to Monitoring (the score dropping under 0.50) while the peak stayed Possible.
- The detector no longer lowers it: once an incident's peak is Possible or Likely, a Monitoring score keeps the stored status at Possible until the incident closes (core/management/commands/_incidents.py, in `_incident_sync`, just before `_trigger_info`). Monitoring-only events are untouched and stay on the quiet watchlist.
- Rows already in the database with level=monitoring and a Possible/Likely peak (ALT-0205, ALT-0208) are shown as Possible: `AlertSerializer._shown_level` / `to_representation` (core/serializers.py). Nothing was written to the database.
- With that, "peaked at ..." only appears when a Likely alert has fallen to Possible. Monitoring rows are still hidden from the main list unless Include Monitoring is on (core/views.py:1020, on peak level).
- Tests: tests_incidents.py (`test_a_possible_event_never_goes_back_to_monitoring`, `test_a_monitoring_only_event_still_stays_monitoring`, AICheckerWiringTests fade case), tests_peak_level.py (old faded row shows Possible; Likely-to-Possible shows its peak).

## O. Vape rule applied, no-item puff display, solo drinking sip rule (merged_v2.pt only; unit tests, no clip runs)

Part A: vape (commit e185ec8)
- Raised threshold 0.7 -> 0.9: `HAND_RAISED_RATIO`, core/vision/tracking.py:84.
- 3-sample median of the wrist-to-nose ratio (samples within 0.5 s): `GESTURE_MEDIAN_SAMPLES` / `GESTURE_MEDIAN_SPAN_SECONDS`, tracking.py:87-88; applied at the top of `update_gesture`, tracking.py:~480. Side effect: a drop needs two low samples, so a draw reads about one sample (0.1 s) longer.
- Repeated-draw rule is 2 draws (was 3): `STRONG_GESTURE_MIN_DRAWS`, tracking.py:106.
- Cue fix kept: `puff_pattern` also fires on `strong_gesture`, watch_smoking.py:981-983.
- Tests: tests_false_alarm_fixes.py `StrongGesture` rewritten for 2 draws, ratio 0.9, jitter spike; the single down-sample cycles in tests_false_alarm_fixes.py and tests_v6210.py now end with two down samples.

Part B: no-item puff display (commit 5c9d86c)
- `Track.no_item_puffs(now)` lists each held draw's duration within 120 s; `no_item_puffs_summary` builds "puffs (no item) 1/2 (1.4 s)": tracking.py:252, :533.
- Testing view (live processing list) gets it as an extra indicator via `_debug_note(extra_indicators=...)` ([_incidents.py:177](core/management/commands/_incidents.py#L177), watch_smoking.py:1004). It is stored on the alert as `cues.no_item_puffs` (watch_smoking.py:1032) and printed in the score breakdown and the "Triggered by" line (lookout/src/ViolationModal.jsx:1224, :1168). Only shown while no smoking item has been seen on the person.
- Display only. Held draws are not read by the cigarette puff counter (`location_momentum.update_puffs`, `loc.puffs`), by `smoke_item_seen`, or by Likely (puff-only stays clamped to Possible). One thing they still do: a held draw is also an ordinary gesture cycle, so it still feeds the `gesture`/`puffs` score cues, which is what lets the draw rule reach 0.55. Removing that would take the vape path below Possible; say if you want it.

Part C: alert level reset, peak level, confidence
- Already done and committed before this session (1dcf34e, 4d84a01, section L and N): no-cue frame keeps the level (_incidents.py:~521), `peak_level` exists (migration 0055) and the Violations list filters on it (views.py:1020), the card shows "peaked at X", `object_confidence` is the real detection (_incidents.py:~360). Nothing new here; no migration needed. Test: tests_incidents.py:576 `test_one_cueless_frame_leaves_a_possible_row_possible`.

Part D: drinking solo sip rule (commit f43f8e3)
- New core/vision/solo_sip.py: `SoloSipGate`. Bottle conf >= 0.5 on the same person continuously for >= 3.0 s (gap over 1.0 s restarts the run) AND a sip: a confident Bottle in the mouth zone continuously for >= 1.0 s. Both latch once met.
- watch_drinking.py:1030-1050: the gate is fed every frame; until it is satisfied a lone person is capped at Monitoring (`Score(cap=MONITORING)`, card text "capped: solo Possible needs a Bottle held 3 s and a 1 s sip"). A person folded into a group event (min group reached) is not capped: the group (inuman) path is unchanged.
- Score breakdown shows "bottle held N s" and "sip N s" (`cues.solo_sip`, ViolationModal.jsx:~1235); the testing view lists "bottle held 3.2 s" and "sip 1.4 s".
- Mouth zone = the existing `_posture` check (`drinking_mouth_proximity`). With the mouth check off, or no face resolvable (far CCTV range), posture reads "held", so no sip is ever seen and a lone drinker stays at Monitoring. That follows from the rule as specified.
- Tests: core/tests_solo_sip.py (15).

## P. Vape: small-object puffs and the Bottle size gate (merged_v2.pt only; unit tests only, no clip runs)

1. Settings restored / changed
- Raised 0.9, 3-sample median, 2 draws and the `puff_pattern`-on-strong cue fix were already in the code from e185ec8 (tracking.py:84, :87-88, :106; watch_smoking.py:981-983). Nothing to restore.
- Apex hold 1.0 -> 0.7 s: `GESTURE_APEX_HOLD_SECONDS`, tracking.py:93. A pose draw still needs the hand held 1.0 s (`STRONG_GESTURE_MIN_HOLD_S`, tracking.py:106); I did not touch it.
- Differences between the committed code and the earlier experiment that alerted S VapeNightNear: I cannot prove which exact build alerted, but the code from before 83a46be differed in these places, and the committed code does not match them: apex hold 0.6 (now 0.7), hand-down ratio 1.3 (now 1.0), draw hold 1.5 s / box 250 px (now 1.0 s / 150 px), and the stand-down counted any 2 s of Bottle in 10 s (now 3 s continuous). The committed raised/median/2-draw/cue-fix code is identical to the uncommitted experiment I committed. The dry run on that code (S VapeNightNear, this session) gave no smoking alert; the Bottle stand-down is the other known suppressor (section earlier: 183 of 329 samples flagged), which rule 2 below addresses.

2. Stand-down size gate: `watch_merged.holds_real_bottle` (watch_merged.py:78), used where the drinking engine records Bottle-to-person associations (watch_merged.py:672). A Bottle stands smoking down only with conf >= `BOTTLE_STANDDOWN_MIN_CONF` (0.5) AND box height >= `tracking.VAPE_BOTTLE_MAX_FRAC` (0.10) of the person box; the >= 3 s continuous run in `_bottle_standdown` is unchanged. A smaller Bottle never stands down smoking.

3. Small-object puff: a Bottle under 10% of the person height, conf >= `VAPE_PUFF_MIN_CONF` (0.5, my choice; you did not give one), inside the mouth zone (the drinking engine's own `_posture`, so it needs a mouth anchor) for >= `VAPE_PUFF_MIN_HOLD_S` (0.7 s) is one "puff (no item)", pose or no pose. `tracking.SmallBottlePuff` (tracking.py, run logic), `watch_drinking._note_vape_puff` (writes the shared `vape_puffs` list that watch_merged hands to both engines), `watch_smoking._absorb_object_puffs` (reads it, matched by tracker id, or by box overlap under greedy tracking). It joins the pose draws in `Track._draws`; 2 of them (pose or object) open the puff-only Possible exactly as 2 pose draws do (`strong_gesture`, the `gesture`/`puffs`/`puff_pattern` cues use max(gesture cycles, no-item draws)). An object puff within 3 s of a pose draw is the same draw and is not counted twice (`VAPE_PUFF_DEDUP_S`). Never Likely (puff-only stays clamped to Possible), never the cigarette puff counter (`location_momentum` `loc.puffs`), and ignored once a smoking item has been seen on the person. Shown as "puffs (no item) N/2" with each duration, as before.
- Only in watch_merged: standalone watch_drinking has no smoking engine to read the list. `watch_all` has its own loop and does not share it.
- The cigarette puff counter is unchanged.

4. Drinking: a Bottle under 10% of the person height is dropped before the solo gate, so it is neither "bottle held" nor a sip (watch_drinking.py:1036). Group (inuman) paths are unchanged.

5. Constants at the top of tracking.py: `VAPE_BOTTLE_MAX_FRAC` 0.10, `VAPE_PUFF_MIN_HOLD_S` 0.7, `VAPE_PUFF_MIN_CONF` 0.5, `VAPE_PUFF_RUN_GAP_S` 1.0, `VAPE_PUFF_DEDUP_S` 3.0. Tests: core/tests_vape_size_gate.py (one or more per rule), tests_false_alarm_fixes.py (apex hold 0.7).

Not verified on clips: whether these change any of the three vape clips or SD NearFar.

## Q. Drinking: Bottle gap carry-over, upper-body gate, current vs best in the breakdown (unit tests only, no clip runs)

Why (D Near 1.mkv, drinking ID 5, from the replay before this change): the Bottle was seen in bursts of 0.3-1 s; the longest stretch of momentum at or above ON was 1.3 s against the 2.0 s confirm time; 202 frames were rejected "off-anchor" and 77 "flip". Score stayed 0, so no row was created. The sip gate only caps a score, it cannot raise one.

1. Gap carry-over (drinking only)
- Momentum for the Bottle class is frozen (no decay) for `momentum.BOTTLE_HOLD_SECONDS` = 1.0 s after the last ACCEPTED Bottle sighting, so bursts under a second apart chain into one sighting. Uses the existing per-class `MomentumConfig.hold` that the knife already used. [momentum.py:66](core/vision/momentum.py#L66).
- Wired per engine through `IncidentMixin.momentum_holds` ([_incidents.py:306](core/management/commands/_incidents.py#L306), book built at :311-314). Default is the knife's hold (holdup engine, unchanged). `watch_drinking` overrides it with the Bottle only ([watch_drinking.py:981](core/management/commands/watch_drinking.py#L981)); smoking's Cigarette gets no hold.
- Not touched: `object_confirm_seconds` (still 2.0 s), Cigarette, knife.
- A frame whose Bottle the location gate rejected has accepted confidence 0, so it is also carried over inside the 1.0 s.

2. Upper-body location rule (drinking's Bottle only)
- A Bottle is also accepted anywhere inside the top `DRINKING_UPPER_BODY_FRAC` = 0.60 of the SAME person's box (horizontally inside the box too), no keypoints needed. [location_momentum.py:26](core/vision/location_momentum.py#L26), `in_upper_body` :353, applied in `evaluate_track` :404. Cigarette and knife unchanged.
- Hits accepted this way are exempt from the jump check (a bottle rising from the chest to the mouth is a sip): :413.
- What "flip" is: a spot where the other engine (or the same one a moment ago) saw a DIFFERENT class (Cigarette vs Bottle, IoU over 0.3, within 5 processed frames) is treated as ambiguous and rejected, `class_flipped` ([location_momentum.py:208](core/vision/location_momentum.py#L208)), checked BEFORE any location test. This change does NOT affect flips: a flipped Bottle is still rejected wherever it is on the body. The 77 flip frames in D Near 1 will still be rejected; the 202 off-anchor (and the 6 no-kp) frames are the ones this can recover, where the Bottle centre is in the upper 60% of the person's box. "jump" (2 frames) is only exempted for upper-body hits.
- Side effect to know: this widens what counts as a Bottle on the person, so it adds Monitoring rows for people holding a bottle at chest height. The solo Possible still needs the 3 s Bottle + 1 s sip gate and the vape size gate.

3. Kept: the solo gate (Bottle >= 0.5 for 3 s continuous + a 1 s sip) and the small-Bottle size gate (< 10% of person height).

4. Breakdown shows current next to best: "bottle held 1.2 s (best 4.8 s)", "sip 0.0 s (best 2.7 s)". `SoloSipGate.current` / `summary(now)` ([solo_sip.py:31](core/vision/solo_sip.py#L31), :70), stored as `cues.solo_sip.bottle_now_s` / `sip_now_s`, printed in [ViolationModal.jsx:1234](../lookout/src/ViolationModal.jsx#L1234) and the testing view. "Current" is the run going on at that frame (0 once the Bottle has been gone over 1.0 s).

5. Tests: core/tests_drinking_carry.py (hold frozen / decays / chains bursts / per-engine wiring / confirm time unchanged; upper-body accept, lower reject, edge, same-person, cigarette untouched, flip still rejected, no jump), core/tests_solo_sip.py (current vs best).

Not verified on clips: whether D Near 1 ID 5 now alerts.

## R. Drinking: solo gate follows a person across track IDs (unit tests only, no clip runs)

Why (D Far.mkv, from the testing view): the person stayed in one spot but the tracker re-issued IDs (11, 15, 19, 20). The bottle/sip gate lives on the track, so each new ID restarted it: ID 11 held the Bottle 5.0 s but its best sip was 0.7 s (needs 1.0 s), so it stayed capped at Monitoring while the sip time was split across IDs.

- `solo_sip.GateHandover` ([solo_sip.py:92](core/vision/solo_sip.py#L92)): a new track with no gate adopts the gate of an old track only if the old one is gone (last seen at least `HANDOVER_MIN_GAP_S` = 0.25 s ago), was seen at most `HANDOVER_MAX_GAP_S` = 1.5 s ago, and its last box overlaps the new box by `HANDOVER_MIN_IOU` = 0.5. Each old gate is adopted once. Only the bottle/sip timers move; score, momentum, location gate and the 3 s / 1 s thresholds are unchanged.
- Switch: `SOLO_GATE_HANDOVER` ([solo_sip.py:20](core/vision/solo_sip.py#L20)); False restores the old behaviour for an A/B accuracy run.
- Wired in `watch_drinking._process_track` ([watch_drinking.py:1035-1039](core/management/commands/watch_drinking.py#L1035)); one registry per engine instance.
- With stable IDs nothing is ever adopted, so such clips behave as before. Clips with ID changes at the same spot can now reach Possible sooner; that is the intended effect and also the possible source of new false Possibles.
- `SIP_MIN_HOLD_SECONDS` stays 1.0 s.
- Tests: core/tests_solo_gate_handover.py.

Not verified on clips: whether D Far now alerts, or the effect on the accuracy set.

## S. Smoking: side-on gesture ruler and draw carry-over across track IDs (unit tests only, no clip runs)

Why: (a) a person seen in profile has a foreshortened shoulder line, so "shoulder widths" stops being a usable ruler and a hand at the chin read 2-3 widths away or the frame was dropped as "small person"; (b) the tracker re-issues IDs to a person who walks, turns or is briefly hidden, and the hand-to-mouth counter lives on the track, so each new ID restarted from zero (one draw under ID 1 + one under ID 15 never reached the two draws the puff-only path needs).

1. Side-on ruler: `recognition.gesture_ratio_ex` (new; `gesture_ratio` is now a wrapper returning the same pair). For a person box at least `GESTURE_PROFILE_MIN_HEIGHT_PX` = 250 tall whose shoulders are under `GESTURE_PROFILE_SHOULDER_FRAC` = 0.15 of that height, the ruler becomes `GESTURE_PROFILE_BODY_SHOULDER_FRAC` = 0.26 of the height, so the existing 0.9 / 1.0 thresholds mean the same thing. Nose, eye, wrist and shoulder confidences are still required. Smaller (far) people keep the old rule. `watch_smoking._count_gesture` receives `side_on`.
2. Draw carry-over: new `core/vision/gesture_handover.py` (pure bookkeeping, no Django). A new track with no counted draws adopts them from an old track only if the old one has vanished (not matched for `MIN_GAP_S` = 0.25 s, so a live track is never robbed), was last matched at most `MAX_GAP_S` = 1.5 s ago, and the new box's centre lies inside the old last box grown by `MARGIN_X` / `MARGIN_Y`. A track older than `YOUNG_TRACK_S` = 3 s never adopts; each old track is adopted once; `watch_smoking` refuses anyone a Bottle is associated with. Wired in `watch_smoking._carry_draws_over`. Switch: `GESTURE_HANDOVER` (False = old behaviour). With stable IDs nothing is ever adopted.
3. Tests: core/tests_gesture_handover.py, core/tests_false_alarm_fixes.py.

Not verified on clips. Regression replays still owed are listed in REMINDERS.md; look for any NEW smoking alert (a false alarm).

## T. Drinking: separate short sips count; alert cooldown only held by notifying rows (unit tests only, no clip runs)

1. Sip count (`solo_sip.py`): besides one continuous `SIP_MIN_HOLD_SECONDS` hold, `SIPS_NEEDED` = 2 separate holds of at least `SIP_MIN_EACH_SECONDS` = 0.5 s (a gap over `RUN_GAP_SECONDS` apart) inside `SIP_WINDOW_SECONDS` = 30 s also qualify, for a person who sips in short bursts. One accidental hold still cannot qualify. Switch: `SIP_COUNT_ENABLED` (False = the old single-hold rule).
2. Gate handover (section R) loosened: `HANDOVER_MAX_GAP_S` 1.5 -> 4.0 s and the box-overlap (IoU >= 0.5) test replaced by "the new box's centre lies inside the old last box" (nearest centre wins), because a person who moves while the ID is re-issued no longer overlaps their old box enough. The testing view also shows "sips 1/2" while separate short sips are being counted.
3. Cooldown (`_incidents.py`): only a row that notifies (`scoring.NOTIFY_LEVELS`) holds a spot's cooldown. A Monitoring row is the quiet watchlist and is often discarded as a flicker; letting it block meant the same spot reaching Possible inside the cooldown was suppressed and never alerted. A row that opens quiet and later rises to notify starts the cooldown at that moment.
4. `watch_thief`: the fps readout uses `time.time()` (cosmetic).
5. Tests: core/tests_solo_sip_count.py, core/tests_solo_sip.py, core/tests_solo_gate_handover.py, core/tests_false_alarm_fixes.py.

## U. Improper disposal gets its own watch area; dashboard tidy-ups

Why: `watch_disposal` read the camera's `edges`, the same polygon as parking's no-parking area. Parking's is the carriageway (and `trace_zone` builds it from where vehicles MOVED), so saving or tracing it silently confined rubbish detection to the road, when rubbish lies on the footpath and kerb beside it.

1. Storage: `Camera.disposal_edges` + `disposal_edges_width` / `disposal_edges_height` (migration 0066), same `{"key": {"type": "zone", "points": [...]}}` shape as `edges`. `watch_disposal._area_points_from_camera` reads only these. Empty = the whole frame is watched (unchanged default). `--zone <file>` still works. Parking and `trace_zone` are untouched and never write it.
2. Dashboard: Edge Zones (`EdgeEditorModal`) has a second tab, "Improper disposal" (green, no thresholds, optional). Both canvases stay mounted so switching tabs does not lose a half-drawn shape. `EdgeCanvas` takes `label` / `colour` / `hint`. `CameraGrid` passes the three new fields through.
3. Saving sends only the area that changed (parking: shape, % and minutes; disposal: shape). `CameraViewSet.update` then calls `monitor.apply_zone` for `edges` and the new `monitor.apply_disposal_zone` for `disposal_edges`; each swaps only its own process, so the other detectors keep running. Response carries `zone_applied` / `disposal_zone_applied`. A cleared disposal area relaunches disposal (whole frame); it only stops if `disposal_enabled` is off.
4. Upgrade note: a camera whose parking zone disposal was quietly using now watches the whole frame until a disposal area is drawn. Run `python manage.py migrate`.
5. Alert cards: the "peaked at Likely/Possible" note was removed from `AlertFeed` and `ViolationModal`. `LiveMonitor` takes an `actions` prop and sits under the camera grid (`CameraGrid`) instead of being rendered by `admin_dashboard`.
6. Tests: core/tests_disposal_zone.py.

## Tests

New: `core/tests_false_alarm_fixes.py` (38 tests). Updated for the apex hold and the puff-only cap: `core/tests_incidents.py`, `core/tests_v6210.py`.
