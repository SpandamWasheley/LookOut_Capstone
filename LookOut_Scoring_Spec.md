> **SUPERSEDED by docs/specs/LookOut_Scoring_Spec_v6.md** — this file is kept for history only. Do not implement from it.

# LookOut — Indicator Scoring + VLM Verification Spec

Version 2.0 · object-gated · working document for implementation and Chapter 3

---

## 0. What changes

**Detection stays the same.** YOLO, pose, tracking, and the parking module are untouched.

**The decision layer changes:**

| Before | After |
|---|---|
| All gates must pass, or nothing fires | Each indicator adds weighted points |
| `confidence` = YOLO object confidence | `score` = likelihood this is a violation |
| Alert created once | Alert upgrades and downgrades as evidence changes |
| No object = no alert | Object still gates visibility, but behaviour indicators now decide *how serious* it is once the object appears |
| No context check | VLM verifies at Warning |

---

## 1. The three computation steps

These are a calculation, not a timeline. Indicator buffers update as detections arrive; the **score is recomputed every ~0.5 s per track**, not every frame. DB writes happen only on level change.

```
Step 1  ACCUMULATE   is this cue actually true? (evidence over recent frames)
Step 2  SUM          how strong is the case?    (add weights of cues that are ON)
Step 3  CONDITION    does context change it?    (multiply)
                     ↓
                   score → level
```

- **Step 1** — Fernandez-Testa & Salcedo (2024), momentum/evidence accumulation
- **Step 2** — weights ranked from literature, calibrated on our own clips
- **Step 3** — Robielos & Duran (2020), contextual conditioning by time of day

**Why context multiplies instead of adding:** an unfavourable hour must never create an alert on its own. Context can only strengthen or weaken evidence that already exists.

---

## 2. Alert levels

| Score | Level | UI | Notification | VLM |
|---|---|---|---|---|
| < 0.35 | — | nothing | no | no |
| 0.35 – 0.54 | 🟢 Monitoring | Monitoring panel | no | no |
| 0.55 – 0.74 | 🟡 Possible | Main feed, low priority | yes | **triggered** |
| ≥ 0.75 | 🔴 Confirmed | Main feed, full alert | yes | already run |

- Band edges 0.35 and 0.55 match the existing Layer E bands. 0.75 is new.
- Names: use *Monitoring / Possible / Confirmed* in the UI (clearer for barangay staff than Watch/Warning/Violation).
- **Hysteresis:** to *enter* a band, cross the threshold. To *leave* it, fall 0.05 below. Prevents flicker (arXiv 2604.14329).
- Score is capped at **1.00**.

---

## 2b. The visibility gate (added in v2)

Live testing showed that behaviour-only tracks — an ordinary stationary group — would surface on every camera all day. Ordinary presence must never reach a screen.

**Rule:** indicators are scored internally for every track, but **nothing is written to the database or shown in any UI unless the object indicator is ON** (bottle / smoking item / knife, accumulated over ~2 s per §3).

**One exception — smoking:** repeated puff motion alone may reach 🟢 Monitoring, because the cigarette is the hardest object to detect at CCTV distance. It **cannot** progress beyond Monitoring without the object cue or a VLM confirmation. No other violation has a behaviour-only path.

**Consequences**
- Behaviour cues no longer create alerts. They decide *how serious* an alert is once the object opens the gate.
- Weights are rebalanced accordingly (§4): the object carries more, supporting cues carry less, so no combination of supporting cues can reach a visible level on its own.
- The no-object drinking path (periodic VLM check on long stationary groups) is **off by default**. Keep it off for the defense; precision matters more than coverage in front of a panel.

---

## 3. Step 1 — cue accumulation

A cue turns ON only when evidence persists. Defaults:

