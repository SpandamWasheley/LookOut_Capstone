"""Scoring and matching rules for the alpha test. Pure functions: no I/O, no database, no Streamlit.

Everything here follows the spec in the build brief (Section 5) and mirrors the formulas in the Excel
template's Results Log sheet, so the tool's outcome and the sheet's outcome are always comparable.

Times are seconds of VIDEO time. Status names use the template's spelling: Monitoring / Possible / Likely.
"""
from dataclasses import dataclass, field
from typing import Optional

EPS = 1e-6


def r1(x):
    return None if x is None else round(float(x), 1)


RANK = {"Monitoring": 1, "Possible": 2, "Likely": 3}

TAG_DUPLICATE = "Duplicate"
TAG_FALSE_ALARM = "False alarm"
TAG_UNSCRIPTED_FP = "Unscripted – false alarm"
TAG_UNSCRIPTED_REAL = "Unscripted – real violation"
TAG_LATE = "Late (real act)"

TAG_OUTCOME = {
    TAG_DUPLICATE: "Duplicate",
    TAG_FALSE_ALARM: "FP",
    TAG_UNSCRIPTED_FP: "FP",
    TAG_UNSCRIPTED_REAL: "Excluded",
    TAG_LATE: "Late",
}

TYPES = ["Smoking", "Drinking", "Parking Obstruction", "Holdup"]


@dataclass
class Settings:
    alert_window_sec: float = 60.0
    start_tolerance_sec: float = 5.0
    monitoring_counts_as_alert: bool = False
    parking_threshold_main_sec: float = 120.0
    parking_threshold_validation_sec: float = 300.0
    drinking_group_duration_main_sec: float = 60.0
    drinking_group_duration_validation_sec: float = 600.0

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        kw = {}
        for f in cls.__dataclass_fields__:
            if f in d and d[f] is not None:
                kw[f] = bool(d[f]) if f == "monitoring_counts_as_alert" else float(d[f])
        return cls(**kw)


@dataclass
class Key:
    """One answer-key row."""
    id: object
    violation_type: str
    kind: str = "Violation"
    start: Optional[float] = None
    instance_no: int = 1
    who: str = ""
    added_after_run: bool = False


@dataclass
class Event:
    """One detected event (after parsing events.jsonl)."""
    event_id: str
    violation_type: str
    first_monitoring: Optional[float] = None
    first_possible: Optional[float] = None
    first_likely: Optional[float] = None
    highest_status: str = "Monitoring"
    ai_verdict: str = "Not run"
    ai_suggested_status: Optional[str] = None
    confidence: Optional[float] = None
    track_id: object = None

    def alert_time(self, monitoring_counts=False):
        """Video time when the event first reached a counting status, or None if it never did."""
        times = [t for t in (self.first_possible, self.first_likely) if t is not None]
        if monitoring_counts and self.first_monitoring is not None:
            times.append(self.first_monitoring)
        return round(min(times), 1) if times else None        # the sheet works with 0.1 s values

    def counts(self, monitoring_counts=False):
        return self.alert_time(monitoring_counts) is not None

    def status_at_alert(self, monitoring_counts=False):
        """The status the event had when it first counted (the highest status may come later)."""
        t = self.alert_time(monitoring_counts)
        for name, v in (("Monitoring", self.first_monitoring), ("Possible", self.first_possible),
                        ("Likely", self.first_likely)):
            if v is not None and round(v, 1) == t:
                return name
        return self.highest_status


def waiting_time(violation_type, set_name, s: Settings):
    if violation_type == "Parking Obstruction":
        return s.parking_threshold_validation_sec if set_name == "Validation" else s.parking_threshold_main_sec
    if violation_type == "Drinking":
        return (s.drinking_group_duration_validation_sec if set_name == "Validation"
                else s.drinking_group_duration_main_sec)
    return 0.0


def fmt_time(sec):
    if sec is None:
        return "–"
    m, s = divmod(float(sec), 60)
    return f"{int(m)}:{s:04.1f}"


def parse_time(text):
    """'1:30.5' / '90' / '90.5' -> seconds; '' -> None. Raises ValueError for junk."""
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    if ":" in t:
        parts = t.split(":")
        if len(parts) != 2:
            raise ValueError(f"time {text!r} must look like mm:ss.s")
        return int(parts[0]) * 60 + float(parts[1])
    return float(t)


@dataclass
class RowResult:
    key: Key
    waiting: float = 0.0
    deadline: Optional[float] = None
    event_id: Optional[str] = None
    alert_time: Optional[float] = None
    status: Optional[str] = None
    status_at_alert: Optional[str] = None
    ai_verdict: Optional[str] = None
    delay: Optional[float] = None
    outcome: str = ""
    alerted_instead: Optional[str] = None
    manual: bool = False
    explanation: str = ""


@dataclass
class UnmatchedResult:
    event: Event
    alert_time: float
    suggested_tag: str
    tag: Optional[str] = None        # confirmed by the reviewer
    outcome: str = "Needs tag"

    @property
    def effective_tag(self):
        return self.tag


