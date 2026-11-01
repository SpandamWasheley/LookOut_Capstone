"""Review and History pages."""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

import config
import db
import matching
import scoring
from ui_common import fmt_t, player

NONE_OPT = "— none —"
OUTCOME_ICON = {"TP": "🟢", "TN": "🟢", "FP": "🔴", "FN": "🟠", "Needs start": "🟡", "Needs tag": "🟡", "Duplicate": "⚪",
                "Late": "⚪", "Excluded": "🔵"}


def _frame(conn, run_id, event_id):
    row = db.one(conn, "SELECT frame_path FROM events WHERE run_id=? AND event_id=?", run_id, event_id)
    p = row["frame_path"] if row else None
    return p if p and Path(p).exists() else None


def _markers(comp):
    m = [{"t": k.start, "label": f"Key {k.violation_type} #{k.instance_no}", "kind": "key"}
         for k in comp["keys"] if k.start is not None]
    mc = comp["settings"].monitoring_counts_as_alert
    for e in comp["events"]:
        t = e.alert_time(mc)
        if t is not None:
            m.append({"t": t, "label": f"{e.violation_type} {e.highest_status} {e.event_id}", "kind": "ev"})
    return sorted(m, key=lambda x: x["t"])


def render_run(conn, run_id, ro, key_prefix="rv"):
    comp = matching.compute_run(conn, run_id, persist=not ro)
    run, clip, res = comp["run"], comp["clip"], comp["result"]
    snap = json.loads(run["snapshot_json"]) if run.get("snapshot_json") else {}
    st.markdown(f"**{clip['clip_id']}** · {clip['set']} · {clip['distance']} · {clip['time_of_day']} — run `{run_id}` "
                f"({run['status']}, {(run['processing_time_sec'] or 0):.0f} s, {(run['processing_fps'] or 0):.2f} fps, "
                f"blocked DB writes: {run.get('blocked_writes') or 0})")
    if ro:
        st.info("Read-only: this is not the latest run of the clip.")
    st.success(matching.summary_line(comp))
    if db.rerun_needed(conn, clip["clip_id"]):
        st.warning("Re-run needed — detection settings changed after this run: " + "; ".join(db.rerun_reasons(conn, clip["clip_id"])))
    with st.expander("Settings snapshot of this run"):
        st.json(snap, expanded=False)

    seek = st.session_state.get(f"{key_prefix}_seek")
    player(clip, markers=_markers(comp), seek=seek, seek_nonce=st.session_state.get(f"{key_prefix}_nonce", 0),
           key=f"{key_prefix}_pl_{run_id}")

    # ---- answer-key rows table -----------------------------------------------------------------
    st.subheader("Answer-key rows")
    table = []
    for r in res.rows:
        table.append({"Row": f"{r.key.violation_type} #{r.key.instance_no}", "Kind": r.key.kind,
                      "Start": fmt_t(r.key.start), "Waiting (s)": r.waiting, "Deadline": fmt_t(r.deadline),
                      "Matched event": r.event_id or "", "Alert time": fmt_t(r.alert_time), "Status": r.status or "",
                      "Delay (s)": None if r.delay is None else round(r.delay, 1), "AI": r.ai_verdict or "",
                      "Outcome": f"{OUTCOME_ICON.get(r.outcome, '')} {r.outcome}", "Alerted instead": r.alerted_instead or "",
                      "Manual": "✋" if r.manual else "", "Explanation": r.explanation})
    st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)

    counting = {e.event_id: e for e in comp["events"] if e.counts(comp["settings"].monitoring_counts_as_alert)}
    for r in res.rows:
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 3, 3])
            c1.markdown(f"{OUTCOME_ICON.get(r.outcome, '')} **{r.key.violation_type} #{r.key.instance_no}** — {r.key.kind} "
                        f"@ {fmt_t(r.key.start)}  \n{r.explanation}")
            if r.key.who:
                c1.caption(r.key.who)
            if r.event_id and (fp := _frame(conn, run_id, r.event_id)):
                c1.image(fp, width=260)
            options = [NONE_OPT] + [f"{eid} · {fmt_t(e.alert_time(comp['settings'].monitoring_counts_as_alert))} · {e.highest_status}"
                                    for eid, e in counting.items() if e.violation_type == r.key.violation_type]
            cur = NONE_OPT if r.event_id is None else next((o for o in options if o.startswith(r.event_id)), NONE_OPT)
            choice = c2.selectbox("Matched alert (reviewer can reassign)", options, index=options.index(cur), disabled=ro,
                                  key=f"{key_prefix}_m_{run_id}_{r.key.id}")
            if choice != cur and not ro:
                matching.assign(conn, run_id, r.key.id, None if choice == NONE_OPT else choice.split(" · ")[0])
                st.rerun()
            if r.manual and not ro and c2.button("Back to automatic", key=f"{key_prefix}_auto_{run_id}_{r.key.id}"):
                matching.clear_manual(conn, run_id, r.key.id)
                st.rerun()
            rv = comp["review"].get(r.key.id) or {}
            if r.outcome in ("FN", "FP"):
                opts = [""] + config.MISS_REASONS
                mr = c3.selectbox("Miss / FP reason (required)", opts, index=opts.index(rv.get("miss_reason") or ""),
                                  disabled=ro, key=f"{key_prefix}_r_{run_id}_{r.key.id}")
                if mr != (rv.get("miss_reason") or "") and not ro:
                    matching.set_row_review(conn, run_id, r.key.id, miss_reason=mr or None)
                    st.rerun()
            notes = c3.text_input("Notes", rv.get("notes") or "", disabled=ro, key=f"{key_prefix}_n_{run_id}_{r.key.id}")
            if notes != (rv.get("notes") or "") and not ro:
                matching.set_row_review(conn, run_id, r.key.id, notes=notes)

    # ---- unmatched alerts ----------------------------------------------------------------------
    st.subheader("Unmatched alerts")
    if comp["result"].non_counting:
        st.caption(f"{len(comp['result'].non_counting)} event(s) never reached a counting status (Monitoring only) — shown on the "
                   "timeline but never scored.")
    if not res.unmatched:
        st.write("None.")
    elif not ro and st.button("Accept all suggestions", key=f"{key_prefix}_acc_{run_id}"):
        n = matching.accept_all_suggestions(conn, run_id)
        st.toast(f"Accepted {n} suggestion(s).")
        st.rerun()
    for u in res.unmatched:
        eid = u.event.event_id
        tr = comp["tag_rows"].get(eid) or {}
        with st.container(border=True):
            c0, c1, c2, c3 = st.columns([2, 3, 3, 3])
            if fp := _frame(conn, run_id, eid):
                c0.image(fp, use_container_width=True)
            c1.markdown(f"{OUTCOME_ICON.get(u.outcome, '')} **{u.event.violation_type}** `{eid}`  \nat {fmt_t(u.alert_time)} · "
                        f"{u.event.highest_status} · AI: {u.event.ai_verdict}  \nSuggested tag: **{u.suggested_tag}**  \nOutcome: **{u.outcome}**")
            opts = [""] + config.TAGS
            tag = c2.selectbox("Tag", opts, index=opts.index(u.tag or ""), disabled=ro, key=f"{key_prefix}_t_{run_id}_{eid}")
            if tag != (u.tag or "") and not ro:
                matching.set_tag(conn, run_id, eid, tag)
                st.rerun()
            who = c2.text_input("Who", tr.get("who") or "", disabled=ro, key=f"{key_prefix}_w_{run_id}_{eid}")
            if who != (tr.get("who") or "") and not ro:
                matching.set_tag(conn, run_id, eid, u.tag, who=who)
            if u.outcome == "FP":
                mopts = [""] + config.MISS_REASONS
                mr = c3.selectbox("Miss / FP reason (required)", mopts, index=mopts.index(tr.get("miss_reason") or ""),
                                  disabled=ro, key=f"{key_prefix}_mr_{run_id}_{eid}")
                if mr != (tr.get("miss_reason") or "") and not ro:
                    matching.set_tag(conn, run_id, eid, u.tag, miss_reason=mr)
                    st.rerun()
            nt = c3.text_input("Notes", tr.get("notes") or "", disabled=ro, key=f"{key_prefix}_nn_{run_id}_{eid}")
            if nt != (tr.get("notes") or "") and not ro:
                matching.set_tag(conn, run_id, eid, u.tag, notes=nt)

    # ---- review sign-off -------------------------------------------------------------------------
    st.subheader("Sign-off")
    blockers = matching.review_blockers(conn, run_id)
    if run["reviewed"]:
        st.success(f"Reviewed by {run['reviewer']}.")
        if not ro and st.button("Re-open review", key=f"{key_prefix}_reopen_{run_id}"):
            matching.unmark_reviewed(conn, run_id)
            st.rerun()
    elif not ro:
        if blockers:
            st.warning("Before this run can be marked reviewed:\n\n" + "\n".join(f"- {b}" for b in blockers))
        name = st.text_input("Reviewer name", st.session_state.get("reviewer", ""), key=f"{key_prefix}_rev_{run_id}")
        st.session_state["reviewer"] = name
        rn = st.text_area("Review notes", key=f"{key_prefix}_rnotes_{run_id}")
        if st.button("Mark reviewed", type="primary", disabled=bool(blockers) or not name.strip(), key=f"{key_prefix}_mark_{run_id}"):
            matching.mark_reviewed(conn, run_id, name, rn)
            st.rerun()
    return comp


