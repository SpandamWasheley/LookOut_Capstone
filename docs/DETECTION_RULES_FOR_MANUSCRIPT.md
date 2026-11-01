# LookOut — Detection Rules, Heuristics and Violation Indicators

> **Purpose of this file.** Hand this to Claude Chat so it can build the manuscript tables and prose describing how LookOut detects each violation. Everything below was read from the code (scoring spec **v6.2.11**, `lookout_backend/core/vision/` and `management/commands/`). Nothing here is a measured accuracy result. Every threshold is a *reasoned default*, not a field-calibrated figure, and the manuscript should say so.
>
> **Suggested tables for Claude Chat to produce** (one per section marked 📋):
> 1. Violations in scope and the models/algorithms behind each
> 2. Status levels (Monitoring / Possible / Likely)
> 3. Indicators table per violation: Smoking, Drinking, Holdup, Parking, Improper Disposal
> 4. Adjustable timing parameters
> 5. Holdup time-of-day table
> 6. Literature basis of each indicator

---

## 1. System overview

LookOut is a **single-camera** barangay security system (one Hikvision DS-2CD1047G2, code `CAM-SMOKE-01`). It detects five violations only. There is **no facial recognition** and no identification of people; detection is object- and behaviour-based.

| # | Violation | Stored code | Detection family |
|---|---|---|---|
| 1 | Smoking | `smoking` | Object (cigarette) + pose (hand-to-mouth) → weighted score |
| 2 | Public drinking | `drinking` | Object (bottle) + pose + group/time cues → weighted score |
| 3 | Holdup (theft with a knife) | `thief` / holdup | Object (knife) + two-person behaviour + time of day → weighted score |
| 4 | Parking obstruction | `parking` | Vehicle detection + polygon zone + dwell-time accumulation |
| 5 | Improper disposal (discarded waste) | `disposal` | Background subtraction (static object) + zero-shot YOLO-World check |

Dashboard display names: Smoking, Drinking, Discarded Waste, Parking Obstruction in Road, Holdup.

### 1.1 Processing pipeline (shared chain)

```
Camera (RTSP) → A. Detect → B. Associate/Track → C. Accrue over time → E. Pattern rules → D. Decide → Alert (+ evidence image/clip)
                                                                                                  ↘ AI checker (display only)
```

| Stage | What happens | Code |
|---|---|---|
| A. Detect | YOLOv8 finds people and violation objects; YOLOv8-pose gives body keypoints | `recognition.py` |
| B. Associate | Each detected object is attached to the person holding it; people are tracked frame-to-frame | `tracking.py` (ByteTrack + greedy IoU/centre-distance matching, tombstone re-ID) |
| C. Accrue | An object must stay detected before it counts ("momentum") | `momentum.py`, `location_momentum.py` |
| E. Pattern | Class-specific behaviour rules (hand-to-mouth, frozen pair, gathering, vehicle dwell) | `theft.py`, `location_momentum.py`, `obstruction_zone.py` |
| D. Decide | Cues are summed into a score and mapped to a status; cooldowns prevent repeat alerts | `scoring.py`, `watch_*.py` |

### 1.2 Models used 📋

| Model | Role | File / identifier |
|---|---|---|
| **merged_v2** (custom YOLOv8, 3 classes: `Bottle`, `Cigarette`, `knife`) | Detects the smoking item, bottle and knife | `core/vision/merged_v2.pt` |
| YOLOv8-pose | 17-point body keypoints (nose, shoulders, elbows, wrists) for the mouth anchor and hand-to-mouth gesture | `recognition.detect_pose` |
| Stock YOLOv8n (COCO) | Person boxes, and vehicle boxes (car, motorcycle, bus, truck) | `yolov8n.pt` |
| ByteTrack | Persistent person IDs across frames | via ultralytics `bytetrack.yaml` |
| MOG2 (OpenCV) ×2 | Dual background subtraction for the static-object stage of disposal | `disposal.py` |
| YOLO-World (`yolov8s-worldv2.pt`) | Zero-shot "is this garbage?" check for disposal | `disposal.py` |
| Qwen3-VL via Ollama (2B-instruct for smoking/drinking, 4B-instruct for holdup) | **AI checker**: reads a short run of frames and gives a *display-only* second opinion. It never changes the official score or status | `ai_checker.py`, `ai_status.py` |

Training data for merged_v2: Roboflow project *lookout_violation_objectdetection* v2 "All_Violations_v2" (CC BY 4.0), 3,716 full-resolution frames (2560×1440) cut from the team's own recorded footage, 70/20/10 split; trained on 256×384 person-sized crops (14,442 crops incl. 1,410 negatives). Instances (full-res set): Bottle 3,446 · Cigarette 1,103 · knife 1,360.

---

## 2. The scoring model (smoking, drinking, holdup)

Smoking, drinking and holdup use one **weighted-sum evidence score**:

```
score  = min(1.0,  Σ ( weight_i × cue_i )  ×  Π multiplier_j )
```

