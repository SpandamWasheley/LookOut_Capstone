import os
import sys
import threading
import time

from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        """Start live detection by itself when the web server starts, if Settings -> System says so.

        Only for `runserver` (in its reloaded child process, so it runs once), never for migrate,
        tests or management commands. Delayed a few seconds so the server is up first.
        """
        if "runserver" not in sys.argv:
            return
        if os.environ.get("RUN_MAIN") != "true" and "--noreload" not in sys.argv:
            return

        # Django imports the URL config (and with it views -> vision -> PyTorch) on
        # the FIRST request, so the dashboard's first call - login, /cameras/ -
        # sat waiting for it. Do that import now, off the main thread, so the
        # server is already warm when the first browser request arrives.
        def _warm_urls():
            try:
                from django.urls import get_resolver
                get_resolver().url_patterns
            except Exception:                                        # noqa: BLE001
                import logging
                logging.getLogger(__name__).exception("URL warm-up failed")

        threading.Thread(target=_warm_urls, daemon=True, name="warm-urls").start()

        def _auto_start():
            time.sleep(8)
            _close_orphaned_jobs()
            try:
                from core.models import SystemSettings
                from core.monitor import monitor
                if SystemSettings.load().auto_start_detection:
                    monitor.start()
            except Exception:                                        # noqa: BLE001
                import logging
                logging.getLogger(__name__).exception("Automatic start of detection failed")

        threading.Thread(target=_auto_start, daemon=True, name="auto-start-detection").start()


def _close_orphaned_jobs():
    """Mark as failed any DetectionJob still called 'running' whose process is gone.

    A job's status is updated by _watch_detection_job, a thread inside THIS
    server process (there is no task queue — see DetectionJobViewSet.run). So
    when the server restarts, every thread watching a live subprocess dies with
    it and those rows stay 'running' for ever.

    Two costs, both seen in practice. The history showed runs that had ended
    days earlier as still in progress; and because a 'running' job's staged
    clip is treated as in use, nine dead rows pinned 3.4 GB of video on a disk
    that had 0.44 GB left, which is what stopped new uploads from being
    written at all.

    A PID that no longer exists is the honest signal, and the only one
    available after a restart. Deliberately conservative: a row whose PID IS
    still alive is left alone, so a detector that genuinely outlived the server
    keeps its row.
    """
    try:
        import psutil
        from django.utils import timezone

        from core.models import DetectionJob

        running = list(DetectionJob.objects.filter(status=DetectionJob.Status.RUNNING))
        if not running:
            return
        alive = {p.pid for p in psutil.process_iter(["pid"])}
        for job in running:
            if job.pid and job.pid in alive:
                continue
            job.status = DetectionJob.Status.FAILED
            job.finished_at = job.finished_at or timezone.now()
            job.error = (job.error or "") + (
                "\nThe server restarted while this run was in progress, so its "
                "outcome was never recorded. The detector process is gone."
            )
            job.save(update_fields=["status", "finished_at", "error"])
    except Exception:                                                # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("Could not close orphaned detection jobs")
