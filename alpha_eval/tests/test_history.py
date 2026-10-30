"""Verification 8: re-running a clip adds a run; the old run and its results stay exactly as they were."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import fake_data  # noqa: E402
import matching  # noqa: E402


def test_rerun_keeps_old_run_untouched_and_both_are_listed():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    db.import_template_clips(conn)
    old_id, ids = fake_data.make_run(conn, "SMK-V01", "Main", [("E1", "Smoking", 40, 41, None)],
                                     [("Smoking", "Violation", 30.0, "a")])
    before_run = db.get_run(conn, old_id)
    before_events = db.run_events(conn, old_id)
    before_matches = db.rows(conn, "SELECT * FROM matches WHERE run_id=?", old_id)
    new_id = db.queue_run(conn, "SMK-V01")
    assert new_id != old_id
    runs = db.rows(conn, "SELECT run_id, status FROM runs WHERE clip_id='SMK-V01' ORDER BY queued_at")
    assert {r["run_id"] for r in runs} == {old_id, new_id}
    assert db.get_run(conn, old_id) == before_run
    assert db.run_events(conn, old_id) == before_events
    assert db.rows(conn, "SELECT * FROM matches WHERE run_id=?", old_id) == before_matches
    assert db.latest_run(conn, "SMK-V01")["run_id"] == new_id            # the new run is the latest -> old one is read-only
    # the re-run gets different events; the old run still scores the same
    conn.execute("UPDATE runs SET status='done', started_at='x' WHERE run_id=?", (new_id,))
    fake_data.add_event(conn, new_id, "E1", "Smoking", None, 35, None)
    conn.commit()
    old = matching.compute_run(conn, old_id, persist=False)["result"].rows[0]
    new = matching.compute_run(conn, new_id, persist=False)["result"].rows[0]
    assert (old.alert_time, new.alert_time) == (41.0, 35.0) and old.outcome == new.outcome == "TP"
