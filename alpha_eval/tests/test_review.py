import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import fake_data  # noqa: E402
import matching  # noqa: E402


def conn_with_unreviewed():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    db.import_template_clips(conn)
    # an FN row, a late holdup (Late suggestion), a smoking false alarm: nothing tagged, no reasons yet
    rid, ids = fake_data.make_run(
        conn, "HLD-V01", "Main", [("E1", "Smoking", 69, 70, None), ("E2", "Holdup", None, 200, 201)],
        [("Holdup", "Violation", 60.0, "Actor 1")], reviewed=False)
    return conn, rid, ids


def test_review_is_blocked_until_tags_and_reasons_exist():
    conn, rid, ids = conn_with_unreviewed()
    blockers = matching.review_blockers(conn, rid)
    assert any("confirm a tag" in b for b in blockers) and any("miss / FP reason" in b for b in blockers)
    with pytest.raises(ValueError):
        matching.mark_reviewed(conn, rid, "Ana")


def test_accept_all_suggestions_then_reasons_then_review():
    conn, rid, ids = conn_with_unreviewed()
    assert matching.accept_all_suggestions(conn, rid) == 2
    comp = matching.compute_run(conn, rid)
    tags = {u.event.event_id: (u.tag, u.outcome) for u in comp["result"].unmatched}
    assert tags == {"E1": ("False alarm", "FP"), "E2": ("Late (real act)", "Late")}
    blockers = matching.review_blockers(conn, rid)
    assert len(blockers) == 2                                   # the FN row and the FP false alarm still need reasons
    matching.set_row_review(conn, rid, ids[0], miss_reason="Alert too late")
    matching.set_tag(conn, rid, "E1", "False alarm", miss_reason="Look-alike object")
    assert matching.review_blockers(conn, rid) == []
    with pytest.raises(ValueError):
        matching.mark_reviewed(conn, rid, "   ")                # a name is required
    matching.mark_reviewed(conn, rid, "Ana", "ok")
    assert db.get_run(conn, rid)["reviewed"] == 1 and db.get_run(conn, rid)["reviewer"] == "Ana"


def test_summary_line_and_alerted_instead():
    conn, rid, ids = conn_with_unreviewed()
    matching.accept_all_suggestions(conn, rid)
    comp = matching.compute_run(conn, rid)
    assert comp["result"].rows[0].alerted_instead == "Smoking"
    line = matching.summary_line(comp)
    assert "Holdup 1 FN" in line and "Smoking 1 FP" in line and "1 late" in line


def test_reviewed_only_filter_in_all_flat():
    conn, _ = fake_data.build()[0], None
    n_all = len(matching.all_flat(conn, reviewed_only=False))
    assert n_all > 0 and len(matching.all_flat(conn, reviewed_only=True)) == n_all   # fake runs are reviewed
    conn.execute("UPDATE runs SET reviewed=0 WHERE run_id='fake-SMK-V01'")
    conn.commit()
    assert len(matching.all_flat(conn, reviewed_only=True)) < n_all
