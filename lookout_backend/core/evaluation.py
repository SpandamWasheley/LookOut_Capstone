"""What each vehicle in a run did, kept so a person can check it afterwards.

A detection run answers "did anything violate the rule". Evaluating that run
asks a different question of the same footage -- "for THIS vehicle, what did
the system think, and was it right?" -- and the detector throws away almost
everything needed to answer it. Its per-vehicle state is working memory, wiped
seconds after a vehicle leaves, and only the vehicles that alerted ever reach
the database.

So the run writes a ledger beside its own log, one row per vehicle, and this
module reads it back. Nothing here decides whether the system was RIGHT: that
needs ground truth, which only a person watching the clip has. What it does is
put the system's answer and the evidence for it in front of that person, and
work out the verdict once they have said what really happened.

TWO SOURCES, because a run that happened before the ledger existed is still
worth evaluating. The JSON ledger is complete (it carries each vehicle's box,
so a frame can be drawn). Failing that, the run's own printed table is parsed
out of the log -- same times, no boxes, so the frame comes back unmarked. The
reader says which it used rather than quietly degrading.
"""
import csv
import json
import re
import subprocess
from pathlib import Path

LEDGER_SUFFIX = ".vehicles.json"

# "  motorcycle #390    |      4:22 |      4:35 |     - |    0s | in the zone..."
ROW_RE = re.compile(
    r"^\s*(?P<label>car|motorcycle|bus|truck)\s+#(?P<vid>\d+)\s*\|"
    r"\s*(?P<enter>[\d:]+|-)\s*\|"
    r"\s*(?P<leave>[\d:]+|-)\s*\|"
    r"\s*(?P<alert>[\d:]+|-)\s*\|"
    r"\s*(?P<dwell>[\d.]+)s\s*\|"
    r"\s*(?P<note>.*?)\s*$"
)
HEADER_RE = re.compile(
    r"Zone results for (?P<clip>.+?) - threshold (?P<threshold>[\d.]+)s, "
    r"inside-the-zone rule (?P<pct>[\d.]+)%"
)


def ledger_path(source_path) -> Path:
    """Beside the run's own log, named off the same clip, so a job needs no new
    column to find it."""
    return Path(str(source_path) + LEDGER_SUFFIX)


def write_ledger(source_path, *, threshold, enter_fraction, vehicles,
                 zone_points=None):
    """Called by the detector when a run ends. Never raises: a run that has
    already produced its alerts must not fail at the finish line over a
    bookkeeping file."""
    try:
        path = ledger_path(source_path)
        path.write_text(json.dumps({
            "clip": Path(source_path).name,
            "threshold_seconds": threshold,
            "enter_fraction": enter_fraction,
            # The polygon as fractions of the frame. Kept HERE rather than read
            # off the camera afterwards: the -TEST camera is rewritten by the
            # next run that stages onto it, so by the time anyone evaluates
            # this run the camera no longer holds the area it was judged
            # against.
            "zone_points": zone_points,
            "vehicles": vehicles,
        }, indent=1), encoding="utf-8")
        return path
    except Exception:                                            # noqa: BLE001
        return None


def _clock_to_seconds(text):
    """'4:22' -> 262.0; '-' and '' -> None."""
    text = (text or "").strip()
    if not text or text == "-":
        return None
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(text)
    except ValueError:
        return None


