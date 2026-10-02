"""Run the AI context checker over alerts that already exist.

The checker normally runs live, inside the watcher, on the frames that produced
the alert. On hardware that cannot answer in seconds that is impractical -- a
call measured at 515s on a CPU-only laptop blocks the frame loop or times out,
and the alert publishes with no context at all.

This command decouples the two. Detection runs at full speed with the checker
off; afterwards, this reads each alert's own saved evidence image and fills in
the context at whatever pace the machine manages. Nothing waits on it.

    python manage.py enrich_alert --latest 1
    python manage.py enrich_alert ALT-0232 ALT-0231
    python manage.py enrich_alert --latest 5 --missing-only

What it CANNOT recover is motion. The live path sends three crops about a second
apart, which is what `hand_to_mouth_activity` compares; a saved alert has one
still. So a smoking verdict from here is weaker than one from a live run, and
that difference should be reported rather than glossed over.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import Alert, SystemSettings
from core.vision import scoring, vlm

# Alert.type.code -> the prompt spec that fits it. watch_thief files several
# patterns under one type, and all of them ask the holdup questions.
KIND_BY_TYPE = {
    "drinking": "drinking",
    "smoking": "smoking",
    "theft": "holdup",
    "holdup": "holdup",
}


class Command(BaseCommand):
    help = "Run the AI context checker over existing alerts' evidence images."

    def add_arguments(self, parser):
        parser.add_argument("codes", nargs="*",
                            help="Alert codes, e.g. ALT-0232. Omit and use --latest.")
        parser.add_argument("--latest", type=int, default=0,
                            help="Enrich the N most recent alerts instead.")
        parser.add_argument("--missing-only", action="store_true",
                            help="Skip alerts that already have a reading.")
        parser.add_argument("--rescore", action="store_true",
                            help="Also apply the scene multiplier to the stored "
                                 "score. OFF by default: the score was computed "
                                 "live from what the detector actually saw, and "
                                 "rewriting it afterwards from a single still "
                                 "would misrepresent how the alert was reached.")

    def handle(self, *args, **options):
        alerts = self._select(options)
        if not alerts:
            self.stdout.write(self.style.WARNING("No alerts matched."))
            return

        cfg = SystemSettings.load()
        # Deliberately ignores cfg.vlm_enabled. That switch exists to keep the
        # checker out of the LIVE path on slow hardware; running it here is an
        # explicit, blocking request where waiting is the whole point.
        verifier = vlm.build_verifier(
            enabled=True, provider=cfg.vlm_provider,
            api_key=cfg.vlm_api_key or None, model=cfg.vlm_model,
            # An hour. This command is explicitly the blocking, offline path --
            # nothing is waiting on it, so giving up early only wastes the work
            # already done. The live path has its own, much shorter, timeout.
            timeout=max(cfg.vlm_timeout, 3600), endpoint=cfg.vlm_endpoint)
        # Respect the configured per-call cost. This used to send whatever the
        # module defaults were -- 2 images at 1024px -- which on CPU-only
        # hardware is the heaviest possible call and timed out at 15 minutes
        # while the Settings said 512px. The cheap path has to be the one the
        # operator chose, everywhere.
        self.cost = {
            "max_edge": cfg.vlm_max_edge,
            "send_scene": cfg.vlm_send_scene,
            "max_images": (1 if cfg.vlm_send_scene else 0) + cfg.vlm_frames,
        }
        self.stdout.write(vlm.describe(verifier, cfg.vlm_model))
        self.stdout.write(
            f"  per call: {self.cost['max_images']} image(s) @ "
            f"{self.cost['max_edge']}px")
        if isinstance(verifier, vlm.DisabledVerifier):
            self.stdout.write(self.style.ERROR(
                "Nothing to run. Fix the reason above and try again."))
            return
        if not cfg.vlm_enabled:
            self.stdout.write(self.style.WARNING(
                "(the checker is switched off for live runs -- running it here "
                "anyway, which is what this command is for)"))

        self.stdout.write("")
        for alert in alerts:
            self._enrich(alert, verifier, options["rescore"])

    # ------------------------------------------------------------------ parts
    def _select(self, options):
        qs = Alert.objects.select_related("type").order_by("-timestamp")
        if options["codes"]:
            qs = qs.filter(code__in=options["codes"])
        elif options["latest"]:
            qs = qs[:options["latest"]]
        else:
            self.stdout.write(self.style.ERROR(
                "Give alert codes, or --latest N."))
            return []
        alerts = list(qs)
        if options["missing_only"]:
            alerts = [a for a in alerts if not a.vlm_reason]
        return alerts

    def _enrich(self, alert, verifier, rescore):
        import cv2

        label = f"{alert.code} ({alert.type.code if alert.type else '?'})"
        kind = KIND_BY_TYPE.get(alert.type.code if alert.type else "")
        if kind is None:
            self.stdout.write(f"  {label}: no prompt spec for this type, skipped")
            return

        path = self._image_path(alert)
        if path is None:
            self.stdout.write(self.style.WARNING(
                f"  {label}: evidence image missing, skipped"))
            return

        frame = cv2.imread(str(path))
        if frame is None:
            self.stdout.write(self.style.WARNING(
                f"  {label}: could not read {path.name}, skipped"))
            return

        self.stdout.write(f"  {label}: asking... ", ending="")
        self.stdout.flush()
        # box=None: the saved evidence frame is already the scene, and the
        # person box that produced it was not stored. Sending it whole is both
        # honest and what the scene questions need.
        verdict = vlm.verify_frame(verifier, frame, kind, box=None, **self.cost)

        if not verdict.ok:
            self.stdout.write(self.style.ERROR(f"failed ({verdict.error[:60]})"))
            return

        alert.vlm_verdict = verdict.verdict
        alert.vlm_confidence = verdict.confidence
        alert.vlm_reason = verdict.reason
        cues = dict(alert.cues or {})
        cues["vlm"] = verdict.as_dict()
        # Recorded so nobody later mistakes this for a live reading. The live
        # path sees three frames a second apart; this saw one still.
        cues["vlm"]["source"] = "enriched from saved evidence (single frame)"

        if rescore and verdict.enum_multipliers():
            factor = min(verdict.enum_multipliers().values())
            alert.confidence = round(min(1.0, alert.confidence * factor), 4)
            cues["score"] = alert.confidence
            cues["level"] = scoring.level_of(alert.confidence)
            cues["label"] = scoring.label_of(cues["level"])
            alert.level = cues["level"]

        alert.cues = cues
        alert.save(update_fields=["vlm_verdict", "vlm_confidence", "vlm_reason",
                                  "cues", "confidence", "level"])

        ordinary = verdict.enum_multipliers()
        flag = self.style.WARNING(" [reads as ordinary activity]") if ordinary else ""
        self.stdout.write(self.style.SUCCESS(
            f"{verdict.verdict} ({verdict.tier}, {verdict.latency:.0f}s)") + flag)
        self.stdout.write(f'      "{verdict.reason}"')
        for name, value in sorted(verdict.cues.items()):
            self.stdout.write(f"      {name:<38}: {value}")

    @staticmethod
    def _image_path(alert):
        """Alert.image_url -> a file under MEDIA_ROOT, or None."""
        if not alert.image_url:
            return None
        name = Path(alert.image_url).name
        path = Path(settings.MEDIA_ROOT) / "violations" / name
        return path if path.exists() else None
