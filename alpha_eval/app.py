"""alpha_eval - alpha-testing tool for LookOut (Streamlit UI).  Run:  streamlit run app.py --server.address localhost"""
import os
import re
import time
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import mimetypes
for _ext, _mime in ((".mp4", "video/mp4"), (".mkv", "video/x-matroska"), (".avi", "video/x-msvideo"), (".mov", "video/quicktime")):
    mimetypes.add_type(_mime, _ext)        # Windows may have no registry entry; the static video handler needs these

import pandas as pd
import streamlit as st

import config
import db
import keyedit
import matching
import scoring
from ui_common import (clip_status, ensure_setup, fmt_t, get_conn, player, runner_alive, setup_ok, sha256_stream,
                       start_runner, video_props)

st.set_page_config(page_title="LookOut alpha_eval", page_icon="🔍", layout="wide")

conn = get_conn()
if "planned_imported" not in st.session_state:
    db.import_template_clips(conn)
    st.session_state["planned_imported"] = True

PAGES = ["1 · Setup check", "2 · Clips (test plan)", "3 · Upload + Answer key", "4 · Queue / Live", "5 · Review",
         "6 · History", "7 · Summary", "8 · Settings", "9 · Export"]


def goto(page, **state):
    """Navigate on the next run (the sidebar radio's own key cannot be changed after it was drawn)."""
    st.session_state.update(state)
    st.session_state["_goto"] = page


st.sidebar.title("alpha_eval")
st.sidebar.caption(f"v{config.TOOL_VERSION} · scoring spec {config.SCORING_SPEC}")
if "_goto" in st.session_state:
    st.session_state["nav"] = st.session_state.pop("_goto")
if "nav" not in st.session_state:
    st.session_state["nav"] = PAGES[1]
page = st.sidebar.radio("Pages", PAGES, key="nav", label_visibility="collapsed")
alive = runner_alive()
st.sidebar.markdown(("🟢 queue runner running" if alive else "🔴 queue runner stopped"))
if not alive and st.sidebar.button("Start queue runner"):
    start_runner()
    time.sleep(1.5)
    st.rerun()


# ======================================================================================
# 1 Setup check
# ======================================================================================
def page_setup():
    st.header("Setup check")
    if st.button("Run / re-run checks") or "setup" not in st.session_state:
        st.session_state.pop("setup", None)
        ensure_setup()
    checks, raw = st.session_state["setup"]
    for name, ok, detail in checks:
        st.markdown(f"{'🟢' if ok else '🔴'} **{name}** — {detail}")
    git = raw.get("git", {})
    st.markdown(f"**LookOut git commit:** `{git.get('commit', '?')}`")
    if git.get("dirty"):
        st.warning("LookOut has uncommitted changes (dirty). The run snapshot records this; commit or note it before "
                   "collecting final results.")
    if setup_ok():
        st.success("All checks are green — runs are allowed.")
    else:
        st.error("Runs are blocked while any item is red.")
    if not raw.get("camera_zone_in_db", True):
        st.info(f"The installed camera {config.CAMERA_CODE} has no parking area saved in LookOut, so alpha_eval uses its "
                f"own zone file `parking_zone.json` (copied from lookout_backend/zone_mine.json). Redraw it with "
                f"`python manage.py draw_zone` if the camera view changed, then copy the JSON here.")


# ======================================================================================
# 2 Clips
# ======================================================================================
def clip_table():
    out = []
    for c in db.list_clips(conn):
        status, latest, done = clip_status(conn, c["clip_id"])
        keys = db.key_rows(conn, c["clip_id"])
        if done and any(k["kind"] == "Violation" and k["action_start_sec"] is None for k in keys):
            status = "Needs action start"
        summary = ""
        if done:
            summary = matching.summary_line(matching.compute_run(conn, done["run_id"], persist=False))
        out.append({"Clip": c["clip_id"], "Focus": c["planned_focus"], "Scenario": c["scenario"], "Distance": c["distance"],
                    "Time": c["time_of_day"], "Set": c["set"], "Recorded": c["recording_status"] if c["video_path"] is None
                    else "Recorded", "Video": "✔" if c["video_path"] else "", "Run": status,
                    "Reviewed": "Yes" if (done and done["reviewed"]) else "No", "Answer-key rows": len(keys),
                    "Re-run needed": "⚠" if db.rerun_needed(conn, c["clip_id"]) else "", "Latest outcome": summary})
    return out


