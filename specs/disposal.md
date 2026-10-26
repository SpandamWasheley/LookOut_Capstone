# Improper disposal: detection reference

**Code** `disposal` · **Colour** `#22c55e` · **Label** "Improper Disposal"

The one detector that runs **no trained violation class**. The merged model knows Bottle, Cigarette
and knife; garbage has no class in it. Rather than retrain, disposal asks two cheap questions in
sequence, and carries its own confirmation logic instead of the weighted-sum scoring layer — for the
same reason parking does: there is no cited cue table to sum.

It shares no model forward pass with the merged detectors, so it is deliberately **not** part of
`watch_all` / `watch_merged` / `watch_merged_all`. `core/monitor.py` launches it as its own live
process, gated on `disposal_enabled` at launch time.

## Pipeline

```
frame (full res)
  │
  ├─ downscale to PROC_WIDTH (960) ─┐
  │                                 ▼
  │   STAGE 1  dual MOG2 background subtraction
  │     slow model (LONG_LR  0.0005) — holds a new object for minutes
  │     fast model (SHORT_LR 0.02)   — absorbs a still object in seconds
  │     static = slow_fg AND NOT fast_fg
  │     size gate MIN_AREA_FRAC .. MAX_AREA_FRAC
  │     blobs matched within MATCH_DIST, dropped after CANDIDATE_GRACE unseen
  │
  ├─ mover filter (one stock yolov8n pass every MOVER_CHECK_SECONDS)
  │     blobs inside a person/vehicle box never become candidates
  │     a candidate HIDDEN behind one keeps its id (OCCLUSION_HOLD_SECONDS)
  │
  ├─ owner hold — whoever stood over it as it appeared must walk away
  │
  ▼  still >= disposal_static_seconds, owner gone, not occluded
  │
  │   STAGE 2  YOLO-World zero-shot on a padded full-res crop
  │     detect at WEAK_PREDICT_CONF, decide at disposal_confidence
  │     garbage must clear the floor AND beat every reject class
  │
  ├─ SpatialCooldown → annotate → suspect crop → Alert
  │
  └─ PIN  once stage 1 loses the blob but stage 2 already called it garbage
        held at its last box for up to PIN_MAX_SECONDS
        re-asked every PRESENCE_RECHECK_SECONDS — presence is now stage 2's call
        released after PIN_MISS_LIMIT consecutive "not garbage" answers
```

### Why the pin exists

Both background models keep learning. `LONG_LR` makes absorption slow, not impossible — after a
minute or two the slow model has the dumped bag in its background as well, `static` goes empty, no
blob matches the candidate, and `CANDIDATE_GRACE` drops it two seconds later. **The rubbish is still
lying there.** A change detector has nothing left to detect once the change has become the scene.

This cannot be fixed inside stage 1, so what is fixed instead is the assumption that stage 1 is the
only thing allowed to keep a candidate alive. Stage 2 is an *appearance* check on the live frame, so
it still sees a bag the background models have forgotten. A candidate stage 2 has already engaged
with — `confirmed`, or `weak` and still queued for a recheck — is therefore **pinned** at its last
known box when its grace lapses, and from then on its presence is decided by re-asking stage 2.

Only engaged candidates are pinned: holding every transient blob stage 1 forgot would spend a model
pass indefinitely on noise the detector never had a reason to believe in. A `rejected` candidate is
not pinned either — a reject verdict is an *answer*, not a maybe.

What the pin changes, concretely:

- the live view keeps the id, the box and the accruing dwell instead of the object vanishing;
- a **weak match now actually gets its recheck** — before the pin it was dropped before
  `disposal_recheck_seconds` came round, which was a missed alert, not a cosmetic problem;
- a confirmation reached from the presence loop goes through the same `_alert` path (cooldown,
  annotated evidence, suspect crop) as one reached the ordinary way;
- a pinned candidate is skipped by `due_for_check`, so one object never costs two passes a cycle.

Three things bound the hold, because a claim about an object nobody can observe must not be
unfalsifiable: `PIN_MAX_SECONDS` expires it outright, `PIN_MISS_LIMIT` consecutive rejections release
it, and a returning blob un-pins it (keeping its identity — the usual reason a blob comes back is
that this same object flickered, and treating that as new would restart the timer and re-alert).
Occluded candidates are not asked at all, since the crop would be of whatever is standing in front.

## What alerts

**Garbage must beat every reject class to be reported at all.** `disposal_confidence` then decides
*how* it is reported, not *whether*: it is the line between Possible and Likely. The two conditions
fail different things and both stay load-bearing — the comparison is the gate, the floor is the
grading.