def parse_log(text):
    """The run's printed table, back into rows. For runs older than the ledger.

    Returns (meta, rows). Times are seconds; the table prints m:ss, so they are
    whole seconds here even though the ledger keeps them exact.
    """
    meta, rows = {}, []
    m = HEADER_RE.search(text or "")
    if m:
        meta = {"clip": m.group("clip"),
                "threshold_seconds": float(m.group("threshold")),
                "enter_fraction": float(m.group("pct")) / 100.0}
    for line in (text or "").splitlines():
        hit = ROW_RE.match(line)
        if not hit:
            continue
        rows.append({
            "display_id": int(hit.group("vid")),
            "label": hit.group("label"),
            "first_inside": _clock_to_seconds(hit.group("enter")),
            "last_inside": _clock_to_seconds(hit.group("leave")),
            "alerted_at": _clock_to_seconds(hit.group("alert")),
            "max_score": float(hit.group("dwell")),
            "ever_inside": hit.group("enter").strip() != "-",
            "note": hit.group("note"),
            "box": None,
            "box_at": _clock_to_seconds(hit.group("enter")),
        })
    return meta, rows


def read_ledger(source_path, log_text=None):
    """Everything known about the vehicles in one run.

    -> {"source": "ledger"|"log"|"none", "threshold_seconds", "enter_fraction",
        "vehicles": [...]}
    """
    path = ledger_path(source_path)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["source"] = "ledger"
            return data
        except (OSError, ValueError):
            pass
    meta, rows = parse_log(log_text or "")
    if rows:
        meta.update({"vehicles": rows, "source": "log"})
        return meta
    return {"source": "none", "vehicles": [], "threshold_seconds": None,
            "enter_fraction": None}


def find(vehicles, display_id):
    for row in vehicles:
        if int(row.get("display_id", -1)) == int(display_id):
            return row
    return None


# ---- the verdict -----------------------------------------------------------
#
# The system's half is `alerted`. The person's half is what the vehicle REALLY
# did. Neither alone is a result, which is the whole point of writing them in
# two separate columns.

VERDICTS = {
    ("parked", True): ("TP", "True positive",
                       "It was parked in the area and the system said so."),
    ("parked", False): ("FN", "False negative",
                        "It was parked in the area and the system missed it."),
    ("passing", True): ("FP", "False positive",
                        "It was only passing through and the system alerted anyway."),
    ("passing", False): ("TN", "True negative",
                         "It was only passing through and the system correctly "
                         "left it alone."),
}


def verdict(truth, alerted):
    """`truth` is "parked" or "passing" -- a person's answer, never inferred."""
    return VERDICTS.get((truth, bool(alerted)))


def timer_check(row, truth_enter, threshold, tolerance=None):
    """How close the alert was to when it was due.

    `truth_enter` is the second a person saw the vehicle arrive. Expected =
    that plus the threshold. Returns None when there is nothing to compare --
    no alert, or no ground truth to compare it against, which is most rows.
    """
    alerted = row.get("alerted_at")
    if alerted is None or truth_enter is None or not threshold:
        return None
    expected = float(truth_enter) + float(threshold)
    diff = float(alerted) - expected
    if tolerance is None:
        # 5% of the threshold, never under 2 s: a detector only checks on the
        # frames it processes, and a vehicle must hold still for about a
        # second before the count starts at all, so a small positive diff is
        # built into the design rather than an error.
        tolerance = max(2.0, 0.05 * float(threshold))
    return {"expected": expected, "diff": diff, "tolerance": tolerance,
            "pass": abs(diff) <= tolerance}


# ---- the picture -----------------------------------------------------------


def frame_command(source, at_seconds, out_path):
    """One frame out of the clip at `at_seconds`.

    -ss before -i so ffmpeg seeks instead of decoding from the start; a 300 MB
    clip is otherwise a minute of reading to reach the middle of it. The cost
    is that the frame is keyframe-accurate rather than exact, which for
    identifying WHICH VEHICLE this is does not matter."""
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{max(0.0, float(at_seconds)):.3f}", "-i", str(source),
            "-frames:v", "1", "-q:v", "3", str(out_path)]


