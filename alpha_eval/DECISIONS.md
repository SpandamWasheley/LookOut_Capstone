# DECISIONS — choices made without asking (autonomous build)

Each entry: what was decided, and why.

**D1 — Engine driven: `watch_merged_all`, not `watch_merged`.** `watch_merged` only runs smoking/drinking/holdup; parking is the 4th
in-scope violation and lives in `watch_merged_all` (`EXTRA_ENGINES=("parking",)`). It is the same command the Run Detection page
launches for "merged4". Defaults are kept (far 2×2 tiling, ByteTrack, cascade).

**D2 — Alerts are captured in memory; the DB is also read-only.** LookOut's rule layer creates/updates `Alert` rows
(`Alert.objects.create/filter().update/delete`). In the worker, `Alert` is swapped in the five modules that use it for an in-memory
store (`capture.FakeAlert`) whose create/update/delete calls become `events.jsonl` lines. On top of that: (a) the Django DB is
LookOut's `db.sqlite3` opened with SQLite `mode=ro`, (b) a `connection.execute_wrapper` raises on INSERT/UPDATE/DELETE/REPLACE/CREATE/
ALTER/DROP and logs to `blocked_writes.log`. Nothing tried to write in any test run (0 blocked). The block was never weakened.
No file in `lookout_backend` was edited; the settings wrapper only changes `MEDIA_ROOT` (→ run sandbox) and `DATABASES` (→ read-only).

**D3 — Evidence clips are not produced.** `ClipRecorder.save` and `recognition.cut_raw_clip` (ffmpeg) are replaced by no-ops in the
worker. They only write evidence videos for the dashboard; they do not influence any detection decision and cost minutes per run.
Event images are saved by alpha_eval itself (`runs/<id>/frames/`).

**D4 — Video clock (what exactly is patched).** Investigation (`INVESTIGATION.md` §2) shows LookOut's rule layer already runs on the frame's
video position for files. The remaining wall-clock reads were patched *in the worker process only*: the module-level name `time` in
`watch_merged`, `watch_merged_all`, `watch_smoking`, `watch_drinking`, `watch_thief`, `watch_parking` → `videoclock.VideoClock`
(`time()/monotonic()/perf_counter()` return the current frame's `CAP_PROP_POS_MSEC/1000`, `sleep()` is a no-op); `core.vision.ai_checker.time`
→ `NoSleepTime` (real clock, no sleeping; it only measures AI latency). The clock is advanced by `ClockedCapture.read()` immediately after every
frame read. This makes the Settings re-read cadence (every 5 *video* seconds) deterministic too. Proven by the slowed run (see REPORT).

**D5 — Footage clock (`--clock`).** Holdup's night block and the drinking evening band use the time of day. With no `--clock` LookOut falls back to
the *processing* wall clock, which would make results depend on when the test was run. The worker is therefore given a footage start
time: clip date + "recorded time" if typed, else a typical time for the clip's Time of day (Day 10:00, Afternoon 17:00, Night 23:00).
It is derived from clip metadata, never from the answer key. If no date is recorded, LookOut's own fallback (wall clock) applies and the
snapshot says so.

**D6 — AI checker scheduling.** `ai_checker.check_async` is replaced by a deferral: the call is held until *video* time ≥ trigger + `FRAMES_AFTER`,
then the original function runs it synchronously (`wait=True`); everything still pending at end-of-file is run then. LookOut's real
async thread sleeps 1 wall-second and then crops whatever frames the ring holds — which depends on processing speed. The deferral gives the AI
the same frames every run. The Qwen call, prompts, crops and verdict rules are LookOut's own. A call that fails/times out is recorded as `Not run`.

**D7 — Parking has no Monitoring/Possible stages.** LookOut's parking rule fires one alert when the stopped-in-zone time reaches the threshold.
alpha_eval records it as status `Likely` at that video time (a counting status), with its alert time = the moment the threshold was reached.

**D8 — Parking zone.** The installed camera `CAM-SMOKE-01` has `edges={}` in LookOut's DB and no `zones/obstruction_zone.json` exists. alpha_eval uses its
own `parking_zone.json`, seeded by *copying* `lookout_backend/zone_mine.json` (normalised polygon drawn on 2560×1440 footage), passed with `--zone`.
Redraw with `draw_zone` and copy the JSON here if the camera view changes.

**D9 — AI verdict mapping.** `Agrees` = LookOut's badge "AI: supports this alert"; `Disagrees` = "may be ordinary activity" or "unclear" (the AI did not
support it); `Not run` = AI off, call failed, invalid reply, or never returned. The raw badge and the AI's observation are kept in the event record, so the
mapping can be changed later without re-running. The suggested status is LookOut's `ai_status.suggest_status` evaluated against the event's *peak* status.

