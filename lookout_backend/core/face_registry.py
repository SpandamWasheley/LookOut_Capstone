"""Bridges the Person/FaceEmbedding DB rows to the flat face_db.json file that
core/vision/recognition.py's match_embedding() reads at detection time.

Kept separate from core/vision/recognition.py on purpose — that module is
deliberately pure CV plumbing with no Django model access (see CLAUDE.md), so
the DB-aware "regenerate the file" step lives here instead.
"""

from core.models import Person
from core.vision import recognition


def rebuild_face_db():
    """Regenerates face_db.json from FaceEmbedding rows for enrolled people.

    One entry per (person, angle) — match_embedding() just scans every entry
    for the best cosine-similarity hit, so multiple angles per person need no
    special handling there.
    """
    entries = []
    people = Person.objects.filter(status=Person.Status.ENROLLED).prefetch_related("embeddings")
    for person in people:
        for embedding in person.embeddings.all():
            entries.append({
                "person_id": person.id,
                "code": person.person_code,
                "name": person.full_name,
                "angle": embedding.angle,
                "embedding": embedding.embedding,
            })
    recognition.save_face_db(entries)
    return len(entries)


def match_face_in_frame(frame, box, threshold_pct):
    """Attempts to match a face against the enrolled registry.

    `box` is an (x1, y1, x2, y2) crop within `frame` to search (typically a
    tracked person's bounding box); pass None to search the whole frame.
    Returns (Person, confidence_pct) on a match at or above `threshold_pct`,
    or (None, None) otherwise — including when face_db.json is empty, no
    face is detected, or the matched entry's Person has since been deleted
    (a stale, un-rebuilt face_db.json entry). Raises nothing itself; callers
    that need "never blocks alert creation" should still wrap the call, since
    this only covers the matching logic, not e.g. a corrupt frame upstream.
    """
    face_db = recognition.precompute_face_db(recognition.load_face_db())
    if not face_db:
        return None, None

    if box is not None:
        x1, y1, x2, y2 = (int(v) for v in box)
        crop = frame[max(y1, 0):y2, max(x1, 0):x2]
    else:
        crop = frame

    embedding = recognition.compute_face_embedding(crop)
    match, score_pct = recognition.match_embedding(embedding, face_db, threshold_pct)
    if match is None:
        return None, None

    person = Person.objects.filter(id=match.get("person_id")).first()
    if person is None:
        return None, None
    return person, score_pct
