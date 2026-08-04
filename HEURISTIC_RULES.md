# LOOKOUT — Heuristic Rules

Complete reference for the heuristic rule layer that sits between the detectors
and the `Alert` table.

## 1. Detector inventory

| Violation | Command | Detection basis | Rule generation |
|---|---|---|---|
| Illegal Parking | `watch_parking` | COCO vehicle classes | Own (§7) |
| Public Smoking | `watch_smoking` | Custom `smoking.pt` | Shared (§4–6) |
| Theft / Threat | `watch_thief` | Custom `thief.pt` | Shared (§4–6) |
| Public Drinking | `watch_drinking` | Custom `drinking.pt` | Shared (§4–6) |
| Noise | — | — | **Not implemented** |
| Waste / Garbage | — | — | **Not implemented** |

Noise and waste have full `SystemSettings` configuration (`noise_threshold_db`,
`noise_duration`, `waste_confidence`, `waste_dwell`, `waste_collection_start`
/`_end`) and appear in the dashboard, but **no management command exists for
either**. They are configured, not built. Anything claiming they detect is
inaccurate.

## 2. Two rule generations

This matters for reading the rest of the document, and for the honest assessment
in §11.

**Smoking, theft and drinking share one rule layer**, implemented once in
`core/vision/tracking.py` and described in §4–§6. It is the newer and
considerably stricter of the two.

**Parking carries its own, older confirmation logic**, written before the shared
layer existed. It is simpler: no temporal voting, no spatial cooldown, no
proximity-fallback tracking, and dwell measured as *elapsed* rather than
*accumulated* time. It is documented separately in §7, because describing it
under the shared headings would misrepresent what it does.

## 3. Why a rule layer exists at all

Every detector answers a narrower question than the ordinance does. The model
reports *what is visible*; the ordinance concerns *what someone is doing*. The
gap differs per violation, and closing it is the rule layer's entire job:

- **Parking** — a detected vehicle is not an illegally parked one. The gap is
  *motion*: parked means stationary, so the rules measure stillness.
- **Smoking** — a cigarette detected anywhere on a body is not evidence of
  smoking. The gap is *posture*.
- **Theft** — `gun` and `knife` translate fairly directly; `robbery activity`
  and `stealing` are **actions inferred from a single still frame**, a
  structural mismatch for an object detector. The gap is *time*.
- **Drinking** — the model recognises one beer *brand*. A bottle in a store, in
  a bag, or held by a bystander looks identical to one being drunk from. The gap
  is *product versus act*.

No confidence threshold closes any of these. The rules below do the work.

---

# Shared rule layer (smoking, theft, drinking)

## 4. Layer 0 — Preconditions

Evaluated before detection, so a failing frame costs nothing.

| Rule | Condition | System Action | Scope |
|---|---|---|---|
| **P1** Detector Enabled | `*_enabled` true in `SystemSettings` | Process the frame | all |
| **P2** Ordinance Hours | Local time inside `drinking_start`–`drinking_end` | Process; else skip | drinking |
| **P3** Zone Mask | Detection centre inside a configured zone polygon | Keep; else discard | drinking |

**P2.** Public-drinking ordinances are scoped by hour. The window handles
wrapping past midnight (22:00–05:00 evaluates correctly). **Disabled by
default**, so enabling the detector never silently stops it alerting by day.

**P3.** One camera often covers public street *and* private frontage or a
licensed venue. Uses the polygon JSON format of
`detection_sandbox/zones.example.json`. No zones configured means everything
counts.

Settings are re-read every 5 s, so dashboard edits apply without a restart.

## 5. Layers A–B — Detection and Spatial Association

| Rule | Condition | System Action |
|---|---|---|
| **A1** Object Detection | YOLOv8 identifies a violation class | Emit candidate |
| **A2** Person Detection | COCO person at confidence >= 0.5 | Establish tracking anchor |
| **A3** Confidence Threshold | Score >= `*_confidence / 100` | Accept candidate |
| **A4** Class-Specific Confidence | Score >= threshold x class factor | Accept; else discard |
| **A5** Multi-Scale Redundancy | Same object from >1 scale pass, IoU >= 0.5 | Merge, keep highest |
| **A6** Generic Vessel Extension | COCO `bottle`/`cup`/`wine glass` found | Admit as weak evidence (B7) |
| **B1** Identity Association | Person region overlaps a track, IoU >= 0.3 | Continue that track |
| **B2** Fallback Proximity Matching | Centres within 0.8 x mean box size | Continue track by proximity |
| **B3** Person–Detection Association | Detection centre inside person region + 0.45 x **width** | Assign; smallest region wins |
| **B4** Mouth Proximity *(smoking)* | Object within 2.5 face-widths of the mouth | Accept; **abstain if no face** |
| **B5** Unassociated Detection Tracking | Assigned to no person | Location-keyed scene track |
| **B6** Posture Classification *(drinking)* | Position relative to the mouth | Label at-mouth / held / unattended |
| **B7** Generic Vessel Restriction *(drinking)* | A COCO vessel is `at-mouth` | Admit at 2x dwell; else never alerts |

