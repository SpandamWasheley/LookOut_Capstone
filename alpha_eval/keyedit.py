"""Applies the answer-key table edits (from the Streamlit data editor) to eval.db."""
import math

import db
import scoring


def _blank(v):
    return v is None or (isinstance(v, float) and math.isnan(v)) or str(v).strip() in ("", "None", "nan", "<NA>")


def apply_edits(conn, clip_id, records):
    """records: dicts with keys id, Type, Kind, 'Start (mm:ss.s)', 'Who / description'. Returns a list of error strings
    (nothing is written when there is any error). Rows missing from `records` are deleted; rows without an id are added;
    rows whose type/kind/start/who changed are updated (flagging 'added after run' when a run already started)."""
    existing = {k["id"]: k for k in db.key_rows(conn, clip_id)}
    ops, errs, seen = [], [], set()
    for r in records:
        if _blank(r.get("Type")) or _blank(r.get("Kind")):
            continue
        raw = r.get("Start (mm:ss.s)")
        try:
            start = None if _blank(raw) else scoring.parse_time(raw)
        except ValueError as exc:
            errs.append(f"{r['Type']}: {exc}")
            continue
        who = "" if _blank(r.get("Who / description")) else str(r["Who / description"])
        rid = None if _blank(r.get("id")) else int(r["id"])
        ops.append((rid, r["Type"], r["Kind"], start, who))
        if rid:
            seen.add(rid)
    if errs:
        return errs
    for kid in set(existing) - seen:
        db.delete_key_row(conn, kid)
    for rid, t, kd, start, who in ops:
        if rid is None or rid not in existing:
            db.add_key_row(conn, clip_id, t, kd, start, who)
        else:
            o = existing[rid]
            if (o["violation_type"], o["kind"], o["action_start_sec"], o["who"] or "") != (t, kd, start, who):
                db.update_key_row(conn, rid, violation_type=t, kind=kd, action_start_sec=start, who=who)
    return []