| Cue type | Rule to turn ON | Rule to turn OFF |
|---|---|---|
| Object (bottle, cigarette, knife) | accumulated confidence over the last 2.0 s ≥ threshold | no detection for 3.0 s |
| Pose (at-mouth, hand-to-mouth) | fires in ≥ 3 of the last 10 frames | no fire for 2.0 s |
| Group / stationary | existing tracker logic | existing |
| Duration | timer met | group disperses |

**Object accumulation formula (Fernandez-Testa):**

```
momentum = Σ ( conf_t × decay^(now - t) )      decay = 0.9 per frame
cue ON if momentum ≥ cue_threshold
```

This is what makes a one-frame glint decay away while a real object accumulates.

### 3b. Pose gating (added in v2)

Pose is a second model pass and is the real FPS risk on an RTX 4050 (6 GB). It does **not** run on everyone. Run pose on a track only when a cheap pre-condition holds:

- an object was detected on or near them, **or**
- they are stationary and alone for 10 s+ (possible smoker), **or**
- they are in a stationary group of 2+ (possible inuman), **or**
- they are inside a monitored zone

Cap the number of tracks receiving pose per frame (e.g. 5). **One shared pose pass per frame** must serve both smoking and holdup — never one per module.

---

## 4. Indicator weights

All weights are **starting values ranked from the literature**. Final values come from calibration (§9).

### 4.1 🍺 Drinking

| Indicator | Weight | Source of truth | Citation |
|---|---|---|---|
| **Bottle / vessel — GATE** | **0.40** | YOLO | Omamalin (2022) |
| Group of 2+ | 0.10 | tracking | Omamalin; Setti et al. (2015) |
| Group stationary | included in group cue | tracking | Setti et al. (2015) |
| Duration met (600–900 s) | 0.15 | timer | Omamalin (3–5 hr sessions) |
| At-mouth posture | 0.15 | pose | skeleton drink-action (arXiv 2507.00566) |
| Time band = High (4 PM–12 AM) | 0.05 | clock | Omamalin; Thai ED (2021); Trinity |
| VLM: beverage container visible | 0.10 | VLM | Omamalin; AnyAnomaly |
| VLM: glass or cup visible | 0.10 | VLM | Omamalin |
| VLM: table / chairs / seating | 0.10 | VLM | Omamalin |
| VLM: food or snacks | 0.05 | VLM | Omamalin |
| VLM: group appears to be drinking | 0.20 | VLM | AnyAnomaly; LAVAD |

- **Gate:** nothing visible without the bottle cue ON. Supporting cues total 0.45 — below the 0.55 Possible band even if all fire.
- **No context multiplier for drinking.** Time is additive here because Omamalin gives qualitative bands, not percentages. State this choice in Chapter 3.
- **Anti-double-count:** at-mouth posture scores only if the bottle cue is ON.
- **VLM cap:** total VLM contribution capped at 0.45.

**Current code to change:** raise `group_duration` from 25 s (flagged as a test value) to 600–900 s; convert the 22:00–05:00 hours window from an off-by-default gate to the scored time band (if that window comes from the ordinance, keep it as a separate legal gate and cite the ordinance).

### 4.2 🚬 Smoking

| Indicator | Weight | Source of truth | Citation |
|---|---|---|---|
| **Smoking item detected — GATE** | **0.40** | YOLO | YOLOv8-MNC (2023) |
| Item near mouth | 0.15 | face + object | keypoints + YOLOv8 (IEEE) |
| Hand-to-mouth gesture | 0.20 | pose | keypoints + YOLOv8 (IEEE) |
| Repeated puffs (≥2 in window) | 0.20 | pose | keypoints + YOLOv8; PACT2.0 |
| VLM: smoking item visible | 0.10 | VLM | Frontiers multimodal smoking (2024) |
| VLM: item at hand or lips | 0.10 | VLM | NTR e-cigarette (2026) |
| VLM: person appears to be smoking | 0.20 | VLM | GPT-4o tobacco; AnyAnomaly |