- Each *indicator* (cue) is either present (1) or absent (0) and carries a fixed weight in points (0.40 = 40 points).
- The score is a **bounded evidence score, not a probability.**
- Weights and the 55 / 75 cut-offs are **fixed by the specification and are deliberately not user settings** (changing them would change what "Possible" and "Likely" mean). Only timings and conditions are adjustable (Section 8).
- **Object gate ("no object, no event").** Every violation has one gate indicator (cigarette / bottle / knife). Without it nothing is shown. The one exception is the capped *puff-only* smoking path (Section 4.1).
- **Dependent indicators** (e.g. "item at the mouth") only score if their gate object also fired — a hand near a face is also eating, phoning or scratching.
- The AI checker adds **no points**, applies **no multiplier** and never changes a status.

### 2.1 Status levels 📋

| Status | Stored value | Rule | What happens |
|---|---|---|---|
| **Monitoring** | `monitoring` | Gate object is ON and score < 0.55 | Quiet watchlist entry; stored, **no notification** |
| **Possible** | `warning` | Score ≥ 0.55 (55 points) | Alert sent, low priority |
| **Likely** | `violation` | Score ≥ 0.75 (75 points) | Full alert |
| Not shown | `none` | Gate object OFF | Behaviour points are still logged for calibration |

**Hysteresis.** Rising is immediate. Falling out of Possible/Likely requires the score to drop **5 points below** the threshold that admitted the status (Possible is left below 0.50; Likely below 0.70). This stops a score hovering on a boundary from flapping between statuses.

**Alert lifecycle.** `active` → `dispatched` → `resolved`, or `active/dispatched` → `acknowledged` (dismissed).

### 2.2 Object cue — "momentum" (how a flickering detection becomes a stable ON/OFF)

A small object (cigarette, bottle, knife) flickers in and out of YOLO detection even when it never leaves the scene. A hard "detected N seconds in a row" rule would reset on every dropped frame. Instead each (person-track, class) slot keeps a running score:

```
momentum = momentum × DECAY + confidence_this_frame        (capped at MAX)
cue ON   when momentum ≥ ON
cue OFF  when momentum <  OFF        (OFF < ON → hysteresis)
```

| Parameter | Value |
|---|---|
| DECAY (per processed frame) | 0.90 (memory ≈ 10 frames) |
| ON threshold | 1.5 |
| OFF threshold | 0.4 |
| MAX | 3.0 |
| Knife hold (momentum frozen after last sighting) | 1.0 s (small object, flickers when the hand turns) |
| Object must stay detected before Monitoring (`object_confirm_seconds`) | 2.0 s |
| A behaviour cue (e.g. hand at mouth) still counts for (`cue_hold_seconds`) | 5.0 s |
| A Monitoring event shorter than this that never grew is dropped (`monitoring_min_seconds`) | 3.0 s |

**Location-aware momentum (v6.2).** A detection only adds momentum when it is **where a held object can be**: at a wrist or at the mouth. Detections at the chest, legs, a sudden jump of more than 0.25 person-heights, a class flip within 5 frames, or with no keypoints add nothing. "Close" means within **0.5 × shoulder width** of the anchor (0.15 × person height when a shoulder is missing). This stops a lamp post, phone or pen at the hip from feeding the cigarette cue. Momentum increment is scaled by `min(1, confidence / 0.6)`.

### 2.3 Detection confidence floors (Settings defaults)

| Detector | Confidence floor | Dwell setting (legacy) |
|---|---|---|
| Parking (vehicle) | 35 % | 60 s (superseded by zone alert score, below) |
| Smoking | 30 % | 3 s |
| Holdup (knife) | 30 % | 3 s |
| Drinking | 35 % | 8 s |
| Disposal (zero-shot) | 15 % | 2 s static |

The custom model scores genuine matches on a lower raw-YOLO scale (~0.3–0.9) than a "percent sure" intuition, hence the low floors. Inference runs at larger input sizes (e.g. 1280 for vehicles) with tiled "far mode" so distant objects stay resolvable.

---

## 3. Shared heuristics and guards

| Heuristic | Rule | Why |
|---|---|---|
| Person tracking | Greedy IoU ≥ 0.3, fallback centre distance ≤ 0.8 box-size; track survives 2.0 s unseen; tombstone memory 10 s with re-ID radius 1.2 | Dwell timers follow a person through brief occlusion instead of restarting |
| Detection-to-person assignment | Object belongs to the person whose box (grown by 0.45 × box width on both axes) contains it; otherwise it becomes a position-keyed *scene track* | A cigarette/knife held out sideways would otherwise fall outside the box |
| Spatial cooldown | A place, not a track ID, is the cooldown key (drinking radius 1.5 person-heights; disposal 60 px; parking 1.5 box-widths for 120 s) | Track IDs churn; the same spot must not alert repeatedly |
| Distance-invariant thresholds | Distances are measured in face-widths, shoulder-widths, person-heights or box-widths, never raw pixels | Same rule works for near and far people |
| Edge-case guards (holdup) | Shadow filter, co-walker rejection, pass-through rejection (Section 6) | Remove the commonest false positives seen in footage |
| Evidence | Every alert saves an evidence image/clip under `media/violations/` and the full cue vector (for audit and calibration) | Traceability for the panel and for later threshold checking |

