# REPORT — alpha_eval build (autonomous session)

Status: **built and verified.** 43 automated tests pass; the nine Section-11 checks were run on real footage with LookOut's real
detection (details below). Items that could not be done exactly as specified are listed in §5.

## 1. What was built (file by file)

| File | Purpose |
|---|---|
| `worker.py` | `run` = the **blind** detection process (inputs: run id, video, the Set's two thresholds, footage clock); `daemon` = queue runner; `check` = setup self-test. Writes `events.jsonl`, `frames/`, `snapshot.json`, `progress.json`, `result.json`, `run.log`, `blocked_writes.log`. |
| `capture.py` | Drives LookOut's real `watch_merged_all` (smoking + drinking + holdup + parking). In-memory `Alert` store → every create/status change/AI verdict/discard becomes an `events.jsonl` line with **video time**, track id, box; saves the box-annotated frame at each first status; defers the AI checker to video time. |
| `videoclock.py` | Video clock shim (+ `ClockedCapture`), patched into LookOut's modules inside the worker only. |
| `dbguard.py` | `execute_wrapper` that raises on INSERT/UPDATE/DELETE/REPLACE/CREATE/ALTER/DROP and logs to `blocked_writes.log`. |
| `lookout_settings_wrapper.py` | LookOut's settings + `MEDIA_ROOT` → run sandbox + LookOut's `db.sqlite3` opened `mode=ro`. |
| `scoring.py` | Pure matching/scoring/aggregation (Section 5). |
| `matching.py` | Scores a stored run with the answer key, manual overrides, tags, review rules, summary line, flat records. |
| `db.py`, `keyedit.py`, `queue_runner.py` | `eval.db` schema (clips, answer_key, runs, events, matches, unmatched_tags, row_review, eval_settings, exports, meta), answer-key editing, restart-safe queue. |
| `exporter.py` | Fills a **copy** of the template (values only), recalculates, reads back Check/Summary, labels NOT FINAL. |
| `app.py`, `ui_review.py`, `ui_summary.py`, `ui_common.py`, `mediaserver.py`, `components/player/` | Streamlit UI (9 pages), video player with clickable timeline and "Use current time". |
| `config.py`, `start.bat`, `requirements.txt`, `.gitignore`, `README.md`, `DECISIONS.md`, `INVESTIGATION.md`, `REPORT.md` | Config/launch/docs. |
| `tests/` | `test_scoring` (all 14 Section-5 tests + extras), `test_db`, `test_review`, `test_keyedit`, `test_history`, `test_export`, `test_export_limits`, `test_videoclock`, `test_blind`; helpers `fake_data`, `e2e_setup`, `lookout_snapshot`, `verify_tools`. |
| `parking_zone.json`, `templates/`, `work/` | Parking polygon (copied from `zone_mine.json`), the untouched template, copies of two YOLO weights. |
| `evidence/` | Before/after fingerprints of LookOut, per-run `events.jsonl`/`snapshot.json`/`result.json`, `verification_outputs.txt`. |

## 2. Investigation findings (full detail with line numbers: `INVESTIGATION.md`)
* Engine: `watch_merged_all.Command` (= `watch_merged` + parking as `EXTRA_ENGINES`). Loop `watch_merged._run` L402; per-frame `_process_frame` → `_extra_frame` (parking) + `_merged_pass`.
* Status changes/alerts: `_incidents.IncidentMixin._incident_sync` (L424) → `Alert.objects.create` / `_incident_write` → `.update(level=…)`; AI trigger L540; AI result `store_ai_result` L94; parking alerts come straight from `ObstructionZone.update` hits.
* Video time: `reader.get(CAP_PROP_POS_MSEC)/1000` (`watch_merged.py:498`); LookOut's rule layer already uses it. Wall-clock reads: settings re-read cadence (5 s), `ai_checker` sleep/monotonic, and `watch_thief._clock_now` (processing-time of day unless `--clock`).
* Names: `smoking`, `drinking`, `theft` (=Holdup), `parking`; levels `monitoring/warning/violation` = Monitoring/Possible/Likely; identity = the Alert row keyed by `(kind, track/cluster id)`.
* Parking threshold = `--alert-score`; drinking group duration = `SystemSettings.drinking_group_duration`. The installed camera `CAM-SMOKE-01` has **no** parking polygon in LookOut's DB → own `parking_zone.json`.

## 3. Decisions (one line each; reasons in `DECISIONS.md`)
D1 drive `watch_merged_all` (parking is in scope) · D2 alerts captured in an in-memory store + read-only DB + write-block safety net · D3 evidence clips/ffmpeg skipped · D4 video clock patched into 6 command modules + `ai_checker`, advanced on every frame read · D5 `--clock` = clip date + recorded/typical time of day · D6 AI checks deferred to video time, flushed at EOF · D7 parking alert recorded as *Likely* at threshold time · D8 parking zone from own file seeded from `zone_mine.json` · D9 AI verdict: supports→Agrees, ordinary/unclear→Disagrees, failed/none→Not run · D10 thresholds via `--alert-score` and in-memory `SystemSettings.load` wrapper · D11 scoring at 0.1 s resolution (matches the sheet) · D12 unit test 13 follows the sheet's deadline rule (+ in-window variant) · D13 NOT FINAL also for MIXED settings / unresolved rows / no recalculation · D14 `formulas` package for recalculation (LibreOffice install was cancelled by UAC) · D15 Model version = file name · D16 export = latest done run per clip · D17 discarded flickers dropped; manual assignment limited to counting events of the same type · D18 worker never touches eval.db · D19 working dir with weight copies · D20 own video server for the preview · D21 tests ran on `test_env/` (real `eval.db`/`runs/` start empty) · D22 review rules · D23 `dirty` = backend path only.

## 4. Verification evidence
Machine: RTX 4050 6 GB, Python 3.13 (Anaconda), Ollama with `qwen3-vl:2b-instruct` + `qwen3-vl:4b-instruct`. Test footage (copied, originals untouched): `Aug18_18 - Trim2.mp4` (51.6 s smoking, 2560×1440, 10 fps), `Parking.mp4` (96 s), `Aug17_1 - TrimParkingMorning.mp4` (59 s parking). Their answer keys are placeholders — these runs prove the **pipeline**, not accuracy.

1. **Unit tests** — `python -m pytest tests -q` → **43 passed** (Section-5 tests 1–14 all pass; test 13 per D12, plus `test_13b`).
2. **Zero side effects on LookOut** — fingerprint before vs after **7 end-to-end runs** (smoking clip ×3: direct worker, via the queue, slowed; parking clip ×1; second parking clip ×3 at thresholds 8 s / 20 s / 20 s, one of them crash-recovered) (`tests/lookout_snapshot.py`):
   * `db.sqlite3` SHA-256 `4281dcc499c0cb05f8420b9ae5bea874280ca5e6917ac4650499be31621ac5f7` — **identical** (also file mtime 2026-10-15 unchanged); every table's row count identical (`core_alert` 138, `core_detectionjob` 50, …); `core_systemsettings` row identical (drinking_group_duration still 600).
   * `media/`: **1459 files, 1,823,073,289 bytes** before and after. `git status --porcelain` in `lookout_backend`: 16 lines before and after, identical status and `git diff` hashes (the team's own pre-existing edits; none by alpha_eval).
   * `blocked_writes.log`: **empty in every run** (0 write attempts). The write-block was proven live: the Setup check issues an `INSERT` probe and it is blocked.
   * Note: the very first test run (before the formal "before" snapshot) is covered by the DB file's unchanged mtime/hash.
3. **Blind detection** — `worker.py run` accepts only `--run-id --video --parking-threshold --drinking-duration --clock` (+ test-only `--run-dir --slow`); `tests/test_blind.py` proves the detection modules never import `db/matching/scoring/queue_runner/streamlit` and never mention the answer key; at run end each worker records `blind_check` — `db`, `matching`, `scoring`, `queue_runner`, `ui_common`, `exporter` all **not loaded** (`result.json`).
4. **Video-time accuracy** — saved event frame vs the video frame nearest in content (`evidence/verification_outputs.txt`): smoking run 3 events **0.03 / 0.02 / 0.01 s**; parking-clip run 4 events **0.09 / 0.04 / 0.00 / 0.02 s** (limit 0.5 s). **Slowed run** (0.2 s sleep per frame → 1.46 fps instead of 2.15 fps) vs the normal run: all **5 status events identical** (type, status, video time to the millisecond) and the AI verdict identical.
5. **Threshold override** — parking clip with Main parking threshold 8 s → alert at **27.5 s**; 20 s → alert at **40.0 s** (+12.5 s for a +12 s threshold; 0.5 s extra is the zone's decay/hysteresis between runs, see below). In-memory drinking override proven: snapshot `drinking_group_duration=60` while LookOut's DB still holds 600 and is byte-identical afterwards.
6. **Repeatability** — smoking clip run 3×: direct worker, via queue, and slowed → **identical** status events and AI verdict every time. Parking clip at 20 s run twice → alert **40.008 s both times**, identical events. No differences to explain. (The AI deferral to video time is what removed the async-timing variation.) Caveat: Qwen replies were stable here; GPU/LLM nondeterminism could still occasionally flip an AI verdict (never the alert times).
7. **Export round-trip** (`tests/test_export.py`, fake finished runs covering **TP, FN, TN, FP look-alike, FP false alarm, FP unscripted, Duplicate, Late, Excluded, Needs tag, Needs start, a Validation-set parking TP, an alerted-instead FN and an added-after-run row**): recalculated whole workbook → **0 MISMATCH**; Summary and Validation sheets equal the UI preview in all 5×17 cells ×2 sheets; every formula cell in the copy equals the template's formula (Results Log I/J/N/Q/Z, Clips N, all of Summary/Validation/Breakdown/Confusion Matrix, sheet names/order); template SHA-256 `08cb63c66e0073d6b488a85073f3cfb78b4827b531c2778b0ba0e4fa5fc3240f` unchanged; values reload correctly. A tampered tool outcome is detected (`MISMATCH`). A real-run export (SMK-V01, PRK-V01, PRK-V02) also gave 0 mismatches and was correctly labelled NOT FINAL (MIXED thresholds, 1 alert needing a tag). Overflow errors (>95 new clips, >400 log rows) tested.
8. **History** — SMK-V01 re-run twice: History lists all runs; compare says "No row changed", 1 vs 1 counting events identical, "Identical settings"; `tests/test_history.py` proves the old run's row, events and matches are unchanged after a re-run and the new run becomes the latest (old one read-only).
9. **Restart resilience** — (a) queue runner killed **while its worker was mid-run** → restarted runner **adopted** the live worker, run finished, **still one run row** (attempt 0); (b) runner **and** worker killed mid-run → restarted runner re-queued the **same row** (attempt 1), it completed, **no duplicate**. Streamlit is independent of the runner (state in `eval.db`). UI smoke: every page renders (headless `AppTest`), review → sign-off → summary → export works end to end; the video player/timeline/"Use current time" verified in headless Chrome.

Throughput observed: **~2.0–2.2 frames/s** on 2560×1440 (far 2×2 + cascade + pose + Qwen), i.e. a 1-minute clip ≈ 5 min of processing.

## 5. Skipped / deviations / things to know
* **LibreOffice** could not be installed (winget installer cancelled — needs a person to accept UAC). Recalculation uses the `formulas` Python package on the whole workbook (D14). It is slower (~40 s) but exact for these formulas; LibreOffice, if installed later, is picked up automatically.
* **Unit test 13** cannot hold with the numbers given (98 s is after row 1's deadline 90 s); implemented per the sheet's rule with an in-window twin test (D12).
* **No ground-truth footage** exists on this laptop, so no real accuracy figures were produced — only pipeline checks.
* The **AI "unclear" badge maps to "Disagrees"** (D9). On the test clips Qwen mostly says "unclear" for smoking; that makes many alerts "Disagrees". Raw badge + AI observation are stored; the mapping is one function (`capture.Recorder._ai_judgement`) if you prefer otherwise.
* The **parking zone** is a copy of `zone_mine.json`; confirm with the team it matches the installed camera's view.
* `pip install formulas` added `formulas`, `schedula`, `numpy-financial` to the shared Anaconda environment; `lookout_backend/requirements*.txt` untouched.
* The browser `data_editor` for the answer key and the upload widget were not clicked through by a human (no GUI automation for them); their logic is unit-tested (`keyedit`) and the pages render without errors.
* The Setup page shows LookOut as **dirty** (the team has uncommitted edits in `lookout_backend`, e.g. `watch_merged*.py`, `watch_parking.py`); runs record this in each snapshot, so commit before the final data collection if you want a clean hash.

## 6. How to use tomorrow
1. Double-click `alpha_eval\start.bat` (starts Ollama check, the queue runner, and the app at http://localhost:8501).
2. Page **1 Setup check** → all green (GPU, Ollama, Qwen models, DB write-block…). Note the "dirty" warning.
3. Page **8 Settings** → confirm the thresholds (Main 120 s / 60 s, Validation 300 s / 600 s) *before* running anything.
4. Page **3** → pick a planned clip, fill Set/distance/time of day/**date recorded**, upload the video, type the answer key (scrub + *Use current time*), **Queue**. Queue several clips at once and leave it running (~5 min per minute of video).
5. Page **4** shows live alerts; when a run is done, page **5 Review**: check each row, confirm tags (*Accept all suggestions* then adjust), pick miss reasons, enter your name, **Mark reviewed**.
6. Page **7 Summary** previews Chapter-4 numbers; page **9 Export** fills the template into `exports\` — only report files **not** marked NOT FINAL.
7. Back up `eval.db` and `clips\` after each session; nothing in `lookout_backend` or LookOut's database is ever changed by this tool.
