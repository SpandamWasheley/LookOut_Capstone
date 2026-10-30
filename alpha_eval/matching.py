"""Glue between eval.db and scoring.py: scores a stored run, applies the reviewer's manual decisions,
and produces the flat records the UI, the summary preview and the Excel export all use."""
import db
import scoring


def compute_run(conn, run_id, persist=True):
    """Score a run with the clip's CURRENT answer key and the CURRENT eval settings.

    Returns a dict: run, clip, settings, keys, events, result (scoring.ClipResult), tags, review.
    """
    run = db.get_run(conn, run_id)
    clip = db.get_clip(conn, run["clip_id"])
    settings = db.scoring_settings(conn)
    key_rows = db.key_rows(conn, clip["clip_id"])
    ev_rows = db.run_events(conn, run_id)
    keys = db.as_key_objects(key_rows)
    events = db.as_event_objects(ev_rows)
    manual = {m["answer_key_id"]: m["event_id"] for m in
              db.rows(conn, "SELECT * FROM matches WHERE run_id=? AND manual=1", run_id)}
    tag_rows = {t["event_id"]: t for t in db.rows(conn, "SELECT * FROM unmatched_tags WHERE run_id=?", run_id)}
    tags = {eid: t["tag"] for eid, t in tag_rows.items() if t.get("tag")}
    result = scoring.score_clip(keys, events, clip["set"], settings, manual=manual, tags=tags,
                                clip_duration=clip.get("duration_sec"))
    if persist:
        _persist(conn, run_id, result, tag_rows)
        tag_rows = {t["event_id"]: t for t in db.rows(conn, "SELECT * FROM unmatched_tags WHERE run_id=?", run_id)}
    review = {r["answer_key_id"]: r for r in db.rows(conn, "SELECT * FROM row_review WHERE run_id=?", run_id)}
    return dict(run=run, clip=clip, settings=settings, key_rows=key_rows, keys=keys, ev_rows=ev_rows,
                events=events, result=result, tag_rows=tag_rows, review=review)


def _persist(conn, run_id, result, old_tag_rows):
    conn.execute("DELETE FROM matches WHERE run_id=? AND manual=0", (run_id,))
    manual_keys = {r["answer_key_id"] for r in db.rows(conn, "SELECT answer_key_id FROM matches WHERE run_id=?", (run_id))}
    for r in result.rows:
        if r.key.id not in manual_keys and r.event_id is not None:
            conn.execute("INSERT INTO matches (run_id, answer_key_id, event_id, manual) VALUES (?,?,?,0)",
                         (run_id, r.key.id, r.event_id))
    live = {u.event.event_id for u in result.unmatched}
    for u in result.unmatched:
        if u.event.event_id in old_tag_rows:
            conn.execute("UPDATE unmatched_tags SET suggested_tag=? WHERE run_id=? AND event_id=?",
                         (u.suggested_tag, run_id, u.event.event_id))
        else:
            conn.execute("INSERT INTO unmatched_tags (run_id, event_id, suggested_tag) VALUES (?,?,?)",
                         (run_id, u.event.event_id, u.suggested_tag))
    for eid in old_tag_rows:
        if eid not in live:                       # now matched to a row (or gone): no tag needed any more
            conn.execute("DELETE FROM unmatched_tags WHERE run_id=? AND event_id=?", (run_id, eid))
    conn.commit()


# ---- reviewer actions -------------------------------------------------------------------------------

def assign(conn, run_id, key_id, event_id):
    """Reviewer assigns `event_id` to answer-key row `key_id` (or unassigns with None). Always manual.
    The event must be of the same type as the row."""
    key = db.one(conn, "SELECT * FROM answer_key WHERE id=?", key_id)
    if event_id is not None:
        ev = db.one(conn, "SELECT * FROM events WHERE run_id=? AND event_id=?", run_id, event_id)
        if ev is None or ev["violation_type"] != key["violation_type"]:
            raise ValueError("an event can only be assigned to a row of the same violation type")
        # the event leaves whichever manual row had it
        conn.execute("DELETE FROM matches WHERE run_id=? AND event_id=? AND manual=1 AND answer_key_id<>?",
                     (run_id, event_id, key_id))
    conn.execute("INSERT OR REPLACE INTO matches (run_id, answer_key_id, event_id, manual) VALUES (?,?,?,1)",
                 (run_id, key_id, event_id))
    conn.commit()


def clear_manual(conn, run_id, key_id):
    conn.execute("DELETE FROM matches WHERE run_id=? AND answer_key_id=? AND manual=1", (run_id, key_id))
    conn.commit()


def set_tag(conn, run_id, event_id, tag, who=None, miss_reason=None, notes=None):
    conn.execute("INSERT OR IGNORE INTO unmatched_tags (run_id, event_id) VALUES (?,?)", (run_id, event_id))
    conn.execute("UPDATE unmatched_tags SET tag=?, who=COALESCE(?, who), miss_reason=COALESCE(?, miss_reason),"
                 " notes=COALESCE(?, notes) WHERE run_id=? AND event_id=?",
                 (tag or None, who, miss_reason, notes, run_id, event_id))
    conn.commit()


def accept_all_suggestions(conn, run_id):
    comp = compute_run(conn, run_id)
    n = 0
    for u in comp["result"].unmatched:
        if not u.tag:
            set_tag(conn, run_id, u.event.event_id, u.suggested_tag)
            n += 1
    return n


def set_row_review(conn, run_id, key_id, miss_reason=None, notes=None):
    conn.execute("INSERT OR IGNORE INTO row_review (run_id, answer_key_id) VALUES (?,?)", (run_id, key_id))
    conn.execute("UPDATE row_review SET miss_reason=COALESCE(?, miss_reason), notes=COALESCE(?, notes)"
                 " WHERE run_id=? AND answer_key_id=?", (miss_reason, notes, run_id, key_id))
    conn.commit()