### A4 class factors

| Detector | Base floor | Factor 1.0 | Raised |
|---|---|---|---|
| Smoking | 0.30 | `cigarette`, `vape`, `smoking` | `smoke` — **x1.5** (0.45) |
| Theft | 0.30 | `gun`, `knife` | `robbery activity`, `stealing` — **x1.6** (0.48) |
| Drinking | 0.35 | `Red Horse` | — (single class) |

Raised classes are those where the detector is asked something it is
structurally poor at. Unknown classes fall back to factor 1.0, so retraining
with new class names degrades gracefully.

**A6.** The drinking model knows one brand, so a gin session or a drink in a
glass produces nothing. COCO vessel classes come out of the person detector's
own pass — measured 79 ms against 90 ms, effectively free. They say nothing
about *contents*, hence B7's stricter terms. Opt-in via `--include-generic`.

**B2.** At long-range mode's ~1 FPS a walking subject clears their own bounding
box between frames. Overlap-only matching would start a new identity every
frame, resetting every timer permanently — the detector could never alert on
anyone who moves.

**B3.** Reach derives from box *width* on both axes. Scaling each axis by its own
extent gives ~9 px horizontally against ~30 px vertically on a person box, while
people extend their arms *sideways*.

**B4 rejects; B6 escalates.** The contrast is deliberate. A cigarette at knee
height is meaningless, so smoking discards it. An open bottle in someone's hand
is genuine evidence for a drinking ordinance, merely weaker than one at their
lips — so posture scales the dwell instead. B4 abstains when no face is
resolvable (**63% resolvable** in measurement, 19 of 30 persons); treating "no
face" as "not smoking" would disable the detector at exactly the distances
long-range mode exists to cover. Posture is judged on the object being alerted
on, not the person generally, so a glass at the mouth cannot grant the
consumption dwell to a bottle at the hip. Face anchors are cached 1 s relative to
the person box (434 ms → 27 ms per frame).

## 6. Layers C–D — Temporal and Decision

| Rule | Condition | System Action |
|---|---|---|
| **C1** Temporal Voting | >= 40% of frames in a rolling 3.0 s window positive, min 3 | Confirm presence |
| **C2** Detection Currency | Latest detection <= 0.5 s old | Permit dwell to accrue |
| **C3** Dwell Accumulation | Accumulated **confirmed** time >= requirement | Permit alert |
| **C4** Cessation | Subject visible, violation absent > 2 s | Clear accumulated dwell |
| **C5** Occlusion Recovery | Not detected at all; returns within 10 s and 1.2 x box size | Restore identity and dwell |
| **D1** Class/Posture Persistence | Required dwell = base x factor, x2.0 if unassociated | Set the dwell bar |
| **D2** Majority-Class Selection | Class seen in most frames; ties by confidence | Assign reported class |
| **D3** Track Cooldown | No alert for this track within 120 s | Permit alert |
| **D4** Spatial Cooldown | No alert within IoU 0.3 of this location within 120 s | Permit alert |

**C1.** The window is in *time*, not frames, so the criterion is invariant to
processing rate — a fixed frame count means ~1 s at 15 FPS but ~15 s at 1 FPS.

**C2.** The vote ratio has inertia, staying satisfied over a second after the
last detection. Without a currency bound, that latency and C4's grace stack, and
a nominal 3 s dwell becomes satisfiable by ~1.3 s of real observation.

**C3.** Dwell is accumulated confirmed time, not elapsed. Gaps contribute
nothing, so "held for 3 s" means three seconds of it.

**C4 vs C5.** The same absence is read by cause. A visible subject whose
violation stopped has ceased, and the timer clears. A subject no longer detected
is occluded and must not be penalised, or stepping behind a parked vehicle
defeats the system.

**D2.** Taking the highest-confidence box would let one frame of a spurious class
outrank the class present for the whole dwell — how a sustained `stealing`
becomes a one-frame `gun`.

