"""Record the camera continuously, the way an NVR does -- not only violations.

    python manage.py record_cctv                       # the one camera, from its saved stream
    python manage.py record_cctv --source clip.mp4     # a file, for trying it out
    python manage.py record_cctv --mode source         # copy the camera's own H.264, no re-encode
    python manage.py record_cctv --duration 60         # stop after a minute (testing)
    python manage.py record_cctv --dry-run             # print the ffmpeg command and exit

Normally nobody runs this by hand: core/monitor.py starts it with the
detectors when live monitoring starts, and stops it when monitoring stops, so
the tape covers exactly the time the system was connected to the camera.

WHAT THIS PROCESS ACTUALLY DOES
-------------------------------
Almost nothing, and deliberately. ffmpeg does the recording; this supervises
it, which is three jobs no single ffmpeg invocation can do for itself:

1. It restarts ffmpeg when the camera goes away. An RTSP stream that drops
   ends ffmpeg with an error, and without a supervisor the tape stops for good
   at the first brownout -- the failure that makes an archive worthless,
   because nobody notices until they go looking for footage that was never
   recorded. The backoff matches the detectors' (5s doubling to 2 minutes).

2. It watches for a stream that stopped DELIVERING without ending. A camera
   that answers but sends nothing leaves ffmpeg alive and writing nothing at
   all; the only evidence is that the segment on disk stops growing. If
   nothing has been written for STALL_SECONDS, ffmpeg is restarted. (A socket
   timeout would be the tidier fix, but the option for it has been renamed
   twice across ffmpeg versions -- stimeout, then timeout -- and an argv that
   fails on the operator's build would stop the recording it exists to
   protect. Watching the bytes works on every build.)

3. It keeps the archive inside its bounds (core/recording.sweep), on a timer,
   because the thing that eventually stops a continuous recorder is a full
   disk.

Stopping is `q` on ffmpeg's stdin, not a signal: ffmpeg only writes a
segment's moov atom when it closes the file, so a killed ffmpeg leaves its
last segment unplayable. `q` asks it to finish properly. We therefore do NOT
pass -nostdin here (unlike recording.ffmpeg_command's default use elsewhere),
because stdin is the control channel.

And THIS process is asked to stop through a flag file (recording.request_stop),
checked in the same five-second loop, rather than by a signal -- because
Windows has none. os.kill(pid, SIGTERM) there is TerminateProcess: the handler
below never runs, ffmpeg is never told to finish, and the open segment is lost.
The signal handlers are still installed for Ctrl-C in a terminal, where they do
work.
"""
import os
import signal
import subprocess
import time

from django.core.management.base import BaseCommand

from core import recording

# How long the current segment may go without growing before the stream is
# treated as gone. Longer than any sane keyframe interval and than the moment a
# segment rolls over, so a healthy recorder is never restarted by this.
STALL_SECONDS = 60
BACKOFF_START = 5
BACKOFF_MAX = 120
HEALTHY_AFTER = 120        # ffmpeg ran this long = the backoff starts over
SWEEP_EVERY = 300          # seconds between retention sweeps