| `classify()` | Alert row | Live view |
|---|---|---|
| `CONFIRMED` — beats every reject class, clears the floor | **yes** · `violation` (Likely) | Likely |
| `WEAK` — beats every reject class, under the floor | **yes** · `warning` (Possible) | Possible · `Weak match <n>%` |
| `REJECTED` — a reject class won | no | Below Monitoring |

### One place, one violation

The alert is anchored to the **place**, not to the candidate. `SpatialCooldown` is a registry of
which places already have an alert and which alert each one has; a candidate that does not know a row
yet *adopts* the row its place holds (`alert_at`) instead of opening a second one.

That anchoring is what the rule needs, because a candidate is not a lasting identity: stage 1 forgets
an object a couple of unseen seconds after both background models absorb it and raises a **fresh id
for the same bag**. Anchored to the candidate, one sack sitting on one spot became a new alert every
candidate lifetime.

| | |
|---|---|
| same spot, new candidate | **adopts** the open row — status updates, no new alert |
| same spot, weak then confirmed | the one row is **upgraded in place** to Likely |
| **further than `disposal_cooldown_dist`** | a different place, so **its own alert** |

Rubbish on the left and rubbish on the right are two things to go and clear, and reporting them as one
would under-count the street — which is why the gate is a *distance* and not "the disposal detector
alerted recently". `disposal_cooldown_dist` (60 px in the detector's PROC_WIDTH frame) is the radius
inside which two sightings are the same bag seen twice.

`disposal_cooldown` is the **idle** window on a place, not a cap: every report re-arms it, and
`_run_stream` additionally re-arms the place of every live candidate that owns a row on every frame —
which a confirmed candidate needs, since it is never offered to stage 2 again and nothing else would
touch its place. A place is released only once nothing has been seen there for the whole window, so
fresh rubbish dumped on a spot cleared long ago is a new violation.

An upgrade moves the one row: `level` rises to `violation`, a `status` event is appended to the
timeline, the evidence still is re-shot (its caption carries the score), and `peak_level` never falls
— that is the field the Potential Violations list reads, and an event a reviewer has already been
shown must not vanish mid-review. A verdict that reads back weaker lowers `level` only.

### Synonymous prompts are combined

`PROMPTS` describes **one** physical thing nine ways — a plastic bag *is* a garbage bag *is* a trash
bag. An open-vocab head scores each independently, so a real bag commonly returns three or four weak
hits on the same box. Taking the single best reported the strength of *one witness out of three*, and
that is what put correct distant objects under the floor: 0.10 / 0.09 / 0.08 was reported as **0.10**.

Garbage hits on one box are therefore combined with noisy-OR, `1 - Π(1 - s)` — the three above
become **0.25**. `REJECT` keeps a plain maximum, and the asymmetry is the argument: the garbage
prompts are different *descriptions of one hypothesis* so they corroborate, while `dog` and
`motorcycle` on one box is the model being **confused**, not two witnesses agreeing.

Three guards stop it being a free multiplier:

| Guard | Why |
|---|---|
| `GROUP_IOU` 0.5 | only hits on the *same object* combine — no borrowing across the crop |
| class dedup | a prompt cannot corroborate *itself*; corroboration needs a different description |
| single-hit short-circuit | one hit returns bit-exactly its own score, so a clear match is provably unaffected |

Noisy-OR is bounded below 1, so no pile of weak hits ever fabricates certainty.

The **comparison** suppresses look-alikes: a bare floor would fire on a dog at 0.4 as readily as a
sack at 0.4, so garbage has to *lead* on this crop. The **floor** is what trades recall for
precision — it decides which of the two statuses a detection is filed under, and so what an operator
triages first. It grades rather than gates, deliberately: a sub-floor lead the dashboard never showed
was the one detection nobody could settle, and what settles a maybe is a person watching ten seconds
of clip. Reporting it amber and saying *why* costs a reviewer a glance; hiding it costs the dumping.

The cost is real and is the operator's to weigh, not to be hidden:

- An open-vocab head spreads probability across 20 prompts, so a correct bag across the street scores
  low per prompt — though combining synonyms (above) recovers much of that, lifting a 0.10/0.09/0.08
  bag to 0.25. Raising the floor buys precision by losing distant
  objects; lowering it does the reverse.
- `WEAK_PREDICT_CONF` (0.05) is the bottom of the dial — the model's own NMS discards boxes under it
  before `classify()` is reached, so lowering `disposal_confidence` past 5 buys nothing.
- When the floor is the wrong lever (it costs true positives as fast as false ones), the targeted
  ones are: a drawn **area** (Edge Zones), `disposal_static_seconds`, `disposal_min_area`, and naming
  the actual offender in **`REJECT`** — which kills one look-alike without touching recall anywhere
  else.

