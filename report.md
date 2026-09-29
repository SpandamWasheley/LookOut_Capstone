# LookOut — Change Report

**Date:** 24 September 2026
**Branch:** `deploy/hosting`
**Scope:** 28 files changed · +1,487 / −614 lines · 8 new source files · 2 migrations · 103 backend tests passing

---

## Summary

The detection pipeline moved from a chain of hard gates to a weighted-sum scoring
model. Previously a violation was reported only if every gate passed — the object
had to be detected, the dwell had to elapse, the posture check had to pass — and
the number stored as the alert's confidence was simply the YOLO score of the
winning box.

That number answered "how sure is the model that this is a bottle?" It did not
answer "how likely is it that this is a drinking violation?" Only the second
question matters to a barangay official acting on the alert.

Each indicator now contributes points in proportion to how strongly it
distinguishes a violation from ordinary behaviour, and the total becomes the
reported violation likelihood. The practical consequence: **a missed object
detection no longer silences the system, it only prevents escalation to the
highest level.**

A vision-language model was added as a second verification stage, the parking
detector gained a road-zone polygon, and a review control was added so the
weights can eventually be calibrated against real footage rather than reasoned
defaults.

---

## 1. Weighted-sum scoring model (new)

**File:** `lookout_backend/core/vision/scoring.py`

```
raw   = Σ (weight_i × cue_i)        cue_i = 1 if fired, else 0
final = min(1.0, raw × Π multipliers)
```

Additive cues are evidence that a violation is occurring. Multiplicative factors
are context that makes the same evidence more or less suspicious — time of day is
the clearest case, which is why it scales the score rather than adding to it.

| Level | Threshold | What happens |
| --- | --- | --- |
| Violation | ≥ 0.75 | Alert written, filed as a confirmed violation |
| Warning | ≥ 0.55 | Alert written, actionable |
| Watch | ≥ 0.35 | Scored and logged, never dispatched |
| None | < 0.35 | Discarded |

Watch-band events exist to supply the labelled negatives that calibration needs.
Discarding them would leave a future fit with only positives to learn from.

Other properties:

- **Hysteresis (0.05).** A level, once entered, is only left when the score falls
  0.05 below the threshold that admitted it. Without it a score sitting on a
  boundary oscillates frame to frame and each upward crossing reads as a new
  incident.
- **Redundancy suppression.** A bottle held at the mouth implies a bottle is
  present. Summing both double-counts one observation, so only the heavier cue of
  a declared pair scores.
- **Cue vector retained.** Every scored event stores which indicators fired and
  what each was worth, even for events that scored too low to dispatch.
- **Score capped at 1.0.** It is reported as a likelihood, and evidence beyond
  certainty is still certainty.

Drinking and smoking were ported onto this engine. Holdup's existing Layer E
pattern engine now shares it rather than carrying its own copy.

---

## 2. VLM verification (new)

**File:** `lookout_backend/core/vision/vlm.py`

A vision-language model answers the one question the geometry cannot: is a bottle
on a table a family lunch or an inuman, and is a close-proximity freeze a holdup
or a vendor handing over goods.

**Called once per candidate alert, never per frame.** By that point the pipeline
has already tracked the person, won the vote window, completed the dwell and
cleared the cooldown, so the call rate is a few per hour rather than a few per
second. Every cheap check sits before it, so each rejection is a call not paid
for.

### Fail-open

No API key, no network, a timeout, a refusal, a malformed reply or a missing
library all produce the same result: no answer, and the alert is published on the
geometry alone. **The VLM can only ever add to a score.** A dead API key must
never silently stop a security system from alerting.

The one exception is holdup, where a confident vendor reading applies ×0.5 and
can pull a score below the band. That is intended, and it requires a successful,
confident answer — never a failure.

Verification is on by default. With no credentials configured the verifier is
inert and the detectors behave exactly as if it did not exist, so switching it on
is a matter of setting `ANTHROPIC_API_KEY` rather than changing any setting.

---

## 3. Per-detector changes

### Public drinking — 11 scored cues

