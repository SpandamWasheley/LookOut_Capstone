"""alpha_eval's own SQLite database (eval.db). Nothing here ever touches LookOut's database."""
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

import config
import scoring

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
  clip_id TEXT PRIMARY KEY, planned_focus TEXT, scenario TEXT, distance TEXT, time_of_day TEXT,
  "set" TEXT DEFAULT 'Main', in_template_plan INTEGER DEFAULT 0, recording_status TEXT DEFAULT 'Not recorded',
  video_path TEXT, video_sha256 TEXT, duration_sec REAL, fps REAL, resolution TEXT, date_recorded TEXT,
  recorded_time TEXT, min_length TEXT, notes TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS answer_key (
  id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id TEXT NOT NULL, violation_type TEXT NOT NULL, kind TEXT NOT NULL,
  instance_no INTEGER DEFAULT 1, who TEXT DEFAULT '', action_start_sec REAL, added_after_run INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, clip_id TEXT NOT NULL, status TEXT NOT NULL, queued_at TEXT, started_at TEXT,
  finished_at TEXT, frames_processed INTEGER, processing_fps REAL, processing_time_sec REAL, error TEXT,
  snapshot_json TEXT, reviewed INTEGER DEFAULT 0, reviewer TEXT, review_notes TEXT,
  set_at_run TEXT, parking_threshold REAL, drinking_duration REAL, clock TEXT, worker_pid INTEGER,
  attempt INTEGER DEFAULT 0, cancel_requested INTEGER DEFAULT 0, blocked_writes INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT NOT NULL, event_id TEXT NOT NULL, track_id TEXT, violation_type TEXT, internal_type TEXT,
  first_monitoring_sec REAL, first_possible_sec REAL, first_likely_sec REAL, highest_status TEXT,
  ai_verdict TEXT, ai_suggested_status TEXT, confidence REAL, frame_path TEXT, bbox TEXT, ai_badge TEXT,
  ai_observation TEXT, PRIMARY KEY (run_id, event_id)
);
CREATE TABLE IF NOT EXISTS matches (
  run_id TEXT NOT NULL, answer_key_id INTEGER NOT NULL, event_id TEXT, manual INTEGER DEFAULT 0,
  PRIMARY KEY (run_id, answer_key_id)
);
CREATE TABLE IF NOT EXISTS unmatched_tags (
  run_id TEXT NOT NULL, event_id TEXT NOT NULL, suggested_tag TEXT, tag TEXT, who TEXT, miss_reason TEXT, notes TEXT,
  PRIMARY KEY (run_id, event_id)
);
CREATE TABLE IF NOT EXISTS row_review (
  run_id TEXT NOT NULL, answer_key_id INTEGER NOT NULL, miss_reason TEXT, notes TEXT,
  PRIMARY KEY (run_id, answer_key_id)
);
CREATE TABLE IF NOT EXISTS eval_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  alert_window_sec REAL, start_tolerance_sec REAL, monitoring_counts_as_alert INTEGER,
  parking_threshold_main_sec REAL, parking_threshold_validation_sec REAL,
  drinking_group_duration_main_sec REAL, drinking_group_duration_validation_sec REAL
);
CREATE TABLE IF NOT EXISTS exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, path TEXT, final INTEGER, mismatches INTEGER,
  basis TEXT, n_clips INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def connect(path=None):
    path = str(path or config.EVAL_DB)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT 1 FROM eval_settings WHERE id=1").fetchone()
    if row is None:
        d = config.DEFAULT_EVAL_SETTINGS
        conn.execute("INSERT INTO eval_settings VALUES (1,?,?,?,?,?,?,?)",
                     (d["alert_window_sec"], d["start_tolerance_sec"], d["monitoring_counts_as_alert"],
                      d["parking_threshold_main_sec"], d["parking_threshold_validation_sec"],
                      d["drinking_group_duration_main_sec"], d["drinking_group_duration_validation_sec"]))
    conn.commit()
    return conn


def rows(conn, sql, *params):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn, sql, *params):
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


# ---- eval settings -------------------------------------------------------------------------------

