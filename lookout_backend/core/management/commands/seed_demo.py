from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date, parse_datetime

from core.models import (
    Alert,
    Camera,
    Officer,
    Person,
    User,
    ViolationType,
)

# Labels here must match the frontend's canonical VIOLATION_TYPES (see
# lookout/src/constants/violationTypes.js) — this command uses
# update_or_create below, so it silently overwrites the DB's label on every
# reseed. See migration 0026 for the one-time rename/merge this mirrors.
VIOLATION_TYPES = [
    {"code": "theft", "label": "Holdup in Public Area", "color": "#ef4444", "icon": "theft"},
    {"code": "parking", "label": "Parking Obstruction in Area", "color": "#f97316", "icon": "car"},
    {"code": "drinking", "label": "Drinking", "color": "#8b5cf6", "icon": "beer"},
    {"code": "smoking", "label": "Smoking", "color": "#64748b", "icon": "smoke"},
]

USERS = [
    {"username": "admin", "password": "admin123", "role": "admin", "display_name": "Brgy. Administrator", "is_staff": True, "is_superuser": True},
    {"username": "dispatcher", "password": "dispatch123", "role": "dispatcher", "display_name": "Dispatch Officer"},
    {"username": "officer", "password": "officer123", "role": "officer", "display_name": "PO2 Mangubat, Lisa"},
]

CAMERAS = [
    {"code": "CAM-01", "name": "Market Entrance", "zone": "Zone 1", "status": "online", "fps": 28,
     "image_url": "https://images.unsplash.com/photo-1544739313-6bdb91d32485?w=800&h=450&fit=crop&auto=format"},
    {"code": "CAM-02", "name": "Barangay Hall Plaza", "zone": "Zone 2", "status": "degraded", "fps": 12,
     "image_url": "https://images.unsplash.com/photo-1579546929518-9e396f3cc809?w=800&h=450&fit=crop&auto=format"},
    {"code": "CAM-03", "name": "R.T. Lim Blvd. Junction", "zone": "Zone 3", "status": "offline", "fps": 0,
     "image_url": "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?w=800&h=450&fit=crop&auto=format"},
    {"code": "CAM-04", "name": "Purok 6 Alley", "zone": "Zone 4", "status": "online", "fps": 25,
     "image_url": "https://images.unsplash.com/photo-1515601914948-8493e1a7cf6d?w=800&h=450&fit=crop&auto=format"},
]

OFFICERS = [
    {"code": "OFC-01", "name": "PO2 Mangubat, Lisa", "badge": "B-091", "status": "responding", "location": "Zone 1", "phone": "+63 917 555 6921", "shift": "6PM - 2AM", "joined_date": "2022-04-11"},
    {"code": "OFC-02", "name": "PO3 Cabrera, Dante", "badge": "B-073", "status": "on-duty", "location": "Zone 4", "phone": "+63 927 221 4845", "shift": "7AM - 3PM", "joined_date": "2021-10-02"},
    {"code": "OFC-03", "name": "PO1 Reyes, Marco", "badge": "B-058", "status": "on-duty", "location": "Zone 3", "phone": "+63 934 882 1175", "shift": "3PM - 11PM", "joined_date": "2023-01-15"},
    {"code": "OFC-04", "name": "PO2 Santos, Joy", "badge": "B-044", "status": "off-duty", "location": "Sector 2", "phone": "+63 918 876 3308", "shift": "10PM - 6AM", "joined_date": "2024-02-20"},
]

PEOPLE = [
    {"person_code": "BRG-TET-0001", "full_name": "Angelica Dela Cruz", "status": "pending"},
    {"person_code": "BRG-TET-0002", "full_name": "Kyle Mendoza", "status": "pending"},
    {"person_code": "BRG-TET-0003", "full_name": "Maria Santos", "status": "pending"},
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
]


class Command(BaseCommand):
    help = "Seed the database with demo data matching the Lookout frontend mock data."

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

        cameras = {}
        for c in CAMERAS:
            obj, _ = Camera.objects.update_or_create(
                code=c["code"],
                defaults={
                    "name": c["name"], "zone": None, "status": c["status"],
                    "fps": c["fps"], "image_url": c["image_url"],
                },
            )
            cameras[c["code"]] = obj
        self.stdout.write(self.style.SUCCESS(f"Cameras: {len(cameras)}"))

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

        for p in PEOPLE:
            Person.objects.update_or_create(
                person_code=p["person_code"],
                defaults={"full_name": p["full_name"], "status": p["status"]},
            )
        self.stdout.write(self.style.SUCCESS(f"People: {len(PEOPLE)}"))

        for a in ALERTS:
            alert, _ = Alert.objects.update_or_create(
                code=a["code"],
                defaults={
                    "type": vtypes[a["type"]],
                    "status": a["status"],
                    "camera": cameras.get(a["camera"]),
                    "timestamp": parse_datetime(a["timestamp"]),
                    "confidence": a["confidence"],
                    "description": a["description"],
                    "image_url": a["image_url"],
                    "suspect": a["suspect"],
                },
            )
            assigned_officer = officers.get(a["officer_assigned"])
            alert.officers_assigned.set([assigned_officer] if assigned_officer else [])
        self.stdout.write(self.style.SUCCESS(f"Alerts: {len(ALERTS)}"))

        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully."))