| Indicator | Weight | Source of the weight |
| --- | --- | --- |
| At-mouth posture | 0.45 | Ours |
| Bottle / vessel | 0.30 | Document |
| VLM verdict (is this inuman?) | 0.30 | Document |
| Gathering of 2+ persons | 0.20 | Document |
| Gathering duration | 0.20 | Document |
| Dwell | 0.20 | Ours |
| Cup / glass (VLM) | 0.10 | Document |
| Chairs / table (VLM) | 0.10 | Document |
| Food / pulutan (VLM) | 0.10 | Ours |
| Time band (16:00–24:00) | 0.10 | Document |
| Gathering stationary | 0.10 | Ours |

At-mouth is priced **above** bottle deliberately. The two are a redundant pair, so
only the heavier scores — and consumption is strictly stronger evidence than
possession. Priced below, raising a bottle to your lips would add nothing over
merely holding it.

**The core behaviour change:** a sustained, stationary gathering with no fresh
bottle sighting used to produce nothing at all. It now reaches Warning on the
gathering evidence alone, and the bottle only decides escalation to Violation.

### Public smoking — 6 scored cues

| Indicator | Weight |
| --- | --- |
| Cigarette near mouth | 0.50 |
| Cigarette / vape | 0.30 |
| VLM verdict | 0.30 |
| Repeated puffs | 0.25 |
| Dwell | 0.25 |
| Hand-to-mouth gesture (pose) | 0.20 |

All six are ours — the design document rates strength for five smoking indicators
but prices none.

Two indicators reached production for the first time:

- **Repeated puffs** was an opt-in hard gate (`--require-puff`, off by default),
  so a "Strong" indicator contributed nothing in any real deployment. It is now a
  scored cue that fires on its own merits. The flag still gates, for ablation runs.
- **Hand-to-mouth gesture** lived only in `watch_smoking_pose`, a standalone
  command the combined runner never invokes. A `--pose` flag now feeds YOLOv8-pose
  into `watch_smoking` and `watch_all`. Off by default because it is a fifth model
  on a CPU box that already runs four.

### Holdup

- **Time-of-day weighting replaced.** The flat nocturnal ×1.3 applied 22:00–05:00
  is gone, replaced by a per-three-hour-block lookup from Robielos and Duran
  (2020). See §7.
- **`Evidence.rescore()` added** so the VLM verdict (E31, 0.30) and vendor context
  (E30, ×0.5) can be folded in once at alert time. Layer E runs on every frame and
  a network round trip cannot.

### Parking obstruction

See §4.

---

## 4. Parking road zone

**File:** `lookout_backend/core/vision/obstruction.py`

The no-parking area can now be marked two ways:

- **Zone** — a closed polygon around the road itself. Anything standing inside it
  is an obstruction.
- **Edge** — an open kerb line plus the side the footpath is on. Unchanged.

Both answer the same question — "is this ground point somewhere a vehicle must not
be?" — so `fraction_past()`, the hysteresis band and the five-minute dwell rule
all work on either **without branching**. The `RoadZone` class is duck-typed with
the existing edge shapes.

A zone is usually easier to get right. An edge divides the whole frame in two, so
it also condemns everything on the footpath side — fine when that really is
footpath, wrong when the frame contains a yard, a shop front or the opposite
pavement. A polygon only ever means the ground inside it.

**Polygon, not a bounding box:** a rectangle in image space is not a rectangle on
the ground. A camera looks down the road at an angle, so the carriageway appears
as a trapezium narrowing towards the vanishing point. An axis-aligned box either
swallows the footpath near the camera or misses the road in the distance.

**Drawing tool:** `lookout/src/EdgeCanvas.jsx` gained a Road zone / Kerb lines
toggle, with zone as the default. The polygon closes and fills from the third
point so the operator sees the exact area being tested.

**Backward compatible:** `type` defaults to `edge`, so every camera configured
before zones existed reads back identically.

---

## 5. Review labelling control

**Files:** `lookout/src/ViolationModal.jsx`, `AlertFeed.jsx`, `RecordsPage.jsx`, `api.js`