---

## 4. SMOKING

**Algorithm.** merged_v2 detects `Cigarette` → location-aware momentum gives the object cue → YOLOv8-pose measures the mouth anchor and hand-to-mouth gestures → cues are summed.

### 4.1 Indicators 📋

| Indicator (code key) | Points | Plain-language label shown to the tanod | How it is detected | Literature / basis |
|---|---|---|---|---|
| `cigarette` — **GATE** | **0.40** | Smoking item seen | merged_v2 `Cigarette` class, momentum cue ON, located at a wrist or the mouth | YOLOv8-MNC (2023) |
| `gesture` | 0.20 | Hand going to the mouth | Pose: wrist reaches the nose region then drops (hand-raised ratio ≤ 0.7 shoulder-widths of the nose; hand-lowered ratio > 1.3) — a completed hand-to-mouth cycle, ≥ 1 puff | Pose-based gesture counter |
| `puffs` | 0.20 | Did it again (2+ puffs) | Same gesture repeated, 2 or more puffs | Pose |
| `puff_pattern` | 0.15 | Repeated puffs (3+ within 5 minutes) | 3 or more puffs inside a 5-minute window (`smoking_puff_count=3`, `smoking_puff_window_minutes=5`) | Pose |
| `near_mouth` — *dependent* | 0.15 | Smoking item at the mouth | Cigarette within **0.7 × shoulder width** of the mouth point; scores only if `cigarette` fired | Mouth-proximity rule |
| **Total** | **1.10, capped at 1.00** | | | |

No smoke-plume indicator is used on purpose: vision-language models fail on small smoke (SmokeBench).

### 4.2 What counts as one "puff" (v6.2.1 – v6.2.11)

A puff = the **wrist reaches the mouth** AND a **cigarette on the same person is seen at that wrist or at the mouth within ±5.0 s** (either order). Refinements:

- Two reaches closer than **1.0 s** count as one.
- If the wrist keypoint is missing, the **elbow** is used (elbow-to-mouth radius 1.1 × shoulder width).
- **Far-range puff from the cigarette's own movement:** when pose cannot resolve a small/distant person, a puff is counted when the cigarette is seen in the **head zone** (top 22 % of the person box) and, within 4 s, lower in the **chest/hand zone** (30–70 %). At most one such puff per track per 2 s.
- **Zoomed pose:** people shorter than 40 % of frame height get keypoints from a pose pass on their own upscaled crop. A puff from the zoomed pose additionally needs a **completed hand-to-mouth cycle** (rejects a person who never lowers their hand).
- A walking-person block exists (> 0.3 box-heights/s) but is **switched off** by default, because a person can smoke while walking.

### 4.3 Puff gate (caps the status)

| Status wanted | Requires |
|---|---|
| Possible | ≥ 1 puff in the last 10 s (else held at Monitoring) |
| Likely | ≥ 2 puffs in the last 30 s (else held at Possible) |

### 4.4 Puff-only path

If **no smoking item** is detected but **3+ puffs occur within 5 minutes**, an alert may still be raised — **capped at Possible** and tagged *"No smoking item detected — based on hand movement only"*. Fewer puffs are logged and never shown. (Used for vaping / small or hidden items.)

### 4.5 Worked examples

| Evidence | Score | Status |
|---|---|---|
| Cigarette only | 0.40 | Monitoring |
| Cigarette + at mouth | 0.55 | Possible |
| Cigarette + at mouth + gesture | 0.75 | Likely (if puff gate met) |
| Cigarette + gesture + puffs | 0.80 | Likely |

---

## 5. DRINKING (public drinking)

**Algorithm.** merged_v2 detects `Bottle` → momentum gate → pose finds bottle-at-mouth → persons are clustered into gatherings ("inuman") → time band checked. Two evidence paths: **Path A (solo)** — an individual's bottle is verifiable on its own near the camera; **Path B (gathering)** — at range a sustained group is still verifiable.

### 5.1 Indicators 📋

| Indicator (code key) | Points | Plain-language label | How it is detected | Literature / basis |
|---|---|---|---|---|
| `bottle` — **GATE** | **0.40** | Bottle seen | merged_v2 `Bottle` class, momentum ON | Omamalin (2022) |
| `gathering` | 0.10 | Group of 2 or more | ≥ 2 stationary people within cluster distance 1.8 box-widths; the group area is the member union box scaled ×1.2; "stationary" = moved < 0.10 × median member height (`drinking_min_group=2`) | Omamalin (2022); Setti (2015) |
| `gathering_duration` | 0.15 | Stayed 10+ minutes | The gathering persists ≥ 600 s (`drinking_group_duration=600`); survives a 3 s dip in headcount; dropped if no group for 3 s | Omamalin (2022) — tagay sessions last hours |
| `at_mouth` — *dependent* | 0.15 | Bottle raised to the mouth | Bottle within **3.0 face-widths** of the mouth (`drinking_mouth_proximity`); scores only if `bottle` fired | arXiv 2507.00566 |
| `time_band` | 0.05 | Evening (4 PM – midnight) | Local time in 16:00–00:00 (adjustable) — a **scored cue, not a gate**: drinking outside the band is still drinking | Omamalin (2022); Thai ED series (2021) |
| **Total** | **0.85** | | | |

