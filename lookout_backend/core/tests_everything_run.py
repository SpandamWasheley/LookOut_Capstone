"""The "Everything" option: all five violations over one clip.

Not a wider merged pass. "Merged" means one shared YOLO forward pass over
merged_v2.pt routed to four rule layers; disposal runs no trained violation
class at all, so it has nothing to share and goes alongside as its own
process. These cover that arrangement — two processes, one job row.
"""

import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Camera, DetectionJob, User
from core.views import (DETECTION_COMMANDS, DETECTION_COMPANIONS,
                        EDGE_CAPABLE, EDGE_REQUIRED)


class EverythingRegistrationTests(TestCase):
    def test_it_runs_the_merged_four_as_its_primary(self):
        self.assertEqual(DETECTION_COMMANDS["everything"], "watch_merged_all")

    def test_disposal_runs_alongside_rather_than_inside(self):
        self.assertEqual(DETECTION_COMPANIONS["everything"], ("watch_disposal",))

    def test_nothing_else_has_companions(self):
        """Every other option is one detector. A companion silently attached to
        one would double its cost and file alerts nobody asked for."""
        self.assertEqual(set(DETECTION_COMPANIONS), {"everything"})

    def test_it_may_use_an_area_but_does_not_require_one(self):
        self.assertIn("everything", EDGE_CAPABLE)
        self.assertNotIn("everything", EDGE_REQUIRED)

    def test_the_dashboard_offers_it(self):
        source = (Path(settings.BASE_DIR).parent / "lookout" / "src"
                  / "constants" / "detectionTypes.js").read_text(encoding="utf-8")
        self.assertIn('key: "everything"', source)

    def test_it_is_presented_as_the_next_merged_option(self):
        """Labelled and styled as the third step in the Merged family, which
        is how an operator reads the picker.

        The label is a product decision, not a description of the mechanism:
        this one is two processes, not one shared pass. The distinction lives
        in the code comment and in DETECTION_COMPANIONS, which the tests below
        pin down — so the naming cannot quietly become an assumption that
        disposal reads the merged model."""
        source = (Path(settings.BASE_DIR).parent / "lookout" / "src"
                  / "constants" / "detectionTypes.js").read_text(encoding="utf-8")
        code = re.sub(r"(?<!:)//.*", "", source)
        block = code[code.index('key: "everything"'):]
        self.assertIn('label: "Merged (All 5)"', block[:140])

    def test_the_merged_family_reads_in_order(self):
        """3, 4, 5 — a picker that skipped or reordered them would read as
        three unrelated options rather than one escalating choice."""
        source = (Path(settings.BASE_DIR).parent / "lookout" / "src"
                  / "constants" / "detectionTypes.js").read_text(encoding="utf-8")
        labels = re.findall(r'label: "(Merged \(All \d\))"', source)
        self.assertEqual(labels, ["Merged (All 3)", "Merged (All 4)", "Merged (All 5)"])


