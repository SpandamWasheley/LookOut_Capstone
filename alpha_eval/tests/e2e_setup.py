"""End-to-end harness (verification): prepares a scratch eval.db in test_env/, with the test clips + answer keys,
and queues them. Run with  ALPHA_EVAL_DB=test_env/eval.db ALPHA_EVAL_RUNS=test_env/runs.

  python tests/e2e_setup.py setup          clips + answer keys (no runs)
  python tests/e2e_setup.py queue SMK-V01 PRK-V01
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
import db  # noqa: E402
from ui_common import video_props  # noqa: E402  (pure helper; streamlit import is harmless)


def sha(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def setup(conn):
    db.import_template_clips(conn)
    for cid, f, date, rtime, keys in (
        ("SMK-V01", "test_smoking_trim2.mp4", "2026-08-18", "19:30", [("Smoking", "Violation", 5.0, "man at the curb")]),
        ("PRK-V01", "test_parking.mp4", "2026-08-14", "10:00", [("Parking Obstruction", "Violation", 5.0, "vehicle")]),
    ):
        p = config.CLIPS_DIR / f
        db.upsert_clip(conn, cid, video_path=str(p), video_sha256=sha(p), date_recorded=date, recorded_time=rtime,
                       recording_status="Recorded", **video_props(p))
        for t, k, s, w in keys:
            if not db.key_rows(conn, cid):
                db.add_key_row(conn, cid, t, k, s, w)


if __name__ == "__main__":
    conn = db.connect()
    print("db:", config.EVAL_DB, "runs:", config.RUNS_DIR)
    if sys.argv[1] == "setup":
        setup(conn)
    elif sys.argv[1] == "queue":
        for cid in sys.argv[2:]:
            print(cid, db.queue_run(conn, cid))
