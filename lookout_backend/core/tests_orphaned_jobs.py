"""Closing detection jobs the server lost track of.

A job's status is written by a thread inside the web server process (there is
no task queue). A restart kills every one of those threads, so rows stay
'running' for ever — the history shows runs that ended days ago as in
progress, and because a running job's staged clip counts as in use, dead rows
pin their video on disk. Nine of them once held 3.4 GB on a disk with 0.44 GB
free, which is what stopped new uploads from being written at all.
"""
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from core.apps import _close_orphaned_jobs
from core.models import DetectionJob


class CloseOrphanedJobsTests(TestCase):
    def _job(self, pid, status=DetectionJob.Status.RUNNING, **over):
        return DetectionJob.objects.create(
            violation_type="parking", source_filename="clip.mkv",
            source_path="/tmp/clip.mkv", status=status, pid=pid, **over)

    def _sweep(self, alive_pids):
        procs = [mock.Mock(pid=p) for p in alive_pids]
        with mock.patch("psutil.process_iter", return_value=procs):
            _close_orphaned_jobs()

    def test_a_running_job_whose_process_is_gone_is_failed(self):
        job = self._job(pid=4242)
        self._sweep(alive_pids={1, 2, 3})
        job.refresh_from_db()
        self.assertEqual(job.status, DetectionJob.Status.FAILED)
        self.assertIsNotNone(job.finished_at)
        self.assertIn("server restarted", job.error)

    def test_a_running_job_whose_process_is_alive_is_left_alone(self):
        # Deliberately conservative: a detector that outlived the server keeps
        # its row, and its staged clip keeps counting as in use.
        job = self._job(pid=4242)
        self._sweep(alive_pids={4242})
        job.refresh_from_db()
        self.assertEqual(job.status, DetectionJob.Status.RUNNING)
        self.assertEqual(job.error, "")

    def test_a_running_job_with_no_pid_recorded_is_failed(self):
        # pid is null between the Popen call and the row being updated; after a
        # restart there is no way to prove such a row is live, and leaving it
        # 'running' for ever is the worse of the two errors.
        job = self._job(pid=None)
        self._sweep(alive_pids={1})
        job.refresh_from_db()
        self.assertEqual(job.status, DetectionJob.Status.FAILED)

    def test_finished_jobs_are_untouched(self):
        done = self._job(pid=1, status=DetectionJob.Status.DONE)
        cancelled = self._job(pid=2, status=DetectionJob.Status.CANCELLED)
        failed = self._job(pid=3, status=DetectionJob.Status.FAILED)
        self._sweep(alive_pids=set())
        for job in (done, cancelled, failed):
            was = job.status
            job.refresh_from_db()
            self.assertEqual(job.status, was)

    def test_an_existing_finished_at_is_not_overwritten(self):
        when = timezone.now() - timezone.timedelta(hours=3)
        job = self._job(pid=4242, finished_at=when)
        self._sweep(alive_pids=set())
        job.refresh_from_db()
        self.assertEqual(job.finished_at, when)

    def test_it_does_nothing_and_raises_nothing_when_there_is_no_work(self):
        with mock.patch("psutil.process_iter") as procs:
            _close_orphaned_jobs()
        procs.assert_not_called()      # no running rows: don't even enumerate processes

    def test_a_failure_to_sweep_never_takes_the_server_down(self):
        # It runs on the startup thread beside auto-start; an exception here
        # must not stop the server coming up.
        self._job(pid=4242)
        with mock.patch("psutil.process_iter", side_effect=OSError("no /proc")):
            _close_orphaned_jobs()     # must not raise
