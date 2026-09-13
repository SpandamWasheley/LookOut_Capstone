from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.db import migrations


def strip_scheme_and_host(url, media_prefix):
    """"http://<whatever-host-was-live-that-session>/media/violations/x.jpg"
    -> "/media/violations/x.jpg". Leaves alone anything already relative, and
    anything whose PATH isn't under our own media prefix — e.g. seed_demo.py's
    Unsplash CDN stills, which are genuinely external and must stay absolute.
    Keying off the path (not the host) means this doesn't need to know what
    host any given row happened to be created under — localhost, a LAN IP, or
    whichever ngrok subdomain was live that session all collapse the same way.
    """
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.scheme and not parts.netloc:
        return url
    if not parts.path.startswith(media_prefix):
        return url
    return urlunsplit(("", "", parts.path, parts.query, parts.fragment))


def relativize_media_urls(apps, schema_editor):
    Alert = apps.get_model("core", "Alert")
    media_prefix = f"/{settings.MEDIA_URL.strip('/')}/"

    for alert in Alert.objects.all():
        update_fields = []
        for field in ("image_url", "video_url", "raw_video_url"):
            current = getattr(alert, field)
            fixed = strip_scheme_and_host(current, media_prefix)
            if fixed != current:
                setattr(alert, field, fixed)
                update_fields.append(field)
        if update_fields:
            alert.save(update_fields=update_fields)


def noop_reverse(apps, schema_editor):
    # Not meaningfully reversible — the original host (localhost, a LAN IP, a
    # since-rotated ngrok subdomain) isn't recoverable from the relative path
    # alone, and AlertSerializer resolves relative paths at read time
    # regardless, so there's nothing a reverse migration should even restore.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_theft_relabel_and_thief_merge"),
    ]

    operations = [
        migrations.RunPython(relativize_media_urls, noop_reverse),
    ]