- **Pose exception:** pose cues total 0.40, which may surface at 🟢 Monitoring with no object — the only behaviour-only path in the system, justified by the cigarette being the hardest object to detect at distance. It **cannot** exceed Monitoring without the object cue or a VLM confirmation.
- **Anti-double-count:** "item near mouth" scores only if the object cue is ON.
- **No smoke-plume indicator.** MLLMs fail on small smoke (SmokeBench); gesture + item is sufficient (MDPI Applied Sciences 2020).

**Current code to change:** wire the hand-to-mouth gesture from `watch_smoking_pose.py` into production; make puff rhythm a scored cue instead of the `--require-puff` gate; raise minimum cycles to 2.

### 4.3 🔪 Holdup

*Pending verification against `holdup_audit.md` — confirm which cues belong to holdup vs. the other theft patterns.*

| Indicator | Weight | Source of truth | Citation |
|---|---|---|---|
| **Knife / weapon present — GATE** | **0.45** | YOLO | Fernandez-Testa & Salcedo (2024) |
| Knife near wrist + plausible size | 0.15 | pose | Ruiz-Santaquiteria et al. (2021) |
| Confrontation freeze / close proximity | 0.20 | tracking | Ruiz-Santaquiteria (2021); Ahmed et al. (2021) |
| Loitering | 0.10 | tracking | ❓ citation pending |
| Knife persistence (momentum) | 0.15 | YOLO over time | Fernandez-Testa & Salcedo (2024) |
| VLM: sharp object visible | 0.10 | VLM | AnyAnomaly |
| VLM: object pointed at a person | 0.15 | VLM | Ruiz-Santaquiteria (2021) |
| VLM: appears to be a holdup | 0.20 | VLM | LAVAD; AnyAnomaly |

**Gate:** nothing visible without the knife cue ON. Supporting cues total 0.30, below the Monitoring band even after the largest time multiplier.

**Hard gate:** at least 2 persons present. RPC Art. 293 requires a victim. Not a scored cue.

**Multipliers (Step 3):**

| Factor | Value | Citation |
|---|---|---|
| Time bin 12–3 AM | ×0.76 | Robielos & Duran (2020) |
| 3–6 AM | ×0.80 | " |
| 6–9 AM | ×0.56 | " |
| 9 AM–12 PM | ×1.25 | " |
| 12–3 PM | ×1.31 | " |
| **3–6 PM** | **×1.36** | " |
| 6–9 PM | ×1.09 | " |
| 9 PM–12 AM | ×0.87 | " |
| VLM `scene_type` = other_activity | **×0.25** | adviser's case; AnyAnomaly |
| VLM `scene_type` = confrontation | ×1.0 | " |
| VLM `scene_type` = unclear | ×1.0 | " |

Multiplier = each bin's probability ÷ 12.5 % (the even-spread baseline). **This replaces the current nocturnal ×1.3 (22:00–05:00)**, which both Philippine datasets contradict — Manila and Butuan both peak in the afternoon/early evening. Report this discrepancy; international evidence (Tompson & Bowers 2013) does link darkness to street robbery.

**Disable by default:** snatch (E6–E9), property theft (E21–E22), carnapping (E15–E19). Gate behind `--enable-legacy-theft`, do not delete.

**Decision needed:** group convergence ×1.5 has no citation and fires on ordinary crowds. Drop it unless a source is found.

### 4.4 🚗 Parking — unchanged

Vehicle ≥50 % past the road edge for 5 minutes → alert. Ordinance No. 601 defines the rule exactly, so no scoring and no VLM.

---

## 5. Promotion rules

1. **Visibility gate (§2b) applies first:** no DB write and no UI at any level without the object cue ON — except smoking on pose, capped at 🟢.
2. **🔴 Confirmed requires the object cue ON, OR the VLM's overall verdict true.**
3. **VLM confidence scales its points:** `high` ×1.0, `medium` ×0.5, `low` ×0.0.
4. **VLM contribution capped** (0.45 drinking, 0.40 smoking, 0.45 holdup) so it can never single-handedly create a violation.
5. **Score is capped at 1.00.**
6. Existing guards (edge, crowd, ID-switch, cooldowns) stay as hard gates. They are engineering parameters and need no citation — justify from our own testing.