**D10 — Test thresholds.** Parking threshold → `--alert-score <seconds>` (the option LookOut itself provides for the zone's seconds). Drinking group
duration → `SystemSettings.load` is wrapped in the worker so the loaded object carries the Set's value on every (periodic) re-read; `.save()` is never called.
Everything else is exactly LookOut's stored settings, dumped into each run's snapshot.

**D11 — Times are compared at 0.1 s.** The sheet stores times with one decimal, and its `L<=J` test must agree with the tool's. All scoring therefore uses
alert times and starts rounded to 0.1 s (deadline computed from those) with a 1e-6 tolerance. Delay = alert − start − waiting.

**D12 — Unit test 13 (manual swap).** With the numbers given (rows 30 and 90, events 41 and 98, window 60), a swapped row 1 would receive an alert at 98, after
its deadline 90, so under the sheet's own formula (`L<=J`) it is FN, not TP — and exporting a TP the sheet calls FN would create a `MISMATCH`. I kept the
reviewer's swap (kept, flagged manual, never re-assigned) but let the deadline rule decide the outcome. `test_13` asserts exactly that; `test_13b` uses
in-window numbers (rows 30/50, events 41/70) and shows both rows staying TP with swapped delays (40 and −9).

**D13 — "NOT FINAL" is broader than MISMATCH.** An export is also labelled NOT FINAL (file name `_NOT_FINAL`, and the Results-basis cell) when: the settings
consistency is MIXED, any row still *Needs tag / Needs start*, preview and sheet differ, or no recalculation engine could run. Rationale: the Settings sheet itself says
"Don't report MIXED results".

**D14 — Recalculation engine.** LibreOffice was installed via winget but the installer was cancelled (UAC needs a person), so it is absent. The export check
falls back to the `formulas` Python package, which recalculates the *whole* workbook (all COUNTIFS/AVERAGEIFS/IF formulas as written in the template),
not just column Q. It was verified to detect a deliberately tampered tool outcome (`MISMATCH`). If LibreOffice is installed later, it is used automatically.

**D15 — Settings sheet "Model version"** is written as the model file name (`merged_v2.pt`) as requested; the template's pre-filled `merged_v2` is overwritten.
Camera (B17) is left as the template has it.

**D16 — Which clips are exported.** Latest *done* run per clip (reviewed ones only when "reviewed only" is on). All clips known to the tool get their status
columns written on the Clips sheet (non-exported clips show their run status but no alert counts).

**D17 — Unmatched/duplicate bookkeeping.** An event LookOut would have deleted (a Monitoring blip shorter than `monitoring_min_seconds`, `_incident_discard`) is
dropped and never listed. A reviewer may only (re)assign *counting* events to a row, and only to a row of the same type. A manually matched event that is outside the
row's deadline is shown as FN with the event attached (matches the sheet formula).

**D18 — Blind worker.** `worker.py run` takes only `--run-id --video --parking-threshold --drinking-duration --clock`; it never imports `db`, never opens `eval.db`,
and runs as a separate process started by the queue runner. The answer key is only read by the UI/scoring code after the run.

**D19 — Weights copy.** LookOut loads `yolov8n.pt` / `yolov8n-pose.pt` by bare file name from the working directory (it downloaded fresh copies on the first test run).
The worker's working directory is `alpha_eval/work/`, which holds *copies* of the two files from `lookout_backend` (SHA-256 recorded in each snapshot) so nothing is
downloaded and nothing is written inside lookout_backend.

**D20 — Serving the preview video.** Streamlit's static folder (`static/clips/`, hard links to `clips/`, so no second copy on disk) with a small custom player component
(`components/player/`) that provides the clickable timeline and the "Use current time" button.

**D21 — Test isolation.** Verification runs used `ALPHA_EVAL_DB` / `ALPHA_EVAL_RUNS` (env overrides in `config.py`) pointing at `test_env/`, so the real `eval.db` and `runs/`
start empty for the team. `test_env/` is git-ignored; copies of the evidence files are in `evidence/`.

**D22 — Review rules.** "Mark reviewed" requires: no row needing a start, every FN/FP row (and every unmatched alert tagged as FP) has a miss reason, every unmatched alert has a confirmed tag,
and a reviewer name. Tag *suggestions* are never applied silently.

**D23 — Git "dirty".** `dirty` in the snapshot is `git status --porcelain` limited to `lookout_backend/` (the team currently has uncommitted edits there, so it reads `true`);
the repo-wide flag is stored as `dirty_repo`. alpha_eval itself never changes those files.
