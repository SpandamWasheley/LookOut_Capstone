"""Summary, Settings and Export pages."""
from pathlib import Path

import pandas as pd
import streamlit as st

import config
import db
import exporter
import matching
import scoring

HEAD = {"real_acts": "Real acts scored", "tp": "TP", "fn": "FN", "tn": "TN", "fp_look": "FP (look-alike / no violation)",
        "fp_actor": "FP (false alarm on actors)", "fp_unscripted": "FP (unscripted)", "fp_total": "FP total",
        "accuracy": "Accuracy", "precision": "Precision", "recall": "Recall", "f1": "F1", "avg_delay": "Avg delay (s)",
        "duplicates": "Duplicates", "late": "Late detections", "unscripted_real": "Unscripted real (excluded)", "needs": "Needs tag"}
PCT = {"accuracy", "precision", "recall", "f1"}


def _fmt(field, v):
    if v is None:
        return "–"
    if field in PCT:
        return f"{v * 100:.1f}%"
    if field == "avg_delay":
        return f"{v:.1f}"
    return int(v)


def metrics_frame(agg):
    rows = []
    for t in scoring.TYPES + ["OVERALL"]:
        rows.append({"Violation": t, **{HEAD[f]: _fmt(f, agg[t][f]) for f in HEAD}})
    return pd.DataFrame(rows)


def confusion(records):
    cols = scoring.TYPES + ["None"]
    m = {t: {c: 0 for c in cols} for t in scoring.TYPES}
    for r in records:
        if r["row_type"] != "Expected" or r["kind"] != "Violation":
            continue
        if r["outcome"] == "TP":
            m[r["type"]][r["type"]] += 1
        elif r["outcome"] == "FN":
            col = r["alerted_instead"] if r["alerted_instead"] in scoring.TYPES else "None"
            m[r["type"]][col] += 1
    df = pd.DataFrame(m).T[cols]
    df["Total"] = df.sum(axis=1)
    df.index.name = "Actual \\ Alerted"
    return df


def breakdown(records, field):
    values = config.DISTANCES if field == "distance" else config.TIMES_OF_DAY
    rows = []
    for v in values:
        sub = [r for r in records if r[field] == v]
        agg = scoring.aggregate(sub)["OVERALL"]
        rows.append({field.replace("_", " ").title(): v, "Real acts": agg["real_acts"], "TP": agg["tp"], "FN": agg["fn"],
                     "TN": agg["tn"], "FP": agg["fp_total"], "Accuracy": _fmt("accuracy", agg["accuracy"]),
                     "Precision": _fmt("precision", agg["precision"]), "Recall": _fmt("recall", agg["recall"])})
    return pd.DataFrame(rows)


def page_summary(conn):
    st.header("Summary (live preview of the template's metrics)")
    reviewed_only = st.toggle("Reviewed clips only", value=True)
    runs = matching.eligible_runs(conn, reviewed_only)
    st.caption(f"{len(runs)} clip(s) included — latest done run per clip.")
    records = matching.all_flat(conn, reviewed_only)
    if not records:
        st.info("No results to show yet." + (" Mark a run as reviewed, or switch off 'reviewed only'." if reviewed_only else ""))
        return
    for set_name, title in (("Main", "Main set"), ("Validation", "Validation set (real thresholds)")):
        st.subheader(title)
        sub = [r for r in records if r["set"] == set_name]
        if not sub:
            st.write("No data.")
            continue
        st.dataframe(metrics_frame(scoring.aggregate(sub)), hide_index=True, use_container_width=True)
        if set_name == "Main":
            c1, c2 = st.columns(2)
            c1.markdown("**By distance**")
            c1.dataframe(breakdown(sub, "distance"), hide_index=True, use_container_width=True)
            c2.markdown("**By time of day**")
            c2.dataframe(breakdown(sub, "time_of_day"), hide_index=True, use_container_width=True)
        st.markdown("**Confusion matrix** (rows = real violation, columns = what the system alerted)")
        st.dataframe(confusion(sub), use_container_width=True)


