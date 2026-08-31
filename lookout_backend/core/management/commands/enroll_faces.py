import cv2
from django.core.management.base import BaseCommand

from core.models import FaceEmbedding
from core.vision import recognition


class Command(BaseCommand):
    help = "Builds the face recognition database from enrolled Person FaceEmbedding photos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--resident",
            help="Only (re-)enroll the person with this code (e.g. BRG-TET-0001).",
        )

    def handle(self, *args, **options):
        embeddings = FaceEmbedding.objects.select_related("person").exclude(image="")
        only_code = options.get("resident")
        if only_code:
            embeddings = embeddings.filter(person__person_code=only_code)

        existing = {entry["code"]: entry for entry in recognition.load_face_db()}
        enrolled = 0
        skipped = 0

        for face in embeddings:
            person = face.person
            self.stdout.write(f"Enrolling {person.person_code} - {person.full_name} ({face.angle})...")
            image = cv2.imread(face.image.path)
            if image is None:
                self.stdout.write(self.style.WARNING(f"  Could not read photo for {person.person_code}."))
                skipped += 1
                continue

            embedding = recognition.compute_face_embedding(image)
            if embedding is None:
                self.stdout.write(self.style.WARNING(f"  No detectable face in photo for {person.person_code}."))
                skipped += 1
                continue

            existing[person.person_code] = {
                "person_id": person.id,
                "code": person.person_code,
                "name": person.full_name,
                "embedding": embedding.flatten().tolist(),
            }
            enrolled += 1
            self.stdout.write(self.style.SUCCESS(f"  Enrolled {person.person_code}."))

        recognition.save_face_db(list(existing.values()))
        self.stdout.write(self.style.SUCCESS(f"Done. Enrolled: {enrolled}, skipped: {skipped}."))
