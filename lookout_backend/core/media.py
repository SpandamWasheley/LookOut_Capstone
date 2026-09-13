"""Relative media-path helper shared by every watch_* command.

Alerts store a PATH here, never an absolute URL — AlertSerializer resolves it
against the incoming request at read time (request.build_absolute_uri), so
the host in the URL a client sees always matches whatever it actually
connected through (localhost, a LAN IP, or whatever ngrok forwarding host
happens to be live that session).

This used to be baked in at write time instead, as
f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{filename}" — which
froze whatever SITE_BASE_URL was (usually its unset http://localhost:8000
default) into the row forever. That's unreachable from a phone under any
network topology, and even a correctly-set value goes stale the moment an
ngrok tunnel restarts, since ngrok's free-tier URL rotates per session.
"""
from django.conf import settings


def violation_media_path(filename: str) -> str:
    """The relative path (e.g. "/media/violations/foo.jpg") for a file saved
    under MEDIA_ROOT/violations/<filename>.

    Always exactly one leading slash regardless of how MEDIA_URL is
    configured (Django's own default, 'media/', has none) — the leading
    slash is required so request.build_absolute_uri() resolves it against
    the site root, not wherever the current request's own path happens to be.
    """
    return f"/{settings.MEDIA_URL.strip('/')}/violations/{filename}"
