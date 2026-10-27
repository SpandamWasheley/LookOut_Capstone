from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date, parse_datetime

from core.models import (
    Alert,
    Camera,
    Officer,
    User,
    ViolationType,
)

# Labels here must match the frontend's canonical VIOLATION_TYPES (see
# lookout/src/constants/violationTypes.js) — this command uses
# update_or_create below, so it silently overwrites the DB's label on every
# reseed. See migration 0026 for the one-time rename/merge this mirrors.
VIOLATION_TYPES = [
    {"code": "theft", "label": "Holdup in Public Area", "color": "#ef4444", "icon": "theft"},
    {"code": "parking", "label": "Parking Obstruction in Road", "color": "#f97316", "icon": "car"},
    {"code": "drinking", "label": "Drinking in Public Area", "color": "#8b5cf6", "icon": "beer"},
    {"code": "smoking", "label": "Smoking in Public Area", "color": "#64748b", "icon": "smoke"},
    {"code": "disposal", "label": "Improper Disposal", "color": "#22c55e", "icon": "trash"},
]

# The disposal demo alerts get a placeholder evidence still DRAWN locally, one
# per alert (see _ensure_demo_evidence). The other demo rows point at stock
# photos on the internet; these are drawn so the cards render with no network
# at all, and so what they show is the ACTUAL shape of watch_disposal's
# output -- the object boxed in the disposal green with a caption above it.
def disposal_evidence_name(suspect):
    """Stable filename for a disposal demo still.

    Keyed on the object (suspect), NOT on the alert code — these rows have no
    fixed code any more, and keying on an auto-assigned one would orphan the
    file every time the sequence moved."""
    slug = "".join(ch if ch.isalnum() else "_" for ch in suspect.lower()).strip("_")
    return f"demo_disposal_{slug}_evidence.jpg"

USERS = [
    {"username": "admin", "password": "admin123", "role": "admin", "display_name": "Brgy. Administrator", "is_staff": True, "is_superuser": True},
    {"username": "dispatcher", "password": "dispatch123", "role": "dispatcher", "display_name": "Dispatch Officer"},
    {"username": "officer", "password": "officer123", "role": "officer", "display_name": "PO2 Mangubat, Lisa"},
]

OFFICERS = [
    {"code": "OFC-01", "name": "PO2 Mangubat, Lisa", "badge": "B-091", "status": "responding", "location": "Zone 1", "phone": "+63 917 555 6921", "shift": "6PM - 2AM", "joined_date": "2022-04-11"},
    {"code": "OFC-02", "name": "PO3 Cabrera, Dante", "badge": "B-073", "status": "on-duty", "location": "Zone 4", "phone": "+63 927 221 4845", "shift": "7AM - 3PM", "joined_date": "2021-10-02"},
    {"code": "OFC-03", "name": "PO1 Reyes, Marco", "badge": "B-058", "status": "on-duty", "location": "Zone 3", "phone": "+63 934 882 1175", "shift": "3PM - 11PM", "joined_date": "2023-01-15"},
    {"code": "OFC-04", "name": "PO2 Santos, Joy", "badge": "B-044", "status": "off-duty", "location": "Sector 2", "phone": "+63 918 876 3308", "shift": "10PM - 6AM", "joined_date": "2024-02-20"},
]

