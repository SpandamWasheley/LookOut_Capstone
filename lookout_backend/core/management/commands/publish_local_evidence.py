"""Uploads evidence still sitting on local disk to object storage and rewrites
the Alert's URL field to the published URL.

Why: a row holds a RELATIVE path while its evidence is local, and
AlertSerializer resolves that against the requesting host. Reached through a
tunnel, the file is then served from the tunnel -- and a <video>/<img> tag
cannot send the ngrok-skip-browser-warning header the API client sends, so the
free tier answers with its interstitial HTML and the browser reports "no
supported sources". Published files are absolute URLs on the storage host and
sidestep the tunnel entirely, which is why this backfill exists.

Rows written before object storage was configured keep their local paths for
ever -- nothing rewrites them retroactively -- and so does anything whose
upload failed at the time (notably every raw clip, which exceeded Cloudinary's
10 MB `raw` ceiling until clips were routed to the `video` type).

  python manage.py publish_local_evidence              # DRY RUN: show what would upload
  python manage.py publish_local_evidence --commit     # actually upload and rewrite
  python manage.py publish_local_evidence --commit --limit 5

Dry run is the DEFAULT here, unlike purge_old_evidence, because committing
writes to a metered external service: it consumes storage quota that is not
trivially reclaimed, so it should never happen because someone omitted a flag.

Files whose bytes are gone from disk cannot be recovered and are only counted;
their rows are left exactly as they are. Clearing those dead references is a
separate decision and deliberately not done here.
"""
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from core.media import _publish, _storage_is_remote, _target_storage
from core.models import Alert

EVIDENCE_FIELDS = ("image_url", "video_url", "raw_video_url")


def _local_name(url):
    """The MEDIA_ROOT/violations file name an evidence value points at, or None
    when the value is already published (absolute) or unusable.

    Only the file NAME is trusted, exactly as purge_old_evidence does, so a
    crafted stored value can never reach outside the violations directory.
    """
    if not url or url.startswith("http://") or url.startswith("https://"):
        return None
    name = Path(urlparse(url).path).name
    return name or None


class Command(BaseCommand):
    help = ("Upload alert evidence that is still on local disk to object "
            "storage and rewrite the Alert URL fields to the published URLs.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit", action="store_true",
            help="Actually upload and save. Without this, nothing is written.")
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Stop after this many fields (0 = no limit). Useful for a "
                 "cautious first run against real quota.")

    def handle(self, *args, **options):
        commit = options["commit"]
        limit = options["limit"]

        if not _storage_is_remote():
            self.stdout.write(self.style.ERROR(
                "Object storage is not configured (STORAGES['default'] is the "
                "local filesystem). Set CLOUDINARY_URL or AWS_STORAGE_BUCKET_NAME "
                "first -- with local storage there is nowhere to publish to, and "
                "the paths already on the rows are correct."))
            return

        root = Path(settings.MEDIA_ROOT) / "violations"
        local_q = Q()
        for f in EVIDENCE_FIELDS:
            local_q |= (~Q(**{f: ""}) & ~Q(**{f"{f}__startswith": "http"}))

        published = failed = missing = oversize = 0
        done = 0

        for alert in Alert.objects.filter(local_q).order_by("id"):
            changed = []
            for field in EVIDENCE_FIELDS:
                if limit and done >= limit:
                    break
                name = _local_name(getattr(alert, field))
                if name is None:
                    continue

                path = (root / name).resolve()
                try:
                    path.relative_to(root.resolve())
                except ValueError:
                    continue
                if not path.is_file():
                    missing += 1
                    continue

                done += 1
                size_mb = path.stat().st_size / (1024 * 1024)
                _, cap = _target_storage(name)
                if cap and size_mb > cap:
                    oversize += 1
                    self.stdout.write(self.style.WARNING(
                        f"  {alert.code} {field}: {name} is {size_mb:.1f} MB, "
                        f"over the {cap} MB cap for its resource type -- skipped"))
                    continue

                if not commit:
                    self.stdout.write(
                        f"  {alert.code} {field}: would upload {name} "
                        f"({size_mb:.1f} MB)")
                    published += 1
                    continue

                url = _publish(name)
                if not url:
                    failed += 1
                    self.stdout.write(self.style.ERROR(
                        f"  {alert.code} {field}: upload FAILED for {name} "
                        f"({size_mb:.1f} MB) -- row left on the local path"))
                    continue

                setattr(alert, field, url)
                changed.append(field)
                published += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  {alert.code} {field}: {name} -> {url}"))

            if changed and commit:
                alert.save(update_fields=changed)
            if limit and done >= limit:
                break

        verb = "published" if commit else "would publish"
        self.stdout.write("")
        self.stdout.write(f"{verb}: {published}")
        if oversize:
            self.stdout.write(f"over the per-type cap: {oversize}")
        if failed:
            self.stdout.write(self.style.ERROR(f"failed: {failed}"))
        if missing:
            self.stdout.write(
                f"referenced but no longer on disk (left untouched): {missing}")
        if not commit and published:
            self.stdout.write(self.style.WARNING(
                "Dry run -- nothing was uploaded or saved. Re-run with --commit."))
