"""Phase-0 calibration summary: reads one or more raw-detection CSVs produced
by `watch_merged --calibration-csv` and prints, per (source file, class), the
max confidence, 99th-percentile confidence, counts at the 0.5/0.6/0.7/0.8
thresholds, and the box-area distribution.

The null-video row's max confidence per class is the empirical ceiling that
justifies every alert threshold chosen downstream — the point of this command
is to turn calibration_null_<date>.csv / calibration_positive_<date>.csv into
that table (see LookOut_v2/lookout/index.html Phase 0 brief) instead of eyeballing
the raw CSV.
"""
import csv
import math
from collections import defaultdict

from django.core.management.base import BaseCommand

THRESHOLDS = (0.5, 0.6, 0.7, 0.8)


def _percentile(sorted_vals, pct):
    """Nearest-rank percentile over an already-sorted list."""
    if not sorted_vals:
        return None
    k = max(0, min(len(sorted_vals) - 1, math.ceil(pct / 100 * len(sorted_vals)) - 1))
    return sorted_vals[k]


class Command(BaseCommand):
    help = (
        "Summarize one or more calibration CSVs (from `watch_merged "
        "--calibration-csv`) into a per-class confidence/box-area table."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_paths", nargs="+",
                            help="One or more calibration CSV files, e.g. "
                                 "calibration_null_2026-09-07.csv "
                                 "calibration_positive_2026-09-07.csv")
        parser.add_argument("--markdown", default="",
                            help="Optional path to also write the table as a "
                                 "Markdown file (for pasting into a thesis chapter).")

    def handle(self, *args, **options):
        lines = []
        for path in options["csv_paths"]:
            lines.extend(self._summarize_file(path))
            lines.append("")

        text = "\n".join(lines)
        self.stdout.write(text)

        if options["markdown"]:
            with open(options["markdown"], "w", encoding="utf-8") as f:
                f.write(text)
            self.stdout.write(self.style.SUCCESS(f"\nWritten to {options['markdown']}"))

    def _summarize_file(self, path):
        by_class = defaultdict(list)  # class_name -> [(confidence, box_area_px), ...]
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    by_class[row["class_name"]].append(
                        (float(row["confidence"]), int(row["box_area_px"])))
        except FileNotFoundError:
            return [self.style.ERROR(f"{path}: not found")]

        out = [self.style.SUCCESS(f"=== {path} ===")]
        if not by_class:
            out.append("  (no detections in file)")
            return out

        header = (f"  {'class':<12} {'n':>6} {'max_conf':>9} {'p99_conf':>9} "
                  + " ".join(f">={t:.1f}".rjust(7) for t in THRESHOLDS)
                  + f" {'area_min':>9} {'area_p50':>9} {'area_max':>9}")
        out.append(header)

        for cls in sorted(by_class):
            rows = by_class[cls]
            confs = sorted(c for c, _ in rows)
            areas = sorted(a for _, a in rows)
            n = len(confs)
            max_conf = confs[-1]
            p99 = _percentile(confs, 99)
            counts = [sum(1 for c in confs if c >= t) for t in THRESHOLDS]
            area_min, area_max = areas[0], areas[-1]
            area_p50 = _percentile(areas, 50)

            out.append(
                f"  {cls:<12} {n:>6} {max_conf:>9.3f} {p99:>9.3f} "
                + " ".join(f"{c:>7d}" for c in counts)
                + f" {area_min:>9d} {area_p50:>9d} {area_max:>9d}"
            )

        return out
