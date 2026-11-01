"""Section 5 unit tests. Times in seconds. Defaults: window 60, tolerance 5, Monitoring off, Main 120 / 60."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scoring as sc  # noqa: E402
from scoring import Event, Key, Settings, score_clip  # noqa: E402

S = Settings()


def ev(eid, vtype, t, status="Possible", monitoring_only=False):
    if monitoring_only:
        return Event(eid, vtype, first_monitoring=t, highest_status="Monitoring")
    return Event(eid, vtype, first_monitoring=t - 1, first_possible=t,
                 first_likely=t if status == "Likely" else None, highest_status=status)


def k(i, vtype, start, kind="Violation"):
    return Key(id=i, violation_type=vtype, kind=kind, start=start)


def rows(res):
    return {r.key.id: r for r in res.rows}


def test_1_two_smoking_and_a_drinking_bystander():
    keys = [k(1, "Smoking", 30), k(2, "Smoking", 90)]
    events = [ev("A", "Smoking", 41), ev("B", "Smoking", 98), ev("C", "Drinking", 106)]
    res = score_clip(keys, events, "Main", S)
    r = rows(res)
    assert (r[1].outcome, r[1].delay) == ("TP", 11)
    assert (r[2].outcome, r[2].delay) == ("TP", 8)
    assert len(res.unmatched) == 1 and res.unmatched[0].event.event_id == "C"
    assert res.unmatched[0].suggested_tag == "False alarm"
    res = score_clip(keys, events, "Main", S, tags={"C": "Unscripted – false alarm"})
    assert res.unmatched[0].outcome == "FP"


def test_2_missed_smoking():
    res = score_clip([k(1, "Smoking", 20)], [], "Main", S)
    assert res.rows[0].outcome == "FN" and res.rows[0].alerted_instead is None


def test_3_lookalike():
    res = score_clip([k(1, "Smoking", 10, "Look-alike")], [ev("A", "Smoking", 15)], "Main", S)
    assert res.rows[0].outcome == "FP"
    res = score_clip([k(1, "Smoking", 10, "Look-alike")], [], "Main", S)
    assert res.rows[0].outcome == "TN"


def test_4_parking_main():
    res = score_clip([k(1, "Parking Obstruction", 10)], [ev("A", "Parking Obstruction", 180, "Likely")], "Main", S)
    r = res.rows[0]
    assert r.deadline == 190 and r.outcome == "TP" and r.delay == 50
    res = score_clip([k(1, "Parking Obstruction", 10)], [ev("A", "Parking Obstruction", 200, "Likely")], "Main", S)
    assert res.rows[0].outcome == "FN"
    assert res.unmatched[0].suggested_tag == "Late (real act)"


def test_5_parking_validation():
    res = score_clip([k(1, "Parking Obstruction", 10)], [ev("A", "Parking Obstruction", 350, "Likely")], "Validation", S)
    r = res.rows[0]
    assert r.deadline == 370 and r.outcome == "TP" and r.delay == 40


def test_6_drinking_main():
    res = score_clip([k(1, "Drinking", 20)], [ev("A", "Drinking", 100)], "Main", S)
    r = res.rows[0]
    assert r.deadline == 140 and r.outcome == "TP" and r.delay == 20


def test_7_holdup_missed_alerted_as_smoking():
    events = [ev("A", "Smoking", 70), ev("B", "Holdup", 200, "Likely")]
    res = score_clip([k(1, "Holdup", 60)], events, "Main", S)
    r = res.rows[0]
    assert r.outcome == "FN" and r.deadline == 120 and r.alerted_instead == "Smoking"
    sug = {u.event.event_id: u.suggested_tag for u in res.unmatched}
    assert sug == {"A": "False alarm", "B": "Late (real act)"}


def test_8_duplicate():
    res = score_clip([k(1, "Smoking", 10)], [ev("A", "Smoking", 20), ev("B", "Smoking", 40)], "Main", S)
    assert res.rows[0].outcome == "TP" and res.rows[0].event_id == "A"
    assert [(u.event.event_id, u.suggested_tag) for u in res.unmatched] == [("B", "Duplicate")]


def test_9_needs_start():
    res = score_clip([k(1, "Smoking", None)], [ev("A", "Smoking", 20)], "Main", S)
    assert res.rows[0].outcome == "Needs start"
    assert res.rows[0].event_id is None          # a row without a start never takes an event


def test_10_monitoring_only_event():
    e = [ev("A", "Holdup", 40, monitoring_only=True)]
    res = score_clip([k(1, "Holdup", 30)], e, "Main", S)
    assert res.rows[0].outcome == "FN" and res.unmatched == [] and len(res.non_counting) == 1
    on = Settings(monitoring_counts_as_alert=True)
    res = score_clip([k(1, "Holdup", 30)], e, "Main", on)
    assert res.rows[0].outcome == "TP" and res.rows[0].delay == 10


def test_11_one_event_two_rows():
    res = score_clip([k(1, "Smoking", 30), k(2, "Smoking", 90)], [ev("A", "Smoking", 41)], "Main", S)
    r = rows(res)
    assert r[1].outcome == "TP" and r[2].outcome == "FN"


def test_12_within_tolerance_negative_delay():
    res = score_clip([k(1, "Smoking", 30)], [ev("A", "Smoking", 27)], "Main", S)
    assert res.rows[0].outcome == "TP" and res.rows[0].delay == -3


def test_13_manual_swap_is_kept_and_flagged():
    """Spec numbers: 98 > row-1 deadline (90), so under the sheet's own rule the swapped row 1 is FN; the
    swap itself is kept, flagged manual and not re-assigned (see DECISIONS D12)."""
    keys = [k(1, "Smoking", 30), k(2, "Smoking", 90)]
    events = [ev("A", "Smoking", 41), ev("B", "Smoking", 98)]
    res = score_clip(keys, events, "Main", S, manual={1: "B", 2: "A"})
    r = rows(res)
    assert r[1].event_id == "B" and r[2].event_id == "A" and r[1].manual and r[2].manual
    assert r[1].delay is None and r[1].outcome == "FN"          # 98 is after 30+60
    assert r[2].outcome == "TP" and r[2].delay == 41 - 90
    assert res.unmatched == []                                   # matched events are never "unmatched"


def test_13b_manual_swap_in_window_stays_tp():
    keys = [k(1, "Smoking", 30), k(2, "Smoking", 50)]
    events = [ev("A", "Smoking", 41), ev("B", "Smoking", 70)]
    auto = rows(score_clip(keys, events, "Main", S))
    assert (auto[1].event_id, auto[2].event_id) == ("A", "B")
    res = score_clip(keys, events, "Main", S, manual={1: "B", 2: "A"})
    r = rows(res)
    assert (r[1].outcome, r[1].delay) == ("TP", 40)
    assert (r[2].outcome, r[2].delay) == ("TP", -9)
    assert r[1].manual and r[2].manual


def test_manual_unassign_frees_the_event():
    keys = [k(1, "Smoking", 30)]
    res = score_clip(keys, [ev("A", "Smoking", 41)], "Main", S, manual={1: None})
    assert res.rows[0].outcome == "FN" and res.rows[0].manual
    assert res.unmatched[0].event.event_id == "A"


def test_14_added_after_run_flag():
    assert sc.should_flag_added_after_run(True) is True
    assert sc.should_flag_added_after_run(False) is False


def test_events_never_fill_two_rows_and_earliest_wins():
    keys = [k(1, "Smoking", 30), k(2, "Smoking", 32)]
    res = score_clip(keys, [ev("A", "Smoking", 40), ev("B", "Smoking", 45)], "Main", S)
    r = rows(res)
    assert r[1].event_id == "A" and r[2].event_id == "B"


def test_aggregate_matches_sheet_logic():
    keys = [k(1, "Smoking", 30), k(2, "Smoking", 90), k(3, "Holdup", 10, "Look-alike")]
    events = [ev("A", "Smoking", 41), ev("C", "Drinking", 106), ev("D", "Smoking", 50)]
    res = score_clip(keys, events, "Main", S, tags={"C": "Unscripted – false alarm", "D": "Duplicate"})
    recs = [dict(row_type="Expected", type=r.key.violation_type, outcome=r.outcome, delay=r.delay) for r in res.rows]
    recs += [dict(row_type="Unmatched alert", type=u.event.violation_type, outcome=u.outcome, tag=u.tag)
             for u in res.unmatched]
    agg = sc.aggregate(recs)
    assert agg["Smoking"]["tp"] == 1 and agg["Smoking"]["fn"] == 1 and agg["Smoking"]["duplicates"] == 1
    assert agg["Drinking"]["fp_unscripted"] == 1 and agg["Holdup"]["tn"] == 1
    assert agg["OVERALL"]["fp_total"] == 1
    assert agg["Smoking"]["recall"] == 0.5 and agg["Smoking"]["avg_delay"] == 11