---

## 6. VLM stage

**Model:** Gemini Flash. Justification: lowest latency (3.43 s) with high precision (79.2 %) on home-security-camera anomaly detection, while the open-source model tested failed the task entirely (SmartHome-Bench, arXiv 2506.12992); proprietary models consistently lead open ones on video benchmarks (Video-MME, SciVideoBench). Local VLM = future work (privacy).

**Trigger:** score ≥ 0.55, once per tracked event, cooldown 120 s per track.

**Input:** 3–5 JPEG frames ~1 s apart, cropped to the person/group box expanded 40 %. Same crop box on every frame. Frames, not video (Gemini samples video at ~1 fps anyway; frames are faster and portable to other VLMs). Pipeline follows detect → crop → query (NTR e-cigarette, 2026).

**Async:** never block the video loop.

**Fallback:** on API failure or no internet, skip the VLM cues and keep scoring. The alert stays at its current level.

**Privacy:** blur faces before sending, or justify in the paper under RA 10173.

### 6.1 Prompts

Shared preamble:

```
These are consecutive frames from a fixed CCTV camera, about 1 second apart.
Answer ONLY with the JSON object. No extra text.
If you are unsure about a field, answer false. Do not guess.
```

**Smoking**
```json
{
  "smoking_item_visible": true/false,
  "item_at_hand_or_lips": true/false,
  "hand_raised_to_mouth": true/false,
  "person_appears_to_be_smoking": true/false,
  "confidence": "high" | "medium" | "low",
  "reason": "one sentence"
}
```
Add: `A smoking item is any object a person smokes or inhales from.`

**Drinking**
```json
{
  "beverage_container_visible": true/false,
  "drinking_glass_or_cup_visible": true/false,
  "table_chairs_or_seating_visible": true/false,
  "food_or_snacks_visible": true/false,
  "group_appears_to_be_drinking_together": true/false,
  "confidence": "high" | "medium" | "low",
  "reason": "one sentence"
}
```

**Holdup**
```json
{
  "knife_or_sharp_object_visible": true/false,
  "object_pointed_at_a_person": true/false,
  "scene_type": "confrontation" | "other_activity" | "unclear",
  "appears_to_be_a_holdup": true/false,
  "confidence": "high" | "medium" | "low",
  "reason": "one sentence"
}
```

**`scene_type` design:** three buckets only. Enumerating benign scenes (vendor, construction, butcher, kitchen, repair, children playing) is unbounded, so everything that is not a confrontation collapses to `other_activity` (×0.25). **`unclear` does not cut the score** — "cannot tell" is not "harmless", and CCTV crops make hedging common; when the VLM abstains, the system indicators decide alone. ×0.25 rather than ×0.1 downgrades to Monitoring instead of erasing, so a wrong VLM call is still reviewable.

Prompt line: `Use "confrontation" only if one person appears to be threatening another. Use "unclear" if you cannot tell. Use "other_activity" for any ordinary use of the object, such as vending, food preparation, work, or play.`

**Design notes:** questions are asked as separate fields, not one verdict, so each can be scored and audited independently (AnyAnomaly; QVAD). `scene_type` forces the model to name an alternative, which is what suppresses the vendor false positive. Never ask for boxes or coordinates — MLLMs cannot localize reliably (SmokeBench). Prompt wording is part of the method and must be reported in full; zero-shot performance is sensitive to phrasing (Sci Rep 2023; GPT-4o tobacco).

---

## 7. What it looks like in practice

### 7.1 Drinking — gate stays shut, then opens