def page_review(conn, goto, PAGES):
    st.header("Review")
    clips = [c for c in db.list_clips(conn) if db.latest_run(conn, c["clip_id"], "done")]
    if not clips:
        st.info("No finished runs yet. Queue a clip first.")
        return
    ids = [c["clip_id"] for c in clips]
    pre = st.session_state.pop("review_clip", None)
    if pre in ids:
        st.session_state["review_clip_box"] = pre                  # jump here from History
    if st.session_state.get("review_clip_box") not in ids:
        st.session_state["review_clip_box"] = ids[0]
    cid = st.selectbox("Clip", ids, key="review_clip_box")
    runs = db.rows(conn, "SELECT * FROM runs WHERE clip_id=? AND status='done' ORDER BY queued_at DESC, run_id DESC", cid)
    latest_any = db.latest_run(conn, cid)
    pre_run = st.session_state.pop("review_run", None)
    run_ids = [r["run_id"] for r in runs]
    labels = {r["run_id"]: f"{r['run_id']} · {r['queued_at']}" + (" · reviewed" if r["reviewed"] else "") for r in runs}
    if pre_run in run_ids:
        st.session_state["review_run_box"] = pre_run
    if st.session_state.get("review_run_box") not in run_ids:
        st.session_state["review_run_box"] = run_ids[0]
    rid = st.selectbox("Run", run_ids, format_func=lambda x: labels[x], key="review_run_box")
    ro = rid != latest_any["run_id"]
    render_run(conn, rid, ro)


