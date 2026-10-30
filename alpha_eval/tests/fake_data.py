"""Builds a scratch eval.db with FAKE finished runs that cover every outcome the export must handle."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import db  # noqa: E402
import matching  # noqa: E402


def snapshot(set_name, es):
    park = es["parking_threshold_validation_sec"] if set_name == "Validation" else es["parking_threshold_main_sec"]
    drink = es["drinking_group_duration_validation_sec"] if set_name == "Validation" else es["drinking_group_duration_main_sec"]
    return json.dumps({
        "alpha_eval_version": config.TOOL_VERSION, "scoring_spec": "v6.1", "model_file": "merged_v2.pt",
        "model_sha256": "ab" * 32, "lookout_git": {"commit": "e6af550", "dirty": False},
        "ai_models": {"vlm_model": "qwen3-vl:2b-instruct"},
        "overrides_in_memory": {"parking_threshold_sec": park, "drinking_group_duration_sec": drink},
        "system_settings_used": {"smoking_dwell": 3, "smoking_confidence": 30, "drinking_group_duration": int(drink),
                                 "updated_at": "x"},
        "worker_inputs": {"video": "v", "run_id": "r"}})


def add_event(conn, run_id, eid, vtype, mon=None, pos=None, lik=None, ai="Agrees"):
    highest = "Likely" if lik is not None else ("Possible" if pos is not None else "Monitoring")
    conn.execute("INSERT INTO events (run_id, event_id, track_id, violation_type, internal_type, first_monitoring_sec,"
                 " first_possible_sec, first_likely_sec, highest_status, ai_verdict) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (run_id, eid, "1", vtype, vtype.lower(), mon, pos, lik, highest, ai))


def make_run(conn, clip_id, set_name, events, keys, tags=None, reviews=None, reviewed=True, added_after=()):
    es = db.get_eval_settings(conn)
    park, drink = db.thresholds_for_set(conn, set_name)
    db.upsert_clip(conn, clip_id, set=set_name, duration_sec=240.0, video_path=f"{clip_id}.mp4", date_recorded="2026-09-14")
    run_id = f"fake-{clip_id}"
    conn.execute("INSERT INTO runs (run_id, clip_id, status, queued_at, started_at, finished_at, frames_processed,"
                 " processing_fps, processing_time_sec, snapshot_json, reviewed, reviewer, set_at_run, parking_threshold,"
                 " drinking_duration) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (run_id, clip_id, "done", "2026-10-01T10:00:00", "2026-10-01T10:00:01", "2026-10-01T10:05:00", 2400, 1.5,
                  300.0, snapshot(set_name, es), 1 if reviewed else 0, "Tester", set_name, park, drink))
    for e in events:
        add_event(conn, run_id, *e)
    ids = []
    for (vtype, kind, start, who) in keys:
        ids.append(db.add_key_row(conn, clip_id, vtype, kind, start, who))
    for i in added_after:
        db.update_key_row(conn, ids[i], added_after_run=1)
    conn.commit()
    comp = matching.compute_run(conn, run_id)           # persists suggestions
    for eid, tag in (tags or {}).items():
        matching.set_tag(conn, run_id, eid, tag, who="bystander" if "Unscripted" in tag else "actor")
    for idx, reason in (reviews or {}).items():
        matching.set_row_review(conn, run_id, ids[idx], miss_reason=reason, notes="n")
    return run_id, ids


def build(path=None):
    path = path or Path(tempfile.mkdtemp(prefix="alpha_fake_")) / "eval.db"
    conn = db.connect(path)
    db.import_template_clips(conn)
    # 1) two smoking TPs, a drinking bystander (unscripted FP) and a duplicate
    make_run(conn, "SMK-V01", "Main",
             [("E1", "Smoking", 40, 41, None), ("E2", "Smoking", 97, 98, 100), ("E3", "Drinking", 100, 106, None),
              ("E4", "Smoking", 128, 130, None)],
             [("Smoking", "Violation", 30.0, "Actor 1"), ("Smoking", "Violation", 90.0, "Actor 2")],
             tags={"E3": "Unscripted – false alarm", "E4": "Duplicate"})
    # 2) missed smoking (FN)
    make_run(conn, "SMK-V02", "Main", [], [("Smoking", "Violation", 20.0, "Actor 1")], reviews={0: "Distance / too small"})
    # 3) holdup missed, alerted as smoking (false alarm) and late holdup
    make_run(conn, "HLD-V01", "Main",
             [("E1", "Smoking", 69, 70, None), ("E2", "Holdup", None, 200, 201)],
             [("Holdup", "Violation", 60.0, "Actor 1")], tags={"E1": "False alarm", "E2": "Late (real act)"},
             reviews={0: "Alert too late"})
    # 4) look-alike alert (FP) and look-alike quiet (TN)
    make_run(conn, "SMK-L01", "Main", [("E1", "Smoking", 14, 15, None)],
             [("Smoking", "Look-alike", 10.0, "pen"), ("Drinking", "No violation", None, "water")],
             reviews={0: "Look-alike object"})
    # 5) unscripted real violation (excluded), needs tag, needs start
    make_run(conn, "SMK-L02", "Main", [("E1", "Holdup", 38, 40, None), ("E2", "Drinking", 50, 52, None)],
             [("Smoking", "Violation", None, "Actor 1")], tags={"E1": "Unscripted – real violation"})
    # 6) validation-set parking TP
    make_run(conn, "PRK-V01", "Validation", [("E1", "Parking Obstruction", None, 350, 350)],
             [("Parking Obstruction", "Violation", 10.0, "van")])
    # 7) drinking TP with a row added after the run
    make_run(conn, "DRK-V01", "Main", [("E1", "Drinking", 99, 100, None)],
             [("Drinking", "Violation", 20.0, "group"), ("Smoking", "No violation", None, "x")], added_after=(1,))
    # 8) a late FP false alarm on an actor
    make_run(conn, "DRK-V02", "Main", [("E1", "Smoking", 10, 12, None)], [("Drinking", "Look-alike", 5.0, "juice")],
             tags={"E1": "False alarm"})
    return conn, path


if __name__ == "__main__":
    c, p = build()
    print(p)