@dataclass
class ClipResult:
    rows: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)
    non_counting: list = field(default_factory=list)       # events shown but never scored


def _window(key: Key, deadline, s: Settings, clip_end):
    """Allowed alert-time window for a key row, or None if the row cannot be matched (no start for a violation)."""
    if key.kind == "Violation":
        if key.start is None:
            return None
        return (key.start - s.start_tolerance_sec, deadline)
    # Look-alike / No violation: from start-tolerance to the end of the clip; whole clip without a start
    if key.start is None:
        return (float("-inf"), float("inf"))
    return (key.start - s.start_tolerance_sec, clip_end if clip_end is not None else float("inf"))


def score_clip(keys, events, set_name, settings: Settings, manual=None, tags=None, clip_duration=None):
    """Match events to answer-key rows and score everything.

    keys      list[Key]
    events    list[Event] (all events of the run; non-counting ones are listed but never scored)
    manual    {key.id: event_id | None} reviewer decisions (None = deliberately unassigned); kept as they are
    tags      {event_id: tag} confirmed by the reviewer for unmatched events
    """
    manual = manual or {}
    tags = tags or {}
    keys = [Key(k.id, k.violation_type, k.kind, r1(k.start), k.instance_no, k.who, k.added_after_run) for k in keys]
    mc = settings.monitoring_counts_as_alert
    by_id = {e.event_id: e for e in events}
    counting = [e for e in events if e.counts(mc)]
    non_counting = [e for e in events if not e.counts(mc)]
    at = {e.event_id: e.alert_time(mc) for e in counting}

    assignment = {}                       # key.id -> event_id
    used = set()
    # 1) manual decisions are kept as they are
    for k in keys:
        if k.id in manual:
            ev = manual[k.id]
            if ev is not None and ev in at and ev not in used:
                assignment[k.id] = ev
                used.add(ev)
    # 2) automatic matching for every other row, type by type, in start order
    for vtype in {k.violation_type for k in keys}:
        rows = [k for k in keys if k.violation_type == vtype and k.id not in manual]
        rows.sort(key=lambda k: (k.start is None, k.start if k.start is not None else 0.0))
        for k in rows:
            waiting = waiting_time(k.violation_type, set_name, settings)
            deadline = None if k.start is None else round(k.start + waiting + settings.alert_window_sec, 6)
            win = _window(k, deadline, settings, clip_duration)
            if win is None:
                continue
            cands = [e for e in counting if e.violation_type == vtype and e.event_id not in used
                     and win[0] - EPS <= at[e.event_id] <= win[1] + EPS]
            if cands:
                best = min(cands, key=lambda e: (at[e.event_id], e.event_id))
                assignment[k.id] = best.event_id
                used.add(best.event_id)

    # 3) row outcomes
    rows_out = []
    for k in sorted(keys, key=lambda k: (TYPES.index(k.violation_type) if k.violation_type in TYPES else 99,
                                         k.start is None, k.start or 0.0, k.instance_no)):
        waiting = waiting_time(k.violation_type, set_name, settings)
        deadline = round(k.start + waiting + settings.alert_window_sec, 6) if (k.kind == "Violation" and k.start is not None) else None
        r = RowResult(key=k, waiting=waiting, deadline=deadline, manual=(k.id in manual))
        ev = by_id.get(assignment.get(k.id))
        if ev is not None:
            r.event_id, r.alert_time, r.status, r.ai_verdict = ev.event_id, at[ev.event_id], ev.highest_status, ev.ai_verdict
            r.status_at_alert = ev.status_at_alert(mc)
        if k.kind == "Violation":
            if k.start is None:
                r.outcome = "Needs start"
            elif ev is not None and r.alert_time <= deadline + EPS:
                r.outcome = "TP"
                r.delay = round(r.alert_time - k.start - waiting, 6)
            else:
                r.outcome = "FN"
        else:
            r.outcome = "FP" if ev is not None else "TN"
        rows_out.append(r)

    # 4) unmatched counting events
    unmatched = []
    tp_types = {r.key.violation_type for r in rows_out if r.outcome == "TP"}
    fn_rows = [r for r in rows_out if r.outcome == "FN" and r.key.kind == "Violation"]
    for e in sorted(counting, key=lambda e: (at[e.event_id], e.event_id)):
        if e.event_id in used:
            continue
        if e.violation_type in tp_types:
            sug = TAG_DUPLICATE
        elif any(r.key.violation_type == e.violation_type and r.deadline is not None and at[e.event_id] > r.deadline + EPS
                 for r in fn_rows):
            sug = TAG_LATE
        else:
            sug = TAG_FALSE_ALARM
        tag = tags.get(e.event_id)
        unmatched.append(UnmatchedResult(event=e, alert_time=at[e.event_id], suggested_tag=sug, tag=tag,
                                         outcome=TAG_OUTCOME.get(tag, "Needs tag") if tag else "Needs tag"))

    # 5) alerted-instead for missed violations, then one-line explanations
    for r in rows_out:
        if r.outcome == "FN" and r.key.kind == "Violation" and r.key.start is not None and r.event_id is None:
            lo, hi = r.key.start - settings.start_tolerance_sec, r.deadline
            others = [u for u in unmatched if u.event.violation_type != r.key.violation_type and lo - EPS <= u.alert_time <= hi + EPS]
            if others:
                r.alerted_instead = min(others, key=lambda u: (u.alert_time, u.event.event_id)).event.violation_type
        r.explanation = explain(r)
    return ClipResult(rows=rows_out, unmatched=unmatched, non_counting=non_counting)


