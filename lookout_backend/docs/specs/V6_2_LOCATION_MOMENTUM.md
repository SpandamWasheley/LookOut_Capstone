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