def page_clips():
    st.header("Clips (test plan)")
    table = clip_table()
    total = len(table)
    reviewed = sum(1 for r in table if r["Reviewed"] == "Yes")
    st.progress(reviewed / total if total else 0.0, text=f"Reviewed {reviewed} / {total} clips")
    c1, c2 = st.columns(2)
    sel_set = c1.multiselect("Set", config.SETS, default=config.SETS)
    sel_focus = c2.multiselect("Focus", sorted({r["Focus"] for r in table if r["Focus"]}))
    view = [r for r in table if r["Set"] in sel_set and (not sel_focus or r["Focus"] in sel_focus)]
    st.dataframe(pd.DataFrame(view), use_container_width=True, hide_index=True, height=520)
    if view:
        o1, o2 = st.columns([2, 1])
        target = o1.selectbox("Open a clip to upload its video / edit its answer key", [r["Clip"] for r in view], key="clips_open")
        if o2.button("Open"):
            goto(PAGES[2], clip_goto=target)
            st.rerun()
    st.subheader("Add a new clip")
    spare_used = sum(1 for c in db.list_clips(conn) if not c["in_template_plan"])
    st.caption(f"New clips use the template's spare rows 60–154 ({config.SPARE_ROWS[1] - config.SPARE_ROWS[0] + 1 - spare_used} left).")
    with st.form("new_clip", clear_on_submit=True):
        a, b, c = st.columns(3)
        cid = a.text_input("Clip ID (e.g. SMK-V21)")
        focus = b.selectbox("Planned focus", config.TYPES)
        scenario = c.text_input("Scenario")
        d, e, f = st.columns(3)
        dist = d.selectbox("Distance", config.DISTANCES)
        tod = e.selectbox("Time of day", config.TIMES_OF_DAY)
        set_ = f.selectbox("Set", config.SETS)
        if st.form_submit_button("Add clip"):
            cid = cid.strip().upper()
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,19}", cid):
                st.error("Clip ID: letters, digits, - or _ only (2–20 characters).")
            elif db.get_clip(conn, cid):
                st.error(f"{cid} already exists.")
            elif spare_used >= config.SPARE_ROWS[1] - config.SPARE_ROWS[0] + 1:
                st.error("The template has no spare rows left (60–154).")
            else:
                db.upsert_clip(conn, cid, planned_focus=focus, scenario=scenario, distance=dist, time_of_day=tod, set=set_,
                               in_template_plan=0)
                st.success(f"Added {cid}.")
                st.rerun()


# ======================================================================================
# 3 Upload + Answer key
# ======================================================================================
HELP = """
* **Parking** — action start = the moment the vehicle enters the zone.
* **Holdup** — rapid approach / knife reveal (not loitering).
* **Inuman (drinking gathering)** — group formed and drinking. One row per inuman gathering; one row per person for solo acts.
* **Smoking / solo drinking** — first light / first puff / first sip.
* *Look-alike* and *No violation* rows are things that must **not** alert (TN if quiet, FP if it alerts).
"""


def parse_start(text):
    try:
        return scoring.parse_time(text), None
    except ValueError as exc:
        return None, str(exc)