def get_eval_settings(conn):
    return dict(conn.execute("SELECT * FROM eval_settings WHERE id=1").fetchone())


def scoring_settings(conn):
    return scoring.Settings.from_dict(get_eval_settings(conn))


DETECTION_FIELDS = ["parking_threshold_main_sec", "parking_threshold_validation_sec",
                    "drinking_group_duration_main_sec", "drinking_group_duration_validation_sec"]


def any_scored_run(conn):
    return one(conn, "SELECT 1 AS x FROM runs WHERE status='done' LIMIT 1") is not None


def update_eval_settings(conn, new):
    old = get_eval_settings(conn)
    cols = [c for c in old if c != "id"]
    vals = [float(new.get(c, old[c])) if c != "monitoring_counts_as_alert" else int(bool(new.get(c, old[c])))
            for c in cols]
    conn.execute(f"UPDATE eval_settings SET {', '.join(c + '=?' for c in cols)} WHERE id=1", vals)
    conn.commit()
    return [c for c in DETECTION_FIELDS if float(old[c]) != float(new.get(c, old[c]))]


def thresholds_for_set(conn, set_name):
    s = get_eval_settings(conn)
    suffix = "validation" if set_name == "Validation" else "main"
    return s[f"parking_threshold_{suffix}_sec"], s[f"drinking_group_duration_{suffix}_sec"]


# ---- clips ---------------------------------------------------------------------------------------

def upsert_clip(conn, clip_id, **fields):
    exists = one(conn, "SELECT clip_id FROM clips WHERE clip_id=?", clip_id)
    if exists is None:
        conn.execute("INSERT INTO clips (clip_id, created_at) VALUES (?,?)", (clip_id, now_iso()))
    for k, v in fields.items():
        col = '"set"' if k == "set" else k
        conn.execute(f"UPDATE clips SET {col}=? WHERE clip_id=?", (v, clip_id))
    conn.commit()


def get_clip(conn, clip_id):
    return one(conn, "SELECT * FROM clips WHERE clip_id=?", clip_id)


def list_clips(conn):
    return rows(conn, "SELECT * FROM clips ORDER BY in_template_plan DESC, rowid")


def import_template_clips(conn):
    """First start: planned clips from the template's Clips sheet, rows 5-59, columns A-G."""
    if one(conn, "SELECT 1 AS x FROM clips LIMIT 1") is not None or not config.TEMPLATE.exists():
        return 0
    import openpyxl
    ws = openpyxl.load_workbook(config.TEMPLATE)["Clips"]
    n = 0
    for r in range(config.PLANNED_ROWS[0], config.PLANNED_ROWS[1] + 1):
        cid = ws.cell(r, 1).value
        if not cid:
            continue
        upsert_clip(conn, str(cid), planned_focus=ws.cell(r, 2).value, scenario=ws.cell(r, 3).value,
                    distance=ws.cell(r, 4).value, time_of_day=ws.cell(r, 5).value, min_length=ws.cell(r, 6).value,
                    set=ws.cell(r, 7).value or "Main", in_template_plan=1,
                    recording_status=ws.cell(r, 8).value or "Not recorded")
        n += 1
    return n


def clip_has_started_run(conn, clip_id):
    return one(conn, "SELECT 1 AS x FROM runs WHERE clip_id=? AND started_at IS NOT NULL", clip_id) is not None


# ---- answer key ----------------------------------------------------------------------------------

