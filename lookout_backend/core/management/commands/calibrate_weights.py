"""Fit the scoring weights to OUR footage instead of to reasoned defaults.

WHY THIS COMMAND IS THE POINT
-----------------------------
Every weight in core/vision/scoring.py is currently an initial value, set from
how central each indicator is to the violation as the cited literature
describes it. That is a defensible starting point and nothing more: no
published study gives weights for this particular combination of indicators on
barangay CCTV.

The stronger justification is this command. Fitting a logistic regression to
labelled clips from the deployment cameras produces, by construction, the
coefficients that best separate violations from non-violations in OUR footage
-- and the precision/recall it reports is a measured result rather than an
assertion.

HOW TO USE IT
-------------
1. Run the detectors over real footage. Every scored event stores its full cue
   vector on the Alert (``Alert.cues``), including events that scored too low
   to dispatch -- those are the labelled negatives, and without them the fit
   has only positives to learn from.
2. Label each alert: set ``Alert.reviewed_valid`` to True (a real violation) or
   False (a false positive). Unlabelled alerts are skipped.
3. Run this command. It reports the current weights' precision/recall, the
   fitted weights, and the fitted precision/recall, so the before/after is a
   number you can put in the paper.
4. Copy the fitted values into scoring.py and state in the write-up that the
   method is cited while the values are calibrated on project data.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not write scoring.py. A weight change silently applied by a script is
a weight nobody reviewed, and these values decide what gets reported to a
barangay official. The command prints a diff; a human applies it.

It also refuses to fit on too little data rather than producing a confident-
looking number from twelve clips.
"""

import json

from django.core.management.base import BaseCommand

from core.models import Alert
from core.vision import scoring

# Below this, a fit is noise dressed up as a result. The scoring document asks
# for 50-100 labelled clips; this is the floor at which the command will run at
# all, and it still warns loudly under 50.
MIN_SAMPLES = 20
RECOMMENDED_SAMPLES = 50

# Both classes must actually be present. A dataset of 60 positives and 0
# negatives has nothing to separate and will happily "fit" every weight to
# infinity.
MIN_PER_CLASS = 5

# Plain batch gradient descent on the logistic loss. No sklearn: the only
# dependency here is numpy, which the deployment already carries, and the
# problem is a handful of features over a few dozen rows.
LEARNING_RATE = 0.5
ITERATIONS = 4000
# L2 penalty. Small, but not zero: with few samples and correlated cues (a
# bottle at the mouth implies a bottle) an unpenalised fit drives coefficients
# apart arbitrarily. This keeps them in a range that still reads as weights.
L2 = 0.02

WEIGHT_SETS = {
    "drinking": scoring.DRINKING_WEIGHTS,
    "smoking": scoring.SMOKING_WEIGHTS,
}