# ======================================================================================
# History
# ======================================================================================

def _flatten(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}{k}."))
    elif isinstance(d, list):
        out[prefix.rstrip(".")] = json.dumps(d, default=str)
    else:
        out[prefix.rstrip(".")] = d
    return out


VOLATILE = ("worker_inputs.", "snapshot_json")


def compare_runs(conn, a_id, b_id):
    a, b = matching.compute_run(conn, a_id, persist=False), matching.compute_run(conn, b_id, persist=False)
    st.markdown(f"**A** `{a_id}` vs **B** `{b_id}`")
    ra = {r.key.id: r for r in a["result"].rows}
    rb = {r.key.id: r for r in b["result"].rows}
    changes = []
    for kid in sorted(set(ra) | set(rb)):
        x, y = ra.get(kid), rb.get(kid)
        label = f"{(x or y).key.violation_type} @ {fmt_t((x or y).key.start)}"
        ox, oy = x.outcome if x else "(absent)", y.outcome if y else "(absent)"
        if ox != oy:
            changes.append(f"- **{label}**: {ox} → {oy}")
        elif x and y and (x.alert_time != y.alert_time):
            changes.append(f"- {label}: same outcome ({ox}) but alert time {fmt_t(x.alert_time)} → {fmt_t(y.alert_time)}")
    st.markdown("**Outcome changes per row**")
    st.markdown("\n".join(changes) if changes else "No row changed.")

    def sig(comp):
        mc = comp["settings"].monitoring_counts_as_alert
        return {(e.violation_type, round(e.alert_time(mc), 1), e.highest_status) for e in comp["events"] if e.counts(mc)}
    sa, sb = sig(a), sig(b)
    st.markdown("**Event differences**")
    only_a, only_b = sorted(sa - sb), sorted(sb - sa)
    st.write(f"{len(sa)} counting events in A, {len(sb)} in B; {len(sa & sb)} identical.")
    if only_a:
        st.write("Only in A:", ", ".join(f"{t} {fmt_t(x)} {s}" for t, x, s in only_a))
    if only_b:
        st.write("Only in B:", ", ".join(f"{t} {fmt_t(x)} {s}" for t, x, s in only_b))
    sna = _flatten(json.loads(a["run"]["snapshot_json"] or "{}"))
    snb = _flatten(json.loads(b["run"]["snapshot_json"] or "{}"))
    diff = [{"Setting": k, "A": sna.get(k), "B": snb.get(k)} for k in sorted(set(sna) | set(snb))
            if sna.get(k) != snb.get(k) and not k.startswith(VOLATILE)]
    st.markdown("**Settings snapshot diff**")
    st.dataframe(pd.DataFrame(diff), hide_index=True, use_container_width=True) if diff else st.write("Identical settings.")


