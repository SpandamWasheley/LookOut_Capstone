from django.core.management.base import BaseCommand

from core.models import Resident
from core.vision import recognition


class Command(BaseCommand):
    help = "Builds the face recognition database from Resident.image_url photos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--resident",
            help="Only (re-)enroll the resident with this code (e.g. RES-05).",
        )

    def handle(self, *args, **options):
        residents = Resident.objects.exclude(image_url="")
        only_code = options.get("resident")
        if only_code:
            residents = residents.filter(code=only_code)

        existing = {entry["code"]: entry for entry in recognition.load_face_db()}
        enrolled = 0
        skipped = 0

        for resident in residents:
            self.stdout.write(f"Enrolling {resident.code} - {resident.name}...")
            image = recognition.fetch_image_as_array(resident.image_url)
            if image is None:
                self.stdout.write(self.style.WARNING(f"  Could not download photo for {resident.code}."))
                skipped += 1
                continue

            embedding = recognition.compute_face_embedding(image)
            if embedding is None:
                self.stdout.write(self.style.WARNING(f"  No detectable face in photo for {resident.code}."))
                skipped += 1
                continue

            existing[resident.code] = {
                "resident_id": resident.id,
                "code": resident.code,
                "name": resident.name,
                "age": resident.age,
                "embedding": embedding.flatten().tolist(),
            }
            enrolled += 1
            self.stdout.write(self.style.SUCCESS(f"  Enrolled {resident.code}."))

        recognition.save_face_db(list(existing.values()))
        self.stdout.write(self.style.SUCCESS(f"Done. Enrolled: {enrolled}, skipped: {skipped}."))