class Command(BaseCommand):
    help = "Continuously record a camera to dated MP4 segments (the CCTV tape)."

    def add_arguments(self, parser):
        parser.add_argument("--camera", default=None,
                            help="Camera code (default: the system's one camera).")
        parser.add_argument("--source", default=None,
                            help="Stream URL or file (default: the camera's saved stream).")
        parser.add_argument("--mode", choices=[recording.MODE_CLIP, recording.MODE_SOURCE],
                            default=None,
                            help="clip = re-encode to evidence-clip quality; "
                                 "source = copy the camera's own video untouched.")
        parser.add_argument("--segment-minutes", type=int, default=None)
        parser.add_argument("--no-stamp", action="store_true",
                            help="Do not burn the date and time into the picture.")
        parser.add_argument("--duration", type=int, default=0,
                            help="Stop after this many seconds (0 = run until stopped).")
        parser.add_argument("--force", action="store_true",
                            help="Record even when recording is switched off in Settings.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print the ffmpeg command that would run, and exit.")

    # ---- setup -------------------------------------------------------------

    def handle(self, *args, **opts):
        from core.models import Camera, SystemSettings

        cfg = SystemSettings.load()
        code = opts["camera"] or _default_camera_code()
        camera = Camera.objects.filter(code=code).first()
        if camera is None and not opts["source"]:
            raise SystemExit(f"Camera {code} was not found, and no --source was given.")

        source = opts["source"] or (camera.resolved_stream_url if camera else "")
        if not source:
            raise SystemExit("No stream address. Save an RTSP URL on Live Feeds, "
                             "set STREAM_URL in this machine's .env, or pass --source.")
        if not recording.have_ffmpeg():
            raise SystemExit("ffmpeg was not found on PATH; continuous recording needs it.")
        if not cfg.recording_enabled and not opts["force"] and not opts["dry_run"]:
            self.stdout.write("Continuous recording is switched off in Settings; nothing to do.")
            return

        mode = opts["mode"] or cfg.recording_mode
        minutes = opts["segment_minutes"] or cfg.recording_segment_minutes
        self.segment_seconds = max(60, int(minutes) * 60)
        self.code = code
        self.source = source
        self.mode = mode
        self.cfg = cfg
        self.label = code
        # No stamp in "source" mode: drawing on the frames means decoding and
        # re-encoding them, which is the entire cost that mode exists to avoid.
        self.stamp = (not opts["no_stamp"] and cfg.recording_timestamp
                      and mode == recording.MODE_CLIP)
        # A file source is read as fast as the disk allows unless throttled,
        # which would name a whole evening of segments within one second.
        self.rate_limit = not _is_live(source)
        self._font = False          # False = not probed yet; None = no stamp

        recording.camera_dir(code).mkdir(parents=True, exist_ok=True)

        if opts["dry_run"]:
            self.stdout.write(" ".join(self._command()))
            return

        # Anything left unfinalised belongs to a run that was killed, and
        # cannot be played by anything. Done before the pid file is claimed, so
        # it can never touch a segment a live recorder has open.
        for name in recording.discard_unfinished(code):
            self.stdout.write(f"discarded an unplayable leftover segment: {name}")

        self._stop = False
        self._proc = None
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass        # not the main thread, or a platform without it

        # A flag left by the stop that ended the PREVIOUS recorder would stop
        # this one within five seconds of starting. Cleared before the pid file
        # is claimed, so there is no window where we are visible but doomed.
        recording.clear_stop(code)
        recording.write_pids(code, os.getpid())
        try:
            self._run(deadline=time.time() + opts["duration"] if opts["duration"] else None)
        finally:
            recording.clear_pids(code)
            recording.clear_stop(code)

    def _on_signal(self, *_):
        self._stop = True
        self._ask_ffmpeg_to_finish()

    # ---- the supervisor ----------------------------------------------------

    def _run(self, deadline=None):
        backoff = BACKOFF_START
        self.stdout.write(f"Recording {self.code} from {_redact(self.source)} "
                          f"({self.mode}, {self.segment_seconds // 60} min segments) "
                          f"into {recording.camera_dir(self.code)}")
        while not self._stop:
            started = time.time()
            code = self._record_until_it_stops(deadline)
            if self._stop or (deadline and time.time() >= deadline):
                break
            if time.time() - started >= HEALTHY_AFTER:
                backoff = BACKOFF_START
            self.stderr.write(f"ffmpeg stopped (exit {code}). Reconnecting in {backoff}s.")
            if not self._sleep(backoff):
                break
            backoff = min(backoff * 2, BACKOFF_MAX)
        self.stdout.write("Recording stopped.")

    def _record_until_it_stops(self, deadline):
        """Run one ffmpeg, watching it until it exits, stalls, or we are
        stopped. Returns its exit code (or None when we ended it)."""
        cmd = self._command()
        # stdin is the control channel ('q' = finish the open segment and
        # exit), so it is a pipe and -nostdin is dropped. stderr joins our own
        # log, which core/monitor.py points at media/uploads/live_recording.log.
        self._proc = subprocess.Popen([a for a in cmd if a != "-nostdin"],
                                      stdin=subprocess.PIPE)
        recording.write_pids(self.code, os.getpid(), self._proc.pid)
        last_size, last_change = self._tape_size(), time.time()
        last_sweep = time.time()
        while True:
            try:
                return self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            if recording.stop_requested(self.code):
                self._stop = True
            if self._stop or (deadline and time.time() >= deadline):
                self._ask_ffmpeg_to_finish()
                return None
            now = time.time()
            size = self._tape_size()
            if size != last_size:
                last_size, last_change = size, now
            elif now - last_change > STALL_SECONDS:
                self.stderr.write(f"No video written for {STALL_SECONDS}s — "
                                  "restarting the recorder.")
                self._ask_ffmpeg_to_finish()
                return None
            if now - last_sweep > SWEEP_EVERY:
                last_sweep = now
                self._sweep()

    def _command(self):
        """The argv for one run.

        `basetime` is read as late as possible — the burnt-in clock counts
        forward from it using the stream's own timestamps, so every moment
        spent between reading it and ffmpeg receiving its first frame is a
        moment the stamp runs behind. What remains is the connect time
        (fractions of a second on the LAN, a second or two through a tunnel),
        which is why the authoritative time for a segment is the archive's own
        listing and not the number painted on the picture.
        """
        return recording.ffmpeg_command(
            self.source, recording.output_pattern(self.code),
            mode=self.mode, segment_seconds=self.segment_seconds,
            label=self.label, font=self._stamp_font(),
            base_time_us=int(time.time() * 1_000_000),
            rate_limit=self.rate_limit)

    def _stamp_font(self):
        """The font to burn the clock with, or None to go without.

        Probed ONCE and remembered, including the answer "no": the check costs
        an ffmpeg launch, and a camera that reconnects every few minutes would
        otherwise pay for it all day and log the same complaint each time."""
        if not self.stamp:
            return None
        if self._font is not False:
            return self._font
        font = recording.find_font()
        if font and not recording.probe_stamp(font, int(time.time() * 1_000_000), self.label):
            self.stderr.write("This ffmpeg cannot draw the timestamp (no font, or a "
                              "build without libfreetype); recording without it.")
            font = None
        elif not font:
            self.stderr.write("No usable font was found; recording without a timestamp.")
        self._font = font
        return font

    def _ask_ffmpeg_to_finish(self):
        """'q' on stdin, then wait: ffmpeg writes the open segment's moov atom
        on a clean exit and nothing at all on a kill, so the difference between
        the two is whether the last ten minutes of footage exist."""
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write(b"q")
            proc.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _tape_size(self):
        """Total bytes in the camera's directory. The stall watchdog's only
        signal: it grows while frames are arriving and stops when they are
        not, whatever ffmpeg's own process state says."""
        try:
            return sum(p.stat().st_size for p in recording.camera_dir(self.code).iterdir()
                       if p.is_file())
        except OSError:
            return 0

    def _sweep(self):
        """Re-read the settings first: retention is edited in the dashboard,
        and a recorder that ran for a week on the values it started with would
        ignore an operator who had just made room on the disk."""
        try:
            self.cfg.refresh_from_db()
        except Exception:                                        # noqa: BLE001
            pass
        deleted, freed, reason = recording.sweep(
            self.code, self.cfg.recording_retention_days, self.cfg.recording_max_gb)
        if deleted:
            self.stdout.write(f"Deleted {deleted} old segment(s), {freed / (1 << 20):.0f} MB, "
                              f"to stay inside {reason}.")

    def _sleep(self, seconds):
        """Sleep in short steps so a stop is noticed now, not in two minutes.
        Returns False if we were asked to stop while waiting."""
        for _ in range(int(seconds)):
            if self._stop or recording.stop_requested(self.code):
                return False
            time.sleep(1)
        return not self._stop


def _default_camera_code():
    from core.monitor import CAMERA_CODE
    return CAMERA_CODE


def _is_live(source: str) -> bool:
    return str(source).lower().startswith(("rtsp", "rtmp", "http", "udp", "tcp")) \
        or not os.path.exists(str(source))


def _redact(url: str) -> str:
    """The stream URL carries the camera's password. It is printed into a log
    file the dashboard can show, so the credentials come out first."""
    if "@" not in str(url):
        return str(url)
    head, _, tail = str(url).partition("://")
    return f"{head}://***@{tail.split('@', 1)[1]}" if "@" in tail else str(url)