def page_history(conn, goto, PAGES):
    st.header("History")
    st.caption("Every clip and every run ever made. Runs are never deleted; re-running adds a new run.")
    runs = db.rows(conn, "SELECT r.*, c.planned_focus, c.\"set\" AS cset, c.distance, c.time_of_day, c.date_recorded "
                         "FROM runs r JOIN clips c ON c.clip_id=r.clip_id ORDER BY r.queued_at DESC")
    recs = []
    for r in runs:
        types, outcomes, summary = set(), set(), ""
        if r["status"] == "done":
            comp = matching.compute_run(conn, r["run_id"], persist=False)
            types = {x.key.violation_type for x in comp["result"].rows} | {u.event.violation_type for u in comp["result"].unmatched}
            outcomes = {x.outcome for x in comp["result"].rows} | {u.outcome for u in comp["result"].unmatched}
            summary = matching.summary_line(comp)
        recs.append(dict(r, types=types, outcomes=outcomes, summary=summary))
    f1, f2, f3, f4, f5 = st.columns(5)
    q = f1.text_input("Search (clip / run / notes)")
    ftype = f2.multiselect("Type", config.TYPES)
    fout = f3.multiselect("Outcome", ["TP", "FN", "TN", "FP", "Duplicate", "Late", "Excluded", "Needs tag", "Needs start"])
    fset = f4.multiselect("Set", config.SETS)
    frev = f5.selectbox("Reviewed", ["any", "reviewed", "not reviewed"])
    g1, g2, g3 = st.columns(3)
    fdist = g1.multiselect("Distance", config.DISTANCES)
    ftod = g2.multiselect("Time of day", config.TIMES_OF_DAY)
    dates = g3.text_input("Run date contains (e.g. 2026-10-17)")

    def keep(r):
        if q and q.lower() not in f"{r['clip_id']} {r['run_id']} {r.get('error') or ''} {r['summary']}".lower():
            return False
        if ftype and not (set(ftype) & r["types"]):
            return False
        if fout and not (set(fout) & r["outcomes"]):
            return False
        if fset and r["cset"] not in fset:
            return False
        if frev != "any" and bool(r["reviewed"]) != (frev == "reviewed"):
            return False
        if fdist and r["distance"] not in fdist:
            return False
        if ftod and r["time_of_day"] not in ftod:
            return False
        if dates and dates not in (r["queued_at"] or ""):
            return False
        return True
    view = [r for r in recs if keep(r)]
    st.dataframe(pd.DataFrame([{"Run": r["run_id"], "Clip": r["clip_id"], "Focus": r["planned_focus"], "Set": r["cset"],
                                "Distance": r["distance"], "Time": r["time_of_day"], "Queued": r["queued_at"],
                                "Status": r["status"], "Reviewed": "Yes" if r["reviewed"] else "No",
                                "Result": r["summary"]} for r in view]), hide_index=True, use_container_width=True)
    if view:
        pick = st.selectbox("Open a run", [r["run_id"] for r in view], key="hist_open")
        if st.button("Open in Review"):
            r = db.get_run(conn, pick)
            if r["status"] != "done":
                st.warning("Only finished runs have a review page.")
            else:
                goto(PAGES[4], review_clip=r["clip_id"], review_run=pick)
                st.rerun()
    st.subheader("Compare two runs of the same clip")
    clip_ids = sorted({r["clip_id"] for r in recs if r["status"] == "done"})
    if clip_ids:
        cid = st.selectbox("Clip", clip_ids, key="cmp_clip")
        done = [r["run_id"] for r in recs if r["clip_id"] == cid and r["status"] == "done"]
        if len(done) < 2:
            st.info("This clip has only one finished run.")
        else:
            c1, c2 = st.columns(2)
            a = c1.selectbox("Run A", done, index=min(1, len(done) - 1), key="cmp_a")
            b = c2.selectbox("Run B", done, index=0, key="cmp_b")
            if a != b:
                compare_runs(conn, a, b)
