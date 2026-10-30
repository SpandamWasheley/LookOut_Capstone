import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
import matching  # noqa: E402


def fresh():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    db.upsert_clip(conn, "SMK-V01", planned_focus="Smoking", **{"set": "Main"}, video_path="x.mp4", date_recorded="2026-09-14",
                   time_of_day="Night", duration_sec=100.0)
    return conn


def test_14_added_after_run_is_flagged_in_the_database():
    conn = fresh()
    k1 = db.add_key_row(conn, "SMK-V01", "Smoking", "Violation", 30.0, "a")
    assert db.key_rows(conn, "SMK-V01")[0]["added_after_run"] == 0          # typed before any run
    run_id = db.queue_run(conn, "SMK-V01")
    assert db.key_rows(conn, "SMK-V01")[0]["added_after_run"] == 0          # queued is not started
    conn.execute("UPDATE runs SET status='running', started_at='now' WHERE run_id=?", (run_id,))
    conn.commit()
    k2 = db.add_key_row(conn, "SMK-V01", "Smoking", "Violation", 90.0, "b")
    rows = {r["id"]: r for r in db.key_rows(conn, "SMK-V01")}
    assert rows[k1]["added_after_run"] == 0 and rows[k2]["added_after_run"] == 1
    db.update_key_row(conn, k1, who="renamed only")
    assert db.key_rows(conn, "SMK-V01")[0]["added_after_run"] == 0          # a name change does not matter
    db.update_key_row(conn, k1, action_start_sec=31.0)
    assert {r["id"]: r for r in db.key_rows(conn, "SMK-V01")}[k1]["added_after_run"] == 1


def test_a_clip_is_never_queued_twice():
    conn = fresh()
    a = db.queue_run(conn, "SMK-V01")
    b = db.queue_run(conn, "SMK-V01")
    assert a == b
    assert len(db.rows(conn, "SELECT * FROM runs")) == 1


def test_run_uses_the_sets_thresholds_and_rerun_flag():
    conn = fresh()
    rid = db.queue_run(conn, "SMK-V01")
    r = db.get_run(conn, rid)
    assert (r["parking_threshold"], r["drinking_duration"]) == (120.0, 60.0)
    assert r["clock"] == "2026-09-14 23:00"                                    # Night -> 23:00 default footage clock
    conn.execute("UPDATE runs SET status='done' WHERE run_id=?", (rid,))
    conn.commit()
    assert not db.rerun_needed(conn, "SMK-V01")
    changed = db.update_eval_settings(conn, {"parking_threshold_main_sec": 150})
    assert changed == ["parking_threshold_main_sec"] and db.rerun_needed(conn, "SMK-V01")
    db.update_eval_settings(conn, {"alert_window_sec": 90})                    # scoring-only: no re-run needed
    db.update_eval_settings(conn, {"parking_threshold_main_sec": 120})
    assert not db.rerun_needed(conn, "SMK-V01")


def test_manual_assignment_roundtrip_and_type_check():
    conn = fresh()
    rid = db.queue_run(conn, "SMK-V01")
    conn.execute("UPDATE runs SET status='done' WHERE run_id=?", (rid,))
    k = db.add_key_row(conn, "SMK-V01", "Smoking", "Violation", 30.0)
    for eid, vt, t in (("E1", "Smoking", 41.0), ("E2", "Drinking", 42.0)):
        conn.execute("INSERT INTO events (run_id, event_id, violation_type, first_possible_sec, highest_status, ai_verdict)"
                     " VALUES (?,?,?,?,?,?)", (rid, eid, vt, t, "Possible", "Not run"))
    conn.commit()
    comp = matching.compute_run(conn, rid)
    assert comp["result"].rows[0].event_id == "E1" and not comp["result"].rows[0].manual
    try:
        matching.assign(conn, rid, k, "E2")
        assert False, "different type must be refused"
    except ValueError:
        pass
    matching.assign(conn, rid, k, None)                                         # reviewer unassigns
    comp = matching.compute_run(conn, rid)
    assert comp["result"].rows[0].outcome == "FN" and comp["result"].rows[0].manual
    assert {u.event.event_id for u in comp["result"].unmatched} == {"E1", "E2"}
