# Automatic parking-zone tracing

Proposes parking's no-parking polygon from footage, so the area canvas opens with the road already
traced instead of empty.

- Rule: `core/vision/zone_autodetect.py` (no Django, no torch — boxes in, points out)
- **Automatic**: `watch_parking` traces one when no area is saved — nothing to draw, nothing to press
- CLI: `manage.py trace_zone --source <clip>`
- API: `POST /api/detection-jobs/trace-zone/` (admin)
- UI: the optional **Trace it for me** button on the area step of **Run Detection**

It sits in Run Detection, not in Live Feeds → Edge Zones, because that is where the operator already
is when the area needs drawing — and the clip is already staged there, so tracing it costs no
upload. A camera-scoped version was built first and removed; `test_it_is_not_on_the_camera_route_any_more`
keeps it gone, because a second entry point would mean a second upload path.

## Drawing an area is now optional everywhere

`watch_parking` used to **refuse to start** without a polygon ("alerting on dwell alone reads as a
broken detector, not as a missing polygon"). It now traces one from the footage instead, so starting
a parking run requires no tracing step at all. The order it resolves an area in:

1. the area saved on the camera (what the Edge Zones editor writes) — an operator who drew one
   always gets the one they drew;
2. a `--zone` file (`draw_zone`, terminal-only setups);
3. **traced from the footage**;
4. only then, refuse — and print the draw-it-by-hand instructions, because an unusable automatic
   answer must not take the manual route away with it.

`--no-auto-zone` restores the old refusal.

**Opt-in, and off by default.** `watch_merged_all` shares `watch_parking._load_zone` and is
documented to refuse without an area rather than fall back to anything. So `_auto_zone` checks an
attribute that `watch_parking.handle` sets explicitly; inheriting the method cannot quietly grant a
fallback. That is not hypothetical — reading the flag out of the options dict did exactly that, and
`tests_merged_all` caught it.

### Where a traced area is kept

| Camera | Persisted? | Why |
|---|---|---|
| a real camera (`CAM-SMOKE-01`) | **yes**, if nothing was stored | fixed view — the next run should not pay again, and the operator can see and adjust what was decided |
| a `-TEST` camera | **no** | per-*run* scratch for uploaded clips; the next run is a different clip of a different street |
| anything with an area already | **never** | a traced area must not overwrite one somebody drew on purpose |

The server clears a `-TEST` camera's stored area when a run supplies none, so "start it without
drawing" means "work *this* clip out" rather than "quietly reuse whatever the last operator traced".

### Thresholds are not part of the polygon

"Inside the road %" and "for N minutes" are parking's *rule* and apply to a traced area exactly as to
a drawn one. They used to be staged only inside the `edges` block, so an auto-traced run would have
silently used whatever was stored last. They are now sent and staged independently —
`TYPES_WITH_AREA_THRESHOLDS`, deliberately not the inverse of `TYPES_WITH_OPTIONAL_EDGES`, since
disposal draws an area and has no thresholds while parking is now the other way round.

## Why traffic, and not pixels

A still frame does not say where the road is. Kerbs are hidden behind parked cars, shadows read as
edges, and an elevated oblique CCTV view is nothing like the dashcam framing road-segmentation
models are trained on — a generic segmentation model would need per-camera fine-tuning, which
defeats the point of "automatic".

What the system already knows, because `watch_parking` computes it every frame anyway, is **where
vehicles are**. A vehicle that drove across a patch of ground has proved that patch is drivable road
far more directly than any edge detector could. So the road is traced from the **ground contact** of
vehicles seen to move — the bottom edge of the box, where the wheels meet the road — hulled into
a polygon.

The bottom **edge**, not its centre, because that is what the rule being fed tests: `ObstructionZone`
samples `FOOTPRINT_SAMPLES` (21) points across a vehicle's bottom edge and asks what fraction are
inside. `zone_autodetect` imports `_footprint_points` from it rather than copying the sampling, so
the traced shape and the tested shape cannot come to disagree about what a footprint is.

Centres describe the line traffic drives *along*; footprints describe the width it *sweeps*, which is
the pathway itself. Note the limit: footprint points spread along the box's own x-axis, so for traffic
running purely left-to-right at a fixed distance they add no width at all. They widen the hull where
the direction of travel differs from that axis — traffic receding up a street, which is the real
framing.

This needs no new model and no new weights: it reuses `recognition.detect_vehicles`, the same
detector the parking rule already runs.

## The two filters

| Filter | Kills | Why |
|---|---|---|
| seen ≥ `MIN_TRACK_SAMPLES` (3) times | single-frame false positives | a bush one frame called a car never forms a track |
| travelled ≥ `MOVED_MIN_WIDTHS` (1.0) of its own box width | **parked** vehicles | see below |

Excluding parked vehicles is counter-intuitive — a car parked on the carriageway is exactly what
this detector exists to catch, so its ground point really is inside the zone. But the rule cannot
tell that car from one parked legitimately in a driveway, a forecourt or a yard, and **a convex hull
is maximally sensitive to its outliers**: one vehicle sitting off-road swallows the whole frame.
Only moving vehicles are allowed to define the shape. Stationary ones are what the finished zone is
then used to judge.

Displacement is measured in **box widths**, not pixels, so the rule means the same thing for a
motorcycle at the kerb and a truck down the block — the same near/far invariance
`obstruction_zone.py`'s `_centre_dist` uses. `span` is the greatest distance between any two of a
track's points, not first-to-last: a vehicle that pulls out, drives up the road and parks again near
where it started has still proved the road in between.

## Association

Short-horizon greedy nearest-centre matching between consecutive samples (`associate`), **not**
ultralytics' tracker. `load_yolo()` returns one shared model whose tracker keeps `persist=True` state
between calls, so two concurrent callers would interleave into a single tracker conversation.
"Did this thing move?" needs no lasting identity, and this costs no state that can be corrupted — it
also keeps the whole rule unit-testable without torch.

`ASSOC_MAX_WIDTHS` (2.5) is deliberately wide: samples are ~0.4 s apart and a vehicle covers ground
in that time. Over-merging two vehicles into one track is harmless (the result is still "something
drove along this line"); splitting one vehicle into stationary fragments is not, because each
fragment would then fail the motion test.

## Point cloud → polygon

`convexHull` → `approxPolyDP` (`SIMPLIFY_EPSILON_FRAC`, 1.5% of the longest edge) → pad.

Simplify never goes below 3 points: a tolerance that collapsed the road to a line would hand the
editor a shape it cannot save, so an over-simplified result falls back to the hull it came from.

Padding (`PAD_FRACTION`, **2%**) grows the polygon outward **about its centroid** — a small residual
for detector box slop, not a geometric correction. It was 6% when the hull came from box *centres*,
which really did sit a half-vehicle inboard of the kerb; tracing the whole footprint already reaches
the outer wheels, so most of that justification went away and 6% then over-reached. Measured on the
sample footage, 6% pushed the right-hand boundary off the carriageway onto the concrete gutter; at 2%
it tracks the kerb.

Scaling about the centroid rather than offsetting each edge pads a wide near kerb more than a narrow
far one — the right way round for a carriageway receding to a vanishing point — and cannot
self-intersect.

## Refusals

`trace` returns `(None, report)` with a `reason` rather than a shape it does not believe in. The
report always says what was seen, because the next step differs completely:

| Reason | What the operator does |
|---|---|
| no vehicles detected at all | use footage of a street with vehicles on it |
| vehicles, but none moved | use footage with traffic *passing through* |
| too few sightings of moving vehicles | use a longer clip or a busier stretch |
| the traffic covered no width of road | use footage with vehicles driving *through* the area |

The endpoint returns these as **422**, not 400: the request was perfectly well formed, and the clip
simply cannot answer the question.

## Input: a staged clip, not an upload

The endpoint takes the `staged_token` of a clip already uploaded for a run. The clip is routinely
200 MB and is on the server by the time the area is being drawn, so re-uploading it to look at it a
second time would be the most expensive possible way to ask.

A direct `file` is accepted too, on the same reasoning as `DetectionJobViewSet.create`'s dual path:
it is the simplest way to drive this from a script or a test. **Only that scratch copy is deleted
afterwards.** A staged clip belongs to the run that staged it — deleting it here would pull the
footage out from under the detection the operator is about to start.

Points come back in the **staged frame's own pixels**, which needs no scaling:
`_staged_frame_response` reads frame 0 at native resolution and `trace_clip`'s first sample is that
same frame. The resolution is returned anyway so the page can *check* the two agree, rather than
silently misplacing the zone.

## Suggestion, never a commit

The endpoint saves **nothing**. The shape lands in the ordinary `EdgeCanvas` — the same canvas a
hand-traced area is drawn on, with the same Undo/Clear — and the operator still presses Start
themselves. A polygon nobody has looked at must not become the rule a vehicle is reported under.

`trace_zone`'s `--save` is opt-in for the same reason, and it always writes a preview image first.

`EdgeCanvas` seeds its points from `initialPoints` at **mount**, so a traced result reaches it by
remounting under a new `key` rather than by a new "replace the points" prop — every way in is then
"here is a frame and the points that go with it", and one seeding path cannot drift from another.
`reset()` bumps the same key, or a new clip would open showing the last clip's zone.

## Cost, and where it actually goes

Measured on 2560x1440 street footage, per sampled frame:

| | per frame |
|---|---|
| `grab()` (skipped frame) | 0.014 s |
| `read()` | 0.038 s |
| `detect_vehicles` @1280 | **1.14 s** |
| `detect_vehicles` @960 | 0.88 s |
| `detect_vehicles` @640 | 0.64 s |

**Inference dominates; decoding is noise.** Two wrong guesses were made before this was measured —
first that frame seeking was the cost, then that decoding was. Seeking *was* worth removing
(`sample_clip` reads sequentially and skips with `grab()`, which also collapsed the file and live
paths into one), but it bought 124 s → 110 s. The resolution bought the rest.

`TRACE_IMGSZ` is therefore **640**, not the detection run's 1280. Detection needs a precise box on
one vehicle at one moment; tracing needs roughly where a hundred vehicles were, and the hull is
decided by the *extremes* of that cloud, so per-box imprecision averages out. Same 75 samples:

| | time | polygon |
|---|---|---|
| @1280 | 63.8 s | baseline |
| **@640** | **27.1 s** | **IoU 0.96** vs baseline |

Sample **count**, unlike resolution, does matter — it is coverage of footage, and therefore how much
traffic is seen. At n=40 the traced area was less than half that at n=75 on the same clip.

| | Samples | Why |
|---|---|---|
| `MAX_SAMPLES` | 120 | CLI and `watch_parking`'s own trace |
| `WEB_MAX_SAMPLES` | 60 | a held-open HTTP connection behind a spinner |

### What a parking run actually costs to start

On a 30 s clip with no area saved, from launch to the first detected frame:

| Phase | Before | After |
|---|---|---|
| Django + torch import | 15 s | 12 s |
| **auto-trace** | **124 s** | **37 s** |
| vehicle model load (cached) | 0.2 s | 0.2 s |
| **to "Watching"** | **140 s** | **49 s** |

A **real** camera pays this once — the traced area is saved onto it, so later starts skip straight
to detection. A `-TEST` camera pays it every run, because each run is a different clip; that is the
price of not having to trace a polygon by hand, and the log says what it is doing while it does it.

## Measured## Measured

On `CAM-SMOKE-01` street footage (2560×1440), 40 samples at 1 s stride: 105 vehicle detections, 9
tracks, all 9 moving, 105 sightings → a 7-point zone covering the carriageway and correctly
excluding the footpath and the walled gutter on the right. 24 samples gave a coarser 5-point shape
from 5 moving tracks — more samples buy a better outline, which is what the two budgets above are
trading.

Switching from centres to footprints on that same clip moved the near-left corner from `(117, 1439)`
to `(0, 1439)` and the bottom edge from `(515, 1439)` to `(1084, 1439)` — i.e. it stopped
under-covering the near lane — and is what made the 2% pad correct.

The evidence floor counts **sightings of qualifying vehicles**, not points: a sighting contributes a
whole footprint's worth, so counting points would measure the sampling resolution rather than the
traffic.

## Not done

- **Live-camera tracing.** Clip-only; a clip has a bounded length and so a bounded runtime.
- **Vertex dragging** in `EdgeCanvas` — it can still only append and undo-last, so refining a
  suggested shape means clearing and re-clicking. The obvious next upgrade, and the one that makes
  "check and adjust" genuinely cheaper than "trace from scratch".
- Tracing for a LIVE camera's saved zone (Live Feeds → Edge Zones). That editor has no staged clip,
  so it would need its own upload — which is what moving the feature out of it avoided.
