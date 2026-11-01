# Zone trace in the Edge Zones editor — report

## 1. Investigation

| Question | Answer |
|---|---|
| How the editor loads/edits/saves | `lookout/src/EdgeEditorModal.jsx`: fetches a live snapshot (`GET /cameras/{id}/snapshot/`, sub-stream `…02`) or falls back to a clip frame (`POST /cameras/{id}/edge-frame/`). `acceptFrame` scales any saved zone from `edges_width/height` to the frame. `EdgeCanvas` owns the points (seeded from `initialPoints` **only at mount**). Save = `PATCH /cameras/{id}/` with `edges`, `edges_width`, `edges_height`, `obstruction_pct/minutes`. |
| Parking skipped when no zone, others still run | `core/monitor.py` `_launch` (~l.324–): `merged` is always launched; `parking` only `if has_road_edge(camera)`; `disposal` if `disposal_enabled`. Covered by `ApplyZoneTests.test_no_zone_still_runs_the_other_violations`. `status()` shows the "draw the road edge" notice. |
| Does `zone_autodetect` support RTSP? | `sample_clip` uses `cv2.VideoCapture` + `grab()`/`read()` so it technically accepts a URL, and `watch_parking._auto_zone` uses it with `MAX_SAMPLES` (120). But it paces by frame count from the reported fps (RTSP often reports none), has no deadline and cannot be cancelled. |
| Second RTSP connection vs running detector | Each detector (`watch_parking._run_stream`) owns its own `cv2.VideoCapture` wrapped by `LatestFrameReader`; the trace opens a separate capture, so nothing is shared in-process. The camera must allow an extra concurrent stream (Hikvision typically allows several). Extra cost: the trace runs YOLO in the **web server process** (CPU/GPU contention with the detectors on the same machine). |
| How the running parking process gets its zone | Loaded **once** at start (`watch_parking.handle` → `_load_zone`, l.253). A saved zone has no effect on a running process. |

## 2. What was built

- **`lookout_backend/core/vision/live_trace.py`** (new): `run_trace` (own short-lived capture, wall-clock sampling, 90 s deadline, 15 s first-frame timeout, cancel flag, always releases the capture), per-camera job registry (`start`/`get`/`cancel`), failure-code mapping. Calls `zone_autodetect.trace()` unchanged.
- **`core/views.py:648` `CameraViewSet.trace_zone`** — `POST|GET|DELETE /api/cameras/{id}/trace-zone/`, admin only. **Deviation from the brief:** POST starts a background job (202) and the editor polls GET, instead of one long blocking request. Reason: ~48 s outlasts many tunnels/proxies, and a blocking request can neither show progress nor be cancelled. Second POST while running → 409. Nothing is written to the database.
- **`core/views.py:689` `CameraViewSet.update`** — after a save that includes `edges` on `CAM-SMOKE-01`, calls `monitor.apply_zone` and returns `zone_applied` in the response.
- **`core/monitor.py:124` `apply_zone`, `_swap_parking`, `_retire`, `_spawn`** — see section 4. `_launch` now uses `_spawn` (behaviour unchanged).
- **`lookout/src/api.js:435`** helpers; **`EdgeEditorModal.jsx`**: "Trace zone" button, progress text + Cancel, confirm before replacing existing points, traced polygon scaled from stream resolution to the snapshot frame size and loaded as ordinary editable points (remounting `EdgeCanvas` via `key`), success note, failure note that leaves the canvas untouched, post-save note about parking.
- **`core/tests_live_zone_trace.py`** (new, 27 tests).

## 3. How the live trace works (for the manuscript)

- Own RTSP connection (TCP, `stimeout` 5 s), released when done.
- A sample every **0.4 s** of stream time, up to **120 samples** (~48 s of traffic; ≈50 s measured with the scripted clock), hard limit **90 s** including inference.
- Vehicle detector: `recognition.detect_vehicles` (the shared COCO YOLOv8n model), conf **0.4**, inference size **640** (`TRACE_IMGSZ`).
- Then unchanged `zone_autodetect.trace`: tracks seen ≥3 times that moved ≥1 box-width, ≥12 sightings, footprint hull → simplify (1.5 %) → pad (2 %).
- Result: points in the stream's pixels + width/height; the editor scales to its frame (same idea as `acceptFrame`).
- Failure reasons shown: camera unreachable / opened but no frames / no vehicles detected / vehicles but none moved / too few sightings / traffic covered no width / timed out / cancelled / server has detection disabled (503) / no stream configured (400).

## 4. How a new zone is applied

**Parking-only restart, automatic.** `watch_parking` reads its zone once, so after Save `apply_zone` pops only the `parking` process from the monitor's process table, terminates it and spawns a new one on the saved zone. It is popped *before* it is killed because the supervisor restarts **everything** when any tracked process exits; this way merged (smoking/drinking/holdup) and disposal never stop. If parking was never running (no zone before) it is started; if the zone was cleared it is stopped; if monitoring is off nothing happens (the next start reads the zone). The editor shows which of these happened. Risk accepted: a brief gap in parking coverage (model load, tens of seconds) and a parking dwell timer reset.

## 5. Tests

- `core.tests_live_zone_trace`: 27 pass — scripted camera + clock (success, no vehicles, none moved, too few sightings, unreachable, no frames, deadline, cancel, mid-trace stall, capture always released), job failure-code mapping, endpoint (admin-only 403/401, success, camera row unchanged, failure reason, 409 on concurrent request, cancel, no stream 400, detection disabled 503, idle), `apply_zone` (start / restart-only-parking / stop / not running), API save returns `zone_applied`, no-zone launch still runs the other detectors.
- Full suite: **996 tests OK** (428 s).
- **Skipped / not verified:** the camera (192.168.1.64:554) was not reachable from this machine, so there was **no real live-stream trace and no check that a running detector keeps its FPS during a trace**. A recorded clip does not substitute: sampling is paced on the wall clock, so a file is drained at decode speed (4–6 samples) and the two clips tried showed no vehicles. The real model did load and run in-process through `run_trace` (11.7 s, correct "no vehicles" reason). The frontend passes ESLint but was not built or exercised in a browser. **Please do one trace against the real camera with monitoring running.**

## 6. Known limitations

- Needs moving traffic during the ~48 s; a quiet street gives "none moved"/"too few sightings".
- The convex hull fills curves/junctions and can cover non-road; it is a suggestion to be edited.
- The trace runs YOLO in the web process: on the same machine it competes with the detectors for CPU/GPU; the camera must accept one more stream.
- The job registry is in-process memory (single web worker; lost on restart).
- Parking restart resets its in-progress dwell timers.