| Time | Event | Score | Visible? |
|---|---|---|---|
| 19:44 | 3 people stop near a store | 0.10 | no — gate shut |
| 19:47 | still there, evening | 0.15 | no — gate shut |
| 19:53 | duration met (600 s) | 0.30 | no — gate shut |
| 19:55 | bottle accumulates over 2 s | 0.70 | **gate opens** → 🟡, VLM called |
| 19:55 | VLM: glass ✓, seating ✓, drinking ✓ (high) | 1.00 (capped) | 🔴 Confirmed |

Under v1 this group would have appeared on the Monitoring list at 19:53 with no object at all. That was the false-positive source found in live testing.

### 7.2 Drinking — bottle but nothing else

| Time | Event | Score | Level |
|---|---|---|---|
| 15:10 | person walks past carrying a bottle | 0.40 | 🟢 Monitoring |
| 15:11 | keeps walking, no group, no duration | 0.40 → decays | drops off |

Correct behaviour: carrying a drink is not a violation.

### 7.3 Smoking — the pose exception

| Time | Event | Score | Level |
|---|---|---|---|
| 14:03 | hand-to-mouth fires 3 of 10 frames | 0.20 | nothing |
| 14:03 | second puff within the window | 0.40 | 🟢 Monitoring (pose exception) |
| 14:10 | cigarette still never detected | 0.40 | stays 🟢 — cannot progress |
| — | if the item is later detected | +0.40 | 🔴 Confirmed |

Puff motion is a reason to look, not proof. The ceiling is deliberate.

### 7.4 Holdup — vendor false positive, suppressed

| Time | Event | Score | Level |
|---|---|---|---|
| 16:30 | knife detected near a wrist, 2 people close | 0.45 + 0.15 + 0.20 = 0.80 | gate open |
| 16:30 | × time bin 3–6 PM (1.36) | 1.00 (capped) | 🔴, VLM already called at 0.55 |
| 16:30 | VLM: `scene_type = other_activity`, holdup false | 1.00 × 0.25 = **0.25** | 🟢 Monitoring, reviewable |

### 7.5 Holdup — true positive

| Time | Event | Score | Level |
|---|---|---|---|
| 22:15 | person loitering 25 s | 0.10 | no — gate shut |
| 22:16 | second person approaches, freeze | 0.30 | no — gate shut |
| 22:16 | knife accumulates | (0.30 + 0.45 + 0.15) = 0.90 × 0.87 = 0.78 | **gate opens** → 🔴 |
| 22:16 | VLM: pointed ✓, `confrontation`, holdup ✓ (high) | 1.00 | 🔴 Confirmed |

## 7b. Capping arithmetic

Order of operations:

```
1. sum the system indicators        (YOLO, pose, tracking, time band)
2. sum the VLM answers, then CAP    (0.45 / 0.40 / 0.45)
3. add 1 + 2
4. apply multipliers                (holdup: time bin × scene_type)
5. CAP at 1.00
```

The VLM cap is applied **before** the total. Capping only at the end would let the VLM carry a weak case over the line alone, which is what the cap exists to prevent.

| Violation | System max | VLM raw → capped | Raw total | Final |
|---|---|---|---|---|
| Drinking | 0.85 | 0.55 → 0.45 | 1.30 | 1.00 |
| Smoking | 0.95 | 0.40 → 0.40 | 1.35 | 1.00 |
| Holdup | 1.05 | 0.45 → 0.45 | 1.50 (× multipliers) | 1.00 |

**Holdup, same evidence, four outcomes:**

| Case | Arithmetic | Result |
|---|---|---|
| Confrontation, 3–6 PM | 1.50 × 1.36 × 1.0 = 2.04 | 1.00 🔴 |
| Other activity, 3–6 PM | 1.50 × 1.36 × 0.25 = 0.51 | 🟢 |
| Unclear, 3–6 PM | 1.50 × 1.36 × 1.0 = 2.04 | 1.00 🔴 |
| Weak case, 6–9 AM, other activity | 0.75 × 0.56 × 0.25 = 0.11 | nothing |

**Store the raw score alongside the capped one.** Many true positives will sit at exactly 1.00 after capping, which hides how strong each case was — the raw value is what weight calibration needs.