def page_upload():
    st.header("Upload + Answer key")
    st.caption("Fill the answer key BEFORE running detection. The detector never sees it.")
    clips = db.list_clips(conn)
    ids = [c["clip_id"] for c in clips]
    if not ids:
        st.info("Add a clip on the Clips page first.")
        return
    if st.session_state.get("clip_goto") in ids:
        st.session_state["clip_select_box"] = st.session_state.pop("clip_goto")
    if st.session_state.get("clip_select_box") not in ids:
        st.session_state["clip_select_box"] = st.session_state.get("clip_pick") if st.session_state.get("clip_pick") in ids else ids[0]
    pick = st.selectbox("Clip", ids, key="clip_select_box")
    st.session_state["clip_pick"] = pick
    clip = db.get_clip(conn, pick)

    with st.expander("Clip details", expanded=not clip["video_path"]):
        c1, c2, c3 = st.columns(3)
        set_ = c1.selectbox("Set", config.SETS, index=config.SETS.index(clip["set"] or "Main"), key=f"set_{pick}")
        dist = c2.selectbox("Distance", config.DISTANCES, index=config.DISTANCES.index(clip["distance"]) if clip["distance"] in config.DISTANCES else 0, key=f"dist_{pick}")
        tod = c3.selectbox("Time of day", config.TIMES_OF_DAY, index=config.TIMES_OF_DAY.index(clip["time_of_day"]) if clip["time_of_day"] in config.TIMES_OF_DAY else 0, key=f"tod_{pick}")
        scenario = st.text_input("Scenario", clip["scenario"] or "", key=f"scn_{pick}")
        d1, d2, d3 = st.columns(3)
        date = d1.text_input("Date recorded (YYYY-MM-DD)", clip["date_recorded"] or "", key=f"date_{pick}")
        rtime = d2.text_input("Time recording started (HH:MM, optional)", clip["recorded_time"] or "", key=f"rt_{pick}",
                              help="Used as the footage clock (holdup night block, drinking evening band). "
                                   "Blank = a typical time for the chosen time of day.")
        rstat = d3.selectbox("Recording status", ["Not recorded", "Recorded", "Re-record"],
                             index=["Not recorded", "Recorded", "Re-record"].index(clip["recording_status"] or "Not recorded"), key=f"rs_{pick}")
        notes = st.text_area("Notes", clip["notes"] or "", key=f"notes_{pick}")
        if st.button("Save details"):
            if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date.strip()):
                st.error("Date must look like 2026-09-14.")
            elif rtime and not re.fullmatch(r"\d{1,2}:\d{2}", rtime.strip()):
                st.error("Time must look like 19:30.")
            else:
                db.upsert_clip(conn, pick, set=set_, distance=dist, time_of_day=tod, scenario=scenario,
                               date_recorded=date.strip() or None, recorded_time=rtime.strip() or None,
                               recording_status=rstat, notes=notes)
                st.success("Saved.")
                st.rerun()

    st.subheader("Video")
    up = st.file_uploader("Upload the trimmed clip (.mp4 / .avi / .mov / .mkv)", type=["mp4", "avi", "mov", "mkv"], key=f"up_{pick}")
    if up is not None and st.button("Store this video"):
        config.CLIPS_DIR.mkdir(exist_ok=True)
        dest = config.CLIPS_DIR / f"{pick}_{re.sub(r'[^A-Za-z0-9._-]', '_', up.name)}"
        with open(dest, "wb") as fh:
            for chunk in iter(lambda: up.read(1 << 20), b""):
                fh.write(chunk)
        with open(dest, "rb") as fh:
            sha = sha256_stream(fh)
        props = video_props(dest)
        dup = db.rows(conn, "SELECT clip_id FROM clips WHERE video_sha256=? AND clip_id<>?", sha, pick)
        db.upsert_clip(conn, pick, video_path=str(dest), video_sha256=sha, recording_status="Recorded", **props)
        if dup:
            st.warning(f"This exact file is already stored for {', '.join(d['clip_id'] for d in dup)}.")
        st.success(f"Stored {dest.name}: {props['duration_sec']} s, {props['fps']} fps, {props['resolution']}.")
        st.rerun()
    clip = db.get_clip(conn, pick)
    if clip["video_path"]:
        st.caption(f"{Path(clip['video_path']).name} · {clip['duration_sec']} s · {clip['fps']} fps · {clip['resolution']} · "
                   f"SHA-256 {clip['video_sha256'][:16]}…")

    st.subheader("Answer key")
    with st.expander("What to type here"):
        st.markdown(HELP)
    cap = None
    if clip["video_path"]:
        cap = player(clip, mark=True, key=f"pl_{pick}")
    keys = db.key_rows(conn, pick)
    n_expected = sum(1 for k in keys if k["kind"] == "Violation")
    st.markdown(f"**Expected violations: {n_expected}**")
    if cap and cap.get("t") is not None and st.session_state.get(f"cap_n_{pick}") != cap["n"]:
        st.session_state[f"cap_n_{pick}"] = cap["n"]
        st.session_state[f"cap_t_{pick}"] = cap["t"]
    ct = st.session_state.get(f"cap_t_{pick}")
    if ct is not None:
        st.info(f"Captured time from the player: **{fmt_t(ct)}**  ({ct:.1f} s)")

    df = pd.DataFrame([{"id": k["id"], "Type": k["violation_type"], "Kind": k["kind"], "Start (mm:ss.s)":
                        "" if k["action_start_sec"] is None else fmt_t(k["action_start_sec"]),
                        "Who / description": k["who"] or "", "Added after run": "Yes" if k["added_after_run"] else ""}
                       for k in keys], columns=["id", "Type", "Kind", "Start (mm:ss.s)", "Who / description", "Added after run"])
    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True, hide_index=True, key=f"ak_{pick}",
        column_config={"id": None, "Type": st.column_config.SelectboxColumn(options=config.TYPES, required=True),
                       "Kind": st.column_config.SelectboxColumn(options=config.KINDS, required=True, default="Violation"),
                       "Start (mm:ss.s)": st.column_config.TextColumn(help="e.g. 0:41.5 or 41.5; blank = needs start"),
                       "Added after run": st.column_config.TextColumn(disabled=True)})
    cols = st.columns([1, 2, 2, 3])
    if cols[0].button("Save answer key", type="primary"):
        errs = keyedit.apply_edits(conn, pick, edited.to_dict("records"))
        if errs:
            st.error("; ".join(errs))
        else:
            st.success("Answer key saved.")
            st.rerun()
    if ct is not None and keys:
        labels = {f"{k['violation_type']} #{k['instance_no']} ({k['kind']})": k["id"] for k in keys}
        target = cols[1].selectbox("Apply captured time to", list(labels), label_visibility="collapsed")
        if cols[2].button("Set as start time"):
            db.update_key_row(conn, labels[target], action_start_sec=ct)
            st.rerun()
    if clip_has := db.clip_has_started_run(conn, pick):
        st.caption("This clip has already been run: rows added or changed now are flagged 'added after run'.")

    st.subheader("Queue for detection")
    ok_checks = ensure_setup()
    if not ok_checks:
        st.error("A setup check is red — see page 1. Runs are blocked.")
    problems = []
    if not clip["video_path"]:
        problems.append("upload the video")
    if not keys:
        problems.append("add at least one answer-key row (use 'No violation' for a clip where nothing should alert)")
    if problems:
        st.warning("Before queueing: " + "; ".join(problems) + ".")
    if any(k["kind"] == "Violation" and k["action_start_sec"] is None for k in keys):
        st.warning("Some Violation rows have no start time — they will show 'Needs start' until you fill it in.")
    if st.button("Queue this clip", disabled=bool(problems) or not ok_checks):
        rid = db.queue_run(conn, pick)
        st.success(f"Queued run {rid}.")
        goto(PAGES[3])
        st.rerun()
    st.markdown("**Queue several clips at once**")
    ready = [c["clip_id"] for c in db.list_clips(conn) if c["video_path"] and db.key_rows(conn, c["clip_id"])]
    many = st.multiselect("Clips with a video and an answer key", ready, key="queue_many")
    if st.button("Queue selected clips", disabled=not many or not ok_checks):
        for cid in many:
            db.queue_run(conn, cid)
        goto(PAGES[3])
        st.rerun()