**D4.** Tracks are dropped after 2 s unobserved and replacements start with a
cleared cooldown, so identity churn would bypass D3. Measured: without D4 the
same subject alerts at 3.1 s **and again at 16.3 s** despite a 120 s cooldown.

---

## 7. Illegal Parking — its own rule set

The distinguishing rule is **motion**: a vehicle is only illegally parked if it
is not moving, so the timer measures stillness rather than presence.

| Rule | Condition | System Action |
|---|---|---|
| **V1** Vehicle Detection | COCO `car`/`motorcycle`/`bus`/`truck` >= `parking_confidence` (0.35) | Emit candidate |
| **V2** Resolution Preservation | Detection runs at `imgsz` 1280, not YOLO's default 640 | Keep distant vehicles detectable |
| **V3** Track Association | Detection overlaps an existing track, IoU > 0.3 | Continue that track |
| **V4** Movement Reset | Centre drifted > `parking_move_tolerance` (40 px) from its anchor | Re-anchor and **restart the timer** |
| **V5** Stationary Dwell | Still for >= `parking_dwell` (60 s) | Permit alert |
| **V6** Track Grace | Not seen for > 2 s | Drop the track |
| **V7** Cooldown | No alert for this track within 120 s | Create alert |

**V4 is the core rule.** Without it the detector would flag every vehicle that
merely appeared for 60 seconds, including moving traffic. The anchor is the
position where the vehicle was last considered to have settled; drifting past
tolerance means it is in motion, and the clock restarts from zero.

**V5's 60 s** is far longer than any other detector's dwell, and correctly so —
a vehicle pausing at a corner is not parked, whereas a person holding a knife for
60 seconds would be an absurd bar.

## 8. Resolved requirements

**Shared layer**, by class:

| Class | Confidence floor | Dwell (on a person) | Dwell (unassociated) |
|---|---|---|---|
| `gun`, `knife` | 0.30 | 3 s | 6 s |
| `robbery activity`, `stealing` | 0.48 | 6 s | 12 s |
| `cigarette`, `vape`, `smoking` | 0.30 | 3 s | 6 s |
| `smoke` | 0.45 | 6 s | 12 s |

**Drinking**, by posture (base dwell 8 s):

| Evidence | At mouth | Held | Unattended |
|---|---|---|---|
| `Red Horse` (branded) | 8 s | 16 s | 24 s |
| Generic vessel (opt-in) | 16 s | never alerts | never alerts |

**Parking:** confidence 0.35, 60 s stationary, movement tolerance 40 px.

## 9. Decision logic

**Theft / Threat:**
```
Alert = P1 AND (A3 AND A4) AND (B3 OR B5) AND (B1 OR B2 OR C5)
        AND (C1 AND C2) AND (C3 AND D1) AND NOT (D3 OR D4 blocking)
```

**Smoking** — as above, plus `AND [ B4 mouth-proximity OR face unresolvable ]`.

**Drinking** — as above, plus `AND P2 hours AND P3 zone AND [ branded OR (generic AND at-mouth) ]`,
with D1 scaled by posture rather than class.

**Parking:**
```
Alert = V1 vehicle AND V3 tracked AND NOT V4 moved
        AND V5 stationary dwell AND NOT V7 cooldown
```

All four are strictly **conjunctive** — failure of any stage suppresses the
alert. B4 and B7 are the only disjunctive terms: B4 abstains when it cannot be
evaluated, B7 admits weaker evidence on stricter terms.

## 10. Measured rule contribution

Ablation harness, 30 s of controlled footage per configuration (shared layer):

| Configuration | Alerts |
|---|---|
| All rules active | 1 |
| Minus cooldown (D3, D4) | 360 |
| Minus all heuristics (raw detector output) | 466 |
| Flickery detector at 30% frame rate, all rules | 0 |
| Same footage, minus voting (C1) | 1 |

**Single-rule ablation understates each rule's value**, because the stages
overlap — noise the vote would reject is often caught downstream by dwell. The
cumulative figure (1 against 466) measures the layer's real contribution; the
flickery rows isolate C1, which the first scenario cannot.

```
python manage.py watch_thief    --source clip.mp4 --dry-run --stats
python manage.py watch_thief    --source clip.mp4 --dry-run --stats --ablate vote
python manage.py watch_thief    --source clip.mp4 --dry-run --stats --ablate vote,dwell,cooldown,class-floor
python manage.py watch_smoking  --source clip.mp4 --dry-run --stats --ablate face
python manage.py watch_drinking --source clip.mp4 --dry-run --stats --include-generic
```