def add_key_row(conn, clip_id, violation_type, kind, start, who="", instance_no=None):
    flag = int(scoring.should_flag_added_after_run(clip_has_started_run(conn, clip_id)))
    if instance_no is None:
        instance_no = (one(conn, "SELECT COUNT(*) AS n FROM answer_key WHERE clip_id=? AND violation_type=?",
                           clip_id, violation_type)["n"]) + 1
    cur = conn.execute(
        "INSERT INTO answer_key (clip_id, violation_type, kind, instance_no, who, action_start_sec, added_after_run,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (clip_id, violation_type, kind, instance_no, who, start, flag, now_iso(), now_iso()))
    conn.commit()
    return cur.lastrowid


def update_key_row(conn, key_id, **fields):
    old = one(conn, "SELECT * FROM answer_key WHERE id=?", key_id)
    if old is None:
        return
    changed_scoring = any(k in fields and fields[k] != old[k]
                          for k in ("violation_type", "kind", "action_start_sec"))
    if changed_scoring and clip_has_started_run(conn, old["clip_id"]):
        fields["added_after_run"] = 1
    fields["updated_at"] = now_iso()
    conn.execute(f"UPDATE answer_key SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                 [*fields.values(), key_id])
    conn.commit()


def delete_key_row(conn, key_id):
    conn.execute("DELETE FROM answer_key WHERE id=?", (key_id,))
    conn.execute("DELETE FROM matches WHERE answer_key_id=?", (key_id,))
    conn.execute("DELETE FROM row_review WHERE answer_key_id=?", (key_id,))
    conn.commit()


def key_rows(conn, clip_id):
    return rows(conn, "SELECT * FROM answer_key WHERE clip_id=? ORDER BY violation_type, "
                      "action_start_sec IS NULL, action_start_sec, id", clip_id)


def expected_violations(conn, clip_id):
    return one(conn, "SELECT COUNT(*) AS n FROM answer_key WHERE clip_id=? AND kind='Violation'", clip_id)["n"]


# ---- runs ----------------------------------------------------------------------------------------

def queue_run(conn, clip_id):
    """Queue a detection run. Refuses a clip that is already queued/running (never duplicates a run)."""
    clip = get_clip(conn, clip_id)
    if clip is None or not clip.get("video_path"):
        raise ValueError(f"{clip_id}: upload a video first")
    busy = one(conn, "SELECT run_id FROM runs WHERE clip_id=? AND status IN ('queued','running')", clip_id)
    if busy:
        return busy["run_id"]
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    park, drink = thresholds_for_set(conn, clip["set"])
    conn.execute(
        "INSERT INTO runs (run_id, clip_id, status, queued_at, set_at_run, parking_threshold, drinking_duration, clock)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (run_id, clip_id, "queued", now_iso(), clip["set"], park, drink, clip_clock(clip)))
    conn.execute("UPDATE clips SET recording_status=? WHERE clip_id=? AND recording_status='Not recorded'",
                 ("Recorded", clip_id))
    conn.commit()
    return run_id


def clip_clock(clip):
    """Footage start 'YYYY-MM-DD HH:MM' for --clock (DECISIONS D5). Empty when no recording date is known."""
    d = clip.get("date_recorded")
    if not d:
        return ""
    hhmm = clip.get("recorded_time") or config.DEFAULT_CLOCK.get(clip.get("time_of_day") or "Day", "10:00")
    return f"{str(d)[:10]} {hhmm}"


def latest_run(conn, clip_id, status=None):
    sql = "SELECT * FROM runs WHERE clip_id=?"
    params = [clip_id]
    if status:
        sql += " AND status=?"
        params.append(status)
    return one(conn, sql + " ORDER BY queued_at DESC, run_id DESC LIMIT 1", *params)


def get_run(conn, run_id):
    return one(conn, "SELECT * FROM runs WHERE run_id=?", run_id)


def run_dir(run_id):
    return config.RUNS_DIR / run_id


def rerun_reasons(conn, clip_id):
    """What changed since the clip's latest done run (empty list = nothing to re-run)."""
    clip = get_clip(conn, clip_id)
    run = latest_run(conn, clip_id, "done")
    if not clip or not run:
        return []
    park, drink = thresholds_for_set(conn, clip["set"])
    out = []
    if float(run["parking_threshold"] if run["parking_threshold"] is not None else -1) != float(park):
        out.append(f"parking threshold {run['parking_threshold']:g} s → {park:g} s")
    if float(run["drinking_duration"] if run["drinking_duration"] is not None else -1) != float(drink):
        out.append(f"inuman duration {run['drinking_duration']:g} s → {drink:g} s")
    if run["set_at_run"] != clip["set"]:
        out.append(f"Set {run['set_at_run']} → {clip['set']}")
    return out


def rerun_needed(conn, clip_id):
    """True when the clip's latest done run used other detection thresholds than the current settings for its Set."""
    return bool(rerun_reasons(conn, clip_id))


# ---- events (parsed from events.jsonl) ----------------------------------------------------------

def parse_events(path):
    """events.jsonl -> list of per-event dicts."""
    by = {}
    order = []
    p = Path(path)
    if not p.exists():
        return []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        d = by.get(e["event_id"])
        if d is None:
            d = by[e["event_id"]] = dict(event_id=e["event_id"], track_id=e.get("track_id"),
                                         internal_type=e.get("violation_type"), first={}, discarded=False,
                                         ai_verdict=None, ai_suggested=None, ai_badge=None, ai_final=False,
                                         confidence=None, bbox=None, ai_obs=None, peak_conf={})
            order.append(e["event_id"])
        kind, status, t = e.get("kind"), e.get("status"), e.get("video_time_sec")
        if kind in ("alert_created", "status_change") and status:
            d["first"].setdefault(status, t)
            if e.get("confidence") is not None:
                d["peak_conf"].setdefault(status, e["confidence"])
            if e.get("bbox"):
                d["bbox"] = e["bbox"]
        elif kind == "discarded":
            d["discarded"] = True
        elif kind == "ai_verdict":
            if e.get("final") or not d["ai_final"]:
                d["ai_verdict"], d["ai_suggested"], d["ai_badge"] = e.get("ai_verdict"), e.get("ai_suggested_status"), e.get("ai_badge")
                reply = e.get("ai_reply") or {}
                d["ai_obs"] = reply.get("observations") if isinstance(reply, dict) else None
                d["ai_final"] = d["ai_final"] or bool(e.get("final"))
    out = []
    for eid in order:
        d = by[eid]
        if d["discarded"]:
            continue
        first = d["first"]
        highest = max(first, key=lambda s: scoring.RANK.get(s, 0)) if first else "Monitoring"
        d["highest"] = highest
        d["confidence"] = d["peak_conf"].get(highest)
        out.append(d)
    return out


def ingest_events(conn, run_id):
    rd = run_dir(run_id)
    parsed = parse_events(rd / "events.jsonl")
    conn.execute("DELETE FROM events WHERE run_id=?", (run_id,))
    for d in parsed:
        f = d["first"]
        frame = None
        for cand in (f"{d['event_id']}.jpg", f"{d['event_id']}_monitoring.jpg"):
            if (rd / "frames" / cand).exists():
                frame = str(rd / "frames" / cand)
                break
        conn.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, d["event_id"], None if d["track_id"] is None else str(d["track_id"]),
             config.INTERNAL_TO_TYPE.get(d["internal_type"], d["internal_type"]), d["internal_type"],
             f.get("Monitoring"), f.get("Possible"), f.get("Likely"), d["highest"],
             d["ai_verdict"] or "Not run", d["ai_suggested"], d["confidence"], frame,
             json.dumps(d["bbox"]) if d["bbox"] else None, d["ai_badge"], d["ai_obs"]))
    conn.commit()
    return len(parsed)


def run_events(conn, run_id):
    return rows(conn, "SELECT * FROM events WHERE run_id=? ORDER BY COALESCE(first_possible_sec, first_likely_sec,"
                      " first_monitoring_sec), event_id", run_id)


def as_event_objects(event_rows):
    return [scoring.Event(event_id=e["event_id"], violation_type=e["violation_type"],
                          first_monitoring=e["first_monitoring_sec"], first_possible=e["first_possible_sec"],
                          first_likely=e["first_likely_sec"], highest_status=e["highest_status"],
                          ai_verdict=e["ai_verdict"] or "Not run", ai_suggested_status=e["ai_suggested_status"],
                          confidence=e["confidence"], track_id=e["track_id"]) for e in event_rows]


def as_key_objects(key_rows_):
    return [scoring.Key(id=k["id"], violation_type=k["violation_type"], kind=k["kind"], start=k["action_start_sec"],
                        instance_no=k["instance_no"], who=k["who"] or "", added_after_run=bool(k["added_after_run"]))
            for k in key_rows_]