# ======================================================================================
# 4 Queue / Live
# ======================================================================================
def tail(path, n=25):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(l.strip() for l in lines[-n:] if l.strip())
    except OSError:
        return ""


def live_events(run_id):
    return db.parse_events(db.run_dir(run_id) / "events.jsonl")


@st.fragment(run_every=2)
def queue_live():
    runs = db.rows(conn, "SELECT * FROM runs WHERE status IN ('queued','running') ORDER BY queued_at")
    cur = next((r for r in runs if r["status"] == "running"), None)
    if cur is None:
        st.info("Nothing is running." if not runs else "Waiting for the queue runner to pick up the next run.")
    else:
        rd = db.run_dir(cur["run_id"])
        prog = {}
        try:
            import json
            prog = json.loads((rd / "progress.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        dur = prog.get("duration") or 0
        frac = min(1.0, (prog.get("video_time", 0) / dur)) if dur else 0.0
        st.markdown(f"**Running {cur['run_id']}** — clip **{cur['clip_id']}** ({cur['set_at_run']}, "
                    f"parking {cur['parking_threshold']:g} s, inuman {cur['drinking_duration']:g} s)")
        st.progress(frac, text=f"video time {fmt_t(prog.get('video_time'))} / {fmt_t(dur)} · frame {prog.get('frames', 0)}"
                               f" · {prog.get('frames', 0) / max(prog.get('elapsed', 1), 1):.2f} fps")
        left, right = st.columns([3, 2])
        with right:
            st.caption("Log")
            st.code(tail(rd / "run.log", 14) or "(starting…)", language="text")
            if st.button("Cancel this run", key="cancel_cur"):
                conn.execute("UPDATE runs SET cancel_requested=1 WHERE run_id=?", (cur["run_id"],))
                conn.commit()
        with left:
            st.caption("Live alerts")
            evs = live_events(cur["run_id"])
            if not evs:
                st.write("No alerts yet.")
            for e in reversed(evs):
                c1, c2 = st.columns([1, 3])
                fr = next((rd / "frames" / n for n in (f"{e['event_id']}_likely.jpg", f"{e['event_id']}_possible.jpg",
                                                       f"{e['event_id']}_monitoring.jpg") if (rd / "frames" / n).exists()), None)
                if fr:
                    c1.image(str(fr), use_container_width=True)
                steps = " → ".join(f"{s} {fmt_t(t)}" for s, t in sorted(e["first"].items(), key=lambda kv: kv[1]))
                c2.markdown(f"**{config.INTERNAL_TO_TYPE.get(e['internal_type'], e['internal_type'])}** `{e['event_id']}` "
                            f"(track {e['track_id']}) — highest **{e['highest']}**  \n{steps}  \nAI: **{e['ai_verdict'] or 'pending / not run'}**")
    if runs:
        st.subheader("Queue")
        st.dataframe(pd.DataFrame([{"Run": r["run_id"], "Clip": r["clip_id"], "Status": r["status"], "Set": r["set_at_run"],
                                    "Queued": r["queued_at"], "Attempt": r["attempt"]} for r in runs]),
                     hide_index=True, use_container_width=True)


def page_queue():
    st.header("Queue / Live")
    if not runner_alive():
        st.warning("The queue runner is stopped, so queued runs will not start. Press 'Start queue runner' in the sidebar "
                   "(start.bat does this automatically).")
    queue_live()
    st.subheader("Recent runs")
    can_run = setup_ok()
    if not can_run:
        st.caption("Retry / re-run are disabled until every item on the Setup check page is green (run the check there).")
    recent = db.rows(conn, "SELECT * FROM runs ORDER BY queued_at DESC LIMIT 15")
    for r in recent:
        if r["status"] in ("queued", "running"):
            continue
        c = st.columns([3, 2, 2, 5, 2])
        c[0].write(f"**{r['clip_id']}** · {r['run_id']}")
        c[1].write(r["status"])
        c[2].write(f"{(r['processing_time_sec'] or 0):.0f} s")
        c[3].write(r["error"] or "")
        if r["status"] == "failed" and c[4].button("Retry", key=f"retry_{r['run_id']}", disabled=not can_run):
            db.queue_run(conn, r["clip_id"])
            st.rerun()
        elif r["status"] in ("done", "cancelled") and c[4].button("Re-run clip", key=f"rerun_{r['run_id']}", disabled=not can_run):
            db.queue_run(conn, r["clip_id"])
            st.rerun()
    qd = db.rows(conn, "SELECT run_id FROM runs WHERE status='queued'")
    if qd and st.button("Cancel all queued runs"):
        conn.execute("UPDATE runs SET status='cancelled', finished_at=? WHERE status='queued'", (db.now_iso(),))
        conn.commit()
        st.rerun()


# ======================================================================================
import ui_review      # noqa: E402
import ui_summary     # noqa: E402

DISPATCH = {PAGES[0]: page_setup, PAGES[1]: page_clips, PAGES[2]: page_upload, PAGES[3]: page_queue,
            PAGES[4]: lambda: ui_review.page_review(conn, goto, PAGES), PAGES[5]: lambda: ui_review.page_history(conn, goto, PAGES),
            PAGES[6]: lambda: ui_summary.page_summary(conn), PAGES[7]: lambda: ui_summary.page_settings(conn),
            PAGES[8]: lambda: ui_summary.page_export(conn)}
DISPATCH[page]()