ALERTS = [
    {"code": "ALT-0040", "type": "theft", "status": "active", "camera": "CAM-01", "timestamp": "2025-06-12T22:14:00Z",
     "confidence": 0.92, "description": "Suspected theft at the market entrance. Individual grabbed goods and fled; footage flagged for barangay staff.",
     "image_url": "https://images.unsplash.com/photo-1549402098-f4d9b419c5f8?w=800&h=450&fit=crop&auto=format",
     "officer_assigned": "PO2 Mangubat, Lisa", "suspect": "Santos, Maria"},
    {"code": "ALT-0039", "type": "drinking", "status": "dispatched", "camera": "CAM-02", "timestamp": "2025-06-12T20:05:00Z",
     "confidence": 0.79, "description": "Group drinking alcohol in the plaza public area. Officer patrol dispatched to investigate.",
     "image_url": "https://images.unsplash.com/photo-1512966885769-8207b47677df?w=800&h=450&fit=crop&auto=format",
     "officer_assigned": "PO3 Cabrera, Dante", "suspect": "Group in plaza"},
    {"code": "ALT-0038", "type": "parking", "status": "acknowledged", "camera": "CAM-04", "timestamp": "2025-06-12T19:12:00Z",
     "confidence": 0.84, "description": "Vehicle illegally parked, obstructing the alley near residential row. Barangay traffic team notified.",
     "image_url": "https://images.unsplash.com/photo-1470420084874-431eb0a8d5b1?w=800&h=450&fit=crop&auto=format",
     "officer_assigned": "PO1 Reyes, Marco", "suspect": ""},
    {"code": "ALT-0037", "type": "smoking", "status": "active", "camera": "CAM-03", "timestamp": "2025-06-12T18:40:00Z",
     "confidence": 0.81, "description": "Individual smoking within a designated no-smoking public place. Reminder issued and logged.",
     "image_url": "https://images.unsplash.com/photo-1527612820672-5b56351f7346?w=800&h=450&fit=crop&auto=format",
     "officer_assigned": "PO1 Reyes, Marco", "suspect": ""},
    # Improper disposal. The confidence is deliberately 0.31, not the 0.8-0.9
    # the other demo rows carry: this detector's score comes from an
    # open-vocabulary (zero-shot) check, which spreads probability across its
    # whole prompt set, so a CORRECT bag really does land around 0.2-0.35. A
    # demo row showing 0.92 would teach the wrong expectation and invite
    # "why is your garbage detector so unconfident?" on the day.
    #
    # Left unassigned on purpose, so the dashboard's unassigned count and the
    # red "Unassigned" chip both have something to show.
    # The disposal rows carry NO hardcoded `code`, unlike the four above, and
    # that is load-bearing. Alert codes are a single running sequence assigned
    # by _next_code (ALT-0001, ALT-0002, ...), so any fixed code this file
    # names will sooner or later collide with a REAL alert — and because the
    # loop below uses update_or_create(code=...), a collision silently
    # OVERWRITES that real alert. The four legacy rows above already do this:
    # ALT-0037..ALT-0040 were genuine smoking alerts before seed_demo claimed
    # their codes. These rows are matched on (type, suspect) instead and let
    # the code auto-assign, so reseeding appends or updates its own row and
    # can never destroy real data.
    #
    # They are also dated RELATIVE TO NOW (`minutes_ago`) rather than with a
    # fixed past date, so they stay near the top of a list the API orders by
    # -id and pages at 50, and so the "Today" counters light up.
    #
    # Every description below is the literal output of descriptions.disposal()
    # for that object and dwell, so the demo rows read exactly as a real alert
    # from watch_disposal would. `evidence` drives the drawn still.
    {"type": "disposal", "status": "active", "camera": "CAM-SMOKE-01",
     "minutes_ago": 35,
     "confidence": 0.31, "object_confidence": 0.31,
     "description": "Garbage bag left behind and unattended for about 2 min.",
     "image_url": "",          # filled in at seed time with the local still
     "officer_assigned": "", "suspect": "garbage bag",
     "evidence": {"still": 120, "spot": (402, 330), "size": (54, 38)}},
    {"type": "disposal", "status": "dispatched", "camera": "CAM-SMOKE-01",
     "minutes_ago": 120,
     "confidence": 0.27, "object_confidence": 0.27,
     "description": "Sack left behind and unattended for 45 s.",
     "image_url": "",
     "officer_assigned": "PO1 Reyes, Marco", "suspect": "sack",
     "evidence": {"still": 45, "spot": (268, 318), "size": (46, 34)}},
    {"type": "disposal", "status": "resolved", "camera": "CAM-SMOKE-01",
     "minutes_ago": 300,
     "confidence": 0.34, "object_confidence": 0.34,
     "description": "Plastic bag left behind and unattended for about 5 min.",
     "image_url": "",
     "officer_assigned": "PO2 Santos, Joy", "suspect": "plastic bag",
     "evidence": {"still": 300, "spot": (545, 342), "size": (40, 30)}},
    # Dismissed: the lowest score of the four, which is what a borderline
    # confirmation looks like — useful for showing that a reviewer can and
    # does throw one out.
    {"type": "disposal", "status": "acknowledged", "camera": "CAM-SMOKE-01",
     "minutes_ago": 480,
     "confidence": 0.22, "object_confidence": 0.22,
     "description": "Pile of trash left behind and unattended for 20 s.",
     "image_url": "",
     "officer_assigned": "", "suspect": "pile of trash",
     "evidence": {"still": 20, "spot": (330, 352), "size": (68, 30)}},
]


