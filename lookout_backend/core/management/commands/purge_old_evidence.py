"""Deletes old alert evidence files and clears their URLs; keeps the Alert rows.

Why: RA 10173 (Data Privacy Act) storage limitation -- footage of identifiable
people should not be kept longer than it is needed. The Alert records
themselves (type, time, camera, score, status, citations) are NOT touched; only
the evidence image, annotated clip and raw clip are deleted, and the three URL
fields are blanked so the dashboard shows "no evidence" instead of a dead link.

  python manage.py purge_old_evidence --dry-run          # show what WOULD go
  python manage.py purge_old_evidence                    # delete (uses Settings' retention days)
  python manage.py purge_old_evidence --days 14
  python manage.py purge_old_evidence --auto             # scheduler entry point: no-op unless Settings'
                                                         # "auto-purge" is ON (it is OFF by default)

Nothing calls this automatically. Evidence from uploaded-footage test runs (cameras
whose code ends in "-TEST", kept for accuracy evaluation) is skipped unless
--include-test is given.
"""
import datetime
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.models import Alert, SystemSettings

EVIDENCE_FIELDS = ("image_url", "video_url", "raw_video_url")


def _local_file(url, root):
    """The file an evidence URL points at, or None if it is not under `root`.

    Alerts store a relative path ("/media/violations/x.jpg"); older rows may hold
    an absolute URL. Either way only the file NAME is trusted, and it is looked
    up inside MEDIA_ROOT/violations -- a crafted value can never point elsewhere.
    """
    if not url:
        return None
    name = Path(urlparse(url).path).name
    if not name:
        return None
    path = (root / name).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


class Command(BaseCommand):
    help = "Delete alert evidence files older than N days and clear their URLs (Alert rows are kept)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None,
                            help="Delete evidence older than this many days "
                                 "(default: Settings > evidence retention).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would be deleted; change nothing.")
        parser.add_argument("--include-test", action="store_true",
                            help="Also purge evidence on '-TEST' (uploaded-footage) cameras.")
        parser.add_argument("--auto", action="store_true",
                            help="Scheduler mode: do nothing unless auto-purge is switched ON in Settings.")

    def handle(self, *args, **options):
        cfg = SystemSettings.load()
        if options["auto"] and not getattr(cfg, "evidence_auto_purge", False):
            self.stdout.write("Auto-purge is OFF in Settings; nothing to do.")
            return

        days = options["days"] if options["days"] is not None else cfg.evidence_retention_days
        if days < 1:
            self.stderr.write("--days must be at least 1.")
            return
        dry = options["dry_run"]
        cutoff = timezone.now() - datetime.timedelta(days=days)
        root = Path(settings.MEDIA_ROOT) / "violations"

        has_evidence = Q()
        for f in EVIDENCE_FIELDS:
            has_evidence |= ~Q(**{f: ""})
        qs = Alert.objects.filter(timestamp__lt=cutoff).filter(has_evidence)
        if not options["include_test"]:
            qs = qs.exclude(camera__code__endswith="-TEST")

        alerts = list(qs.order_by("timestamp"))
        files, missing, freed = {}, 0, 0
        for alert in alerts:
            for f in EVIDENCE_FIELDS:
                path = _local_file(getattr(alert, f), root)
                if path is None:
                    continue
                if path.is_file():
                    if path not in files:
                        files[path] = path.stat().st_size
                else:
                    missing += 1
        freed = sum(files.values())

        header = "DRY RUN -- nothing will be changed." if dry else "Purging evidence."
        self.stdout.write(self.style.WARNING(header))
        self.stdout.write(f"Retention: {days} days (cutoff {cutoff:%Y-%m-%d %H:%M})"
                          f"{'' if options['include_test'] else ', -TEST cameras skipped'}")
        self.stdout.write(f"Alerts with evidence older than the cutoff: {len(alerts)}")
        if alerts:
            self.stdout.write(f"  oldest: {alerts[0].timestamp:%Y-%m-%d %H:%M}   newest: {alerts[-1].timestamp:%Y-%m-%d %H:%M}")
        self.stdout.write(f"Files to delete: {len(files)} ({freed / 1_048_576:.1f} MB); "
                          f"URLs pointing at files already gone: {missing}")

        if dry or not alerts:
            return

        deleted = 0
        for path in files:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                self.stderr.write(f"could not delete {path.name}: {exc}")
        for alert in alerts:
            for f in EVIDENCE_FIELDS:
                setattr(alert, f, "")
            alert.save(update_fields=list(EVIDENCE_FIELDS))
        self.stdout.write(self.style.SUCCESS(
            f"Deleted {deleted} file(s), cleared evidence on {len(alerts)} alert(s). Alert records kept."))
