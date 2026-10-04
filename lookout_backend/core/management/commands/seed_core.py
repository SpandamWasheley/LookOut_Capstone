"""The rows the system cannot function without, created on a fresh database.

A hosted deployment starts with nothing: the SQLite file is deliberately not in
the repository (it once carried live credentials), so Render gets migrations
and an empty schema. `bootstrap_admin` makes the first user. This makes
everything else a running system assumes already exists.

WHY THIS IS NEEDED AT ALL
-------------------------
Every ViolationType row is created by a `get_or_create` inside a watch_*
command -- and in a split deployment those commands only ever run on the EDGE
pc, next to the camera and the GPU. The hosted database therefore never sees
them. The symptom is quiet rather than loud: the dashboard loads, the violation
filters are simply empty, and an alert arriving from the edge has no type to
attach to.

Idempotent by construction: every row is get_or_create'd on its natural key, so
running this twice, or running it after the detectors have already created a
type locally, changes nothing. Safe in a release command.

    python manage.py seed_core
    python manage.py seed_core --camera-name "Hikvision DS-2CD1047G2"
"""
from django.core.management.base import BaseCommand

from core.models import Camera, SystemSettings, ViolationType

# EXACTLY what the detectors create, so a row seeded here and a row created by
# a watcher are the same row. The code is the natural key and must match: note
# that holdup is stored as "theft", never "thief" -- watch_thief.py uses
# "thief" for its COMMAND name and "theft" for the ViolationType, and migration
# 0026 merged the two after they diverged once already.
VIOLATION_TYPES = [
    ("smoking",  "Public Smoking",         "#f59e0b", "cigarette"),
    ("drinking", "Public Drinking",        "#8b5cf6", "beer"),
    ("theft",    "Holdup in Public Area",  "#ef4444", "siren"),
    ("parking",  "Illegal Parking",        "#ef4444", "car"),
]

# LookOut is a single-camera system (see CLAUDE.md). This is the one real
# camera; the "-TEST" cameras are created on demand by uploaded-clip runs and
# are deliberately not seeded.
CAMERA_CODE = "CAM-SMOKE-01"
CAMERA_NAME = "Hikvision DS-2CD1047G2"


class Command(BaseCommand):
    help = ("Creates the violation types, the camera and the settings row a "
            "fresh deployment needs. Idempotent; safe to re-run.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--camera-name", default=CAMERA_NAME,
            help=f"Display name for {CAMERA_CODE} (default {CAMERA_NAME!r}). "
                 "Only used when the camera is created; an existing one is "
                 "left alone.",
        )
        parser.add_argument(
            "--no-camera", action="store_true",
            help="Seed the violation types and settings only. Use when the "
                 "camera row is managed from the dashboard instead.",
        )

    def handle(self, *args, **options):
        created, existing = [], []

        for code, label, color, icon in VIOLATION_TYPES:
            _, made = ViolationType.objects.get_or_create(
                code=code,
                defaults={"label": label, "color": color, "icon": icon},
            )
            (created if made else existing).append(f"type:{code}")

        if not options["no_camera"]:
            # No stream_url: the RTSP address is edge-local and must never be
            # written into a hosted database. It is set on the edge, or typed
            # into Live Feeds by an operator who is on that network.
            _, made = Camera.objects.get_or_create(
                code=CAMERA_CODE,
                defaults={"name": options["camera_name"],
                          "status": Camera.Status.ONLINE},
            )
            (created if made else existing).append(f"camera:{CAMERA_CODE}")

        # load() creates the singleton if it is missing. Called rather than
        # get_or_create'd so the defaults come from the model, in one place.
        SystemSettings.load()

        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Created {len(created)}: {', '.join(created)}"))
        if existing:
            self.stdout.write(f"Already present ({len(existing)}): {', '.join(existing)}")
        self.stdout.write(self.style.SUCCESS("Settings row ready."))

        if not created and not existing:
            self.stdout.write(self.style.WARNING("Nothing to do."))
