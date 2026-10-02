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

        def _auto_start():
            time.sleep(8)
            try:
                from core.models import SystemSettings
                from core.monitor import monitor
                if SystemSettings.load().auto_start_detection:
                    monitor.start()
            except Exception:                                        # noqa: BLE001
                import logging
                logging.getLogger(__name__).exception("Automatic start of detection failed")

        threading.Thread(target=_auto_start, daemon=True, name="auto-start-detection").start()