class Command(BaseCommand):
    help = "Seed the database with demo data matching the Lookout frontend mock data."

    @staticmethod
    def _alert_timestamp(alert):
        """When the alert happened: a fixed ISO stamp, or `minutes_ago` from now.

        Relative is what the disposal rows use, so reseeding always puts them
        at the top of a list ordered by timestamp instead of behind whatever
        real alerts the database has accumulated since.
        """
        if "minutes_ago" in alert:
            from datetime import timedelta

            from django.utils import timezone

            return timezone.now() - timedelta(minutes=alert["minutes_ago"])
        return parse_datetime(alert["timestamp"])

    def _ensure_demo_evidence(self, alert):
        """Draw one disposal alert's evidence still, and return its URL.

        Idempotent: an existing file is reused, so reseeding never rewrites it.
        Drawn rather than downloaded for two reasons -- the cards then render
        with no network at all (useful on a presentation machine), and what
        they show is the real output format of watch_disposal._annotate: the
        confirmed object boxed in the disposal green and captioned.

        Returns "" if the drawing could not be done, in which case the row
        keeps its empty image_url rather than pointing at nothing.
        """
        import os

        from django.conf import settings

        from core.media import violation_media_path

        spec = alert.get("evidence") or {}
        name = disposal_evidence_name(alert["suspect"])

        out_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(out_dir, exist_ok=True)
        target = out_dir / name
        if os.path.isfile(target):
            return violation_media_path(name)

        try:
            import cv2
            import numpy as np
        except ImportError:
            # The CV deps are optional on an API-only host (see
            # requirements-detection*.txt), and demo pictures are not worth
            # making them mandatory.
            self.stdout.write(self.style.WARNING(
                "cv2/numpy unavailable — disposal demo alerts seeded without images."))
            return ""

        width, height = 800, 450
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        # A plain night-pavement gradient. Not a fake photograph: these are
        # obviously placeholders, which is the honest thing for seeded data.
        for y in range(height):
            shade = int(26 + 34 * (y / height))
            frame[y, :] = (shade, shade - 2, shade - 4)
        cv2.line(frame, (0, 300), (width, 266), (70, 70, 74), 2)       # kerb

        cx, cy = spec.get("spot", (402, 330))
        rx, ry = spec.get("size", (54, 38))
        cv2.ellipse(frame, (cx, cy), (rx, ry), 8, 0, 360, (196, 198, 203), -1)
        cv2.ellipse(frame, (cx, cy), (rx, ry), 8, 0, 360, (168, 170, 176), 2)

        green = (94, 197, 34)                                          # BGR #22c55e
        pad = 18
        x, y = cx - rx - pad, cy - ry - pad
        w, h = 2 * (rx + pad), 2 * (ry + pad)
        cv2.rectangle(frame, (x, y), (x + w, y + h), green, 3)

        label = alert.get("suspect") or "garbage"
        score = alert.get("object_confidence") or 0.0
        caption = f"{label} {score:.0%} - still {spec.get('still', 0):.0f}s"
        (tw, _th), _b = cv2.getTextSize(caption, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        # Keep the caption inside the frame when the box sits near an edge.
        tx = max(8, min(x, width - tw - 8))
        cv2.putText(frame, caption, (tx, max(26, y - 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, green, 2)
        cv2.putText(frame, "CAM-SMOKE-01  -  placeholder evidence (seeded demo)",
                    (16, height - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (150, 150, 155), 1)

        if not cv2.imwrite(str(target), frame):
            self.stdout.write(self.style.WARNING(
                f"could not write {target} — {alert['suspect']} has no image."))
            return ""
        return violation_media_path(name)

    def handle(self, *args, **options):
        vtypes = {}
        for v in VIOLATION_TYPES:
            obj, _ = ViolationType.objects.update_or_create(code=v["code"], defaults=v)
            vtypes[v["code"]] = obj
        self.stdout.write(self.style.SUCCESS(f"Violation types: {len(vtypes)}"))

        for u in USERS:
            if not User.objects.filter(username=u["username"]).exists():
                user = User.objects.create_user(
                    username=u["username"], password=u["password"], role=u["role"],
                    display_name=u["display_name"], is_staff=u.get("is_staff", False),
                    is_superuser=u.get("is_superuser", False),
                )
                self.stdout.write(self.style.SUCCESS(f"Created user {user.username}"))

        # LookOut is single-camera: this command no longer creates cameras.
        # Demo alerts attach to the real camera if it exists.
        camera = Camera.objects.filter(code="CAM-SMOKE-01").first()

        officers = {}
        for o in OFFICERS:
            obj, _ = Officer.objects.update_or_create(
                code=o["code"],
                defaults={
                    "name": o["name"], "badge": o["badge"], "status": o["status"],
                    "location": o["location"], "phone": o["phone"], "shift": o["shift"],
                    "joined_date": parse_date(o["joined_date"]),
                },
            )
            officers[o["name"]] = obj
        self.stdout.write(self.style.SUCCESS(f"Officers: {len(officers)}"))

        for a in ALERTS:
            evidence_url = self._ensure_demo_evidence(a) if a.get("evidence") else ""
            # Rows WITH a hardcoded code keep their historical behaviour; rows
            # without one are matched on what they are, so they never claim a
            # code the real sequence will hand out. See the note on ALERTS.
            lookup = ({"code": a["code"]} if "code" in a
                      else {"type": vtypes[a["type"]], "suspect": a["suspect"]})
            alert, _ = Alert.objects.update_or_create(
                **lookup,
                defaults={
                    "type": vtypes[a["type"]],
                    "status": a["status"],
                    "camera": camera,
                    "timestamp": self._alert_timestamp(a),
                    "confidence": a["confidence"],
                    "description": a["description"],
                    "image_url": a["image_url"] or evidence_url,
                    "object_confidence": a.get("object_confidence"),
                    "suspect": a["suspect"],
                },
            )
            assigned_officer = officers.get(a["officer_assigned"])
            alert.officers_assigned.set([assigned_officer] if assigned_officer else [])
        self.stdout.write(self.style.SUCCESS(f"Alerts: {len(ALERTS)}"))

        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully."))
