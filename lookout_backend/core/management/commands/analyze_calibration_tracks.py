"""Phase A (v2 brief) — re-analyse the Phase 0 calibration CSVs at TRACK level,
grouped by the `track_id` column watch_merged's --calibration-csv already
writes. No new video runs.

Why this exists: `summarize_calibration`'s per-class max/p99 confidence is a
max-over-the-WHOLE-VIDEO statistic. It is not the statistic the real alert
path ever consults. Per `core/vision/tracking.py`, `Track.present()` gates
purely on FRAME COUNT — `hits >= VOTE_MIN_FRAMES` and
`hits >= len(votes) * VOTE_MIN_RATIO` over a rolling `VOTE_WINDOW_SECONDS`
window — and never reads confidence at all. Confidence only re-enters
afterwards, in `best_detection()`, to pick which box/label to REPORT in an
alert whose admission was already decided. So the question this command
answers is: of the tracks that would actually have cleared vote+dwell (the
gates that decide whether an alert fires), what does their confidence look
like — mean/median, not max-over-video — and does THAT separate null from
real positives?

Known approximations (the raw CSV records detections only, not the
person-tracker's own per-frame presence/miss timeline, so the exact
vote/dwell state machine in tracking.py can't be replayed frame-for-frame):

  * `votes` (the window's total frame count, hit + miss) is approximated as
    `round(fps * VOTE_WINDOW_SECONDS)` — an UPPER bound on the true
    denominator (the track could not have existed for MORE than fps*window
    frames in `window` seconds). Since the true ratio is
    hits / true_denominator >= hits / this_estimate, a track that clears the
    ratio test against this estimate would DEFINITELY clear the real one —
    this is a conservative (harder-to-pass) proxy, not a lenient one. A track
    that fails this proxy might still have passed the real gate (a possible
    false negative in the "survivors" list), but nothing here is a false
    positive.
  * Dwell accrual is replayed using only the HIT timestamps as tick points
    (real code ticks every processed frame, hit or miss). A gap between two
    consecutive hits longer than PRESENCE_GRACE_SECONDS resets accrued dwell
    to 0 (mirrors the real reset-on-absence rule); a gap longer than
    ACCRUAL_STALE_SECONDS but within the grace period pauses accrual without
    resetting it; a gap at or below ACCRUAL_STALE_SECONDS credits the full
    elapsed time, exactly as `Track.tick()` does when `accruing()` is true
    every frame in between.
  * Bottle's real per-class dwell is POSTURE-scaled (`at-mouth` x1.0,
    `held` x2.0 of the base drinking_dwell) via watch_drinking._dwell_for,
    and posture requires a face-proximity check this CSV has no data for.
    This command uses the UNSCALED base drinking_dwell (the lower, easier
    bound) — flagged explicitly in the output — since posture can't be
    reconstructed from raw detection boxes alone.
"""
import csv
import statistics
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from core.vision import tracking

# Per-class dwell seconds, unscaled (CLASS_POLICY conf_scale/dwell_scale for
# Cigarette/knife is 1.0 in watch_smoking/watch_thief, so base dwell applies
# directly). Bottle uses drinking's UNSCALED base dwell — see module docstring.
# These are the SystemSettings values loaded during the Sep 7-8 calibration
# runs (smoking_dwell=3, drinking_dwell=8, thief_dwell=3); re-check
# SystemSettings.load() before reusing this table against a different run.
CLASS_DWELL_SECONDS = {
    "cigarette": 3.0,
    "bottle": 8.0,
    "knife": 3.0,
}

VOTE_WINDOW_SECONDS = tracking.VOTE_WINDOW_SECONDS
VOTE_MIN_FRAMES = tracking.VOTE_MIN_FRAMES
VOTE_MIN_RATIO = tracking.VOTE_MIN_RATIO
PRESENCE_GRACE_SECONDS = 2.0   # matches watch_smoking/watch_thief/watch_drinking's own constant
ACCRUAL_STALE_SECONDS = 0.5    # tracking.ACCRUAL_STALE_SECONDS


def _simulate_track(hits, fps):
    """Replays the vote/dwell state machine over one track's hit timestamps
    (sorted list of (timestamp, confidence)). Returns (max_dwell_achieved,
    ever_present, best_ratio_seen) — see module docstring for the
    approximations this makes relative to the real frame-by-frame version.
    """
    window = []
    dwell_held = 0.0
    max_dwell = 0.0
    last_ts = None
    ever_present = False
    best_ratio = 0.0
    votes_estimate = max(1, round(fps * VOTE_WINDOW_SECONDS))

    for ts, _conf in hits:
        window.append(ts)
        cutoff = ts - VOTE_WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.pop(0)
        hits_in_window = len(window)
        ratio = hits_in_window / votes_estimate
        best_ratio = max(best_ratio, ratio)
        present_ok = hits_in_window >= VOTE_MIN_FRAMES and hits_in_window >= VOTE_MIN_RATIO * votes_estimate
        ever_present = ever_present or present_ok

        if last_ts is None:
            gap = None
        else:
            gap = ts - last_ts

        if present_ok:
            if gap is not None and gap <= ACCRUAL_STALE_SECONDS:
                dwell_held += gap
            # gap between STALE and GRACE: accrual pauses, nothing added, no reset
        if gap is not None and gap > PRESENCE_GRACE_SECONDS:
            dwell_held = 0.0

        max_dwell = max(max_dwell, dwell_held)
        last_ts = ts

    return max_dwell, ever_present, best_ratio