Two buttons in the violation modal — **Real violation** / **False alarm** —
clearable by clicking the active one again, because a mis-click must be undoable
or the training data inherits it.

The card also shows the reviewer what they need to judge: the score band
(Violation / Warning / Watch) and the VLM's one-sentence reason, when it ran.

The "Confidence" card was relabelled **"Violation likelihood"** on scored alerts.
Its tooltip previously said "How certain the YOLOv8 model is", which stopped being
true when the scoring model shipped.

### The contract, tested

| Field | Client can write? |
| --- | --- |
| `reviewed_valid` | Yes — it is the answer key |
| `level`, `cues`, `vlm_verdict`, `vlm_confidence`, `vlm_reason` | No — read-only |

A client that could PATCH its own cue vector could rewrite the training data after
the fact.

---

## 6. Calibration harness

**File:** `lookout_backend/core/management/commands/calibrate_weights.py`

```
python manage.py calibrate_weights drinking|smoking
```

Fits a logistic regression to labelled alerts and reports precision, recall and F1
for the current weights against the fitted ones. Pure numpy, no new dependency.

- Refuses to run on fewer than 20 labelled alerts, or on a single-class dataset,
  rather than producing a confident-looking number from too little data.
- Warns below the 50 the design document asks for.
- **Never edits the weights file.** It prints a proposed diff; a human applies it.

### Why calibration stays manual

Automatic refitting would train the model on a sample the model itself selected.
Events scoring below the band are never reviewed, so a detection weakness — poor
bottle recall at night, say — would push those cases further down on every cycle
and deepen the blind spot invisibly.

A human choosing what to label can deliberately pull Watch-band clips that never
alerted. An automatic loop can only learn from what it already found.

---

## 7. Configuration changed

| Setting | Before | After | Why |
| --- | --- | --- | --- |
| `drinking_group_duration` | 25s | 600s | 25s was a testing value; Omamalin describes 3–5 hour sessions |
| Drinking time band | 22:00–05:00 | 16:00–24:00 | The old window was copied from curfew and matched no source |
| `vlm_enabled` | — | on by default | Safe: inert without credentials |
| Alert band entry | `> 0.55` | `≥ 0.55` | Weights are 0.05-granular, so sums land on 0.55 exactly |

### Manila time-of-day multipliers (holdup)

Derived from Robielos and Duran (2020), five years of City of Manila robbery and
theft records. Each multiplier is the block's incident probability divided by
12.5%, the value expected if incidents were spread evenly across eight blocks.

| Block | Probability | Multiplier |
| --- | --- | --- |
| 00:00–03:00 | 9.5% | ×0.76 |
| 03:00–06:00 | 10.0% | ×0.80 |
| 06:00–09:00 | 7.0% | ×0.56 |
| 09:00–12:00 | 15.6% | ×1.25 |
| 12:00–15:00 | 16.3% | ×1.31 |
| 15:00–18:00 | 17.0% | ×1.36 |
| 18:00–21:00 | 13.6% | ×1.09 |
| 21:00–24:00 | 10.9% | ×0.87 |

The old rule could only ever scale **up**. The block table also scales **down**, so
the same pattern that reaches the alert band at the 15:00 peak stays well inside
Watch at 07:00.

**Caveat to report rather than hide:** both Philippine datasets examined — Manila
and Butuan City — place the peak in the afternoon, not at night. International
evidence does associate darkness with street robbery, so this table disagrees with
the wider literature. It is also from a different city, combines robbery with
theft, and counts only cases that reached conviction.

---

## 8. Bugs found and fixed

**`near_mouth` could never score.** It was priced at 0.20 against a bare cigarette
at 0.30. The two are a declared redundant pair, so only the heavier counts —
meaning `near_mouth` was suppressed every single time it fired, and a cigarette
clearly at the lips scored identically to one detected anywhere in frame. Now 0.50.

**Smoking had lost its alerting floor.** The old gate chain alerted on a cigarette
plus an elapsed dwell. Under the first draft of the new weights that scored 0.30
and produced nothing. At CCTV range a face is often not resolvable, so the
near-mouth cue cannot fire, and a large share of genuine detections would have
silently stopped alerting. A dwell cue at 0.25 restores the floor to exactly 0.55.

