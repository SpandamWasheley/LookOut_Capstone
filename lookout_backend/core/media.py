"""Where an alert's evidence lives, and the URL stored on the row.

Alerts store a PATH when evidence is on local disk, and an ABSOLUTE URL when it
has been published to object storage. AlertSerializer resolves a bare path
against the incoming request (request.build_absolute_uri), so the host a client
sees always matches whatever it connected through; an absolute URL is passed
through untouched.

This used to be baked in at write time as
f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{filename}" — which
froze whatever SITE_BASE_URL was (usually its unset http://localhost:8000
default) into the row for ever. That is unreachable from a phone under any
network topology, and even a correct value goes stale the moment an ngrok
tunnel restarts, since ngrok's free-tier URL rotates per session.

WHY PUBLISHING EXISTS
---------------------
In the split deployment the detectors run on a PC beside the cameras and the
API runs in the cloud. The detectors do not write through Django's storage API
at all — they call cv2.imwrite and ClipRecorder.save, which put bytes on the
local disk. So configuring object storage in settings changes nothing for them
on its own: the file stays in the barangay, the hosted dashboard asks its own
domain for /media/violations/foo.jpg, and every alert shows a broken image.

Publishing closes that gap at the single point every detector already funnels
through. With local storage it is a no-op and the behaviour is exactly as
before, which is what development wants.
"""
import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

# Biggest file worth sending to object storage, in MB. Raw evidence clips are
# cut at source resolution and run to ~45 MB each; a free Cloudinary plan is
# 25 GB, so a few hundred alerts would exhaust it and then evidence stops
# uploading at the moment somebody needs it. The annotated ~2 MB clip and the
# still are the ones a reviewer actually opens, so those always fit.
# Set EVIDENCE_MAX_UPLOAD_MB=0 to publish everything regardless.
MAX_UPLOAD_MB = 25


def _storage_is_remote():
    """True when STORAGES['default'] is something other than the local disk.

    Read off the setting rather than by inspecting default_storage, so nothing
    here constructs a storage backend. Instantiating the Cloudinary one needs
    credentials, and this is called on a code path whose entire job is to
    decide whether those credentials are even in play.
    """
    backend = settings.STORAGES.get("default", {}).get("BACKEND", "")
    return bool(backend) and not backend.endswith("FileSystemStorage")


def violation_media_path(filename: str) -> str:
    """The URL to store on an Alert for evidence saved under
    MEDIA_ROOT/violations/<filename>.

    Local storage: the relative path (e.g. "/media/violations/foo.jpg"), with
    exactly one leading slash regardless of how MEDIA_URL is configured
    (Django's own default, 'media/', has none) — the leading slash is required
    so request.build_absolute_uri() resolves it against the site root and not
    against whatever path the current request happens to have.

    Object storage: the file is uploaded and its absolute URL returned instead.

    `filename` may contain subdirectories ("ai/alert12/ai_00.jpg"); the AI
    checker stores its sent frames that way.
    """
    local = f"/{settings.MEDIA_URL.strip('/')}/violations/{filename}"
    if not _storage_is_remote():
        return local
    return _publish(filename) or local


def _publish(filename: str) -> str:
    """Upload MEDIA_ROOT/violations/<filename> and return its URL, or "" if it
    could not be published.

    Never raises. A detector that has just witnessed a violation and written
    its evidence to disk must not die because a bucket was unreachable — the
    alert is still worth recording, and the local copy still exists. The row
    falls back to the local path, which a later re-upload can correct.
    """
    from django.core.files.base import File
    from django.core.files.storage import default_storage

    source = os.path.join(settings.MEDIA_ROOT, "violations", filename)
    if not os.path.isfile(source):
        # Called before the file was written, or the write failed. Both are
        # bugs in the caller, but neither is worth an exception here.
        logger.warning("evidence not on disk, not published: %s", source)
        return ""

    cap = int(getattr(settings, "EVIDENCE_MAX_UPLOAD_MB", MAX_UPLOAD_MB) or 0)
    if cap:
        size_mb = os.path.getsize(source) / (1024 * 1024)
        if size_mb > cap:
            logger.info("evidence %s is %.0f MB (cap %d MB), kept local only",
                        filename, size_mb, cap)
            return ""

    target = f"violations/{filename}".replace(os.sep, "/")
    try:
        with open(source, "rb") as fh:
            # save() may append a suffix when the name is taken, so the URL is
            # built from what it actually stored, never from what we asked for.
            stored = default_storage.save(target, File(fh))
        return default_storage.url(stored)
    except Exception:                                            # noqa: BLE001
        logger.exception("could not publish evidence %s", filename)
        return ""
