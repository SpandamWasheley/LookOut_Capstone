# Test clips

Folder: `C:\Users\User\OneDrive\Desktop\Violation testing` (note the space — quote the path).
Use this **same set** for every baseline, calibration and before/after run.
All clips: 2560x1440, 10 fps, fixed high CCTV-style camera. Parking clips are not used.

| Folder | Clip | Length | Why it is in the set |
|---|---|---|---|
| Smoking | `Aug14_3 - MorningMediumBldg - Trim.mp4` | 60 s | Daylight, medium range; a man stands at the curb, often with a hand at the face. Mouth-anchor and hand-to-face ambiguity. |
| Smoking | `Aug18_18 - TrimCigaretteNightFar1.mp4` | 78 s | Night, far range; named for a cigarette. Far / turned faces. |
| Smoking | `Aug18_18 - Trim2.mp4` | 52 s | Night, medium-far; a man standing at the curb. |
| Drinking | `Aug18_4 - TrimDrinkingEveningFar3.mp4` | 93 s | Evening, far range; people on the sidewalk. Far / turned faces. |
| Drinking | `Aug18_5 - DrinkingKabilangRoad1.mp4` | 255 s | Daytime; people seated outside a storefront. The only group-gathering clip. |
| Holdup | `Aug24_16 - TrimHoldupBldg.mp4` | 28 s | Knife sanity check only (not used for mouth calibration). |

## What the set actually covers
Low-confidence tagging pass (merged_v2 conf >= 0.10, 1 frame/s, YOLOv8-pose for the person cues). Counts are sampled frames.
These are automatic tags, not ground truth: the highest-confidence smoking/drinking detections that were inspected by eye
were false positives (blurred vehicles, a motorcycle, a lamp post). The only visually confirmed true detection is the knife
in the Holdup clip.

| Case | Sampled frames (5 smoking+drinking clips, ~540 frames) | Verdict |
|---|---|---|
| Cigarette at mouth | 3 (Night-Far1: 2, Drinking-Evening: 1) | Unconfirmed; the top one (0.77) is a blurred vehicle |
| Cigarette away from mouth | 2 (both in the Kabilang drinking clip) | Unconfirmed |
| Bottle at mouth | 0 | **Not covered** |
| Bottle held down / on a table | 7 held down, 15 with no person nearby | Unconfirmed (detections look like vehicles) |
| Hand-to-face without an object | 1 | Barely covered |
| Group (>= 3 people in frame) | 126 frames (88 in Kabilang) | Covered |
| Turned / far face (no confident nose) | ~250 person-instances; only 13 persons under 170 px tall | Covered for turned, thin for very far |
| Knife (Holdup clip) | 2 sampled frames at conf >= 0.25 (max 0.63) | Confirmed by eye |

Known weak spots: no verified bottle-at-mouth or cigarette-at-mouth example, so alert/cue comparisons on these clips
mostly measure false-positive behaviour. Add 2-3 close clips if before/after alert behaviour matters.