class Command(BaseCommand):
    help = (
        "Fits scoring weights to labelled alerts by logistic regression and "
        "reports the precision/recall before and after. Prints a proposed "
        "diff; never edits scoring.py."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "kind", choices=sorted(WEIGHT_SETS),
            help="Which weight set to calibrate.",
        )
        parser.add_argument(
            "--min-samples", type=int, default=MIN_SAMPLES,
            help=f"Refuse to fit below this many labelled alerts "
                 f"(default {MIN_SAMPLES}).",
        )
        parser.add_argument(
            "--threshold", type=float, default=scoring.SCORE_WARNING,
            help="Score at or above which an alert counts as dispatched, for "
                 "the precision/recall figures (default: the WARNING band).",
        )
        parser.add_argument(
            "--json", action="store_true",
            help="Emit the fitted weights as JSON instead of a report.",
        )

    def handle(self, *args, **options):
        kind = options["kind"]
        weights = WEIGHT_SETS[kind]

        as_json = options["json"]

        rows, labels, skipped = self._load(kind)
        if skipped and not as_json:
            self.stdout.write(self.style.WARNING(
                f"Skipped {skipped} alert(s) with no cue vector or no review label."
            ))

        n = len(rows)
        if n < options["min_samples"]:
            self.stdout.write(self.style.ERROR(
                f"Only {n} labelled alert(s) for '{kind}'; need at least "
                f"{options['min_samples']}. Label more alerts "
                f"(Alert.reviewed_valid) before fitting -- a fit on this much "
                f"data would be noise with a confidence interval."
            ))
            return

        positives = sum(labels)
        negatives = n - positives
        if positives < MIN_PER_CLASS or negatives < MIN_PER_CLASS:
            self.stdout.write(self.style.ERROR(
                f"Need at least {MIN_PER_CLASS} of each class; have "
                f"{positives} violation(s) and {negatives} false positive(s). "
                f"A single-class dataset has nothing to separate."
            ))
            return

        if n < RECOMMENDED_SAMPLES and not as_json:
            self.stdout.write(self.style.WARNING(
                f"{n} samples is below the {RECOMMENDED_SAMPLES}-100 the "
                f"scoring document asks for. Treat the result as indicative "
                f"and say so in the write-up."
            ))

        features = sorted({name for row in rows for name in row} | set(weights))
        fitted = self._fit(rows, labels, features)

        threshold = options["threshold"]
        before = self._evaluate(rows, labels, weights, threshold)
        after = self._evaluate(rows, labels, fitted, threshold)

        if as_json:
            # Machine-readable mode emits JSON and NOTHING else, so it can be
            # piped. Every advisory above is suppressed rather than mixed in.
            self.stdout.write(json.dumps(
                {name: round(v, 3) for name, v in sorted(fitted.items())},
                indent=2,
            ))
            return

        self._report(kind, n, positives, negatives, features,
                     weights, fitted, before, after, threshold)

    # ---- data --------------------------------------------------------------

    def _load(self, kind):
        """Labelled cue vectors for one violation kind.

        A cue counts as FIRED if it is present in the stored vector. The stored
        vector holds the weights that applied at detection time, which is
        exactly the audit trail we want -- but the fit only needs presence, so
        the historical weight is discarded here.
        """
        rows, labels, skipped = [], [], 0
        queryset = Alert.objects.exclude(cues={}).order_by("timestamp")
        for alert in queryset.iterator():
            vector = alert.cues or {}
            if vector.get("kind") != kind:
                continue
            if alert.reviewed_valid is None:
                skipped += 1
                continue
            fired = set(vector.get("cues") or {})
            if not fired:
                skipped += 1
                continue
            rows.append(fired)
            labels.append(1 if alert.reviewed_valid else 0)
        return rows, labels, skipped

    # ---- the fit -----------------------------------------------------------

    def _fit(self, rows, labels, features):
        """Batch gradient descent on the L2-penalised logistic loss.

        Returns a weight per feature, rescaled so the values read on the same
        0-1 scale the hand-set weights use. The rescaling is cosmetic -- it
        preserves every ratio between coefficients, which is what the fit
        actually determines -- but it keeps the output comparable to what it
        replaces and keeps the band thresholds meaningful.
        """
        import numpy as np

        index = {name: i for i, name in enumerate(features)}
        X = np.zeros((len(rows), len(features)), dtype=float)
        for r, fired in enumerate(rows):
            for name in fired:
                if name in index:
                    X[r, index[name]] = 1.0
        y = np.asarray(labels, dtype=float)

        w = np.zeros(len(features))
        b = 0.0
        m = float(len(rows))
        for _ in range(ITERATIONS):
            z = X @ w + b
            # Numerically stable sigmoid: np.exp(710) overflows, and a long
            # run on separable data reaches that easily.
            p = np.where(z >= 0, 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500))),
                         np.exp(np.clip(z, -500, 500)) / (1.0 + np.exp(np.clip(z, -500, 500))))
            error = p - y
            w -= LEARNING_RATE * ((X.T @ error) / m + L2 * w)
            b -= LEARNING_RATE * (error.sum() / m)

        # Negative coefficients mean "this cue argues AGAINST a violation".
        # Clamped to zero: the model is an accumulation of evidence, and a
        # negative additive weight would let one cue cancel unrelated evidence
        # (the same reason the vendor context is a multiplier, not a weight).
        # A clamped cue is reported so the operator can consider dropping it.
        w = np.clip(w, 0.0, None)

        peak = float(w.max()) if w.size and w.max() > 0 else 1.0
        # Scale so the strongest fitted cue lands at 0.45 -- the weight of the
        # strongest hand-set cue (E14 / at_mouth), keeping the fitted set on
        # the same scale the bands were set against.
        scale = 0.45 / peak
        return {name: float(w[i] * scale) for name, i in index.items()}

    # ---- evaluation --------------------------------------------------------

    def _evaluate(self, rows, labels, weights, threshold):
        """Precision / recall / F1 of one weight set at one threshold."""
        tp = fp = fn = tn = 0
        for fired, label in zip(rows, labels):
            score = min(1.0, sum(weights.get(name, 0.0) for name in fired))
            dispatched = score >= threshold
            if dispatched and label:
                tp += 1
            elif dispatched and not label:
                fp += 1
            elif not dispatched and label:
                fn += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) else 0.0)
        return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "precision": precision, "recall": recall, "f1": f1}

    # ---- report ------------------------------------------------------------

    def _report(self, kind, n, positives, negatives, features,
                current, fitted, before, after, threshold):
        w = self.stdout.write
        w(self.style.SUCCESS(f"\nWeight calibration: {kind}"))
        w(f"  {n} labelled alerts  ({positives} violations, "
          f"{negatives} false positives)")
        w(f"  decision threshold: {threshold:.2f}\n")

        w(self.style.SUCCESS("Weights"))
        w(f"  {'cue':<22} {'current':>9} {'fitted':>9} {'change':>9}")
        for name in features:
            now = current.get(name)
            new = fitted.get(name, 0.0)
            if now is None:
                w(f"  {name:<22} {'--':>9} {new:>9.2f} {'NEW':>9}")
                continue
            delta = new - now
            flag = ""
            if new == 0.0:
                # Either the fit found no signal, or it found a negative one
                # that was clamped. Both mean the same thing operationally.
                flag = "  <- no signal in this data; consider dropping"
            w(f"  {name:<22} {now:>9.2f} {new:>9.2f} {delta:>+9.2f}{flag}")

        missing = [name for name in current if name not in features]
        if missing:
            w(self.style.WARNING(
                f"\n  Never observed in the labelled data, so not fitted: "
                f"{', '.join(sorted(missing))}. Their current weights stand "
                f"unvalidated -- say so rather than implying they were fitted."
            ))

        w(self.style.SUCCESS("\nPerformance"))
        w(f"  {'':<10} {'precision':>10} {'recall':>8} {'F1':>7} "
          f"{'TP':>5} {'FP':>5} {'FN':>5} {'TN':>5}")
        for label, stats in (("current", before), ("fitted", after)):
            w(f"  {label:<10} {stats['precision']:>10.3f} "
              f"{stats['recall']:>8.3f} {stats['f1']:>7.3f} "
              f"{stats['tp']:>5} {stats['fp']:>5} "
              f"{stats['fn']:>5} {stats['tn']:>5}")

        delta_f1 = after["f1"] - before["f1"]
        if delta_f1 > 0.01:
            w(self.style.SUCCESS(
                f"\n  F1 improves by {delta_f1:+.3f} on this data."))
        elif delta_f1 < -0.01:
            w(self.style.WARNING(
                f"\n  F1 gets WORSE by {delta_f1:+.3f}. Report this rather "
                f"than quietly keeping the hand-set weights: it means the "
                f"reasoned defaults already fit this footage, or that there "
                f"is not enough labelled data to improve on them."))
        else:
            w(f"\n  F1 essentially unchanged ({delta_f1:+.3f}).")

        w(self.style.WARNING(
            "\n  These figures are measured on the SAME data the weights were "
            "fitted to, so they are optimistic by construction. For a number "
            "to defend, hold out a test split the fit never saw."
        ))
        w("\n  Nothing has been written. Copy the fitted column into "
          "core/vision/scoring.py by hand if you accept it.\n")