Ablatable: `class-floor`, `vote`, `dwell`, `cooldown`; plus `face` (smoking) and
`posture`, `hours`, `zones` (drinking). **Parking has no ablation or stats
instrumentation** — it predates them.

## 11. Traceability

| Constant | Value | File |
|---|---|---|
| `TRACK_MATCH_IOU` / `TRACK_MATCH_DIST` | 0.3 / 0.8 | `core/vision/tracking.py` |
| `TRACK_MAX_GAP` / `TOMBSTONE_SECONDS` | 2.0 s / 10.0 s | `core/vision/tracking.py` |
| `ASSIGN_REACH` | 0.45 | `core/vision/tracking.py` |
| `SCENE_MATCH_DIST` / `SCENE_MAX_GAP` | 1.5 / 2.0 s | `core/vision/tracking.py` |
| `VOTE_WINDOW_SECONDS` / `VOTE_MIN_RATIO` / `VOTE_MIN_FRAMES` | 3.0 s / 0.4 / 3 | `core/vision/tracking.py` |
| `ACCRUAL_STALE_SECONDS` | 0.5 s | `core/vision/tracking.py` |
| `VESSEL_CLASS_IDS` | bottle 39, wine glass 40, cup 41 | `core/vision/recognition.py` |
| `VEHICLE_CLASS_IDS` | car 2, motorcycle 3, bus 5, truck 7 | `core/vision/recognition.py` |
| `VEHICLE_IMGSZ` | 1280 | `core/vision/recognition.py` |
| `PRESENCE_GRACE_SECONDS` / `SETTINGS_REFRESH_SECONDS` / `COOLDOWN_IOU` | 2 s / 5 s / 0.3 | shared-layer watchers |
| `SCENE_DWELL_SCALE` | 2.0 | smoking, thief |
| `FACE_PROXIMITY` / `FACE_CACHE_SECONDS` | 2.5 / 1.0 s | `watch_smoking.py` |
| `POSTURE_DWELL` | at-mouth 1.0, held 2.0, unattended 3.0 | `watch_drinking.py` |
| `MOUTH_PROXIMITY` / `GENERIC_DWELL_SCALE` | 3.0 / 2.0 | `watch_drinking.py` |
| `TRACK_GRACE_SECONDS` | 2 s | `watch_parking.py` |
| `parking_*` | conf 35, dwell 60 s, tolerance 40 px | `core/models.py` |
| `smoking_*` / `thief_*` / `drinking_*` | conf 30/30/35, dwell 3/3/8 s | `core/models.py` |
| `alert_cooldown` | 120 s | `core/models.py` |

## 12. Stated limitations

1. **Two rule generations.** Parking never received the fixes applied to the
   shared layer: it has no temporal voting, no spatial cooldown, no
   proximity-fallback tracking, and measures dwell as **elapsed** rather than
   accumulated time. It is therefore more vulnerable to detector flicker, and its
   track-identity churn can bypass the cooldown the way the shared layer's did
   before D4 was added.
2. **Noise and waste are configured but not implemented.** Settings and
   dashboard controls exist; no detector does.
3. **`robbery activity` and `stealing` are actions judged from single frames.**
   No threshold overcomes this; genuine action recognition needs a temporal
   model. The system detects *weapons* reasonably and *theft behaviour* poorly.
4. **The drinking model recognises one brand.** Ginebra, Tanduay, Emperador and
   San Miguel are invisible. A6/B7 extend coverage to any vessel but only while
   raised to the mouth. The real fix is more brands in training data.
5. **Brand detection is not consumption detection.** Posture narrows the gap;
   someone carrying an unopened bottle home still reads as `held`.
6. **`smoke` is weakly constrained** — exempt from B4 by necessity, leaving A4
   and D1 as its only defences.
7. **B4/B6 degrade with distance**, precisely where A5 is most active.
8. **No severity ranking.** All classes collapse into one `ViolationType` per
   detector; a dispatcher cannot triage `gun` above `stealing`.
9. **Parameters are reasoned defaults, not calibrated values** —
   `FACE_PROXIMITY`, `MOUTH_PROXIMITY`, `VOTE_MIN_RATIO`, the class and posture
   factors, `TOMBSTONE_SECONDS`. Field calibration via `--stats` is pending.
10. **Detector accuracy is unmeasured end to end.** No `yolo val` run for any
    model. `detection_sandbox/runs/thief/results.csv` describes a 6-epoch local
    training run, **not** the shipped `thief.pt` — those figures must not be
    quoted as this system's performance.
11. **Parking is webcam-only.** It hardcodes `VideoCapture(0)` and has no
    `--source`, so it cannot read an RTSP camera.
