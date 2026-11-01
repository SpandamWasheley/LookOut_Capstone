import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
import keyedit  # noqa: E402

NAN = float("nan")


def fresh():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    db.upsert_clip(conn, "SMK-V01", **{"set": "Main"}, video_path="x.mp4")
    return conn


def rec(id_, t, k, start, who=""):
    return {"id": id_, "Type": t, "Kind": k, "Start (mm:ss.s)": start, "Who / description": who}


def test_add_update_delete_roundtrip():
    conn = fresh()
    assert keyedit.apply_edits(conn, "SMK-V01", [rec(NAN, "Smoking", "Violation", "0:41.5", "Actor 1"),
                                                  rec(NAN, "Drinking", "Look-alike", "", "water"),
                                                  rec(NAN, None, None, None)]) == []
    rows = db.key_rows(conn, "SMK-V01")
    assert [(r["violation_type"], r["action_start_sec"]) for r in rows] == [("Drinking", None), ("Smoking", 41.5)]
    smoking = next(r for r in rows if r["violation_type"] == "Smoking")
    keyedit.apply_edits(conn, "SMK-V01", [rec(smoking["id"], "Smoking", "Violation", "1:05", "Actor 1")])
    rows = db.key_rows(conn, "SMK-V01")
    assert len(rows) == 1 and rows[0]["action_start_sec"] == 65.0                   # the Drinking row was removed


def test_bad_time_writes_nothing():
    conn = fresh()
    errs = keyedit.apply_edits(conn, "SMK-V01", [rec(NAN, "Smoking", "Violation", "abc")])
    assert errs and db.key_rows(conn, "SMK-V01") == []
    assert keyedit.apply_edits(conn, "SMK-V01", [rec(NAN, "Smoking", "Violation", "1:2:3")])
