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

# Caps on what gets sent to object storage, in MB, PER CLOUDINARY RESOURCE
# TYPE — because the limit that actually bites is Cloudinary's own, not a
# quota judgement of ours. On the free plan it is 10 MB for the `raw` type and
# 100 MB for `video` (confirmed via cloudinary.api.usage(): raw_max_size_bytes
# / video_max_size_bytes).
#
# This used to be one flat 25 MB cap, which was worse than no cap at all: it
# sat ABOVE the real 10 MB raw ceiling, so a 15 MB raw clip sailed past our
# own check and was then rejected by Cloudinary. _publish swallowed that and
# fell back to the local path, and because a local path is resolved against
# the requesting host, the clip ended up served through whatever tunnel the
# API was reached by — where a <video> tag cannot play it. Every raw clip in
# the system had failed this way; not one had ever published.
#
# Uploading full-resolution raw clips does consume real quota (~15 MB each
# against a 25 GB plan). That is the intended trade: evidence that cannot be
# played is worth less than the space it saves. Lower EVIDENCE_MAX_UPLOAD_MB
# to put a tighter ceiling back on, or set it to 0 to publish everything.
RAW_MAX_UPLOAD_MB = 10
VIDEO_MAX_UPLOAD_MB = 100

# Non-Cloudinary backends (S3/R2) have one bucket, no per-type ceiling, and
# their own much larger object limit, so they keep a single cap.
MAX_UPLOAD_MB = 100

# Extensions routed to Cloudinary's `video` resource type. Everything else
# (the .jpg stills, the AI checker's sent frames) stays on `raw`, where it
# fits well inside 10 MB and where byte-for-byte storage is what we want.
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm")


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


def _capped(type_cap: int) -> int:
    """`type_cap` unless EVIDENCE_MAX_UPLOAD_MB overrides it.

    Compared against "" as well as None because python-decouple hands back
    strings: a blank environment variable must not read as the number zero,
    which would mean "no cap" — the opposite of "not configured".
    """
    override = getattr(settings, "EVIDENCE_MAX_UPLOAD_MB", None)
    return int(type_cap) if override in (None, "") else int(override)


def _target_storage(filename: str):
    """(storage, cap_mb) to publish `filename` through, cap already resolved.

    Cloudinary splits uploads by RESOURCE TYPE, and the type decides the size
    limit. The configured default is the `raw` storage, which stores any file
    byte-for-byte — right for the stills, but capped at 10 MB on the free plan,
    which every raw evidence clip exceeds. Clips therefore go through the
    `video` storage instead, where the same plan allows 100 MB.

    Only Cloudinary is special-cased. An S3/R2 backend is one bucket with one
    set of rules, so it keeps the default storage and the single cap; this must
    not silently re-point those deployments at Cloudinary classes.

    An explicit EVIDENCE_MAX_UPLOAD_MB replaces whichever cap applies, so a
    deployment can be stingier than its plan allows; 0 means no cap at all.
    The cap is resolved HERE rather than at the call site so there is exactly
    one place that decides it — the bug this function exists to fix was two
    places disagreeing about which limit was in force.
    """
    from django.core.files.storage import default_storage

    backend = settings.STORAGES.get("default", {}).get("BACKEND", "")
    if "cloudinary" not in backend.lower():
        return default_storage, _capped(MAX_UPLOAD_MB)

    if filename.lower().endswith(VIDEO_SUFFIXES):
        from cloudinary_storage.storage import VideoMediaCloudinaryStorage

        # A fresh instance, not the module-level one in the library's
        # RESOURCE_TYPES map: save() and url() must be called on the SAME
        # storage, since the public_id save() returns omits the extension for
        # non-raw types and only a video-typed url() rebuilds a working URL
        # from it.
        return VideoMediaCloudinaryStorage(), _capped(VIDEO_MAX_UPLOAD_MB)

    return default_storage, _capped(RAW_MAX_UPLOAD_MB)


def _publish(filename: str) -> str:
    """Upload MEDIA_ROOT/violations/<filename> and return its URL, or "" if it
    could not be published.

    Never raises. A detector that has just witnessed a violation and written
    its evidence to disk must not die because a bucket was unreachable — the
    alert is still worth recording, and the local copy still exists. The row
    falls back to the local path, which a later re-upload can correct.
    """
    from django.core.files.base import File

    source = os.path.join(settings.MEDIA_ROOT, "violations", filename)
    if not os.path.isfile(source):
        # Called before the file was written, or the write failed. Both are
        # bugs in the caller, but neither is worth an exception here.
        logger.warning("evidence not on disk, not published: %s", source)
        return ""

    storage, cap = _target_storage(filename)
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
            stored = storage.save(target, File(fh))
        return storage.url(stored)
    except Exception:                                            # noqa: BLE001
        logger.exception("could not publish evidence %s", filename)
        return ""