Other drinking parameters: bottle evidence in a cluster must be seen within **12 s** (`drinking_evidence_max_age`) or it no longer arms the cluster; a bottle merely *held* (not at mouth) needs a longer bar (`drinking_held_dwell` 24 s, legacy); optional hard hour gate `drinking_hours_enabled` is OFF by default.

### 5.2 Worked examples

| Evidence | Score | Status |
|---|---|---|
| Bottle only | 0.40 | Monitoring |
| Bottle + at mouth | 0.55 | Possible |
| Bottle + at mouth + group | 0.65 | Possible |
| Bottle + group + 10 min + evening | 0.70 | Possible |
| Bottle + at mouth + group + 10 min | 0.80 | Likely |
| All five | 0.85 | Likely |

---

## 6. HOLDUP (knife-point theft)

**Algorithm.** merged_v2 detects `knife` → knife momentum (held 1 s) → two-person geometry rules (the "E-codes") → time-of-day added points. A holdup is defined by the **inverse of a snatch**: someone loiters, closes in, and the pair *freezes* in a confrontation.

The theft module also contains snatch (E6–E9), carnapping (E15–E19) and unattended-property (E21–E22) rules. These are **disabled by default** and inert in the live system; the scope is holdup only, so a clean precision figure can be reported for the one pattern claimed.

### 6.1 Indicators 📋

| Indicator (code) | Points | Plain-language label | How it is detected | Literature / basis |
|---|---|---|---|---|
| `E14` — **GATE** | **0.45** | Knife seen near a hand | merged_v2 `knife` class, accepted hit ≤ 2.0 s old; within **0.5 person-heights** of a person; momentum ON | Fernandez-Testa (2024) |
| `E12` | 0.20 | Two people frozen close together | **"Stayed close":** two people within **1.0 person-height** of each other for **≥ 2.0 s** of video time (gaps < 0.3 s tolerated; overlapping boxes count as close; not widened in crowds). The knife must be seen **during** the stay or up to **3 s before / 3 s after** it | Ruiz-Santaquiteria (2021) |
| `E10` | 0.10 | Someone loitering first | A track present ≥ 20 s (`holdup_loiter_seconds`) with path efficiency ≤ 0.30; **remembered for 60 s** (and re-linked to a new track ID within 3 s / 0.5 body-heights) | Citation pending |
| `E11E12` | 0.10 | Approach, then freeze | Rapid approach (from > 3.0 to < 1.0 person-heights within 3 s) followed by E12 within 5 s. E11 alone is worth 0 | Layer-E rule |
| `E20` | +1 to +10 points | Time of day | Added points from the Manila block table (below). Counted **only after** a behavioural cue (E12) corroborates the knife | Robielos & Duran (2020) |

Holdup keeps a *separate* maximum-style table in `scoring.HOLDUP_WEIGHTS` (E14 0.45, E12 0.20, E10 0.10 = 0.75), which is the basis of the status thresholds; the live engine (`theft.WEIGHTS`) adds `E11E12` and `E20`.

### 6.2 Hard requirements and caps

| Rule | Effect |
|---|---|
| A knife alone (or with nobody near the holder) | **Always Monitoring only** — a lone weapon is a watchlist entry, not an alert |
| Second person must be **near the knife holder** | Within **1.75 holder-heights** (`holdup_near_person_heights`, range 0.5–4.0), else status capped at Monitoring |
| **Knife + frozen pair (E12) required** to rise above Monitoring | With the v6.2 location rules ON, loitering (E10) is still scored but no longer lifts a knife above Monitoring on its own |
| E20 cannot change the band | base ≥ 75 → base + E20; 55 ≤ base < 75 → min(base + E20, 74) stays Possible; base < 55 → min(base + E20, 54) stays Monitoring. The hour only ever **adds**; no hour can hide a holdup or create one |
| Sticky status (v6.2.1) | The caps block only the *first* rise to Possible/Likely |

### 6.3 False-positive guards

| Guard | Rule |
|---|---|
| Shadow filter | A pair is ignored when it is a person and their own shadow: bottom edges within 0.15 × taller height and boxes touching sideways, **and** one box < 0.6 × as bright **or** < 0.65 × as tall |
| Co-walker rejection | If **both** people keep walking (median speed > 0.25 box-heights/s) they are walking together, not in a standoff — no E12 |
| Pass-through rejection | Someone moving before **and** after the closest approach, and the pair back outside the close distance within 2.0 s, is a walk-past — E12 waits 2 s to be sure |
| Absolute speeds | Walking is 0.5–1.4 box-heights/s; standing sway reaches ~0.2; threshold 0.25 |
| Clip time for E20 | On a file source, E20 is off unless the footage start time (`--clock`) is given; live streams use the wall clock |

