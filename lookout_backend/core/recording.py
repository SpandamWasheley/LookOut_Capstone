"""Start/stop the continuous CCTV recorder as a detached background process,
controlled by dashboard login/logout.

The recorder is the `record_camera` management command (writes segment files to
`cctv records/`). Here we launch it detached so it outlives the request, track
its PID in a file (survives Django worker restarts), and stop it on logout.

Windows note: os.kill(pid, 0) does NOT work as a liveness probe on Windows — it
would TERMINATE the process. So liveness is checked via `tasklist` and killing
is done via `taskkill /T` (whole tree), with POSIX signals as the fallback.
"""
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings

PID_FILE = Path(settings.BASE_DIR) / "recorder.pid"
STOP_FILE = Path(settings.BASE_DIR) / "recorder.stop"
RECORD_CAMERA_CODE = "CCTV"
RETENTION_DAYS = 7


def _pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_recording():
    """True if a recorder process we started is still alive."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return False
    if _pid_alive(pid):
        return True
    # stale pid file — clean it up
    try:
        PID_FILE.unlink()
    except OSError:
        pass
    return False


def start_recording(source, camera=RECORD_CAMERA_CODE, retention=RETENTION_DAYS):
    """Launches the recorder detached. Returns True if it started, False if one
    was already running."""
    if is_recording():
        return False
    # Clear any leftover stop sentinel so the new recorder doesn't exit instantly.
    try:
        STOP_FILE.unlink()
    except OSError:
        pass
    args = [sys.executable, "manage.py", "record_camera",
            "--source", source, "--camera", camera,
            "--retention-days", str(retention), "--stop-file", str(STOP_FILE)]
    flags = 0
    if os.name == "nt":
        # No console window, and detached so it survives the request/worker.
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    proc = subprocess.Popen(
        args, cwd=str(settings.BASE_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    PID_FILE.write_text(str(proc.pid))
    return True


def stop_recording():
    """Stops the recorder GRACEFULLY so the current segment's MP4 is finalised
    (playable). Drops a stop sentinel the recorder polls; waits briefly for it to
    exit; hard-kills only as a last resort. Returns True if something was stopped.
    """
    import time

    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        try:
            PID_FILE.unlink()
        except OSError:
            pass
        return False

    # Ask it to stop; the recorder finishes the segment and exits on its own.
    STOP_FILE.write_text("stop")
    for _ in range(60):          # up to ~6s for a clean finalise
        if not _pid_alive(pid):
            break
        time.sleep(0.1)

    # Last resort: if it didn't honour the sentinel, force it (segment may be lost).
    if _pid_alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True)
        else:
            import signal
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    for f in (PID_FILE, STOP_FILE):
        try:
            f.unlink()
        except OSError:
            pass
    return True