def review_blockers(conn, run_id):
    """Reasons the run cannot be marked reviewed yet (empty list = ready)."""
    comp = compute_run(conn, run_id)
    out = []
    for r in comp["result"].rows:
        label = f"{r.key.violation_type} row #{r.key.instance_no}"
        if r.outcome == "Needs start":
            out.append(f"{label}: type the action start time")
        if r.outcome in ("FN", "FP") and not (comp["review"].get(r.key.id) or {}).get("miss_reason"):
            out.append(f"{label} is {r.outcome}: choose a miss / FP reason")
    for u in comp["result"].unmatched:
        label = f"{u.event.violation_type} alert {u.event.event_id} at {scoring.fmt_time(u.alert_time)}"
        if not u.tag:
            out.append(f"{label}: confirm a tag")
        elif u.outcome == "FP" and not (comp["tag_rows"].get(u.event.event_id) or {}).get("miss_reason"):
            out.append(f"{label} is FP: choose a miss / FP reason")
    return out


def mark_reviewed(conn, run_id, reviewer, notes=""):
    blockers = review_blockers(conn, run_id)
    if blockers:
        raise ValueError("; ".join(blockers))
    if not reviewer.strip():
        raise ValueError("enter the reviewer's name")
    conn.execute("UPDATE runs SET reviewed=1, reviewer=?, review_notes=? WHERE run_id=?", (reviewer.strip(), notes, run_id))
    conn.commit()


def unmark_reviewed(conn, run_id):
    conn.execute("UPDATE runs SET reviewed=0 WHERE run_id=?", (run_id,))
    conn.commit()


# ---- summaries ---------------------------------------------------------------------------------------

def summary_line(comp):
    res = comp["result"]
    parts = []
    by_type = {}
    for r in res.rows:
        by_type.setdefault(r.key.violation_type, []).append(r.outcome)
    for t in scoring.TYPES:
        if t in by_type:
            o = by_type[t]
            bits = [f"{o.count(x)} {x}" for x in ("TP", "FN", "TN", "FP", "Needs start") if o.count(x)]
            parts.append(f"{t} " + ", ".join(bits))
    for u in res.unmatched:
        if u.outcome == "FP":
            kind = " (unscripted)" if u.tag == scoring.TAG_UNSCRIPTED_FP else ""
            parts.append(f"{u.event.violation_type} 1 FP{kind}")
        elif u.outcome == "Excluded":
            parts.append(f"{u.event.violation_type} unscripted real (excluded)")
        elif u.outcome == "Late":
            parts.append(f"{u.event.violation_type} 1 late")
    dups = sum(1 for u in res.unmatched if u.outcome == "Duplicate")
    needs = sum(1 for u in res.unmatched if u.outcome == "Needs tag")
    tail = f"{dups} duplicates" + (f" · {needs} need a tag" if needs else "")
    return " · ".join(parts + [tail]) if parts else tail


def flat_records(comp):
    """One dict per Results-Log row of this run, in export order (answer key first, then unmatched by time)."""
    clip, run = comp["clip"], comp["run"]
    base = dict(clip_id=clip["clip_id"], set=clip["set"], distance=clip.get("distance"),
                time_of_day=clip.get("time_of_day"), run_id=run["run_id"])
    out = []
    ev_by = {e["event_id"]: e for e in comp["ev_rows"]}
    for r in comp["result"].rows:
        rv = comp["review"].get(r.key.id) or {}
        out.append(dict(base, row_type="Expected", type=r.key.violation_type, kind=r.key.kind,
                        instance_no=r.key.instance_no, who=r.key.who, start=r.key.start, waiting=r.waiting,
                        deadline=r.deadline, event_id=r.event_id, alert_time=r.alert_time, status=r.status,
                        delay=r.delay, ai=r.ai_verdict, tag=None, outcome=r.outcome,
                        alerted_instead=r.alerted_instead, added_after_run=bool(r.key.added_after_run),
                        miss_reason=rv.get("miss_reason"), notes=rv.get("notes"), key_id=r.key.id,
                        explanation=r.explanation))
    for u in sorted(comp["result"].unmatched, key=lambda u: (u.alert_time, u.event.event_id)):
        tr = comp["tag_rows"].get(u.event.event_id) or {}
        out.append(dict(base, row_type="Unmatched alert", type=u.event.violation_type, kind=None, instance_no=None,
                        who=tr.get("who"), start=None, waiting=None, deadline=None, event_id=u.event.event_id,
                        alert_time=u.alert_time, status=u.event.highest_status, delay=None,
                        ai=u.event.ai_verdict, tag=u.tag, outcome=u.outcome, alerted_instead=None,
                        added_after_run=None, miss_reason=tr.get("miss_reason"), notes=tr.get("notes"),
                        key_id=None, explanation=""))
    return out


def eligible_runs(conn, reviewed_only=True):
    """Latest done run per clip (reviewed only when asked)."""
    out = []
    for c in db.list_clips(conn):
        run = db.latest_run(conn, c["clip_id"], "done")
        if run and (run["reviewed"] or not reviewed_only):
            out.append(run)
    return out


def all_flat(conn, reviewed_only=True, set_name=None):
    recs = []
    for run in eligible_runs(conn, reviewed_only):
        comp = compute_run(conn, run["run_id"], persist=False)
        if set_name and comp["clip"]["set"] != set_name:
            continue
        recs.extend(flat_records(comp))
    return recs