### 6.4 Holdup time-of-day table (E20, Manila) 📋

Source: Robielos & Duran (2020), IEOM — five years of City of Manila robbery and theft records modelled as a discrete Bayesian network. Points = `round(10 × p_block / p_max)`, so the peak block = 10 and the quietest = 4. Multiplier (informational) = p_block / 0.125.

| Block (Manila time) | Incident probability | Multiplier | E20 added points |
|---|---|---|---|
| 00:00 – 03:00 | 0.095 | 0.76 | 6 |
| 03:00 – 06:00 | 0.100 | 0.80 | 6 |
| 06:00 – 09:00 | 0.070 | 0.56 | 4 |
| 09:00 – 12:00 | 0.156 | 1.25 | 9 |
| 12:00 – 15:00 | 0.163 | 1.31 | 10 |
| **15:00 – 18:00 (peak)** | **0.170** | **1.36** | **10** |
| 18:00 – 21:00 | 0.136 | 1.09 | 8 |
| 21:00 – 24:00 | 0.109 | 0.87 | 6 |

**Honest caveat (should be stated in the manuscript):** international evidence associates darkness with street robbery, so this table disagrees with the wider literature. It comes from a different city, combines robbery with theft, and counts only cases that reached conviction. A bad hour can never create an alert by itself.

### 6.5 Worked examples

| Evidence | Score | Status |
|---|---|---|
| Knife only | 0.45 | Monitoring |
| Knife + frozen pair | 0.65 | Possible |
| Knife + frozen pair + loitering | 0.75 | Likely |
| Knife + frozen pair + approach→freeze | 0.75 | Likely |
| Knife + loitering, nobody frozen close | 0.55 → held | Monitoring (no E12) |

---

## 7. PARKING OBSTRUCTION

**Algorithm.** Not a weighted-sum model: **no indicator points**. A vehicle accrues *seconds of obstruction* while it is stopped inside an operator-drawn polygon; when the banked seconds reach the alert score, one alert fires.

Basis: Ordinance 601 (per the scoring spec: ≥ 50 % of the vehicle past the road edge for 5 minutes). The deployed **defaults are deliberately more sensitive** (35 % for 2 minutes), documented in `docs/specs/parking_defaults.md`.

### 7.1 Indicators and parameters 📋

| Indicator / rule | Value | How it works |
|---|---|---|
| Vehicle detected | car, motorcycle, bus, truck; confidence ≥ 35 %; inference at 1280 px with tiling for distant vehicles | Stock YOLOv8n |
| **No-parking zone** | One polygon drawn by the operator (or suggested automatically, see 7.3) | `draw_zone` / `trace_zone` |
| **Inside-the-road share** | ≥ **35 %** of the vehicle's ground footprint inside the polygon (`Camera.obstruction_pct`, default 35); 21 sample points along the box bottom edge; engage at 35 %, release below 25 % (hysteresis 10 %) | A *share of itself*, so no tape measure or per-camera calibration; a single-point test flipped frame to frame for straddling vehicles |
| **Stopped** | Centre drift ≤ **0.15 box-widths** over a **3.0 s** window | A driving vehicle is traffic, not an obstruction |
| **Moving weight** | **0** — only stopped time banks | A passing car must never accrue |
| **Alert score** | **120 s** (2 minutes) of stopped-inside time | Zone `ALERT_SCORE` |
| Decay while detected but outside the zone | −2.0 s per second | |
| Decay while *not detected* (occluded/dropped) | −0.5 s per second after a 2.0 s grace | A short occlusion should not cost seconds of a 2-minute timer |
| ID-churn fix | A new track ID may inherit a lost vehicle's score if within 1.2 box-widths **and** IoU ≥ 0.55; state forgotten after 30 s unseen | |
| Cooldown | No repeat alert at the same spot within 120 s (radius 1.5 box-widths) | |
| Frame time step clamp | ≤ 2.0 s | Slow FPS / stalls |

Time source: video time for files, wall time for live RTSP.

### 7.2 Status for parking

| Status | Rule |
|---|---|
| Monitoring | Vehicle is being judged (seen, little or nothing banked) |
| Possible | Inside the zone and banked ≥ 55 % of the alert score (≈ 66 s of 120 s) |
| Likely | The alert score is reached → **alert fires** |

The alert card shows **Object confidence only** (no score breakdown).

### 7.3 Automatic zone suggestion

`zone_autodetect.py` / `trace_zone`: runs the vehicle detector over a sampled clip and builds a **convex hull of the ground points** (bottom-centre of each box) of vehicles that were seen to **move**. Two filters: a track must be seen a minimum number of times, and must have travelled a minimum multiple of its own box width (this kills parked vehicles, since a parked car cannot be told apart from a car on the road). It is a **suggestion, never a commit** — the operator checks and edits it before starting.