def explain(r: RowResult):
    k = r.key
    name = k.violation_type
    if r.outcome == "Needs start":
        return "Needs start — type the action start time in the answer key before this row can be scored."
    if k.kind == "Violation":
        if r.outcome == "TP":
            wait = f" after the {r.waiting:g} s rule" if r.waiting else ""
            return (f"TP — {name} alert ({r.status_at_alert}) at {fmt_time(r.alert_time)}, {r.delay:.1f} s after "
                    f"start{wait} (deadline {fmt_time(r.deadline)}).")
        if r.event_id is not None:
            return (f"FN — matched {name} alert at {fmt_time(r.alert_time)} came after the deadline "
                    f"{fmt_time(r.deadline)}.")
        extra = f" Alerted as {r.alerted_instead} instead." if r.alerted_instead else ""
        return f"FN — no {name} alert reached a counting status by the deadline {fmt_time(r.deadline)}.{extra}"
    if r.outcome == "FP":
        return f"FP — {name} alert ({r.status_at_alert}) at {fmt_time(r.alert_time)} on a {k.kind.lower()} item."
    return f"TN — no {name} alert on this {k.kind.lower()} item."


def should_flag_added_after_run(clip_has_started_run):
    """An answer-key row added or edited after the clip's first run started is flagged (test 14)."""
    return bool(clip_has_started_run)


# ---------------------------------------------------------------------------------------------
# Aggregation (mirrors the Summary / Validation sheets exactly)
# ---------------------------------------------------------------------------------------------

def _div(a, b):
    return a / b if b else None


def aggregate(records, vtypes=TYPES):
    """records: flat dicts with keys
         row_type ('Expected'|'Unmatched alert'), type, outcome, tag (unmatched rows), delay.
    Returns {type: metrics, 'OVERALL': metrics}; None where the sheet shows '–'.
    """
    def blank():
        return dict(tp=0, fn=0, tn=0, fp_look=0, fp_actor=0, fp_unscripted=0, dup=0, late=0, unscripted_real=0,
                    needs=0, delays=[])

    acc = {t: blank() for t in vtypes}
    for r in records:
        a = acc.get(r["type"])
        if a is None:
            continue
        if r["row_type"] == "Expected":
            o = r["outcome"]
            if o == "TP":
                a["tp"] += 1
                if r.get("delay") is not None:
                    a["delays"].append(r["delay"])
            elif o == "FN":
                a["fn"] += 1
            elif o == "TN":
                a["tn"] += 1
            elif o == "FP":
                a["fp_look"] += 1
        else:
            tag = r.get("tag")
            if tag == TAG_FALSE_ALARM:
                a["fp_actor"] += 1
            elif tag == TAG_UNSCRIPTED_FP:
                a["fp_unscripted"] += 1
            elif tag == TAG_DUPLICATE:
                a["dup"] += 1
            elif tag == TAG_LATE:
                a["late"] += 1
            elif tag == TAG_UNSCRIPTED_REAL:
                a["unscripted_real"] += 1
        if r["outcome"] in ("Needs tag", "Needs start"):
            a["needs"] += 1

    def finish(a):
        fp = a["fp_look"] + a["fp_actor"] + a["fp_unscripted"]
        tp, fn, tn = a["tp"], a["fn"], a["tn"]
        precision = _div(tp, tp + fp)
        recall = _div(tp, tp + fn)
        f1 = _div(2 * precision * recall, precision + recall) if (precision is not None and recall is not None) else None
        return {
            "real_acts": tp + fn, "tp": tp, "fn": fn, "tn": tn,
            "fp_look": a["fp_look"], "fp_actor": a["fp_actor"], "fp_unscripted": a["fp_unscripted"], "fp_total": fp,
            "accuracy": _div(tp + tn, tp + fn + tn + fp), "precision": precision, "recall": recall, "f1": f1,
            "avg_delay": (sum(a["delays"]) / len(a["delays"])) if a["delays"] else None,
            "duplicates": a["dup"], "late": a["late"], "unscripted_real": a["unscripted_real"], "needs": a["needs"],
            "_delays": list(a["delays"]),
        }

    out = {t: finish(a) for t, a in acc.items()}
    total = blank()
    for a in acc.values():
        for k, v in a.items():
            if k == "delays":
                total["delays"].extend(v)
            else:
                total[k] += v
    out["OVERALL"] = finish(total)
    return out