**The score is a bounded likelihood score, not a probability.** Describe it that way in the paper.

## 8. Data + UI changes

**Alert model — add:**

| Field | Type | Purpose |
|---|---|---|
| `score` | float | final 0–1 score, capped |
| `score_raw` | float | uncapped total, for calibration |
| `level` | enum | monitoring / possible / confirmed / suppressed |
| `score_breakdown` | JSON | `{cue: {on: bool, weight: float, points: float}}` |
| `multipliers` | JSON | `{time_bin: 1.36, vlm_scene: 1.0}` |
| `vlm_called` | bool | whether the VLM ran |
| `vlm_response` | JSON | raw answer |
| `vlm_reason` | text | shown on the dashboard |
| `level_history` | JSON | timestamps of each level change |

**Stop overloading `confidence`.** It currently means YOLO confidence, a Layer E score, or an obstruction fraction depending on the module. Keep it for raw object confidence only.

**Dashboard:**
- Separate **Monitoring** panel (no sound, not counted in violation stats, auto-expires when the score drops).
- Main feed shows 🟡 and 🔴 only.
- Alert detail shows the breakdown line by line + `vlm_reason`. **This screen is the "justifiable decision" requirement.**
- Alerts upgrade/downgrade in place; never create a second alert for the same event.

**Settings page — expose:** per-violation weights, the three band thresholds, hysteresis margin, VLM on/off, VLM trigger threshold, and cooldowns.

---

## 9. Evaluation plan

**Dataset:** 50–100 clips per violation, labelled violation / not violation. Must include hard negatives: vendor with a knife, drinking water, group standing and chatting, person scratching their face, someone holding a phone to their ear.

**Three layers of results:**

1. **Per-indicator** — how often each cue fires when it should. This is where the domain-gap numbers come from (e.g. bottle detected in X % of clips where one is visible). Do the same per VLM field.
2. **Overall** — precision, recall, F1 for 🔴 Confirmed against ground truth. Lead with precision and recall, not accuracy (the dataset is imbalanced).
3. **Threshold sweep** — precision/recall/F1 at 0.55, 0.65, 0.75, 0.85. The chosen threshold is then a justified choice, not an assumption.

**Also report:**
- **Weight calibration:** logistic regression on the labelled set; its coefficients become the final weights.
- **Ablation:** remove one cue at a time using the existing `--ablate` flags; shows each cue earns its place.
- **With vs. without VLM:** same clips, two runs. The delta is the headline VLM result.
- **Early detection:** of true violations, how many appeared as 🟢/🟡 first, and how many seconds before 🔴. **This is the evidence for "proactive."**
- **VLM latency** per call, and failure rate.
- **False alerts per camera-hour** on unlabelled live footage, before and after the visibility gate. This is the number that motivated v2 and is worth reporting directly.
- **Prompt variants:** test one-call-JSON vs. separate calls per question on ~10 clips; report which was chosen and why.

**🟢/🟡 are not scored as wrong answers.** They are not claims that a violation occurred; they are reported separately as early detection.

---

## 10. Build order

**Week 1 — scoring layer (nothing else works without it)**
1. Score engine: cue registry, weights, multipliers, bands, hysteresis, cap.
2. Convert drinking + smoking from gates to cues (keep hard gates: person present, guards, cooldowns).
3. Alert model fields + upgrade/downgrade lifecycle.
4. Free wins: wire the smoking gesture, repeated puffs, drinking time band, Manila time bins, knife persistence, `group_duration` 600–900 s.
5. Disable legacy theft patterns behind a flag.
6. Implement the visibility gate (§2b), the pose gate (§3b), the ~0.5 s recompute cadence, and level-change-only DB writes.

**Week 2 — VLM + UI**
6. Gemini client: crop, 3–5 frames, async, JSON parse, retry, fallback.
7. Wire VLM fields into the score with the confidence multiplier and caps.
8. Dashboard: Monitoring panel, level badges, breakdown view, `vlm_reason`.
9. Settings exposure.

