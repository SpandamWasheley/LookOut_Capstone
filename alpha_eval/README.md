# alpha_eval — alpha-testing tool for LookOut

Runs LookOut's **real** detection (merged_v2 + pose + ByteTrack + heuristic rules + v6.1 scoring + the Qwen AI checker)
on **recorded clips**, completely blind to the answer key, then scores the alerts against the answer key you typed and
fills our Excel template for Chapter 4. It imports LookOut read-only: nothing in `lookout_backend` or LookOut's database
is ever changed.

## One-time setup
1. The same Python (Anaconda base) that LookOut uses. Extra packages: `pip install -r requirements.txt`.
2. Ollama running with `qwen3-vl:2b-instruct` and `qwen3-vl:4b-instruct` (the models LookOut's Settings name).
3. Put `LookOut_Alpha_Testing_Template.xlsx` in `templates/` (already there). Never edit it — exports are written to copies.

## Every time
1. **Start:** double-click `start.bat`. It checks Ollama, starts the queue runner (a small window, minimised) and opens
   http://localhost:8501.
2. **Setup check** (page 1): everything must be green. Runs are blocked while anything is red.
3. **Add the clip + answer key** (page 3, *before* running):
   * pick the planned clip (or add a new one on page 2), fill Set / distance / time of day / date recorded;
   * upload the video (duration, fps, resolution and SHA-256 are stored; a duplicate file is flagged);
   * type the answer key: **one row per expected item** — type, kind (*Violation*, *Look-alike*, *No violation*),
     start time `mm:ss.s`, who. Scrub the video and press **Use current time**, then **Set as start time**.
     Parking = vehicle enters the zone · Holdup = rapid approach / knife reveal · Inuman = group formed and drinking ·
     Smoking / solo drinking = first light / puff / sip. One row per inuman gathering, one per person for solo acts.
   * **Queue this clip** (or several at once). The queue runner processes one clip at a time and keeps going if you close
     the browser.
4. **Queue / Live** (page 4): progress, log, and every alert as it appears (with its picture and AI verdict).
5. **Review** (page 5, per clip):
   * check each answer-key row's outcome and the explanation; reassign an alert to another row of the same type if the
     automatic match is wrong;
   * for every **unmatched alert** confirm a tag (the tool suggests one — *Accept all suggestions* confirms them all);
   * give every FN / FP a **miss reason**, enter your name, press **Mark reviewed**.
6. **Summary** (page 7) previews the template's tables; **Export** (page 9) fills a copy of the template into `exports/`.
   The export recalculates the workbook and compares the tool's outcome with the sheet's own formulas. Any `MISMATCH`
   or unresolved row marks the file **NOT FINAL** — fix it and export again.

## Things worth knowing
* **Main vs Validation set**: Main clips use the shortened thresholds (parking 120 s, inuman 60 s); Validation clips use
  the real ones (300 s, 600 s). Change them on page 8 — changing a detection threshold flags the affected clips
  *Re-run needed*; scoring settings (alert window, tolerance, Monitoring counts) re-score instantly.
* **Re-running a clip** creates a new run; old runs stay in **History** (page 6) where two runs can be compared.
* An answer-key row added or edited *after* a clip's first run started is flagged **Added after run** in the export.
* Alert times are **video time**: the detector's timers run on the video clock, so a slow computer gives the same result.
* The parking area comes from `parking_zone.json` (the installed camera has none saved in LookOut).
* Folders: `clips/` videos · `runs/<run_id>/` events, frames, settings snapshot, log · `exports/` workbooks ·
  `eval.db` everything else. Back up `eval.db` and `clips/` before the final export.

## Troubleshooting
| Symptom | What to do |
|---|---|
| Setup check red: *Ollama reachable* | Start Ollama (`ollama serve` or *Start LookOut.bat*), then *Run / re-run checks*. |
| Red: *Qwen models present* | `ollama pull qwen3-vl:2b-instruct` and `ollama pull qwen3-vl:4b-instruct`. |
| Red: *GPU visible* | Close other GPU programs / reboot; the 6 GB RTX 4050 must be visible to PyTorch. |
| Queued run never starts | Sidebar shows *queue runner stopped* → press **Start queue runner** (or run `start.bat`). |
| A run failed | Page 4 → *Recent runs* shows the error; open `runs/<run_id>/run.log`. Press **Retry**. |
| Closed the window mid-run | Nothing is lost: start again; the runner adopts the running worker or re-queues the same run. |
| `blocked_writes.log` has lines | The write-block stopped something from touching LookOut's DB. Harmless, but tell the developer. |
| Export says NOT FINAL | Read the reasons on page 9: MISMATCH (tool vs sheet), *needs tag / start*, or *settings not consistent*. |
| Video does not play in the browser | Use mp4 (H.264). Other formats still run in detection but may not preview. |
| *Re-run needed* warning | A detection threshold changed after the run: re-queue the clip. |

Run the self-tests any time: `python -m pytest tests -q`.