class EverythingLaunchTests(TestCase):
    """What actually gets spawned, with Popen stubbed."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="adm", password="x", role="admin", display_name="Admin")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def _upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile("clip.mp4", b"\x00" * 64, content_type="video/mp4")

    def _start(self, violation_type):
        """Returns (response, [argv per spawned process], [env per process])."""
        spawned, envs = [], []

        class FakeProc:
            def __init__(self, pid):
                self.pid = pid
            def wait(self):
                return 0

        def fake_popen(argv, **kw):
            spawned.append(argv)
            envs.append(kw.get("env") or {})
            return FakeProc(4000 + len(spawned))

        with mock.patch("subprocess.Popen", side_effect=fake_popen), \
                mock.patch("threading.Thread"), \
                mock.patch("cv2.VideoCapture") as cap:
            cap.return_value.isOpened.return_value = True
            res = self.c.post("/api/detection-jobs/",
                              {"violation_type": violation_type, "file": self._upload()})
        return res, spawned, envs

    def test_it_launches_two_detectors(self):
        res, spawned, _envs = self._start("everything")
        self.assertEqual(res.status_code, 201, res.content)
        commands = [a[2] for a in spawned]
        self.assertEqual(commands, ["watch_merged_all", "watch_disposal"])

    def test_both_get_the_same_source_and_camera(self):
        """Otherwise the two halves of one run file alerts against different
        cameras, and the operator sees a split they never asked for."""
        _res, spawned, _envs = self._start("everything")
        primary, companion = spawned
        self.assertEqual(primary[primary.index("--source") + 1],
                         companion[companion.index("--source") + 1])
        self.assertEqual(primary[primary.index("--camera") + 1],
                         companion[companion.index("--camera") + 1])

    def test_only_the_primary_publishes_the_live_view(self):
        """One directory per job, so a second publisher would overwrite the
        first's frames and subjects."""
        _res, _spawned, envs = self._start("everything")
        primary_env, companion_env = envs
        self.assertIn("LOOKOUT_DEBUG_DIR", primary_env)
        self.assertNotIn("LOOKOUT_DEBUG_DIR", companion_env)

    def test_both_pids_are_recorded(self):
        _res, spawned, _envs = self._start("everything")
        job = DetectionJob.objects.get(violation_type="everything")
        self.assertIsNotNone(job.pid)
        self.assertEqual(len(job.extra_pids), 1)
        self.assertNotIn(job.pid, job.extra_pids)

    def test_an_ordinary_run_still_launches_one(self):
        _res, spawned, _envs = self._start("parking")
        self.assertEqual(len(spawned), 1)
        job = DetectionJob.objects.get(violation_type="parking")
        self.assertEqual(job.extra_pids, [])


class EverythingLifecycleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="adm2", password="x", role="admin", display_name="A")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def _job(self, **over):
        fields = dict(violation_type="everything", source_filename="c.mp4",
                      source_path="/tmp/c.mp4", status=DetectionJob.Status.RUNNING,
                      pid=4001, extra_pids=[4002])
        fields.update(over)
        return DetectionJob.objects.create(**fields)

    def test_cancelling_stops_the_companion_too(self):
        """A companion left alive would go on writing alerts against a job the
        operator has just stopped."""
        job = self._job()
        killed = []
        with mock.patch("psutil.Process") as proc:
            proc.side_effect = lambda pid: mock.Mock(
                terminate=lambda: killed.append(pid), children=lambda **k: [])
            self.c.post(f"/api/detection-jobs/{job.pk}/cancel/")
        self.assertIn(4002, killed)

    def test_a_dead_companion_does_not_block_the_cancel(self):
        """NoSuchProcess, which is what psutil actually raises for a pid that
        has already exited — the common case, since a companion may well
        finish before the operator presses Cancel."""
        import psutil
        job = self._job()
        with mock.patch("psutil.Process", side_effect=psutil.NoSuchProcess(4002)):
            res = self.c.post(f"/api/detection-jobs/{job.pk}/cancel/")
        self.assertIn(res.status_code, (200, 202, 204))
        job.refresh_from_db()
        self.assertEqual(job.status, DetectionJob.Status.CANCELLED)

    def test_a_job_is_not_orphaned_while_its_companion_lives(self):
        """Disposal commonly outlives the merged pass — it holds pinned
        candidates and defers its evidence clips. Marking the run FAILED then
        would both lie and stop the page polling a detector still filing
        alerts."""
        from core.apps import _close_orphaned_jobs
        job = self._job()
        fake = [mock.Mock(pid=4002)]      # only the companion is alive
        with mock.patch("psutil.process_iter", return_value=fake):
            _close_orphaned_jobs()
        job.refresh_from_db()
        self.assertEqual(job.status, DetectionJob.Status.RUNNING)

    def test_a_job_with_nothing_alive_is_still_closed(self):
        from core.apps import _close_orphaned_jobs
        job = self._job()
        with mock.patch("psutil.process_iter", return_value=[]):
            _close_orphaned_jobs()
        job.refresh_from_db()
        self.assertEqual(job.status, DetectionJob.Status.FAILED)

    def test_the_watcher_waits_for_the_companion_before_deleting_the_clip(self):
        """Removing the source out from under a still-running detector loses
        whatever it had not written yet."""
        import inspect
        from core import views
        source = inspect.getsource(views._watch_detection_job)
        self.assertIn("for extra in extras:", source)
        self.assertLess(source.index("extra.wait()"), source.index("os.remove"))