**Week 3 — evaluation + paper**
10. Label the clip set.
11. Calibrate weights, sweep thresholds, run ablations and VLM on/off.
12. Chapter 3 methodology + Chapter 4 results.

**Cut first if time runs short:** periodic VLM checks, food indicator, local-VLM comparison, victim-pose indicator.

---

## 11. Citation map

| What it justifies | Source | Link |
|---|---|---|
| Evidence accumulation, threshold alerting, knife persistence, two-stage design | Fernandez-Testa & Salcedo (2024) | https://arxiv.org/html/2410.09731v1 |
| Contextual conditioning by time of day (Manila bins) | Robielos & Duran (2020), IEOM | https://www.ieomsociety.org/ieom2020/papers/697.pdf |
| Mindanao support for afternoon/evening crime peak | Arcite (2021), Butuan, IJAMS | https://www.ijams-bbp.net/wp-content/uploads/2021/09/IJAMS-AUGUST-58-66.pdf |
| Darkness and street robbery (international counterpoint) | Tompson & Bowers (2013) | https://pubmed.ncbi.nlm.nih.gov/25076797/ |
| Tagay: vessel, group, 3–5 hr duration, late-evening timing, public venues | Omamalin (2022), PSSJ | https://philssj.org/index.php/main/article/download/494/264 |
| Group = 2+ people, close in space and time | Setti et al. (2015), PLOS One | https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4440729/ |
| Drink action = arm to mouth + head tilt | arXiv 2507.00566 | https://arxiv.org/pdf/2507.00566 |
| First drink 4–8 PM (SE Asia) | Thai ED study (2021) | https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8011167/ |
| Time as a weighted cue, not a gate | W. Australia ED study; Trinity | https://arxiv.org/html/2404.07887v1 |
| Cigarette detection; small-object difficulty | YOLOv8-MNC (2023), Frontiers | https://www.frontiersin.org/journals/computational-neuroscience/articles/10.3389/fncom.2023.1243779/full |
| Hand-to-mouth distance, angles, smoking cycle | Keypoints + YOLOv8 (IEEE, paywalled) | https://ieeexplore.ieee.org/document/10795694/ |
| Hand-to-mouth = puff (open backup) | PACT2.0, MDPI Sensors | https://pmc.ncbi.nlm.nih.gov/articles/PMC6387353/ |
| Gesture + item without smoke detection | MDPI Applied Sciences (2020) | https://www.mdpi.com/2076-3417/10/24/8912 |
| Pose + weapon appearance | Ruiz-Santaquiteria et al. (2021), IEEE Access | (search title; IEEE Access is open) |
| Victim–offender interaction | Ahmed et al. (2021), Wiley | https://onlinelibrary.wiley.com/doi/10.1155/2021/2449603 |
| Robbery requires taking + violence/intimidation | Revised Penal Code, Art. 293 | public law |
| Context-aware VQA with user-defined text (our method) | AnyAnomaly (arXiv 2503.04504) | https://arxiv.org/pdf/2503.04504v4 |
| Two-stage cascade; only ambiguous segments to VLM | SlowFastVAD (arXiv 2504.10320) | https://arxiv.org/pdf/2504.10320 |
| Cascade cost numbers (why not local) | Cerberus (arXiv 2510.16290) | https://arxiv.org/pdf/2510.16290 |
| Question-centric, training-free VLM querying | QVAD (arXiv 2604.03040) | https://arxiv.org/html/2604.03040v1 |
| Model choice: latency/precision, open-source failure | SmartHome-Bench (arXiv 2506.12992) | https://arxiv.org/pdf/2506.12992 |
| Detect → crop → VLM query pipeline | NTR e-cigarette (2026) | https://pubmed.ncbi.nlm.nih.gov/42423759/ |
| VLM as filter, not sole detector | Frontiers multimodal smoking (2024) | https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2024.1326050/full |
| Zero-shot works; prompt phrasing matters | Zero-shot alcohol (Sci Rep 2023); GPT-4o tobacco | https://pmc.ncbi.nlm.nih.gov/articles/PMC10363523/ |
| Naive object-focused models miss context | LAVAD (CVPR 2024) | https://openaccess.thecvf.com/content/CVPR2024/papers/Zanella_Harnessing_Large_Language_Models_for_Training-free_Video_Anomaly_Detection_CVPR_2024_paper.pdf |
| Don't ask VLMs for localization / small smoke | SmokeBench (arXiv 2512.11215) | https://arxiv.org/pdf/2512.11215 |
| Hysteresis to stabilise frame-level predictions | arXiv 2604.14329 | https://arxiv.org/abs/2604.14329 |
| Multi-cue fusion into an interpretable suspiciousness score | DeepUSEvision (arXiv 2512.09311) | https://arxiv.org/pdf/2512.09311 |
| Explainability motivation for rule/score-based decisions | AnomalyRuler (ECCV 2024) | https://arxiv.org/abs/2407.10299 |

