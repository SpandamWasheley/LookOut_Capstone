"""Uploading a detection clip in pieces, and resuming one that did not finish.

The clips this system is pointed at are routinely 200 MB. Sent as a single
multipart POST that is one request which has to survive from first byte to
last: a dropped connection, a tunnel hiccup or a proxy read timeout at 90%
threw away every byte, and a page refresh did the same, because a browser
cannot re-read a File it no longer holds a handle to.

So a clip is declared once and then sent as chunks against that session. What
matters here is less the happy path than the awkward ones — a chunk sent twice,
a chunk of the wrong length, a second attempt at a file the server already has
most of, and the disk not filling up with pieces nobody came back for.
"""
import datetime
import os
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import DetectionJob, UploadSession
from core.views import _purge_stale_uploads


def _readable_clip():
    """A cv2.VideoCapture that decodes one frame — complete() reads the first
    frame of the stitched clip, and the bytes in these tests are not video."""
    cap = mock.MagicMock()
    cap.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))
    cap.isOpened.return_value = True
    return cap


class ChunkedUploadTests(TestCase):
    def setUp(self):
        # Part files and stitched clips are real files, so they go somewhere
        # disposable rather than into the project's own media/.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)

        User = get_user_model()
        self.admin = User.objects.create_user(username="adm", password="x", role="admin")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    # -- helpers ----------------------------------------------------------
    def _start(self, name="clip.mp4", size=10, fingerprint="clip.mp4|10|1"):
        return self.c.post("/api/uploads/",
                           {"filename": name, "size": size, "fingerprint": fingerprint})

    def _send(self, session_id, index, payload):
        return self.c.post(
            f"/api/uploads/{session_id}/chunk/",
            {"index": index, "chunk": SimpleUploadedFile("part", payload)},
        )

    def _complete(self, session_id):
        with mock.patch("cv2.VideoCapture", return_value=_readable_clip()), \
             mock.patch("cv2.imencode", return_value=(True, np.zeros((10,), dtype=np.uint8))):
            return self.c.post(f"/api/uploads/{session_id}/complete/")

    def _small_chunks(self, size=4):
        """A session whose chunk_size is tiny, so a handful of bytes exercises
        the same multi-chunk path a 200 MB clip does."""
        session = UploadSession.objects.create(
            filename="clip.mp4", size=size * 3, fingerprint="f", chunk_size=size,
            created_by=self.admin,
        )
        os.makedirs(session.part_dir, exist_ok=True)
        return session

    # -- declaring an upload ---------------------------------------------
    def test_starting_an_upload_reports_how_to_send_it(self):
        r = self._start(size=UploadSession.CHUNK_BYTES * 2 + 5)
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body["total_chunks"], 3)
        self.assertEqual(body["chunk_size"], UploadSession.CHUNK_BYTES)
        self.assertEqual(body["received_indices"], [])
        self.assertEqual(body["status"], "uploading")

    def test_the_type_is_refused_before_any_bytes_are_sent(self):
        # The point of checking at declaration time: a 200 MB .mov should cost
        # one tiny request to reject, not the whole upload.
        r = self._start(name="clip.mov")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Unsupported file type", r.json()["detail"])
        self.assertFalse(UploadSession.objects.exists())

    def test_a_clip_over_the_limit_is_refused_at_declaration(self):
        r = self._start(size=2 * 1024 * 1024 * 1024)
        self.assertEqual(r.status_code, 400)
        self.assertIn("too large", r.json()["detail"])

    def test_a_missing_or_nonsense_size_is_refused(self):
        self.assertEqual(self.c.post("/api/uploads/", {"filename": "a.mp4"}).status_code, 400)
        self.assertEqual(self._start(size="lots").status_code, 400)
        self.assertEqual(self._start(size=0).status_code, 400)

    @override_settings(DETECTION_ENABLED=False)
    def test_a_server_that_cannot_run_detectors_refuses_the_upload(self):
        # Staging a clip on an API-only host is 200 MB uploaded for nothing —
        # so it is refused here rather than at job creation.
        r = self._start()
        self.assertEqual(r.status_code, 503)
        self.assertIn("does not run detectors", r.json()["detail"])

    # -- sending chunks ---------------------------------------------------
    def test_chunks_land_and_are_reported_back(self):
        session = self._small_chunks()
        self.assertEqual(self._send(session.id, 0, b"aaaa").status_code, 200)
        self.assertEqual(self._send(session.id, 2, b"cccc").status_code, 200)

        body = self.c.get(f"/api/uploads/{session.id}/").json()
        self.assertEqual(body["received_indices"], [0, 2])
        self.assertEqual(body["received_bytes"], 8)

    def test_chunks_may_arrive_out_of_order(self):
        # They go up in parallel, so order is not something the client controls.
        session = self._small_chunks()
        for index, payload in ((2, b"cccc"), (0, b"aaaa"), (1, b"bbbb")):
            self.assertEqual(self._send(session.id, index, payload).status_code, 200)

        r = self._complete(session.id)
        self.assertEqual(r.status_code, 200, r.content)
        staged = settings.MEDIA_ROOT / "uploads" / r.json()["staged_token"]
        self.assertEqual(staged.read_bytes(), b"aaaabbbbcccc")
        staged.unlink()

    def test_re_sending_a_chunk_is_harmless(self):
        # A client that gave up waiting for the reply sends it again; that must
        # not append, duplicate or corrupt anything.
        session = self._small_chunks()
        self._send(session.id, 0, b"aaaa")
        self._send(session.id, 0, b"aaaa")
        self._send(session.id, 1, b"bbbb")
        self._send(session.id, 2, b"cccc")

        r = self._complete(session.id)
        staged = settings.MEDIA_ROOT / "uploads" / r.json()["staged_token"]
        self.assertEqual(staged.read_bytes(), b"aaaabbbbcccc")
        staged.unlink()

    def test_a_chunk_of_the_wrong_length_is_refused(self):
        # This is the check that makes the stitched file provably the size that
        # was declared — and so provably inside the upload limit.
        session = self._small_chunks()
        r = self._send(session.id, 0, b"aa")
        self.assertEqual(r.status_code, 400)
        self.assertIn("should be 4 bytes", r.json()["detail"])
        self.assertEqual(session.received_indices(), [])

    def test_the_last_chunk_is_the_remainder_not_a_full_one(self):
        session = UploadSession.objects.create(
            filename="clip.mp4", size=10, fingerprint="f", chunk_size=4,
            created_by=self.admin,
        )
        os.makedirs(session.part_dir, exist_ok=True)
        self.assertEqual(session.total_chunks, 3)
        self.assertEqual(self._send(session.id, 2, b"xx").status_code, 200)
        self.assertEqual(self._send(session.id, 2, b"xxxx").status_code, 400)

    def test_an_index_past_the_end_is_refused(self):
        session = self._small_chunks()
        r = self._send(session.id, 9, b"aaaa")
        self.assertEqual(r.status_code, 400)
        self.assertIn("out of range", r.json()["detail"])

    def test_a_short_part_on_disk_counts_as_missing(self):
        # A chunk request killed mid-write leaves a truncated file. Reporting it
        # as received would resume past bytes that were never stored.
        session = self._small_chunks()
        self._send(session.id, 0, b"aaaa")
        session.part_path(0).write_bytes(b"aa")
        self.assertEqual(session.received_indices(), [])

    # -- finishing --------------------------------------------------------
    def test_completing_early_says_what_is_missing(self):
        session = self._small_chunks()
        self._send(session.id, 0, b"aaaa")
        r = self._complete(session.id)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["missing"], [1, 2])

    def test_completing_hands_back_the_same_payload_as_a_one_shot_stage(self):
        # The contract that keeps the rest of the flow (edges, trim, Start)
        # unaware of how the bytes arrived.
        session = self._small_chunks()
        for index, payload in enumerate((b"aaaa", b"bbbb", b"cccc")):
            self._send(session.id, index, payload)
        body = self._complete(session.id).json()
        self.assertEqual(sorted(body),
                         ["height", "image", "source_filename", "staged_token", "width"])
        self.assertTrue(body["image"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(body["source_filename"], "clip.mp4")
        (settings.MEDIA_ROOT / "uploads" / body["staged_token"]).unlink()

    def test_the_parts_are_cleared_once_they_are_stitched(self):
        # Stitching deletes each piece as it is consumed, so finishing needs
        # about the clip's own size free rather than twice it.
        session = self._small_chunks()
        for index, payload in enumerate((b"aaaa", b"bbbb", b"cccc")):
            self._send(session.id, index, payload)
        token = self._complete(session.id).json()["staged_token"]
        self.assertFalse(session.part_dir.exists())
        (settings.MEDIA_ROOT / "uploads" / token).unlink()

    def test_a_clip_that_will_not_decode_is_thrown_away(self):
        session = self._small_chunks()
        for index, payload in enumerate((b"aaaa", b"bbbb", b"cccc")):
            self._send(session.id, index, payload)
        dead = mock.MagicMock()
        dead.read.return_value = (False, None)
        with mock.patch("cv2.VideoCapture", return_value=dead):
            r = self.c.post(f"/api/uploads/{session.id}/complete/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Could not read", r.json()["detail"])
        # The session goes too, so a retry starts clean instead of resuming
        # into a file that will never open.
        self.assertFalse(UploadSession.objects.filter(pk=session.pk).exists())

    def test_completing_twice_re_reads_the_staged_clip(self):
        session = self._small_chunks()
        for index, payload in enumerate((b"aaaa", b"bbbb", b"cccc")):
            self._send(session.id, index, payload)
        first = self._complete(session.id).json()
        again = self._complete(session.id)
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.json()["staged_token"], first["staged_token"])
        (settings.MEDIA_ROOT / "uploads" / first["staged_token"]).unlink()

    def test_completing_works_from_the_dashboards_json_fetch(self):
        # The web dashboard calls complete() and destroy() through its ordinary
        # JSON helper, which always sets Content-Type: application/json.
        session = self._small_chunks()
        for index, payload in enumerate((b"aaaa", b"bbbb", b"cccc")):
            self._send(session.id, index, payload)
        with mock.patch("cv2.VideoCapture", return_value=_readable_clip()), \
             mock.patch("cv2.imencode", return_value=(True, np.zeros((10,), dtype=np.uint8))):
            r = self.c.post(f"/api/uploads/{session.id}/complete/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        (settings.MEDIA_ROOT / "uploads" / r.json()["staged_token"]).unlink()

    def test_a_staged_clip_the_run_already_consumed_is_gone_for_good(self):
        session = self._small_chunks()
        for index, payload in enumerate((b"aaaa", b"bbbb", b"cccc")):
            self._send(session.id, index, payload)
        token = self._complete(session.id).json()["staged_token"]
        (settings.MEDIA_ROOT / "uploads" / token).unlink()
        r = self._complete(session.id)
        self.assertEqual(r.status_code, 410)

    # -- resuming ---------------------------------------------------------
    def test_declaring_the_same_file_again_resumes_it(self):
        # The whole point. A second attempt at a clip the server already has
        # most of must continue, not restart.
        first = self._start(size=10, fingerprint="clip.mp4|10|7").json()
        session = UploadSession.objects.get(pk=first["id"])
        session.chunk_size = 4
        session.save(update_fields=["chunk_size"])
        self._send(session.id, 0, b"aaaa")
        self._send(session.id, 1, b"bbbb")

        again = self._start(size=10, fingerprint="clip.mp4|10|7")
        self.assertEqual(again.status_code, 200)          # rejoined, not created
        body = again.json()
        self.assertEqual(body["id"], str(session.id))
        self.assertEqual(body["received_indices"], [0, 1])
        self.assertEqual(body["received_bytes"], 8)
        self.assertEqual(UploadSession.objects.count(), 1)

    def test_a_different_file_with_the_same_fingerprint_does_not_resume(self):
        # Belt and braces against stitching two different videos together: the
        # declared size has to match as well.
        first = self._start(size=10, fingerprint="clip.mp4|10|7").json()
        again = self._start(size=20, fingerprint="clip.mp4|10|7")
        self.assertEqual(again.status_code, 201)
        self.assertNotEqual(again.json()["id"], first["id"])

    def test_the_resume_list_is_scoped_to_its_own_user(self):
        other = get_user_model().objects.create_user(username="adm2", password="x", role="admin")
        UploadSession.objects.create(filename="theirs.mp4", size=10, fingerprint="t",
                                     created_by=other)
        mine = self._start().json()

        listed = self.c.get("/api/uploads/").json()
        self.assertEqual([s["id"] for s in listed], [mine["id"]])

    def test_another_user_cannot_touch_someone_elses_upload(self):
        other = get_user_model().objects.create_user(username="adm2", password="x", role="admin")
        theirs = UploadSession.objects.create(filename="theirs.mp4", size=10,
                                              fingerprint="t", created_by=other)
        self.assertEqual(self.c.get(f"/api/uploads/{theirs.id}/").status_code, 404)
        self.assertEqual(self._send(theirs.id, 0, b"x").status_code, 404)
        self.assertEqual(self.c.delete(f"/api/uploads/{theirs.id}/").status_code, 404)

    def test_the_list_can_be_narrowed_to_one_file(self):
        self._start(name="a.mp4", size=10, fingerprint="a|10|1")
        wanted = self._start(name="b.mp4", size=20, fingerprint="b|20|1").json()
        listed = self.c.get("/api/uploads/?fingerprint=b|20|1").json()
        self.assertEqual([s["id"] for s in listed], [wanted["id"]])

    def test_a_finished_upload_is_not_offered_for_resuming(self):
        session = self._small_chunks()
        for index, payload in enumerate((b"aaaa", b"bbbb", b"cccc")):
            self._send(session.id, index, payload)
        token = self._complete(session.id).json()["staged_token"]
        self.assertEqual(self.c.get("/api/uploads/").json(), [])
        (settings.MEDIA_ROOT / "uploads" / token).unlink()

    # -- giving up, and not filling the disk ------------------------------
    def test_abandoning_an_upload_frees_its_disk_now(self):
        session = self._small_chunks()
        self._send(session.id, 0, b"aaaa")
        self.assertTrue(session.part_path(0).exists())

        r = self.c.delete(f"/api/uploads/{session.id}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(session.part_dir.exists())
        self.assertFalse(UploadSession.objects.filter(pk=session.pk).exists())

    def test_an_upload_nobody_came_back_to_is_swept(self):
        session = self._small_chunks()
        self._send(session.id, 0, b"aaaa")
        UploadSession.objects.filter(pk=session.pk).update(
            updated_at=timezone.now() - datetime.timedelta(hours=13))

        _purge_stale_uploads()
        self.assertFalse(session.part_dir.exists())
        self.assertFalse(UploadSession.objects.filter(pk=session.pk).exists())

    def test_an_upload_still_moving_is_left_alone(self):
        session = self._small_chunks()
        self._send(session.id, 0, b"aaaa")
        _purge_stale_uploads()
        self.assertTrue(UploadSession.objects.filter(pk=session.pk).exists())
        self.assertTrue(session.part_path(0).exists())

    def test_a_staged_clip_nobody_ran_is_swept_with_its_session(self):
        session = self._small_chunks()
        for index, payload in enumerate((b"aaaa", b"bbbb", b"cccc")):
            self._send(session.id, index, payload)
        token = self._complete(session.id).json()["staged_token"]
        staged = settings.MEDIA_ROOT / "uploads" / token
        UploadSession.objects.filter(pk=session.pk).update(
            updated_at=timezone.now() - datetime.timedelta(hours=25))

        _purge_stale_uploads()
        self.assertFalse(staged.exists())
        self.assertFalse(UploadSession.objects.filter(pk=session.pk).exists())

    def test_a_staged_clip_a_run_is_using_is_never_deleted(self):
        # The subprocess is reading that file right now, and
        # _watch_detection_job removes it when the run ends.
        session = self._small_chunks()
        for index, payload in enumerate((b"aaaa", b"bbbb", b"cccc")):
            self._send(session.id, index, payload)
        token = self._complete(session.id).json()["staged_token"]
        staged = settings.MEDIA_ROOT / "uploads" / token
        DetectionJob.objects.create(violation_type="smoking", source_filename="clip.mp4",
                                    source_path=str(staged))
        UploadSession.objects.filter(pk=session.pk).update(
            updated_at=timezone.now() - datetime.timedelta(hours=25))

        _purge_stale_uploads()
        self.assertTrue(staged.exists())
        staged.unlink()

    def test_one_user_cannot_pile_up_unfinished_uploads(self):
        # The cap is what stops a stuck client filling the disk with parts
        # faster than the sweep clears them.
        ids = [self._start(name=f"c{i}.mp4", size=10, fingerprint=f"c{i}").json()["id"]
               for i in range(5)]
        live = {str(s.pk) for s in UploadSession.objects.all()}
        self.assertEqual(len(live), 3)
        self.assertEqual(live, set(ids[-3:]))          # the oldest were dropped

    # -- who may upload ---------------------------------------------------
    def test_a_dispatcher_cannot_upload(self):
        # Run Detection is an admin tool; so is its upload endpoint.
        User = get_user_model()
        dispatcher = User.objects.create_user(username="disp", password="x", role="dispatcher")
        c = APIClient()
        c.force_authenticate(dispatcher)
        self.assertEqual(c.post("/api/uploads/", {"filename": "a.mp4", "size": 10}).status_code, 403)

    def test_an_anonymous_caller_cannot_upload(self):
        self.assertEqual(APIClient().post("/api/uploads/", {"filename": "a.mp4", "size": 10}).status_code, 401)