## Live processing view

Disposal publishes the system's standard four statuses. **Possible applies to disposal** — it is the
waiting state, covering every way a candidate can be past the still requirement without yet being
confirmed. The colour and label are the shared ones; a **sub-label** beside the status says which
kind of waiting it is, because the colour alone cannot distinguish a rule that is deliberately
holding from a model that is unsure. **Possible and Likely both write an Alert**, at `warning` and
`violation` respectively, so the status on the live view and the status on the card are the same word
for the same verdict.

| Status | Sub-label | Meaning | Score bar |
|---|---|---|---|
| **Monitoring** | — | new object, still-timer counting (`still < disposal_static_seconds`) | **0-54%** = `still / static × 54` |
| **Possible** | `Owner nearby` | still long enough, but whoever put it there has not left (owner-proximity hold) | **55%** |
| **Possible** | `Checking` | stage 2 queued or running | **55%** |
| **Possible** | `Weak match <n>%` | garbage beat every reject class but scored under `disposal_confidence`; re-checked every `disposal_recheck_seconds` | **55-99%**, placed by `score / floor` |
| **Possible** | `Still there · weak <n>%` | as above, but stage 1 has lost the blob — the pin is holding it | **55-99%** |
| **Likely** | — | confirmed garbage — clears the floor and beats every reject class | **100%** |
| **Likely** | `Still there` | confirmed, and now held by the pin rather than by a blob | **100%** |
| **Below Monitoring** | winning reject class | stage 2 says person / dog / bin / … — drawn dashed, hidden in Panel view | 0% |

Two rules hold regardless:

- **A weak match creates the Alert at Possible**, and keeps its place in the recheck queue; a later
  look that clears the floor upgrades that same row to Likely rather than filing a second one.
- **Possible never blocks one.** The two *waiting* sub-labels — `Owner nearby` and `Checking` — have
  no verdict yet and so no row yet. The owner hold expires at `disposal_owner_max_hold`, after which
  the candidate returns to stage 2 and can still reach either status.

The bands line up with the rest of the system on purpose — Possible starts at 0.55 everywhere — so one
bar means one thing across detectors. Disposal has no weighted score to plot, so the bar tracks
*progress* rather than likelihood, and 100% stays reserved for a confirmation.

### Weak matches need a lower detection floor than the decision floor

`predict(conf=…)` is ultralytics' **NMS threshold**: it discards every box beneath it. Passing the
decision floor there would make a sub-floor bag invisible, indistinguishable from nothing at all — so
the `Weak match` state could not exist. Stage 2 therefore detects at `WEAK_PREDICT_CONF` (0.05) and
applies `disposal_confidence` afterwards in `classify()`.

This cannot change any previously-confirmed result: a confirmation needs garbage ≥ floor and garbage ≥
reject, so for a newly visible reject class to overturn one it would have to score above the garbage
and below the old floor simultaneously.

## Stage 1 constants (`core/vision/disposal.py`)

| Constant | Value | Purpose |
|---|---|---|
| `PROC_WIDTH` | 960 | stage-1 processing width |
| `WARMUP_SECONDS` | 5.0 | learn the empty scene first |
| `LONG_LR` / `SHORT_LR` | 0.0005 / 0.02 | the slow/fast pair the whole trick rests on |
| `FG_THRESHOLD` | 200 | MOG2 writes shadows as 127, real foreground as 255 |
| `MIN_AREA_FRAC` / `MAX_AREA_FRAC` | 0.0008 / 0.08 | drops specks; drops the cloud-over-sun frame-wide blob |
| `MATCH_DIST` | 40 px | same-candidate matching distance |
| `CANDIDATE_GRACE` | 2.0 s | unseen tolerance before drop |
| `OCCLUSION_HOLD_SECONDS` | 20.0 s | how long a HIDDEN candidate keeps its id |
| `RECHECK_SECONDS` | 10.0 s | re-ask stage 2 about a rejected or weak candidate |
| `PIN_MAX_SECONDS` | 1800.0 s | cap on holding an object stage 1 has forgotten |
| `PRESENCE_RECHECK_SECONDS` | 15.0 s | how often a pinned object is re-asked (`--presence-seconds`) |
| `PIN_MISS_LIMIT` | 2 | consecutive "not garbage" answers before the pin is released |

## Stage 2

| Constant | Value |
|---|---|
| `DEFAULT_WORLD_MODEL` | `yolov8s-worldv2.pt` |
| `CROP_PAD` / `MIN_CROP` | 0.5 / 96 px |
| `GROUP_IOU` | 0.5 (combine synonymous garbage hits on one box) |
| `CONFIRM_CONF` | 0.15 (decision floor) |
| `WEAK_PREDICT_CONF` | 0.05 (detection floor) |