**Do not cite:** CSI-VAD — could not be verified to exist. Use AnyAnomaly or QVAD instead.
**Verify before citing:** arXiv 2603.13306 and arXiv 2604.14329 — found via search but not re-verified. Open every link before it goes in the paper.

---

## 11b. Performance notes (RTX 4050, 6 GB)

The scoring layer is CPU arithmetic and adds no meaningful GPU load. Live slowness comes from elsewhere:

- **Clip encoding blocks the inference loop** — already a known outstanding item. Making it async is the highest-value fix and is unrelated to scoring.
- **Facial recognition has been removed** (the insightface/ArcFace pass no longer runs). The mouth anchor now comes from YOLOv8-pose keypoints.
- **Two pose passes** (one per module) would be the worst regression this redesign could introduce. Share one pass.
- Other levers: process every 2nd–3rd frame, lower input resolution, cap pose to N tracks per frame, motion-gate idle frames.

Scope reminder: the defense uses two Tetuan cameras with recorded footage, plus one live parking-obstruction demo. Multi-camera scaling is Chapter 5 future work with a stated hardware requirement — do not claim untested camera counts.

## 12. Open items

1. **Run the holdup audit** (`holdup_audit.md`) and correct §4.3 — which cues are holdup vs. snatch/property/carnapping, and whether pose is shared across modules or duplicated.
2. **Citation for loitering** — search pending.
3. **Group convergence** — find a citation or drop it.
4. **Pose sharing** — confirm one pose pass per frame is shared between smoking and holdup. Two passes on 6 GB will hurt FPS.
5. **Drinking hours window** — confirm whether 22:00–05:00 comes from the ordinance (legal gate) or was a design choice (scored cue).
6. **Confirm "proactive"** with the adviser — tiered early alerts, as assumed here.

---

## 13. Honest limitations to state in the paper

- A weighted linear sum assumes indicator independence, which does not strictly hold. Overlapping cues are excluded from double-counting, but the assumption remains.
- No published study provides weights for this specific indicator set. The **method** is cited; the **values** are calibrated on our own data.
- Manila probabilities come from a different city, combine robbery with theft, and cover only convicted cases.
- No Philippine study gives hourly percentages for inuman, so drinking time is banded, not weighted by probability.
- The three levels and their thresholds are a design decision of this project, justified by our threshold sweep rather than by a published standard.
- Cloud VLM use means frames leave the device; RA 10173 compliance is handled by face blurring and is a stated limitation.
- The visibility gate means a violation whose object is never detected produces no alert. This is a deliberate precision-over-recall trade, taken after live testing showed behaviour-only tracks generating unusable false positives. Measured object miss rates are reported as the bound on recall.
- VLMs can hallucinate; the confidence multiplier, contribution cap, and the object-OR-VLM rule for 🔴 are the mitigations.
