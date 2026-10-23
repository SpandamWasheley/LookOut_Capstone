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
| Constant | Value |
|---|---|
| `LOCATION_AWARE_MOMENTUM` | `True` |
| `GATED_CLASSES` | `("cigarette", "bottle")` |
| `KP_CONF_MIN` | `0.3` |
| `KP_MAX_AGE_SECONDS` | `0.05` |
| `POSE_MATCH_IOU` | `0.3` |
| `CLOSE_SHOULDER_FRAC` | `0.5` |
| `CLOSE_HEIGHT_FRAC_NO_SHOULDERS` | `0.15` |
| `JUMP_HEIGHT_FRAC` | `0.25` |
| `JUMP_MEMORY_SECONDS` | `2.0` |
| `CONF_FULL_AT` | `0.6` |
| `CLASS_FLIP_FRAMES` | `5` |
| `CLASS_FLIP_IOU` | `0.3` |
| `PUFF_ARM_HOLD_SECONDS` | `3.0` |
| `PUFF_MIN_GAP_SECONDS` | `1.0` |
| `POSSIBLE_PUFF_WINDOW_SECONDS` | `10.0` |
| `POSSIBLE_MIN_PUFFS` | `1` |
| `LIKELY_PUFF_WINDOW_SECONDS` | `30.0` |
| `LIKELY_MIN_PUFFS` | `2` |
| `CAP_NO_PUFF_TEXT` | `"capped: no puff"` |
| `CAP_FEW_PUFFS_TEXT` | `"capped: needs 2 puffs in 30 s for Likely"` |
| `GROUP_AREA_EXPAND` | `1.2` |
| `STATIONARY_HEIGHT_FRAC` | `0.10` |
| `STATIONARY_MEMORY_SECONDS` | `2.0` |

## Turn it off
Set `LOCATION_AWARE_MOMENTUM = False` in `core/vision/location_momentum.py`: object momentum, the puff gate and the Path B rule all revert to v6 behaviour (the scoring spec string still reads v6.2).

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