---

## 8. IMPROPER DISPOSAL (discarded waste)

**Algorithm.** The only detector with **no trained violation class** — garbage has no class in merged_v2. A hybrid, two-stage pipeline:

```
Stage 1 (classical CV):  what ARRIVED and then STOPPED MOVING?   → dual-background subtraction
Stage 2 (zero-shot VLM): is that thing garbage, and not a dog?  → YOLO-World, garbage must outscore every look-alike
```

It shares no model pass with the merged detectors and runs as its own live process (`watch_disposal`), gated on `disposal_enabled`. It carries its own confirmation logic (no weighted cue table), so the alert shows **Object confidence** and no score breakdown.

### 8.1 Stage 1 — static-object detection 📋

| Rule | Value | Rationale |
|---|---|---|
| Processing width | 960 px | Speed |
| Warm-up (learn the empty scene) | 5.0 s at learning rate 0.05 | Do not detect anything before the scene is learned |
| **Long** background model (MOG2) | learning rate **0.0005** | A dumped object stays "foreground" for minutes |
| **Short** background model (MOG2) | learning rate **0.02** | The same still object is absorbed within seconds |
| **Static-new-object** | `LONG foreground AND NOT SHORT foreground` | A walking person is foreground in both and cancels out; an object set down and left survives the difference. Motion is what gets filtered — "left" is a motion property |
| Shadow handling | Foreground threshold 200 (MOG2 marks shadows 127, real foreground 255) | |
| Blob size bounds | 0.08 % – 8 % of the frame (`disposal` size settings, percent) | Floor drops specks/insects; ceiling drops a cloud re-lighting the whole frame |
| Must stay still | ≥ **2.0 s** (`disposal_static_seconds`) | Rubbish being carried never stops, so never qualifies |
| Candidate matching | 40 px at processing width; candidate dropped after 2.0 s unseen | |
| Ignore movers | ON by default: anything inside a detected person/vehicle box (grown 8 %, ≥ 50 % overlap) is ignored | Measured: people and cars were 62 % of everything stage 2 had to reject |
| **Owner / abandoned-object rule** | The person within 80 px of the object when it *appears* is its owner; the candidate is held until they walk away (≥ 3 s), but never held longer than 3 s in total | "Left" means the person who put it there walked away; operator-chosen short hold means this rule is nearly relaxed |
| Suspect crop ("likely dropped by") | Buffer ~4 frames/s from 3 s before to 1 s after the object appeared; closest person within a 1.5× factor, preferring the larger crop | The only window where the carrier is still in shot |

### 8.2 Stage 2 — zero-shot garbage confirmation 📋