def extract_frame(source, at_seconds, out_path, box=None, timeout=90):
    """Write the frame, with the vehicle's box drawn on it when known.

    Returns True on success. The box comes from the ledger; a run evaluated
    from its log alone has none, and an unmarked frame is still worth showing
    -- it is the difference between "which one is it?" and "no picture".
    """
    try:
        r = subprocess.run(frame_command(source, at_seconds, out_path),
                           capture_output=True, timeout=timeout)
        if r.returncode != 0 or not Path(out_path).is_file():
            return False
    except (OSError, subprocess.SubprocessError):
        return False
    if not box:
        return True
    try:
        import cv2
        img = cv2.imread(str(out_path))
        if img is None:
            return True
        x1, y1, x2, y2 = (int(v) for v in box)
        thick = max(2, img.shape[1] // 400)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 140, 255), thick)
        cv2.imwrite(str(out_path), img)
    except Exception:                                            # noqa: BLE001
        pass        # the frame is the point; the box is a convenience
    return True


# ---- why it did or did not alert -------------------------------------------


def explain(row, threshold):
    """The rule, step by step, against one vehicle -- what it met and where it
    stopped.

    This is the part a person is actually shown. "It alerted" and "it did not"
    are both claims that need evidence, and the evidence is the same three
    questions asked in order: was it in the area, did it hold still, did it
    hold still for long enough. A vehicle that fails the first is a different
    kind of non-alert from one that fails the third, and reading which is which
    off a row of numbers is exactly what people get wrong.
    """
    inside = bool(row.get("ever_inside"))
    banked = float(row.get("max_score") or 0.0)
    alerted = row.get("alerted_at") is not None
    threshold = float(threshold or 0)

    steps = [{
        "step": "Entered the no-parking area",
        "met": inside,
        "detail": (f"At least half the vehicle was inside, from "
                   f"{_clock(row.get('first_inside'))}."
                   if inside else
                   "Never had enough of itself inside the area to be judged."),
    }, {
        "step": "Held still",
        "met": banked > 0,
        "detail": (f"Counted {banked:.0f}s of stopped time."
                   if banked > 0 else
                   "Was in the area but moving throughout, and moving vehicles "
                   "count nothing \u2014 traffic is not an obstruction."),
    }, {
        "step": f"Stayed the full {threshold:.0f}s",
        "met": alerted,
        "detail": (f"Reached the threshold at {_clock(row.get('alerted_at'))}."
                   if alerted else
                   f"Reached {banked:.0f}s of the {threshold:.0f}s required"
                   + (f", then left at {_clock(row.get('last_inside'))}."
                      if row.get("last_inside") is not None else ".")),
    }]

    if alerted:
        headline = "The system raised an alert for this vehicle."
    elif not inside:
        headline = "No alert \u2014 it was never inside the area."
    elif banked <= 0:
        headline = "No alert \u2014 it passed through without stopping."
    else:
        headline = (f"No alert \u2014 it stopped, but for {banked:.0f}s of the "
                    f"{threshold:.0f}s required.")
    return {"alerted": alerted, "headline": headline, "steps": steps,
            "banked_seconds": banked, "threshold_seconds": threshold,
            "progress": min(1.0, banked / threshold) if threshold else 0.0}


def _clock(seconds):
    if seconds is None:
        return "\u2014"
    seconds = max(0.0, float(seconds))
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


# ---- the evidence pack -----------------------------------------------------
#
# One picture per vehicle (arrived / stopped / left) and the table, written at
# the end of every run so the evidence exists before anybody asks for it.
# Imports of cv2 and Pillow stay INSIDE the functions: this module is imported
# by views.py, and the hosted API deliberately installs neither.

PANEL_W = 900              # each of the three stills
CAPTION_H = 150
GAP = 14
BG = (13, 16, 22)
CARD = (22, 27, 35)
INK = (232, 236, 242)
SOFT = (151, 163, 180)
AMBER = (240, 162, 74)
GREEN = (74, 222, 128)
GREY = (140, 150, 165)
RED = (248, 113, 113)

# Within this many seconds of where the run has REACHED, "last frame inside"
# means the run has not passed the vehicle yet -- it has not left. The ledger
# is checkpointed while a run is alive, so without this the third panel reports
# a leaving time for a vehicle still sitting in the area: a wrong number going
# straight into an evaluation sheet.
STILL_INSIDE_SLACK = 5.0

MOMENTS = (
    ("arrive", "ARRIVED", "first seen"),
    ("stopped", "STOPPED", "timer starts"),
    ("leave", "LEFT", "last frame inside the area"),
)


def clock(seconds):
    if seconds is None:
        return "—"
    s = max(0.0, float(seconds))
    return f"{int(s) // 60}:{int(s) % 60:02d}"




def _moment_times(row):
    """(arrive, stopped, leave), falling back for a run whose ledger predates
    the three moments: the entry time stands in for arrival, and there is no
    stop time to show rather than a guessed one."""
    m = row.get("moments") or {}
    out = {}
    for key in ("arrive", "stopped", "leave"):
        v = m.get(key)
        out[key] = (v.get("t"), v.get("box")) if isinstance(v, dict) else (None, None)
    if out["arrive"][0] is None:
        out["arrive"] = (row.get("first_inside"), row.get("box"))
    if out["leave"][0] is None:
        out["leave"] = (row.get("last_inside"), row.get("box"))
    return out

def frontier_of(rows):
    """How far into the clip the run has actually got."""
    if not rows:
        return 0.0
    return max((r.get("last_seen") or r.get("first_seen") or 0.0) for r in rows)


def compose_shots(source, row, threshold, zone_points, out_path, frontier=None):
    import subprocess
    import tempfile

    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
    except Exception:                                        # noqa: BLE001
        raise SystemExit("This needs opencv and Pillow installed.")

    if not source.is_file():
        return False
    times = _moment_times(row)
    panels = []
    with tempfile.TemporaryDirectory() as tmp:
        for key, title, note in MOMENTS:
            # "Last frame inside" is only a DEPARTURE if the run has moved on
            # past it. While the run is still alive and working through the
            # clip, that time is just how far it has got, and calling it a
            # leaving time puts a number in an evaluation sheet that the
            # footage does not support.
            if key == "leave" and frontier is not None:
                last = row.get("last_inside")
                if last is not None and frontier - float(last) < STILL_INSIDE_SLACK:
                    title, note = "STILL INSIDE", "the run has not passed it yet"
            at, box = times[key]
            shot = Path(tmp) / f"{key}.jpg"
            if at is None:
                panels.append((title, note, None, None, None))
                continue
            r = subprocess.run(
                frame_command(source, at, shot),
                capture_output=True, timeout=120)
            img = cv2.imread(str(shot)) if r.returncode == 0 and shot.is_file() else None
            panels.append((title, note, img, box, at))

        if all(p[2] is None for p in panels):
            return False

        # every panel scaled to the same width
        shots = []
        for title, note, img, box, at in panels:
            if img is None:
                shots.append((title, note, None, at))
                continue
            h, w = img.shape[:2]
            if zone_points:
                pts = np.array([[int(px * w), int(py * h)] for px, py in zone_points],
                               np.int32)
                overlay = img.copy()
                cv2.fillPoly(overlay, [pts], (0, 191, 255))
                cv2.addWeighted(overlay, 0.18, img, 0.82, 0, img)
                cv2.polylines(img, [pts], True, (0, 191, 255), max(2, w // 700))
            if box:
                x1, y1, x2, y2 = (int(v) for v in box)
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 0), max(3, w // 500))
            k = PANEL_W / w
            img = cv2.resize(img, (PANEL_W, int(h * k)), interpolation=cv2.INTER_AREA)
            shots.append((title, note, img, at))

    ph = max((s[2].shape[0] for s in shots if s[2] is not None), default=360)
    W = PANEL_W * 3 + GAP * 4
    H = 118 + ph + CAPTION_H + GAP
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    F = "C:/Windows/Fonts/"
    try:
        f_h = ImageFont.truetype(F + "seguisb.ttf", 34)
        f_t = ImageFont.truetype(F + "seguisb.ttf", 22)
        f_b = ImageFont.truetype(F + "segoeui.ttf", 19)
        f_v = ImageFont.truetype(F + "consolab.ttf", 22)
        f_l = ImageFont.truetype(F + "seguisb.ttf", 14)
    except OSError:
        f_h = f_t = f_b = f_v = f_l = ImageFont.load_default()

    banked = float(row.get("max_score") or 0)
    alerted = row.get("alerted_at") is not None
    d.text((GAP + 6, 26), f"{row.get('label','vehicle')}  #{row['display_id']}",
           font=f_h, fill=INK)
    verdict = ("ALERTED" if alerted else
               "NO ALERT · never stopped" if banked <= 0 else
               f"NO ALERT · {banked:.0f}s of {threshold:.0f}s")
    vw = d.textlength(verdict, font=f_t)
    d.text((W - GAP - 6 - vw, 34), verdict, font=f_t,
           fill=RED if alerted else (GREEN if banked > 0 else GREY))

    x = GAP
    for title, note, img, at in shots:
        y = 108
        if img is not None:
            sheet.paste(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), (x, y))
        else:
            d.rounded_rectangle([x, y, x + PANEL_W, y + ph], radius=8, fill=CARD)
            # Two different absences, and saying the wrong one is a lie
            # about the detector: a vehicle that banked seconds DID stop,
            # and an empty panel there means the run was too old to have
            # written the moment down.
            if title != "STOPPED":
                msg = "not recorded"
            elif banked > 0:
                msg = "stopped — but this run recorded no time for it"
            else:
                msg = "never stopped"
            mw = d.textlength(msg, font=f_b)
            d.text((x + (PANEL_W - mw) / 2, y + ph / 2 - 10), msg, font=f_b, fill=GREY)
        cy = y + ph + 14
        d.text((x + 4, cy), title, font=f_t, fill=AMBER)
        tw = d.textlength(clock(at), font=f_v)
        d.text((x + PANEL_W - 4 - tw, cy + 2), clock(at), font=f_v, fill=INK)
        d.text((x + 4, cy + 32), note, font=f_b, fill=SOFT)
        d.text((x + 4, cy + 60), "real time: ______", font=f_b, fill=GREY)
        x += PANEL_W + GAP

    still = (frontier is not None and row.get("last_inside") is not None
             and frontier - float(row["last_inside"]) < STILL_INSIDE_SLACK)
    out_txt = "still inside" if still else f"{clock(row.get('last_inside'))} out"
    foot = (f"{clock(row.get('first_inside'))} in · {out_txt} · "
            f"{banked:.0f}s stopped of {threshold:.0f}s"
            + (f" · alerted {clock(row.get('alerted_at'))}" if alerted else ""))
    d.text((GAP + 6, H - 34), foot, font=f_b, fill=SOFT)
    sheet.save(out_path)
    return True



HEADERS = ["Clip", "Vehicle", "Real enter", "System enter", "Real leave",
           "System leave", "Expected alert", "Actual alert", "Zone result",
           "Alert diff", "Timer result", "Notes / miss reason"]

def write_table(clip_name, data, rows, out_dir, stem="vehicles"):
    """The sheet's own columns. The system's are filled; the person's are
    left empty, because a value invented here would be indistinguishable
    from one somebody checked."""
    path = out_dir / f"{stem}.csv"
    clip = clip_name
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([f"Threshold used: {data.get('threshold_seconds')} s",
                    f"Inside-the-zone rule: "
                    f"{round((data.get('enter_fraction') or 0) * 100)}% of the vehicle"])
        w.writerow(HEADERS)
        for r in sorted(rows, key=lambda r: (r.get("first_inside") is None,
                                             r.get("first_inside") or 0)):
            banked = float(r.get("max_score") or 0)
            alerted = r.get("alerted_at") is not None
            note = ("Alerted." if alerted else
                    "In the area but moving throughout — banked nothing."
                    if banked <= 0 else
                    f"Stopped, reached {banked:.0f}s of "
                    f"{data.get('threshold_seconds'):.0f}s, then left.")
            w.writerow([
                clip, f"{r.get('label','')} (#{r.get('display_id')})",
                "", clock(r.get("first_inside")),
                "", clock(r.get("last_inside")),
                "", clock(r.get("alerted_at")),
                "", "", "", note,
            ])
    return path


def _time_in_zone(row):
    a, b = row.get("first_inside"), row.get("last_inside")
    return (b - a) if (a is not None and b is not None) else 0.0


def choose(rows, alerted=1, short=1, passing=3):
    """A BALANCED sample of the run, not simply everything that happened.

    A component test needs one of each kind of outcome, and a run supplies
    them in wildly different numbers: hundreds of pass-bys, a handful that
    stopped, and if you are lucky one alert. Picturing all of them buries the
    three rows that carry the argument.

    One of each, and the most useful specimen of each:

      ALERTED      the true positive. Earliest, so the clip can be watched
                   forward from it.
      STOPPED SHORT  parked, but left before the threshold -- the case that
                   shows the rule is "stayed", not "was there". The one that
                   got CLOSEST is chosen: a car that banked 58 of 60 seconds
                   is a far better question to put to a panel than one that
                   managed two.
      PASSING      drove through and banked nothing: the true negatives. Those
                   that spent LONGEST in the area, because a vehicle that
                   clipped the corner for half a second proves nothing, while
                   one that drove the full length of it and still banked
                   nothing is the case worth showing.
    """
    def banked(r):
        return float(r.get("max_score") or 0)

    did_alert = sorted([r for r in rows if r.get("alerted_at") is not None],
                       key=lambda r: r.get("alerted_at") or 0)
    stopped_short = sorted([r for r in rows
                            if r.get("alerted_at") is None and banked(r) > 0],
                           key=banked, reverse=True)
    passed_by = sorted([r for r in rows
                        if r.get("alerted_at") is None and banked(r) <= 0
                        and r.get("ever_inside")],
                       key=_time_in_zone, reverse=True)

    picked, seen = [], set()
    for group, quota in ((did_alert, alerted), (stopped_short, short),
                         (passed_by, passing)):
        for row in group[:max(0, int(quota))]:
            if id(row) not in seen:
                seen.add(id(row))
                picked.append(row)
    return picked


def build_pack(source, data, out_dir, only_mattering=True, quotas=None, log=None):
    """Write the shots and the table for one run. -> (pictures, table path).

    Never raises. A run that has already produced its alerts must not fail at
    the finish line because a frame could not be cut."""
    say = log or (lambda m: None)
    try:
        rows = data.get("vehicles") or []
        if only_mattering:
            rows = choose(rows, **(quotas or {}))
        if not rows:
            return [], None
        source = Path(str(source))
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        threshold = data.get("threshold_seconds") or 0
        zone_points = data.get("zone_points")
        frontier = frontier_of(data.get("vehicles") or rows)
        made = []
        for row in sorted(rows, key=lambda r: (r.get("first_inside") is None,
                                               r.get("first_inside") or 0)):
            vid = int(row["display_id"])
            path = out_dir / f"vehicle_{vid:04d}.png"
            try:
                if compose_shots(source, row, threshold, zone_points, path, frontier):
                    made.append(path)
                    say(f"  #{vid} -> {path.name}")
            except Exception as exc:                             # noqa: BLE001
                say(f"  #{vid}: no picture ({exc})")
        table = write_table(source.stem, data, rows, out_dir)
        return made, table
    except Exception as exc:                                     # noqa: BLE001
        say(f"  evidence pack skipped: {exc}")
        return [], None