**`watch_parking` dropped the `type` key.** It rebuilt the spec dict before calling
`build_edge`, so a zone would have fallen through to the edge default and been read
as an open path along its own outline — still building, still running, silently
judging vehicles against something the operator never drew.

The second one is the more useful lesson: converting gates to weights can remove
recall without any test failing, because the tests assert what the weights do
rather than what the old gates did.

---

## 9. Files

### New

| File | Purpose |
| --- | --- |
| `core/vision/scoring.py` | Weighted-sum engine, bands, hysteresis, Manila blocks |
| `core/vision/vlm.py` | VLM verifier, prompt specs, fail-open |
| `core/management/commands/calibrate_weights.py` | Logistic-regression weight fitting |
| `core/tests_vlm.py` | VLM failure paths, calibration harness, review API |
| `core/tests_obstruction.py` | Road-zone geometry, line/zone equivalence |
| `core/migrations/0032_...` | Alert scoring + VLM fields, SystemSettings VLM block |
| `core/migrations/0033_...` | `vlm_enabled` default on, applied to existing rows |

### Modified

`watch_drinking.py` · `watch_smoking.py` · `watch_thief.py` · `watch_all.py` ·
`watch_parking.py` · `models.py` · `serializers.py` · `vision/theft.py` ·
`vision/obstruction.py` · `vision/recognition.py` · `tests_layer_e.py` ·
`requirements-detection.txt` (+`anthropic`)

Frontend: `EdgeCanvas.jsx` · `EdgeEditorModal.jsx` · `ViolationModal.jsx` ·
`AlertFeed.jsx` · `RecordsPage.jsx` · `api.js`

### Tests

103 backend tests passing, covering the scoring engine, the Manila table, VLM
failure paths, the calibration harness, the review API contract and the parking
zone geometry. Frontend lints clean (10 pre-existing problems unchanged) and
builds.

---

## 10. Outstanding

**Calibration has no data.** The 230 alerts currently in the database predate the
scoring model and carry no cue vector, so they cannot be calibrated against. Fresh
detector runs are needed to generate scored alerts, then 20+ of those labelled
(50–100 per the design document) before `calibrate_weights` will run.

**Nothing verified against real footage.** Every weight is a reasoned initial
value. Precision and recall are unmeasured. The central claim — that a missed
detection demotes rather than silences — is verified by unit test, not by video.

**The parking zone's resolution scaling is untested on a live stream.** When
running `watch_parking --debug` with a zone drawn, the filled polygon should sit
exactly on the carriageway. If it looks offset, the frame size it was drawn at
differs from the stream's.

**The dwell cue is currently constant.** In smoking and in drinking's solo path the
hard gate still runs before scoring, so the cue fires on every row that reaches it.
A logistic regression will find it collinear with the intercept. It becomes a
genuine variable only when that gate is itself converted to a weight.

**CCTV recording removal.** `recording.py`, `record_camera.py` and `record_cctv.bat`
show as deleted in the working tree, with `urls.py` and `views.py` reduced
accordingly — but this was not done in this session. Worth confirming there are no
dangling `startRecording` / `stopRecording` references left in `lookout/src/App.jsx`
and `api.js`.

---

## 11. Known discrepancies with the design document

All deliberate, none silently applied.

| Document says | Code does | Why |
| --- | --- | --- |
| A bottle-less gathering "remains at Warning" at 0.50 | 0.50 is Watch | 0.50 is below the document's own 0.55 Warning floor |
| Alert band entered above 0.55 | Entered at 0.55 | Weights are 0.05-granular; sums land on 0.55 exactly |
| — | Zone boundary inclusive, edge boundary exclusive | A vehicle on the traced kerb is in the road |

**Eight weights are ours, not the document's.** The document's §5.3 table prices only
the gathering path; applied literally it would have scored every solo drinker at
0.40 and eliminated that path entirely.

**A weighted linear sum assumes the indicators are independent, and they are not.**
Redundant pairs are suppressed rather than summed, but the assumption should be
stated in the limitations section rather than implied.
