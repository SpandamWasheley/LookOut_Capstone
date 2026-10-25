# LookOut scoring spec v6.2 — location-aware momentum + puff gate

Weights and the Monitoring / Possible / Likely thresholds are unchanged from v6. What changed is *when an object
detection is allowed to add momentum* and *what a smoking status needs besides the object*.
`scoring.SCORING_SPEC_VERSION = "v6.2"` (also stored as `spec` in each alert's cue vector).

## Where things live (findings)
| Question | Answer |
|---|---|
| Momentum per object | `core/vision/momentum.py` (`update_momentum`, `MomentumBook`; decay 0.90, on 1.5, off 0.4, max 3.0). Called from `IncidentMixin._object_cue` (`management/commands/_incidents.py`). |
| Detection → person | `PersonTracker.assign` (`vision/tracking.py`): a det goes to the smallest person box whose (expanded) box holds its centre; leftovers become *scene* tracks. |
| Pose keypoints | `recognition.detect_pose` (17×3, whole frame). Used: wrists 9/10, nose 0 (+ face width → mouth = nose + 0.29·face), shoulders 5/6. `KP_MIN_CONF = 0.3`. The existing hand-to-mouth counter (`Track.update_gesture`) is fed by `watch_smoking._feed_pose`. |
| near_mouth / puff motion | `near_mouth` = `_near_mouth` (item within 2.5 face-widths of the mouth, held `cue_hold` s). Puff-motion = `gesture` / `puffs` / `puff_pattern` cues from the pose counter (wrist→nose, **no cigarette involved**). |
| Status | `scoring.Score`: score ≥ 0.75 Likely, ≥ 0.55 Possible, object ON below that Monitoring; hysteresis 0.05. |
| Drinking | Path A = `_process_track` (per person). Path B = `_process_cluster` (group); its bottle momentum is fed `cluster.frame_conf` (best bottle on members or on the table, set by the run loop). Table bottles were *already* attached to the group (`_scene_dets_near`), not discarded. Group area = `cluster.bbox`. |
| Spec version string | none existed in the backend; added `scoring.SCORING_SPEC_VERSION`. |

## What v6.2 does
**Location gate (Cigarette and Bottle; knife untouched)** — `core/vision/location_momentum.py`, hooked into `_object_cue`.
A detection adds momentum only if its centre is within 0.5 × shoulder width (0.15 × person height when a shoulder is missing)
of the left wrist, right wrist or mouth anchor (keypoint conf ≥ 0.3). Otherwise: no increment, normal decay, and a tag:
`off-anchor`, `no-kp` (keypoints missing this frame), `jump` (moved > 25 % of the person height since the last accepted hit, unless
wrist↔mouth), `flip` (same spot, IoU > 0.3, seen as another class within 5 frames). Accepted hits add `conf × min(1, conf/0.6)`.
The tags are drawn next to `m=…` on annotated clips / the testing view only.

**Puff gate (smoking)** — a puff = a cigarette seen at a wrist (wrist outside the mouth zone), then that wrist comes within the
mouth "close" distance within 3 s. Object ON but no puff in the last 10 s → status capped at **Monitoring** (`capped: no puff`).
Possible needs ≥ 1 puff / 10 s; Likely additionally needs ≥ 2 puffs / 30 s (`capped: needs 2 puffs in 30 s for Likely`). The score
itself is not changed, only the status. The puff-only path (no cigarette, 3+ pose puffs → at most Possible) is untouched.

**Drinking Path B** — a bottle adds momentum if it is close to a wrist/mouth of a group member (Path A rule) OR is stationary
(moved < 10 % of the members' median box height since its last hit) inside the group box enlarged by 20 %. Picked up / set down /
passed is never a jump. Neither → decay. Path A (solo) uses the same rules as the cigarette.

## Decisions / assumptions
* The existing `gesture/puffs/puff_pattern` scoring cues keep their old (cigarette-independent) definition so the puff-only path and
  all weights behave as before; the new strict puff counter is used only for the gate.
* The last accepted hit is the jump reference for 2 s only (then the gate re-locks), so a cigarette that is genuinely put somewhere
  new is accepted after that.
* A bottle's first sighting on a table is not "stationary" yet (needs a previous hit within 2 s inside the group area).
* Class-flip memory only sees detections that reach the smoking / drinking engines on a person track.
* One whole-frame pose pass per frame is now shared (`location_momentum.shared_poses`) by the hand-to-mouth counter and the gate.
* Existing incident tests feed detections without pose, so `tests_incidents.py` switches the gate off for those classes.

## Constants (all in the `# --- v6.2 location-aware momentum` block of `core/vision/location_momentum.py`)
| Constant | Value | Note |
|---|---|---|
| `LOCATION_AWARE_MOMENTUM` | `True` | master switch; False = behaviour exactly as before v6.2 |
| `GATED_CLASSES` | `("cigarette", "bottle")` | lower-case class names that need a location (knife is untouched) |
| `KP_CONF_MIN` | `0.3` | a keypoint below this confidence is "missing" |
| `KP_MAX_AGE_SECONDS` | `0.05` | keypoints must belong to THIS frame (pose runs every processed frame) |
| `POSE_MATCH_IOU` | `0.3` | pose box <-> person track box |
| `CLOSE_SHOULDER_FRAC` | `0.5` | "close" = within this x shoulder width of an anchor |
| `CLOSE_HEIGHT_FRAC_NO_SHOULDERS` | `0.15` | ... or this x person box height when a shoulder is missing |
| `JUMP_HEIGHT_FRAC` | `0.25` | a hit that moved more than this x person box height is a jump |
| `JUMP_MEMORY_SECONDS` | `2.0` | the last hit only counts as a reference for this long (then re-lock) |
| `CONF_FULL_AT` | `0.6` | momentum increment x min(1, conf / CONF_FULL_AT) |
| `CLASS_FLIP_FRAMES` | `5` | same spot seen as a different class within this many frames |
| `CLASS_FLIP_IOU` | `0.3` | smoking puff gate (v6.2.1: a puff = the wrist reaches the mouth AND a cigarette on the same person is |
| `PUFF_PAIR_WINDOW_SECONDS` | `5.0` | was 3 s one-way in v6.2 |
| `PUFF_CLOSE_SHOULDER_FRAC` | `0.7` | "wrist at the mouth" / "cigarette at the mouth" radius x shoulder width (the momentum gate keeps 0.5) |
| `PUFF_MIN_GAP_SECONDS` | `1.0` | two wrist-to-mouth reaches closer than this are one |
| `POSSIBLE_PUFF_WINDOW_SECONDS` | `10.0` | Possible needs >= POSSIBLE_MIN_PUFFS puff in this window (else capped at Monitoring) |
| `POSSIBLE_MIN_PUFFS` | `1` |  |
| `LIKELY_PUFF_WINDOW_SECONDS` | `30.0` | Likely also needs >= LIKELY_MIN_PUFFS puffs in this window |
| `LIKELY_MIN_PUFFS` | `2` |  |
| `CAP_NO_PUFF_TEXT` | `"capped: no puff"` |  |
| `CAP_FEW_PUFFS_TEXT` | `"capped: needs 2 puffs in 30 s for Likely"` | holdup (v6.2.1) |
| `KNIFE_CLASS` | `"knife"` |  |
| `KNIFE_CONF_FULL_AT` | `0.6` | knife momentum increment x min(1, conf / this) |
| `KNIFE_JUMP_HEIGHT_FRAC` | `0.25` | same jump rule as the cigarette (no anchors: any move this big is a jump) |
| `KNIFE_JUMP_MEMORY_SECONDS` | `2.0` |  |
| `KNIFE_CLASS_FLIP_FRAMES` | `5` |  |
| `KNIFE_PRESENT_SECONDS` | `1.0` | a knife counts as present for E14 only if an accepted hit is this recent (no long "last seen" carry) |
| `FREEZE_SECONDS_V621` | `3.0` | E12: both people close AND slow, continuously, for this long (was 2.0) |
| `FREEZE_SKIP_IOU` | `0.4` | person boxes overlapping more than this: the frame is skipped (neither counts nor resets E12) |
| `CAP_NO_FROZEN_PAIR_TEXT` | `"capped: no frozen pair"` | drinking Path B (inuman cluster) |
| `GROUP_AREA_EXPAND` | `1.2` | group area = cluster box scaled by this (20% larger) |
| `STATIONARY_HEIGHT_FRAC` | `0.10` | stationary = moved less than this x median member box height since its last hit |
| `STATIONARY_MEMORY_SECONDS` | `2.0` | how long a bottle's previous hit stays a reference |

## Turn it off
Set `LOCATION_AWARE_MOMENTUM = False` in `core/vision/location_momentum.py`. Object momentum, the puff gate, the Path B rule and everything
added in v6.2.1 (knife stability, 3 s frozen pair with overlap skipping, "knife + frozen pair" holdup gate, sticky statuses) all revert to v6
behaviour. (The spec string still reads `v6.2.1`.)

## Summary of the change
**Files changed:** `core/vision/location_momentum.py` (new), `core/vision/scoring.py` (spec version, `Score(cap=…)`, `capped` in the breakdown),
`core/management/commands/_incidents.py` (gate hook in `_object_cue`, `why` tag on `ObjectCue`, tag drawn next to `m=`),
`core/management/commands/watch_smoking.py` (shared pose pass, puff update + cap), `core/management/commands/watch_drinking.py` (Path B inputs + gate,
frame stash), `core/tests_location_momentum.py` (new), `core/tests_incidents.py` (gate off for the legacy tests), this document.
`watch_merged.py` / `watch_merged_all.py` were **not** edited (they already carry uncommitted changes by the team); Path B is wired through
`DrinkingCommand._scene_dets_near`, which the merged loop already calls.

**Checks done:** `manage.py check` clean; all changed modules import; 180 tests (incl. 21 new) pass; `watch_merged --dry-run --end 12` on
`yo\Smoking\Aug18_18 - Trim2.mp4` ran to the end without errors (gate stats: 50 off-anchor, 3 no-kp rejections), with MEDIA_ROOT redirected to a temp folder.

**Not done / caveats:** no accuracy tuning or long runs (as requested). The folder `Desktop\Violation testing\Smoking` does not exist; I used a copy of a
smoking clip under `Desktop\yo\Smoking`. The spec doc is in `lookout_backend/docs/specs/` (existing specs are in the repo-root `docs/specs/`).
The jump 2 s re-lock and the 3 s puff arm window are my additions; class-flip only sees detections on person tracks.


# v6.2.1 — looser strict puff, steadier holdup, sticky status

## Findings (investigation)
* **Strict puff (v6.2):** `location_momentum.update_puffs`: a cigarette seen at a wrist (0.5 x shoulder width, wrist outside the mouth zone),
  then that wrist inside the mouth zone within 3 s, one way only. No puff in the last 10 s capped an object-ON smoking incident at Monitoring
  (`Score(cap=...)`); Likely also needed 2 puffs in 30 s. A real smoker whose cigarette was only visible at the lips (or whose hand was
  already up) never produced a puff, hence "capped: no puff".
* **E14 / E12 (holdup):** E14 = the knife momentum cue is ON (+0.45) after the near-hand rule (`watch_thief._apply_weapon_region_rule`) and a
  2-of-5 persistence test whose result was remembered for 3 s (`KNIFE_PERSIST_MEMORY_SECONDS`); the momentum cue itself holds a knife for 1 s
  without decay and the drawn "last seen" box is just the track's last detection. E12 = `PairState.update`: the two people within arm's length
  (`FREEZE_REACH x reach/REACH_NORM`, i.e. 1.0 person height) and both slower than `FREEZE_SPEED = 0.3`, for `FREEZE_SECONDS = 2.0`, and the knife seen
  during the freeze (or 3 s before / after, `holdup_pair_cues`); E12 is then held `cue_hold` (5 s) after the freeze ends. E10 (a loiterer in the last 60 s)
  also counted as the "second sign" that lifts a knife out of Monitoring.
* **Status:** Possible >= 0.55, Likely >= 0.75 (`scoring`); holdup is the same bands via `theft.Evidence`, held at Monitoring unless a second person is
  near AND a behavioural cue (E10 or E12) is present. Weights: E14 0.45, E12 0.20, E10 0.10, E11E12 0.10, E20 time bonus (capped to stay in its band).
  The reported false holdup (71) was E14 0.45 + E12 0.20 + time bonus.

## What changed
**Smoking.** A strict puff is now: the wrist reaches the mouth anchor (within `PUFF_CLOSE_SHOULDER_FRAC` 0.7 x shoulder width) AND a Cigarette on the same
person is seen at that wrist (0.5) or at the mouth (0.7) within +/- `PUFF_PAIR_WINDOW_SECONDS` (5 s), in either order. A wrist already at the mouth when the
person first appears is not a reach. Possible = >= 1 puff in 10 s, Likely = also >= 2 puffs in 30 s, otherwise capped at Monitoring ("capped: no puff") /
Possible ("capped: needs 2 puffs in 30 s for Likely"). The momentum gate keeps its 0.5 radius.

**Holdup.**
* Knife momentum: increment x min(1, conf / 0.6) (a 41 % hit adds about 0.28), jump check (> 25 % of the person height since the last accepted hit within 2 s)
  and class-flip check (5 frames); no wrist/mouth anchoring and no keypoints needed. A knife counts as present for E14 only if an accepted hit is at most 1.0 s old
  (`knife_recent`); otherwise that frame is held back (no long "last seen" carry).
* E12: both people close AND slow continuously for 3.0 s (`FREEZE_SECONDS_V621`); frames where the two person boxes overlap with IoU > 0.4 are skipped (they neither
  count toward the 3 s nor reset it). A pair that approaches, passes and separates cannot reach 3 s.
* Gate: a knife without E12 is capped at Monitoring with the tag "capped: no frozen pair". E10 (loitering) is still scored but no longer lifts a knife above Monitoring.
  Likely stays at 0.75 (E14 + E12 + E11E12 or E10 ...), unchanged.

**Sticky status (both).** A cap only blocks the FIRST rise. An incident already at Possible / Likely is never put back to Monitoring because a window (10 s / 30 s puff,
holdup corroboration, people-near) expires; an incident at Likely is not capped to Possible by the 30 s puff window.

**Version.** `scoring.SCORING_SPEC_VERSION = "v6.2.1"` (also in every stored cue vector as `spec`).

## Tests changed / added (and why)
* `tests_holdup_v2.py`: `test_knife_and_loitering_is_possible` now runs with the switch off (the old rule) plus a new v6.2.1 test (loiter-only = Monitoring);
  `test_e20_adds_in_full_when_it_fits` uses a frozen pair (E10 no longer corroborates); the 2 s freeze test runs with the switch off and a 3 s test was added;
  new `FrozenPairV621Tests` (walk-past, 4 s still pair, overlap frames neither counted nor resetting, switch off).
* `tests_scoring_v6.py`: the "corroborated" holdup example uses E12; loiter-only is asserted to stay Monitoring with the "capped: no frozen pair" tag.
* `tests_location_momentum.py`: knife tests (weighting, jump, flip, 1 s presence, flickering 41 % knife stays below ON), the puff tests for the new definition
  (cigarette only at the mouth, either order, 5 s window, 0.7 radius, walker with a false hit), sticky caps, spec string.
* Full backend suite: 515 tests; the only failures are `tests_mail` (2, Brevo network call) and `tests_live_view.test_monitor_endpoints_are_admin_only` (1, depends on
  the live-monitor state of this machine); neither touches the changed code.

## Not done / caveats
* Real holdup clip (`yo/Holdup/Aug24_16 - TrimHoldupBldg.mp4`): v6.2 reached Possible (0.61, knife + loitering), v6.2.1 holds it at Monitoring ("capped: no frozen pair")
  because no pair in that clip stayed close and slow for long (the longest run of any pair is 1.7 s; the pair carrying the knife managed about 1.3 s). That is the
  rule working as specified, but it means this one real holdup no longer alerts; even the old 2 s hold would not be reached in this clip, so it was reaching Possible
  through loitering (E10), which v6.2.1 no longer accepts on its own.
* The overlap-skip rule can only count frames the tracker keeps as the same two ids.

# v6.2.1 follow-up: holdup / knife rules reverted to v6 (default)
The knife and holdup changes of v6.2.1 made a real holdup clip (`Aug24_16 - TrimHoldupBldg`) stop alerting, so they are now behind their own switch,
`HOLDUP_V621_RULES = False` (in the `# --- v6.2 location-aware momentum` block of `core/vision/location_momentum.py`). With it False the knife, E12, the
holdup cap and the holdup status all follow the v6 rules again: no knife confidence weighting / jump / flip check, no 1 s knife presence limit, E12 held 2 s
without the overlap skip, loitering (E10) corroborates a knife, no "capped: no frozen pair", no sticky holdup status. The clip is back to Possible (0.64).
Everything else of v6.2 / v6.2.1 (smoking and drinking location gate, strict puff, sticky smoking status, Path B) is unchanged and still governed by
`LOCATION_AWARE_MOMENTUM`. To use the stricter holdup rules again set `HOLDUP_V621_RULES = True` (both switches must be True); the tests that cover them switch it on explicitly.

# v6.2.4 — holdup shadow filter + unprimed holdup Qwen

## Diagnosis of ALT-0152 (Holdup, Possible, knife 52 %)
* ALT-0152 came from DetectionJob 55, `Aug18_18 - TrimCigaretteNightFar1.mp4` (merged), knife event at about 23.6 s. The uploaded copy had already been purged, so the
  diagnosis re-ran the identical file (same size) as a dry run (no DB writes, media redirected) with instrumentation: `v62_test_results/v624/logs/diag_alt0152.json`.
* The "frozen pair" was tracks **#4 and #7** (freeze held 19.7-30.4 s, `freeze_ok` true, distance 0.37 person heights).
  * **#4, the real man:** box 120 x 271 px, person confidence 0.56-0.84, pose matched in 71 of 73 frames, 17 keypoints >= 0.3 with 5 face keypoints (best 0.94).
  * **#7, the second "person":** box 78 x 159 px (about 59 % of his height), person confidence only **0.50-0.65** (the detector floor is 0.5), pose matched in just **4 of 56 frames**
    (no skeleton the rest of the time), brightness 58-90 % of the man's (median 73 %), bottom edge within 12 % of the man's height and the boxes touching sideways.
    That is his shadow on the shutter.
* So the filter that matters is the skeleton test. The brightness test alone would have caught only a few frames (the ratio was mostly 0.6-0.8, above the 0.6 limit), so it is a
  secondary guard, as specified.
* Qwen was told "knife detected; two people standing still close together", looked at 8 frames of him with his shadow and confirmed it ("Two people near a closed shutter; one holds a
  knife", likely / confrontation / high), which fed the Possible -> Likely suggestion.

## Changes
**Fake-person filter (holdup pairs; behind `LOCATION_AWARE_MOMENTUM` and `FAKE_PERSON_FILTER`).** `location_momentum.update_person_evidence` runs the shared whole-frame pose pass on the current frame
(handed over by `watch_thief._apply_weapon_region_rule`) and records, per person track, whether a real skeleton was seen: >= `FAKE_PERSON_MIN_KEYPOINTS` (6) keypoints with conf >= 0.3 and
>= 1 of nose / left eye / right eye with conf >= 0.3. A track is real when it had >= `FAKE_PERSON_MIN_GOOD_FRAMES` such frames in the last `FAKE_PERSON_WINDOW_SECONDS` (3.0 s).
`TheftEngine.update` leaves non-real tracks out of the pair evidence and `holdup_pair_cues` ignores any pair containing one, so they cannot form E12 or the holdup pair. It **fails open**:
a track never watched (no pose information at all: pose model failing, no frame, unit tests) still counts. Deliberate deviation from "at least one frame": the minimum is 3 good frames, because the shadow had
4 stray pose matches in 56 frames and one match must not make it real.
**Shadow pair:** `is_shadow_pair` ignores a pair (and drops its state) when the bottom edges are within `SHADOW_FEET_TOUCH_FRAC` (0.15) of the taller box and the boxes touch sideways AND one box's mean brightness is
below `SHADOW_BRIGHTNESS_RATIO` (0.6) of the other's.

**Qwen holdup prompt.** For holdup only, `build_messages` no longer sends the "System detections ..." line (the note is still stored with the alert). The reply gains `people_count` (integer, distinct real
people; shadows, reflections, posters and mannequins do not count). It is optional in a STORED reply (`ai_status.OPTIONAL_FIELDS`) so alerts checked before this stay valid, but it is in the schema sent to Ollama.
Post-processing in `ai_status`: `people_count < 2` -> badge "may be ordinary" and no upgrade (status "No change - ... AI sees fewer than 2 people"); `object_pointed_at_a_person` true with `people_count < 2`
-> badge "unclear", no suggestion; an upgrade needs `people_count >= 2` AND `scene_type = confrontation` AND confidence high (and the existing `holdup_likelihood = likely`); a reply without people_count never upgrades.
`people_count` is in the alert's `ai_context` (top level and as a checklist row "Distinct real people seen"), which the existing serializer already returns. No frontend file was touched.

**Version:** `SCORING_SPEC_VERSION = "v6.2.4"`.

## New constants (in the marked block of `core/vision/location_momentum.py`)
`FAKE_PERSON_FILTER = True`, `FAKE_PERSON_WINDOW_SECONDS = 3.0`, `FAKE_PERSON_MIN_KEYPOINTS = 6`, `FAKE_PERSON_FACE_KEYPOINTS = (0, 1, 2)`, `FAKE_PERSON_MIN_GOOD_FRAMES = 3`,
`SHADOW_FEET_TOUCH_FRAC = 0.15`, `SHADOW_BRIGHTNESS_RATIO = 0.6`. Turn the filter off with `FAKE_PERSON_FILTER = False` (or `LOCATION_AWARE_MOMENTUM = False`). The Qwen prompt change is not behind a switch.

## Re-run of the ALT-0152 clip (v6.2.4)
* Knife event at 23.6 s: before Possible (0.71-0.74, E14 + E12 + time bonus), **after Monitoring** (0.45, E14 only, no E12).
* The pair no longer forms: 0 frames with a frozen pair (before: 73 frames, pair (4, 7) frozen 19.7-30.4 s); 290 frames logged "track without a skeleton ignored".
* Qwen (qwen3-vl:4b-instruct) on the same 8 frames of ALT-0152, new unprimed prompt, run twice with identical output: "Two people stand near a closed shutter; one appears to be holding an object.",
  `people_count: 2`, pointed false, likelihood **unlikely**, scene other_activity, confidence medium -> badge "may be ordinary", suggestion "No change". So Qwen still counts the shadow as a second person
  (people_count 2), but without the hint it no longer calls it a confrontation. The people_count rule would not have caught this one; the skeleton filter did.

## Caveats
* The filter needs a visible skeleton, so a REAL second person who is far, small or turned away (no face keypoint) is not counted, and a real holdup between distant people can be missed. The thresholds are in the constants block.
* The smoking and drinking engines are unchanged by this version.

# v6.2.5 — holdup recall restored, pass-through only

Goal: stop false holdups from people walking past or walking together with a knife hallucination, without blocking real holdups. `SCORING_SPEC_VERSION = "v6.2.5"`.
Holdup rules are switched on again (`HOLDUP_V621_RULES = True`; with `LOCATION_AWARE_MOMENTUM = False` everything is v6).

## Diagnosis (dry runs of 3 real holdups + the ALT-0152 clip, `v62_test_results/v625/`)
Clips: `yo/Holdup/Aug24_16 - TrimHoldupBldg` (the only file in that folder), and two staged holdups from `Desktop/Demo` (`Holdup.mp4`, `Holdup 2.mp4`), plus ALT-0152 (`Aug18_18 - TrimCigaretteNightFar1`, to 32 s).
For every frame of a pair that contains a knife holder the raw facts of each rule were logged (`logs/*_result.json`, summary by `_analyze.py`).
* **What blocked real holdups.** The speed requirement of the old E12 ("both people slower than 0.3"): in the real holdup the two people stayed within the close distance for 7.5 s, but both were slow for at most 0.4 s
  (54 of 76 close frames failed the speed test), so E12 never fired and the alert only reached Possible through loitering (E10). The v6.2.1 changes then made it worse: a 3 s hold, an IoU > 0.4 skip (frames where boxes overlap neither counted nor reset), E10 no longer
  corroborating (cap "no frozen pair"), and a 1 s knife presence window (knife seen > 1 s ago in 22 of 44 close frames of one clip). The keypoint filter of v6.2.4 never bit on these real pairs (their skeletons were seen), but it would have removed
  any real person that is small or far. In the version that was running (v6.2.4 with `HOLDUP_V621_RULES = False`) the real holdups still reached Possible, but only through E10 and with no E12.
* **`Demo/Holdup.mp4` never alerts in any version** (only 9 knife detections, the momentum cue never reaches ON), so it is not a regression and was not used as a pass/fail case.
* **ALT-0152:** the "second person" was the man's shadow, tracked as #7 and carrying the knife detection. Its brightness is 58-90 % of the man's (never below 60 %), but its box is only 50-63 % as tall with the bottom edge on the same line.

## Changes (all behind `LOCATION_AWARE_MOMENTUM`; named constants in the marked block)
1. **E12 = "stayed close".** Both people within `CLOSE_DISTANCE` (1.0 person heights, not widened in crowds) for `CLOSE_STAY_SECONDS` (2.0 s of video time). No speed requirement for both, overlapping boxes count as close (the IoU skip is gone),
   a break shorter than `CLOSE_GAP_TOLERANCE_SECONDS` (0.3 s) does not end the stay.
2. **Pass-through rejection.** If a person was moving (speed > `PASS_MOVING_SPEED` 0.3) over `PASS_BEFORE_WINDOW_SECONDS` before the closest approach AND after it, E12 waits `PASS_SEPARATE_SECONDS` (2.0 s) after the closest approach;
   if the pair separates past the close distance in that time it is a pass-through and the encounter never gets E12. A pair that stays close longer is not a pass-through.
3. **Additions I made beyond the brief, because the clips needed them** (each has a switch / constant):
   * `CLOSE_DISTANCE` is not widened by the legacy crowd rule (E23 widens every gate by 1.3 above 6 people; a scooter passing a pedestrian counted as close in `drk_KabilangRoad1`).
   * `CO_WALKERS_REJECTED = True`: two people who BOTH keep walking (median speed > 0.3 over the last 2 s) are not a standoff; E12 is withdrawn while that holds and its 5 s hold is counted from the last frame that was not co-walking. One of the two must be slow.
     Without this, two pedestrians walking side by side plus a knife hallucination gave a false Possible in `drk_KabilangRoad1` (the brief's "no speed requirement for both" does not distinguish them). Real holdups in the clips had at least one slow person (medians 0.12-0.33).
   * `SHADOW_HEIGHT_RATIO = 0.65`: a pair on the same foot line (bottom edges within 15 %, boxes touching sideways) is also a shadow when the shorter box is under 65 % of the taller's height. The brightness rule alone (< 60 %) never fired on ALT-0152.
     In the real holdups the pair height ratios were 0.79+ and 0.89+. A child standing next to an adult at the same depth could be mistaken for a shadow.
4. **Fake-person filter:** the face-keypoint / 6-keypoint requirement is removed (functions and constants deleted). Only the shadow rule remains (`FAKE_PERSON_FILTER`, `SHADOW_FEET_TOUCH_FRAC` 0.15, `SHADOW_BRIGHTNESS_RATIO` 0.6, `SHADOW_HEIGHT_RATIO` 0.65).
5. **Knife:** confidence weighting and the jump / flip checks stay; a knife counts as present for E14 if an accepted hit is at most `KNIFE_PRESENT_SECONDS` (2.0 s, was 1.0 s) old.
6. **Kept:** knife without E12 caps at Monitoring (`capped: no frozen pair`), sticky status, the v6.2.4 Qwen holdup changes (no system note, `people_count`).

## Results (dry runs; highest status per knife event)
| clip | event | v6.2 original | v6.2.4 | v6.2.5 |
|---|---|---|---|---|
| Aug24_16 real holdup | #2 (21.7 s) | Possible | Possible | Likely (first 19 frames capped at Monitoring until E12 held) |
| | #1 (24.8 s) | Possible | Possible | Likely |
| Demo Holdup 2 (real) | #1 (38 s) | Possible | Possible | Likely |
| | #2 (45 s) | Likely | Possible | Possible |
| Demo Holdup (real) | - | no event | no event | no event |
| ALT-0152 (shadow) | #4 (23.6 s) | Possible | Monitoring | Monitoring |
| Trim2 (walk-past) | #8 | Monitoring | Monitoring | no event |
| Kabilang (walk-pasts) | #61 / #91 / #102 | Monitoring | Monitoring | no event / Monitoring / Monitoring |
Real holdups reach Possible or Likely; ALT-0152 and the walk-pasts stay at Monitoring or below. Note the real holdups now often reach Likely (E10 + E12 + E14), higher than before, because E12 now fires on them.

## Limits
* The close-stay rule fires on anyone who stands near a knife holder for 2 s, including friends standing together; the knife cue and the cap are then the only protection.
* The shadow height rule is a heuristic tuned on one footage set (ALT-0152).
* Three real holdup clips (one never detected) and two walk-past clips are a small sample; thresholds are in the constants block.

# v6.2.8 — far-range puff from the cigarette's own movement (`FAR_PUFF_FROM_OBJECT`)
`SCORING_SPEC_VERSION = "v6.2.8"`. Switches: `FAR_PUFF_FROM_OBJECT` and `NEAR_MOUTH_HELD_BEFORE_CONFIRM` (both in the marked block of `core/vision/location_momentum.py`; `LOCATION_AWARE_MOMENTUM = False` turns everything off).

## Diagnosis (uploaded clip `Sep19_2 - TrimSmoking.mp4`, ALT-0164's clip; dry run with `watch_smoking --cascade`, logs in `v62_test_results/far_puff/logs/`)
* ALT-0164 itself (Likely 0.75) is the near smoker, track #1 (15-24 % of the frame tall, 25 pose puffs). The far smoker is track **#23** (box 5-16 % of the frame, 68-225 px; present 32.7-61.2 s) - Monitoring for 5 frames (43.6-44.0 s, score 0.40) and never Possible. Track #8 (21-31 %, back turned) also only reached Monitoring (5 frames).
* **The hypothesis "no puff because pose cannot resolve the person" is wrong for track 23.** On this build (v6.2.6 zoomed pose) its keypoints were fine: nose 0.98, wrists 0.9, anchors found in 187 of 281 frames. Pose puffs WERE counted (4 in total: puffs in 10 s = 1 from 37.3 s and 2 from 42.0 s), so the gate read "capped: needs 2 puffs in 30 s for Likely" from 37.3 s and no cap from 42.0 s: Possible was allowed.
* **What actually kept it at Monitoring:** (1) the cigarette is detected in only 54 of 281 frames, in short bursts (41.3-42.2 s: 9 frames, 'near mouth', head zone rel_y 0.10); the object cue turned ON at ~41.6 s but must stay ON 2.0 s (`object_confirm_seconds`) before it counts, so it only confirmed at 43.6 s, when the cigarette had already left view and momentum faded below 0.4 by 44.0 s. (2) `near_mouth` (+0.15, "held for cue_hold") was only evaluated while the cue was ON, so the mouth sighting at 41-42 s was never remembered: at 43.6 s the score was cigarette 0.40 only (Monitoring) instead of 0.55 (Possible).
* Track 8: nose confidence 0.09-0.37 (back turned) so no mouth anchor and no reach; the cigarette was only ever at chest height (rel_y 0.38-0.52), so the object fallback cannot help it either.

## Changes
1. **Object-based puff** (`update_puffs` -> `_update_object_puff`): used only when the person box is shorter than `FAR_PUFF_BELOW_FRAC` (0.25) of the frame height, or the nose / both wrists are under conf 0.3 (or there are no keypoints). A puff = the track's cigarette seen in the head zone (top `FAR_PUFF_HEAD_ZONE` 22 % of the box) and, within `FAR_PUFF_PAIR_SECONDS` (4.0 s) before or after, in the chest / hand zone (`FAR_PUFF_BODY_ZONE` 30-70 %); both sightings are consumed; at most one per `FAR_PUFF_MIN_GAP_SECONDS` (2.0 s). It appends to the same puff list, so it lifts the "no puff" cap and counts towards the 2 puffs for Likely; nothing else changes. Per-track counters `n_object_puffs` / `n_pose_puffs`.
2. **`NEAR_MOUTH_HELD_BEFORE_CONFIRM = True` (not asked for; needed for the far smoker):** `near_mouth` is now evaluated every frame (also while the object cue is still confirming), so its 5 s hold works. This is what lifts track 23 from Monitoring 0.40 to Possible 0.55.

## Results (dry runs, `watch_smoking --cascade`; before = commit 718a7d2, after = this change; highest status)
| clip | track | before | after | puffs pose / object (after) |
|---|---|---|---|---|
| Sep19_2 (ALT-0164) | #1 near smoker | Likely | Likely | 24 / 7 |
| | **#23 far smoker** | Monitoring | **Possible** (43.6 s) | 3 / 6 |
| | #8 | Monitoring | Monitoring | 3 / 0 |
| S2 FarVape (smoker) | #32 | Monitoring | Monitoring | 1 / 0 |
| S VapeNightNear (smoker) | #3 | Possible | Possible | 1 / 0 |
| Vape Far Night (to 40 s) | - | no event | no event | 2 / 0 |
| Aug14_12 TrimNull (no smoker) | - | no event | no event | 0 / 3 |
| Kabilang (walkers, to 70 s) | - | no event | no event | 4 / 1 |
| **Trim2** (the clip behind the earlier false Likely) | #1 | Monitoring (capped: no puff) | **Possible** | 1 / 11 |

## Caveats
* The object puff alone did not change any status on the far clip; the near_mouth change did. The object puff makes object-visible smoking count as puffs, which also lifts the cap on **Trim2**: track 1 (19-22 % of the frame, so under the 25 % size rule although its keypoints are good) shows the cigarette cycling head (rel_y 0.1-0.15) <-> chest (0.35-0.6) 11 times in 40 s, which looks like genuine puffing but has no ground truth. Someone should watch that clip before trusting it. If it is a false positive the size rule (`FAR_PUFF_BELOW_FRAC`) should only apply when keypoints are also unreliable.
* The walking / no-smoker clips stay without any event. Jittering cigarette detections inside a box can fabricate object puffs (3 on the null clip, 1 on Kabilang) but they only matter when the cigarette cue is ON.
* `FAR_PUFF_*` thresholds were not tuned beyond the specified starting values.