def page_settings(conn):
    st.header("Settings")
    es = db.get_eval_settings(conn)
    scored = db.any_scored_run(conn)
    if scored:
        st.warning("Results already scored will be re-scored; detection thresholds changed → affected clips must be re-run.")
    with st.form("evalset"):
        c1, c2, c3 = st.columns(3)
        win = c1.number_input("Alert window (s)", 0.0, 3600.0, float(es["alert_window_sec"]), 1.0)
        tol = c2.number_input("Start tolerance (s)", 0.0, 120.0, float(es["start_tolerance_sec"]), 1.0)
        mon = c3.checkbox("Count 'Monitoring' as an alert", bool(es["monitoring_counts_as_alert"]))
        st.markdown("**Detection thresholds** (changing these requires re-running affected clips)")
        d1, d2, d3, d4 = st.columns(4)
        pm = d1.number_input("Parking — Main (s)", 1.0, 7200.0, float(es["parking_threshold_main_sec"]), 10.0)
        pv = d2.number_input("Parking — Validation (s)", 1.0, 7200.0, float(es["parking_threshold_validation_sec"]), 10.0)
        dm = d3.number_input("Inuman duration — Main (s)", 1.0, 7200.0, float(es["drinking_group_duration_main_sec"]), 10.0)
        dv = d4.number_input("Inuman duration — Validation (s)", 1.0, 7200.0, float(es["drinking_group_duration_validation_sec"]), 10.0)
        if st.form_submit_button("Save settings", type="primary"):
            changed = db.update_eval_settings(conn, dict(
                alert_window_sec=win, start_tolerance_sec=tol, monitoring_counts_as_alert=mon,
                parking_threshold_main_sec=pm, parking_threshold_validation_sec=pv,
                drinking_group_duration_main_sec=dm, drinking_group_duration_validation_sec=dv))
            st.success("Saved. Scoring-only settings re-score instantly.")
            if changed:
                affected = [c["clip_id"] for c in db.list_clips(conn) if db.rerun_needed(conn, c["clip_id"])]
                st.warning("Detection thresholds changed (" + ", ".join(changed) + "). Re-run needed for: "
                           + (", ".join(affected) if affected else "no finished clips"))
    st.subheader("Clips that need a re-run")
    need = [c["clip_id"] for c in db.list_clips(conn) if db.rerun_needed(conn, c["clip_id"])]
    st.write(", ".join(need) if need else "None.")


def page_export(conn):
    st.header("Export to the Excel template")
    reviewed_only = st.toggle("Reviewed clips only", value=True, key="exp_rev")
    st.caption("A COPY of templates/LookOut_Alpha_Testing_Template.xlsx is filled (values only). The template is never changed.")
    soffice = exporter.find_soffice()
    st.write(f"Recalculation engine: **{'LibreOffice' if soffice else 'Python `formulas` package (LibreOffice not found)'}**")
    if st.button("Export now", type="primary"):
        try:
            with st.spinner("Writing the workbook and recalculating (can take a minute)…"):
                res = exporter.build_export(conn, reviewed_only=reviewed_only)
        except exporter.ExportError as exc:
            st.error(str(exc))
            return
        if res["final"]:
            st.success(f"Exported {res['n_clips']} clip(s), {res['n_rows']} rows → {res['path']}")
        else:
            st.error(f"NOT FINAL — {'; '.join(res.get('why_not_final', []))}")
            for m in res["mismatches"]:
                st.error("MISMATCH " + m)
            for d in res["preview_diffs"][:20]:
                st.warning(d)
            st.write(f"File: {res['path']}")
        st.write(f"Recalculated with: {res['method']}.  Settings consistency: **{res['consistency']}**.")
        for p in res["problems"]:
            st.warning(p)
        if Path(res["path"]).exists():
            st.download_button("Download", Path(res["path"]).read_bytes(), file_name=Path(res["path"]).name)
    st.subheader("Past exports")
    ex = db.rows(conn, "SELECT * FROM exports ORDER BY id DESC")
    if ex:
        st.dataframe(pd.DataFrame([{"When": e["created_at"], "File": Path(e["path"]).name, "Final": "Yes" if e["final"] else "NOT FINAL",
                                    "Mismatches": e["mismatches"], "Clips": e["n_clips"], "Basis": e["basis"], "Note": e["note"]}
                                   for e in ex]), hide_index=True, use_container_width=True)
    else:
        st.write("None yet.")
