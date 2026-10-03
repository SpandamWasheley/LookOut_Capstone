"""Wipes test/demo data so the system starts clean for testing or the defense.

DELETES: every alert and its evidence files (image, annotated clip, raw clip, AI frames),
all citations and violators, finished detection jobs (and their processing-view frames).
KEEPS: users, officers, system settings, cameras, zones, violation types.
Running detection jobs are never touched.

  python manage.py reset_demo_data --dry-run     # counts only, nothing changes
  python manage.py reset_demo_data               # asks you to type RESET to confirm
  python manage.py reset_demo_data --yes         # no prompt
  python manage.py reset_demo_data --orphans     # also delete media/violations files no alert points at
                                                 # (media/uploads is never touched)

Back up db.sqlite3 first. After a reset, new alerts start at ALT-0001: alert codes are
the highest existing code + 1, and on SQLite the row-id counters are reset too.
"""
import shutil
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from core.models import Alert, Citation, DetectionJob, Violator

EVIDENCE_FIELDS = ("image_url", "video_url", "raw_video_url")


def _evidence_files(alert, root):
    """Local files this alert points at, all resolved INSIDE media/violations (only the path
    below that folder is trusted, so a crafted URL can never reach elsewhere)."""
    urls = [getattr(alert, f) for f in EVIDENCE_FIELDS]
    urls += list((alert.ai or {}).get("frame_files") or [])
    out = set()
    for u in urls:
        if not u:
            continue
        rel = urlparse(u).path.split("/violations/", 1)[-1]
        if not rel:
            continue
        p = (root / rel).resolve()
        try:
            p.relative_to(root.resolve())
        except ValueError:
            continue
        out.add(p)
    return out


class Command(BaseCommand):
    help = "Delete all alerts, evidence, citations, violators and finished detection jobs (keeps users, officers, settings, cameras, zones, violation types)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show counts only; change nothing.")
        parser.add_argument("--orphans", action="store_true",
                            help="Also delete files in media/violations that no alert points at.")
        parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")

    def handle(self, *args, **opts):
        media = Path(settings.MEDIA_ROOT)
        root = media / "violations"
        alerts = list(Alert.objects.all())
        files = set()
        for a in alerts:
            files |= _evidence_files(a, root)
        ai_dirs = [d for d in (root / "ai").glob("alert*") if d.is_dir()] if (root / "ai").is_dir() else []
        existing = {f for f in files if f.exists()}
        size = sum(f.stat().st_size for f in existing)
        # Evidence nobody points at (left over from alerts deleted earlier).
        all_files = {p.resolve() for p in root.rglob("*") if p.is_file()} if root.is_dir() else set()
        in_ai = {p for d in ai_dirs for p in d.rglob("*") if p.is_file()}
        orphans = all_files - {f.resolve() for f in existing} - {p.resolve() for p in in_ai}

        jobs = DetectionJob.objects.exclude(status=DetectionJob.Status.RUNNING)
        job_ids = list(jobs.values_list("id", flat=True))
        live_dirs = [media / "live" / f"job-{i}" for i in job_ids if (media / "live" / f"job-{i}").is_dir()]

        by_status = {}
        for a in alerts:
            by_status[a.status] = by_status.get(a.status, 0) + 1
        legacy = sum(1 for a in alerts if not a.level)

        w = self.stdout.write
        dry = opts["dry_run"]
        w(self.style.WARNING("DRY RUN -- nothing will be changed." if dry else "Resetting demo data."))
        w(f"Alerts: {len(alerts)}  {by_status}  (without a v6 level: {legacy})")
        w(f"  evidence files: {len(existing)} ({size / 1_048_576:.1f} MB), AI frame folders: {len(ai_dirs)}")
        w(f"Citations: {Citation.objects.count()}   Violators: {Violator.objects.count()}")
        w(f"Finished detection jobs: {len(job_ids)} (running ones are kept: "
          f"{DetectionJob.objects.filter(status=DetectionJob.Status.RUNNING).count()}), processing-view folders: {len(live_dirs)}")
        orphans -= in_ai
        orphan_mb = sum(p.stat().st_size for p in orphans) / 1_048_576
        w(f"Files in media/violations that no alert points at: {len(orphans)} ({orphan_mb:.1f} MB) "
          f"-> {'deleted' if opts['orphans'] else 'left alone (use --orphans)'}")
        w("Kept untouched: users, officers, system settings, cameras, zones, violation types.")
        if dry:
            return

        if not opts["yes"]:
            if input("Type RESET to delete the above: ").strip() != "RESET":
                self.stderr.write("Cancelled.")
                return

        with transaction.atomic():
            Citation.objects.all().delete()
            Violator.objects.all().delete()
            Alert.objects.all().delete()
            jobs.delete()
            if connection.vendor == "sqlite":
                with connection.cursor() as c:
                    tables = [m._meta.db_table for m in (Alert, Citation, Violator, DetectionJob)]
                    tables.append(Alert.officers_assigned.through._meta.db_table)
                    tables.append(Citation.violations.through._meta.db_table)
                    for t in tables:
                        c.execute("DELETE FROM sqlite_sequence WHERE name = %s", [t])
        gone = 0
        for f in existing:
            try:
                f.unlink()
                gone += 1
            except OSError as exc:
                self.stderr.write(f"could not delete {f.name}: {exc}")
        if opts["orphans"]:
            for f in orphans:
                try:
                    f.unlink()
                    gone += 1
                except OSError as exc:
                    self.stderr.write(f"could not delete {f.name}: {exc}")
        for d in ai_dirs + live_dirs:
            shutil.rmtree(d, ignore_errors=True)
        self.stdout.write(self.style.SUCCESS(
            f"Deleted {len(alerts)} alerts, {gone} evidence files; citations, violators and finished jobs cleared. "
            "The next alert will be ALT-0001."))