```python
PROMPTS = ["plastic bag", "garbage bag", "trash bag", "pile of trash", "sack",
           "cardboard box", "rice sack", "styrofoam", "pile of plastic bottles"]
REJECT  = ["person", "dog", "cat", "motorcycle", "car", "bicycle", "chair",
           "trash can", "garbage bin", "bucket", "potted plant"]
```

`"box"` is deliberately **not** a reject class: while it was, every dumped carton was thrown out by the
very comparison meant to catch look-alikes. What needed rejecting was the *container* a box is confused
with — a bin or bucket that lives there and was never dumped — so those are named explicitly.

The prompt cache (`core/vision/world_cache/disposal-world-<hash>.pt`) is keyed on a hash of
`PROMPTS + REJECT`, so editing either list rebuilds it automatically rather than silently having no
effect.

## Settings (Settings → Improper Disposal)

| Column | Default | Meaning |
|---|---|---|
| `disposal_enabled` | `true` | gates launch *and* the loop |
| `disposal_confidence` | 15 % | **gates** the alert; the one precision/recall dial — see *What alerts* |
| `disposal_static_seconds` | 2 | still-time before stage 2 runs at all |
| `disposal_cooldown` / `_dist` | 120 s / 60 px | how long a place stays claimed once nothing is seen there, and the radius that counts as the same place |
| `disposal_min_area` / `_max_area` | 0.08 % / 8 % | stage-1 size gate |
| `disposal_warmup_seconds` | 5 | empty-scene learning window |
| `disposal_recheck_seconds` | 10 | retry window for a rejected or weak candidate |
| `disposal_ignore_movers` | `true` | skip blobs inside a person/vehicle box |
| `disposal_owner_radius` | 80 px | how close counts as standing over it |
| `disposal_owner_away_seconds` | 3 | owner must be gone this long |
| `disposal_owner_max_hold` | 3 | never hold longer than this, measured from appearance |
| `disposal_suspect_confidence` | 25 % | person-detection floor for the suspect crop |

All are re-read every few seconds mid-run except the warmup, which has already happened.

The pin has **no settings columns** — it is a correctness fix for a failure mode with no operator
trade-off to tune, so its three constants live in `core/vision/disposal.py` and only the re-ask
cadence is exposed, as `--presence-seconds`, for calibration runs.

> With `owner_away_seconds` and `owner_max_hold` both at 3 s and `static_seconds` at 2 s, the hold can
> only apply for about one second — the abandoned-object rule is close to disabled by configuration.
> That is an operator choice, not a default: expect alerts on people standing beside their own goods.

## Alert record

`level` / `peak_level` are `warning` (**Possible**) or `violation` (**Likely**) — the two status names
are borrowed from `core/vision/scoring.py` for their vocabulary, nothing more. `cues` stays **empty**:
there is no cue table to sum, so the card shows Object confidence and no score breakdown, exactly like
parking. `confidence` and `object_confidence` both carry the zero-shot bag score. Disposal also writes `suspect_image_url` / `suspect_status` (`person` | `carrier` | `none`),
`owner_left_at`, and `footage_offset` (position in an uploaded clip — `timestamp` is wall clock).

## Known limits

- **A bag already lying there when the run starts never alerts.** During warmup both models learn fast,
  so it is absorbed as background. The detector only sees things that *arrive*.
- A bag that sits long enough is eventually absorbed by the **slow** model too (~100 s at 20 fps).
  Stage 1's observable window is therefore bounded at both ends; the **pin** is what carries a
  candidate past the far end, but only one stage 2 had already called garbage. An object absorbed
  *before* it was ever checked is still lost — with `disposal_static_seconds` at 2 s that needs
  absorption inside two seconds, which no realistic `LONG_LR` produces, so in practice the gap is
  closed rather than merely narrowed.
- The pin costs one zero-shot pass per pinned object every `PRESENCE_RECHECK_SECONDS`. At the 30-minute
  cap that is ~120 passes per dumped object, which is cheap next to stage 1 running every frame — but
  it is unbounded in the number of *objects*, so a view that genuinely collects rubbish all day will
  carry a growing re-check load until each pin expires.
- No frame enhancement, deliberately: background subtraction depends on photometric consistency
  *between* frames, and per-frame adaptive enhancement injects exactly the false foreground stage 1
  reads as "a new object appeared".
- `pick_suspect` has no maximum distance — with nobody near the object it still picks the nearest
  detected person in frame. The crop is a lead for a reviewer, never an identification.
- Every constant here is reasoned against the sample clips in the repository root, **not**
  field-calibrated.