| Item | Value |
|---|---|
| Model | YOLO-World `yolov8s-worldv2.pt`, crop padded by 50 % (min crop 96 px) |
| Garbage prompts (accepted) | plastic bag, garbage bag, trash bag, pile of trash, sack, cardboard box, rice sack, styrofoam, pile of plastic bottles |
| Reject prompts (look-alikes) | person, dog, cat, motorcycle, car, bicycle, chair, trash can, garbage bin, bucket, potted plant |
| Combining synonymous prompts | **Noisy-OR** on the same object (IoU ≥ 0.5): `1 − Π(1 − s)`. Several weak hits on one box corroborate each other. Reject classes use the plain **maximum** (different objects are not witnesses) |
| Confirmation floor | `disposal_confidence` = **15 %** (zero-shot scores run low; a correct sack often lands near 0.2) |
| Model prediction floor | 5 % (so a weak match is visible instead of being discarded by the model's own NMS) |
| Verdicts | **Confirmed** = garbage leads *and* ≥ 15 % → **Likely**. **Weak** = garbage leads but < 15 % → **Possible**. **Rejected** = a reject class wins → below Monitoring |
| Re-check | A rejected candidate is re-asked after 10 s |
| Pinned candidates | Once judged garbage, a candidate is pinned at its box when stage 1 forgets it (the slow background model eventually absorbs the bag); an appearance check re-asks every 15 s; pin lasts at most 30 min; drops after 2 consecutive misses |
| One alert per place | Place-keyed registry: a re-raised candidate adopts the open alert (status rises, peak level never falls). Cooldown `disposal_cooldown` = 120 s idle window per place; rubbish further than `disposal_cooldown_dist` = 60 px away is a separate violation |

### 8.3 Waste-collection window 📋

Barangay Tetuan allows garbage to be set out for collection only **04:00 – 07:00, Monday to Saturday**; there is **no collection on Sunday**. A bag set out inside the window is allowed, so it is parked on the quiet watchlist instead of alerting. Outside the window, and all day Sunday, disposal behaves exactly as described in 8.1 – 8.2.

| Rule | Value |
|---|---|
| Inside the window (Mon–Sat, 04:00 ≤ time < 07:00, Asia/Manila) | A candidate stage 2 judges **Confirmed** or **Weak** is stored at **Monitoring** (`monitoring`): no notification, not in the normal alert list. Evidence, the place-keyed registry and the "likely dropped by" crop are kept exactly as for any other disposal alert |
| Reason shown | The alert description ends "Within collection window (4:00–7:00 AM)." so a reviewer can see why it stayed at Monitoring |
| Kept tracking until the window closes | A window-held candidate is exempt from the 30-minute pin cap: it stays tracked until the window closes **plus two presence checks** (2 × 15 s), so a bag set out at 05:00 is still followed at 07:00 even after the slow background model has absorbed it. The 30-minute cap restarts when the hold is released |
| When the window closes (07:00) | Every held candidate is re-asked once (a visible one by a dedicated re-check, a pinned one by its ordinary presence check). **Still there and still judged garbage** → the same alert row is raised to its normal status: **Likely** if Confirmed, **Possible** if Weak (peak level rises; the reason is removed from the description). **Not garbage on two consecutive looks** (collected, or never a bag) → the row stays at Monitoring |
| A status already reached is never lowered | The window applies only to a row that is new or already held at Monitoring; a Possible/Likely row that exists when 04:00 arrives is left as it is |
| Time source (same as holdup E20) | **Live RTSP:** the wall clock in Asia/Manila. **Uploaded file:** footage start time from `--clock` ("Recorded at") plus the position in the clip. **File with no `--clock`:** the rule is skipped (treated as outside the window) and a warning is logged once |
| Settings | `disposal_collection_enabled`, `disposal_collection_start`, `disposal_collection_end`, `disposal_collection_days` (section 9). The active values are logged with every disposal alert |

Because the window is a legal allowance, not a detection property, it changes only the **status** an alert is filed at and never whether the object is detected; switching it off (`disposal_collection_enabled` = false) restores the pre-window behaviour for every hour of every day.

---

## 9. Adjustable parameters (spec defaults) 📋

Only **timings and conditions** are adjustable (Settings page). Points per indicator and the 55 / 75 cut-offs are fixed.

| Parameter | Default | Allowed range | Applies to |
|---|---|---|---|
| `object_confirm_seconds` | 2.0 s | 0.5 – 10 | all |
| `cue_hold_seconds` | 5.0 s | 0 – 30 | all |
| `monitoring_min_seconds` | 3.0 s | 0 – 60 | all |
| `drinking_group_duration` | 600 s | 60 – 7200 | drinking |
| `drinking_min_group` | 2 | 2 – 20 | drinking |
| `drinking_start` / `drinking_end` | 16:00 / 00:00 | any | drinking |
| `smoking_puff_count` | 3 | 2 – 10 | smoking |
| `smoking_puff_window_minutes` | 5 min | 1 – 30 | smoking |
| `holdup_loiter_seconds` | 20 s | 5 – 300 | holdup |
| `holdup_near_person_heights` | 1.75 | 0.5 – 4.0 | holdup |
| `parking_confidence` | 35 % | – | parking |
| `obstruction_pct` (per camera) | 35 % | – | parking |
| `disposal_confidence` | 15 % | – | disposal |
| `disposal_static_seconds` | 2 s | – | disposal |
| `disposal_cooldown` / `_dist` | 120 s / 60 px | – | disposal |
| `disposal_collection_enabled` | on | on / off | disposal |
| `disposal_collection_start` / `disposal_collection_end` | 04:00 / 07:00 | end must be later than start (no midnight span) | disposal |
| `disposal_collection_days` | `0,1,2,3,4,5` (Mon–Sat; Monday = 0, Sunday = 6) | any subset of 0–6 | disposal |

The active settings are logged with every alert so a reader knows which timings produced a given status.

---

## 10. AI checker (display-only second opinion)

A local **Qwen3-VL** model (Ollama, temperature 0, JSON-schema constrained reply) is shown 8–12 full-resolution frames cropped around the alert subject (up to 5 s before and 1 s after the trigger). It answers what is *happening* (the detectors already answered what is *there*). It runs once, at the first Possible/Likely alert.

| Violation | Questions asked (reply fields) |
|---|---|
| Smoking | smoking item visible; hand-to-mouth activity = smoking / other_activity / none / unclear; confidence |
| Drinking | drinking likelihood; table/chairs visible; drinking items visible; scene type (drinking_session / other_activity / unclear); confidence |
| Holdup | people count; object pointed at a person; victim response visible; holdup likelihood; scene type (confrontation / other_activity / unclear); confidence |

**Display only.** It never changes the official score or status, never affects notifications or priority. It produces an AI badge and a *suggested* status that moves the official status by **at most one step**. Going **up** is stricter than going down (needs high confidence and, for drinking and holdup, two agreeing answers); puff-only smoking is never suggested above Possible. A failed or invalid reply shows "AI context unavailable". Rationale: a wrong downward suggestion makes the tanod look more carefully; a wrong upward one can push an officer to act on someone innocent.

---

## 11. Indicator summary (quick reference for the manuscript) 📋

| Violation | Gate (required object) | Supporting indicators | Max score | Possible ≥ 0.55 example | Likely ≥ 0.75 example |
|---|---|---|---|---|---|
| Smoking | Cigarette (0.40) | Hand to mouth 0.20 · 2+ puffs 0.20 · 3+ puffs/5 min 0.15 · Item at mouth 0.15 | 1.00 (sum 1.10) | Cigarette + at mouth | Cigarette + at mouth + hand-to-mouth |
| Drinking | Bottle (0.40) | Group of 2+ 0.10 · Stayed 10+ min 0.15 · Bottle at mouth 0.15 · Evening 0.05 | 0.85 | Bottle + at mouth | Bottle + at mouth + group + 10 min |
| Holdup | Knife near a hand (0.45) | Frozen pair 0.20 · Loitering first 0.10 · Approach→freeze 0.10 · Time of day +0.01–0.10 | ≈ 0.95–1.00 | Knife + frozen pair | Knife + frozen pair + loitering |
| Parking obstruction | Vehicle inside the no-parking zone (≥ 35 % of footprint) | Stopped (≤ 0.15 box-widths drift over 3 s); dwell accrues seconds | 120 s dwell | ≥ 66 s banked | 120 s banked → alert |
| Improper disposal | Static new object (≥ 2 s still, not a person/vehicle) | Zero-shot garbage score ≥ 15 % and above every look-alike; owner has walked away | – | Garbage leads but < 15 % | Garbage ≥ 15 % and beats all rejects |

---

## 12. Literature basis of the indicators 📋

| Source | Used for |
|---|---|
| Omamalin (2022), *PSSJ* | Drinking: tagay sessions are group, long (3–5 h), afternoon-into-evening activity → gathering, 10-minute duration, evening band |
| Setti (2015) | Drinking: group of 2+ |
| arXiv 2507.00566 | Drinking: bottle raised to the mouth |
| Thai emergency-department series (2021) and Western Australian ED series | Drinking: 16:00–00:00 high band |
| YOLOv8-MNC (2023) | Smoking: cigarette as the object gate |
| SmokeBench | Reason a smoke-plume indicator is **rejected** (VLMs fail on small smoke) |
| Fernandez-Testa (2024) | Holdup: knife near a wrist as the gate |
| Ruiz-Santaquiteria (2021) | Holdup: confrontation freeze |
| Robielos & Duran (2020), *IEOM* | Holdup: time-of-day block probabilities (City of Manila) |
| Ordinance 601 | Parking: obstruction of the road edge |
| E10 (loitering) | Citation pending in the spec |

---

## 13. Limitations and notes the manuscript should state honestly

1. **Not field-calibrated.** Every constant in the code is a reasoned default; none has been calibrated against footage from the mounted camera. The weights are an initialisation, not a result.
2. **Single camera, single site.** All behaviour was tuned on the team's own footage from one viewpoint.
3. **Time-of-day (E20) caveat** in Section 6.4: it is the only indicator drawn from a different city and conviction-only data, so it is capped to add points only and cannot create or hide an alert.
4. **Code vs. written spec differences.** The spec says parking = ≥ 50 % past the edge for 5 minutes; the deployed defaults are 35 % for 2 minutes. The spec says the AI checker is called when an event enters Monitoring; the code calls it once, at the first Possible/Likely.
5. **Zero-shot disposal** scores are low by nature (an open-vocabulary head spreads probability over ~20 prompts); a correct bag across the street can land near 0.10 and will not alert at the 15 % floor. Raising the floor buys precision at the cost of distant objects.
6. **Vaping** has no class in the model; only the capped puff-only path (hand movement, Possible at most) can flag it.
7. **No facial recognition**: people are never identified. The disposal "likely dropped by" crop is a cropped image of the nearest person for human review only.
8. The AI checker is advisory; the **official status comes from system indicators only** (object detector, pose model, tracker, clock).
9. Accuracy figures (precision/recall per violation) are **not** in this file — none were found in the code or docs. Insert measured results from the evaluation runs separately.

---

## 14. Source files (for reference)

| Topic | File |
|---|---|
| Scoring, weights, statuses, Manila table | `lookout_backend/core/vision/scoring.py` |
| Adjustable timings and spec defaults | `lookout_backend/core/vision/spec_settings.py` |
| Object momentum | `lookout_backend/core/vision/momentum.py` |
| Location-aware momentum, puff gate, holdup guards | `lookout_backend/core/vision/location_momentum.py` |
| Holdup E-codes | `lookout_backend/core/vision/theft.py` |
| Tracking, gesture, gatherings | `lookout_backend/core/vision/tracking.py` |
| Detectors / pose / vehicle inference | `lookout_backend/core/vision/recognition.py` |
| Parking zone | `lookout_backend/core/vision/obstruction_zone.py`, `zone_autodetect.py` |
| Disposal | `lookout_backend/core/vision/disposal.py` |
| AI checker | `lookout_backend/core/vision/ai_checker.py`, `ai_status.py` |
| Watchers | `lookout_backend/core/management/commands/watch_*.py` |
| Settings model | `lookout_backend/core/models.py` (`SystemSettings`) |
