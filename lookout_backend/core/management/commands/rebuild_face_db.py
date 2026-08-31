from django.core.management.base import BaseCommand

from core.face_registry import rebuild_face_db


class Command(BaseCommand):
    help = "Regenerates core/vision/face_db.json from FaceEmbedding rows in the DB."

    def handle(self, *args, **options):
        count = rebuild_face_db()
        self.stdout.write(self.style.SUCCESS(f"face_db.json rebuilt with {count} embedding(s)."))