class Command(BaseCommand):
    help = (
        "Phase A: re-analyse watch_merged --calibration-csv output at TRACK "
        "level (grouped by track_id) instead of raw per-frame max confidence. "
        "Reports which tracks would have cleared the real vote+dwell gates in "
        "core/vision/tracking.py, and their mean/median (not max) confidence."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_specs", nargs="+",
            help="One or more PATH[:FPS] specs, e.g. "
                 "calibration_null_2026-09-07.csv:20.0 "
                 "calibration_positive_cigarette_2026-09-07.csv:10.01 "
                 "calibration_positive_bottle_2026-09-07.csv:10.0 "
                 "(FPS defaults to 10.0 if omitted — the source video's native "
                 "frame rate, needed to approximate the vote window's frame "
                 "count; see the module docstring).",
        )
        parser.add_argument("--markdown", default="",
                            help="Optional path to also write the report as Markdown.")

    def handle(self, *args, **options):
        lines = []
        for spec in options["csv_specs"]:
            path, _, fps_str = spec.partition(":")
            try:
                fps = float(fps_str) if fps_str else 10.0
            except ValueError:
                raise CommandError(f"Invalid FPS in spec {spec!r}")
            lines.extend(self._analyze_file(path, fps))
            lines.append("")

        text = "\n".join(lines)
        self.stdout.write(text)
        if options["markdown"]:
            with open(options["markdown"], "w", encoding="utf-8") as f:
                f.write(text)
            self.stdout.write(self.style.SUCCESS(f"\nWritten to {options['markdown']}"))

    def _analyze_file(self, path, fps):
        try:
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except FileNotFoundError:
            return [self.style.ERROR(f"{path}: not found")]

        out = [self.style.SUCCESS(f"=== {path}  (fps={fps}) ===")]

        by_class_track = defaultdict(list)  # (class, track_id) -> [(ts, conf)]
        scene_count = defaultdict(int)      # class -> count of blank-track_id rows
        for r in rows:
            cls = r["class_name"]
            if not r["track_id"]:
                scene_count[cls] += 1
                continue
            by_class_track[(cls, r["track_id"])].append(
                (float(r["timestamp"]), float(r["confidence"])))

        classes = sorted({cls for cls, _ in by_class_track})
        for cls in classes:
            required_dwell = CLASS_DWELL_SECONDS.get(cls.lower())
            out.append(f"\n-- {cls} "
                       f"(required dwell ~{required_dwell:.0f}s"
                       f"{' [Bottle: unscaled/held-posture lower bound]' if cls.lower() == 'bottle' else ''}) --")
            out.append(f"  discarded as scene (no person track): {scene_count.get(cls, 0)} rows")

            tracks = [(tid, sorted(pts)) for (c, tid), pts in by_class_track.items() if c == cls]
            out.append(f"  distinct tracks: {len(tracks)}")

            survivors = []
            for tid, pts in tracks:
                confs = [c for _, c in pts]
                max_dwell, ever_present, best_ratio = _simulate_track(pts, fps)
                passed_vote = ever_present
                passed_dwell = required_dwell is not None and max_dwell >= required_dwell
                if passed_vote and passed_dwell:
                    survivors.append({
                        "track_id": tid,
                        "hits": len(pts),
                        "span": pts[-1][0] - pts[0][0],
                        "max_dwell": max_dwell,
                        "mean_conf": statistics.mean(confs),
                        "median_conf": statistics.median(confs),
                        "max_conf": max(confs),
                        "best_ratio": best_ratio,
                    })

            out.append(f"  tracks surviving vote (hits>=2, hits>=0.4*votes) + "
                       f"dwell>={required_dwell:.0f}s: {len(survivors)}")
            if survivors:
                out.append(f"    {'track':<8}{'hits':>6}{'span_s':>8}{'dwell_s':>9}"
                           f"{'mean_conf':>11}{'median_conf':>13}{'max_conf':>10}")
                for s in sorted(survivors, key=lambda s: -s["mean_conf"]):
                    out.append(
                        f"    {s['track_id']:<8}{s['hits']:>6}{s['span']:>8.1f}"
                        f"{s['max_dwell']:>9.1f}{s['mean_conf']:>11.3f}"
                        f"{s['median_conf']:>13.3f}{s['max_conf']:>10.3f}"
                    )
                all_means = [s["mean_conf"] for s in survivors]
                all_medians = [s["median_conf"] for s in survivors]
                out.append(f"  survivors' mean-confidence range: "
                           f"{min(all_means):.3f}-{max(all_means):.3f}  "
                           f"(median-confidence range: {min(all_medians):.3f}-{max(all_medians):.3f})")

        return out
