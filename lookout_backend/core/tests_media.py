"""Where evidence ends up, and what gets written on the Alert row.

The detectors do not write through Django's storage API — they call
cv2.imwrite and ClipRecorder.save, which put bytes on local disk. So merely
configuring object storage changes nothing for them: in a split deployment the
file stays on the PC beside the camera, the hosted dashboard asks its OWN
domain for /media/violations/foo.jpg, and every alert shows a broken image.
violation_media_path closes that at the one point all 17 call sites funnel
through.
"""
import os
from unittest import mock

from django.test import TestCase, override_settings

from core import media


LOCAL = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


@override_settings(STORAGES=LOCAL)
class LocalStorageTests(TestCase):
    """With local storage this must behave exactly as it always has.

    STORAGES is pinned rather than inherited: this project's own .env sets
    CLOUDINARY_URL (the edge PC publishes evidence), so without the override
    these would run against remote storage and pass for the wrong reason — the
    fallback path happens to equal the local path.
    """

    def test_it_returns_a_relative_path(self):
        self.assertEqual(media.violation_media_path("foo.jpg"),
                         "/media/violations/foo.jpg")

    def test_exactly_one_leading_slash_whatever_media_url_is(self):
        # Django's own default is 'media/' with no slashes; the leading one is
        # required so build_absolute_uri resolves against the site root rather
        # than the current request's path.
        for value in ("media/", "/media/", "media", "/media"):
            with override_settings(MEDIA_URL=value):
                self.assertEqual(media.violation_media_path("a.jpg"),
                                 "/media/violations/a.jpg")

    def test_it_does_not_touch_storage_at_all(self):
        with mock.patch("django.core.files.storage.default_storage.save") as save:
            media.violation_media_path("foo.jpg")
        save.assert_not_called()

    def test_subdirectories_survive(self):
        # The AI checker stores the frames it sent as ai/alert12/ai_00.jpg.
        self.assertEqual(media.violation_media_path("ai/alert12/ai_00.jpg"),
                         "/media/violations/ai/alert12/ai_00.jpg")


REMOTE = {"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
          "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


@override_settings(STORAGES=REMOTE)
class PublishingTests(TestCase):
    """With object storage the file is uploaded and its URL returned."""

    def setUp(self):
        from django.conf import settings
        self.dir = os.path.join(settings.MEDIA_ROOT, "violations")
        os.makedirs(self.dir, exist_ok=True)

    def _write(self, name, size=1024):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"x" * size)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_the_file_is_uploaded_and_its_url_returned(self):
        self._write("_t_published.jpg")
        with mock.patch("django.core.files.storage.default_storage.save",
                        return_value="violations/_t_published.jpg") as save, \
             mock.patch("django.core.files.storage.default_storage.url",
                        return_value="https://cdn.example/violations/_t_published.jpg"):
            url = media.violation_media_path("_t_published.jpg")
        save.assert_called_once()
        self.assertEqual(save.call_args[0][0], "violations/_t_published.jpg")
        self.assertEqual(url, "https://cdn.example/violations/_t_published.jpg")

    def test_the_url_comes_from_what_storage_actually_stored(self):
        # save() appends a suffix when the name is taken. Building the URL from
        # the requested name instead would point at the wrong object, or at
        # nothing.
        self._write("_t_clash.jpg")
        with mock.patch("django.core.files.storage.default_storage.save",
                        return_value="violations/_t_clash_a1b2c3.jpg"), \
             mock.patch("django.core.files.storage.default_storage.url",
                        side_effect=lambda n: f"https://cdn.example/{n}"):
            url = media.violation_media_path("_t_clash.jpg")
        self.assertEqual(url, "https://cdn.example/violations/_t_clash_a1b2c3.jpg")

    def test_a_missing_file_falls_back_to_the_local_path(self):
        self.assertEqual(media.violation_media_path("_t_absent.jpg"),
                         "/media/violations/_t_absent.jpg")

    def test_an_upload_failure_falls_back_and_never_raises(self):
        # A detector that has just witnessed a violation must not die because a
        # bucket was unreachable. The alert is still worth recording and the
        # local copy still exists.
        self._write("_t_boom.jpg")
        with mock.patch("django.core.files.storage.default_storage.save",
                        side_effect=OSError("bucket unreachable")):
            url = media.violation_media_path("_t_boom.jpg")
        self.assertEqual(url, "/media/violations/_t_boom.jpg")

    @override_settings(EVIDENCE_MAX_UPLOAD_MB=1)
    def test_a_file_over_the_cap_is_kept_local(self):
        # Raw clips run to ~45 MB and a free Cloudinary plan is 25 GB; a few
        # hundred alerts would exhaust it and uploads would then start failing
        # at the moment somebody needs them.
        self._write("_t_huge.mp4", size=2 * 1024 * 1024)
        with mock.patch("django.core.files.storage.default_storage.save") as save:
            url = media.violation_media_path("_t_huge.mp4")
        save.assert_not_called()
        self.assertEqual(url, "/media/violations/_t_huge.mp4")

    @override_settings(EVIDENCE_MAX_UPLOAD_MB=0)
    def test_a_zero_cap_publishes_everything(self):
        self._write("_t_nocap.mp4", size=2 * 1024 * 1024)
        with mock.patch("django.core.files.storage.default_storage.save",
                        return_value="violations/_t_nocap.mp4") as save, \
             mock.patch("django.core.files.storage.default_storage.url",
                        return_value="https://cdn.example/x.mp4"):
            media.violation_media_path("_t_nocap.mp4")
        save.assert_called_once()


class BackendDetectionTests(TestCase):
    @override_settings(STORAGES=LOCAL)
    def test_filesystem_storage_is_not_remote(self):
        self.assertFalse(media._storage_is_remote())

    @override_settings(STORAGES=REMOTE)
    def test_anything_else_is_remote(self):
        self.assertTrue(media._storage_is_remote())

    @override_settings(STORAGES={"default": {
        "BACKEND": "cloudinary_storage.storage.RawMediaCloudinaryStorage"}})
    def test_cloudinary_is_remote(self):
        self.assertTrue(media._storage_is_remote())

    @override_settings(STORAGES={"default": {"BACKEND": "nonexistent.module.Storage"}})
    def test_it_never_constructs_the_backend(self):
        # Decided from the setting alone. Instantiating the Cloudinary backend
        # needs credentials, and this runs on the path whose whole job is to
        # work out whether those credentials are in play at all — so an
        # unimportable backend must still answer, not raise.
        self.assertTrue(media._storage_is_remote())

    @override_settings(STORAGES={})
    def test_no_configured_backend_is_treated_as_local(self):
        self.assertFalse(media._storage_is_remote())
