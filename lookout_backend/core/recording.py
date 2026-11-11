"""The ordinary CCTV tape: everything the camera sees, not only the violations.

LookOut's evidence clips are cut from a rolling in-memory buffer around a
detection, so the only footage that survives is footage a detector fired on.
That is the wrong archive for a barangay: the question asked a week later is
usually "what happened at about four o'clock on Tuesday", and nobody can answer
it from a folder of twenty-second violation clips. This module is the second,
dumber recorder that answers it -- a continuous tape, the way a traditional NVR
keeps one.

WHAT IT WRITES
--------------
One ffmpeg process per camera, writing fixed-length MP4 segments named for the
wall-clock moment each one starts:

    <RECORDINGS_ROOT>/CAM-SMOKE-01/2026-11-11_14-30-00.mp4

Flat, not a directory per day: this build of ffmpeg's segment muxer has no
`strftime_mkdir`, so a dated subdirectory would have to be created by someone
else at midnight -- a scheduled job that, when it failed, would stop the tape.
The date is in the name instead, so listing a day is a prefix match and the
recorder never needs a directory it did not already have.

Segments, not one long file, for the reason the RawFrameRecorder docstring in
core/vision/recognition.py gives for not reusing a continuous recorder as an
evidence source: an MP4 is not playable until its moov atom is written at
close. A 24-hour file would be unplayable for 24 hours. At ten minutes, all of
the tape but the last ten minutes is finished and seekable, and that property
is also how `is_finished` below decides -- `+faststart` puts moov at the FRONT
of a finished segment, so "has a moov atom near the start" is an exact test for
"ffmpeg closed this file", far more reliable than guessing from its mtime.

QUALITY
-------
Mode "clip" re-encodes to exactly the ladder every evidence clip is written
with (1280 wide, libx264 veryfast, CRF 28) so the tape and the clips look the
same -- which is the point: a reviewer moving between them should not be
judging two different pictures, and a segment pulled out as evidence should
stand beside a detector's clip without explanation.

Mode "source" copies the camera's own H.264 through untouched (`-c copy`).
Full sensor resolution, no CPU at all -- but roughly four times the disk for a
4 MP camera. It exists because the detection PC is already running three
detectors and a vision-language model, and on weak hardware the honest answer
to "can it also encode video all day" is sometimes no.

RETENTION IS NOT OPTIONAL
-------------------------
A continuous recorder fills any disk given time, so this one is a ring buffer
like every NVR: `sweep` deletes the OLDEST segments once the archive passes
either bound (days or gigabytes). That is a deliberate exception to the rule
that deleting evidence is opt-in (`evidence_auto_purge`) -- the alternative is
not "footage is kept", it is "the disk fills and recording stops", and a tape
that quietly stopped last month is worse than one that only reaches back a
week. It touches nothing but segment files inside this camera's own directory:
alert evidence under media/violations/ is a different retention rule, a
different root, and is never swept from here.

No Django models are imported here. This is paths, names, bytes and an argv
list, so it is unit-testable without a database and without ffmpeg, and the
settings that drive it are read by the caller.
"""
import datetime
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from django.conf import settings

# Local wall clock, because that is what ffmpeg's -strftime writes and what an
# operator means by "Tuesday afternoon". Parsed back with astimezone() rather
# than Django's timezone.make_aware: TIME_ZONE here is UTC while the recorder
# runs on a PC set to Asia/Manila, and labelling 14:30 local as 14:30 UTC would
# put every segment eight hours from where it belongs.
SEGMENT_NAME_FORMAT = "%Y-%m-%d_%H-%M-%S"
SEGMENT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})\.mp4$")

# How far into a file to look for the moov atom. With +faststart it is within
# the first few hundred bytes; 64 KB is slack enough for a large one and small
# enough to read for every segment in a listing without noticing.
MOOV_SCAN_BYTES = 1 << 16

# Treated as "this is the segment being written right now" when it is also
# unfinished. Only used for labelling -- completeness itself is the moov test,
# never this.
LIVE_MTIME_SECONDS = 30

# The encode must stay identical to the one every evidence clip is written with
# (core/vision/recognition.py's H264_ARGS / EVIDENCE_SCALE). Duplicated rather
# than imported because that module imports cv2 and numpy, which the hosted API
# deliberately does not install; core/tests_recording.py asserts the two agree
# wherever both can be imported.
CLIP_SCALE = "scale=1280:-2"
CLIP_H264_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                  "-pix_fmt", "yuv420p"]

MODE_CLIP = "clip"
MODE_SOURCE = "source"

# Fonts the burnt-in clock is drawn with, first one that exists. The stamp is
# the same green-on-black the evidence clips carry (ClipRecorder._stamp), for
# the same reason: a segment exported onto a USB stick has to carry its own
# time, since the filename does not travel with the picture.
FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
)


def root() -> Path:
    """Where the tape lives: settings.RECORDINGS_ROOT, NOT MEDIA_ROOT.

    Everything under MEDIA_ROOT is served as static files with no
    authentication, which is why the authenticated view in core/views.py would
    have been theatre if the same bytes were also sitting at
    /media/recordings/... Hours of continuous footage of a public street is a
    different privacy object from one clip attached to one alert; this root is
    reachable only through that view. See the setting for the full reasoning."""
    return Path(getattr(settings, "RECORDINGS_ROOT",
                        Path(settings.MEDIA_ROOT).parent / "recordings"))


def camera_dir(code: str) -> Path:
    """Where `code`'s tape lives.

    The code is a business code assigned by the model's save() ("CAM-SMOKE-01"),
    never operator free text, but it is still filtered to the characters those
    codes use -- this builds a filesystem path, and one unchecked "../" would
    point it anywhere."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "", code or "")
    if not safe:
        raise ValueError("a camera code is required")
    return root() / safe


def output_pattern(code: str) -> Path:
    """What ffmpeg is given as its output: a strftime template it expands per
    segment. The directory must already exist -- see the module docstring."""
    return camera_dir(code) / "%Y-%m-%d_%H-%M-%S.mp4"


def started_at(name: str):
    """The local datetime a segment file name encodes, or None if the name is
    not one of ours.

    Also the only membership test used when listing: anything else in the
    directory (the pid file, a half-copied file somebody dropped there) is
    ignored rather than reported as footage."""
    m = SEGMENT_RE.match(name)
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    try:
        return datetime.datetime(y, mo, d, h, mi, s).astimezone()
    except ValueError:                     # 2026-02-30_25-00-00.mp4
        return None


def is_finished(path) -> bool:
    """True when ffmpeg closed this segment, i.e. it is playable.

    `+faststart` relocates the moov atom to the front of a finished file, so
    its presence in the first MOOV_SCAN_BYTES means the file was finalised.
    While a segment is being written there is no moov anywhere in it, which is
    exactly the state a player cannot open."""
    try:
        with open(path, "rb") as fh:
            return b"moov" in fh.read(MOOV_SCAN_BYTES)
    except OSError:
        return False


def _entry(path: Path, now: float, probe=True):
    """One segment's facts. `probe` opens the file to see whether ffmpeg closed
    it; without it `finished` is None.

    Not probing matters: the dashboard asks for the archive's size every few
    seconds, and a week of ten-minute segments is a thousand files -- opening
    and reading 64 KB of each one to answer "how much disk is this using" would
    be 64 MB of reads per poll, for a question that never needed the answer."""
    at = started_at(path.name)
    if at is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    finished = is_finished(path) if probe else None
    return {
        "name": path.name,
        "started_at": at,
        "bytes": stat.st_size,
        "mtime": stat.st_mtime,
        "finished": finished,
        "recording": finished is False and (now - stat.st_mtime) < LIVE_MTIME_SECONDS,
    }


def list_segments(code: str, day=None, now=None, max_seconds=None, probe=True):
    """Every segment on the tape, oldest first; `day` (a date) narrows it.

    Each entry carries the seconds it covers, worked out from the NEXT
    segment's start rather than by probing the file: a day holds ~144 of them
    and running ffprobe over each one to draw a list would take longer than the
    page is willing to wait. The last segment is measured to its own mtime
    instead, which is where ffmpeg last wrote to it.

    A gap -- the camera was unreachable, the PC was off -- would otherwise show
    up as one segment claiming to cover the whole hole. `max_seconds` (the
    configured segment length) clamps it, so the list says "ten minutes from
    14:30" and the next entry's own start time shows the gap, instead of
    claiming four unrecorded hours were filmed.
    """
    now = now if now is not None else time.time()
    directory = camera_dir(code)
    if not directory.is_dir():
        return []
    entries = [e for e in (_entry(p, now, probe) for p in directory.iterdir() if p.is_file()) if e]
    entries.sort(key=lambda e: e["started_at"])
    for i, e in enumerate(entries):
        nxt = entries[i + 1]["started_at"] if i + 1 < len(entries) else None
        end = nxt or datetime.datetime.fromtimestamp(e["mtime"]).astimezone()
        seconds = max(0.0, (end - e["started_at"]).total_seconds())
        if max_seconds:
            seconds = min(seconds, float(max_seconds))
        e["seconds"] = seconds
    if day is not None:
        entries = [e for e in entries if e["started_at"].date() == day]
    return entries


def list_days(code: str, now=None, max_seconds=None):
    """One row per day with footage: how many segments, how much disk, and the
    span covered.

    What the archive page opens on, because a week of ten-minute segments is a
    thousand files and no useful first screen."""
    days = {}
    for e in list_segments(code, now=now, max_seconds=max_seconds, probe=False):
        key = e["started_at"].date()
        row = days.setdefault(key, {"date": key, "segments": 0, "bytes": 0,
                                    "first": e["started_at"], "last": e["started_at"]})
        row["segments"] += 1
        row["bytes"] += e["bytes"]
        row["first"] = min(row["first"], e["started_at"])
        row["last"] = max(row["last"], e["started_at"] +
                          datetime.timedelta(seconds=e.get("seconds") or 0))
    return sorted(days.values(), key=lambda r: r["date"], reverse=True)


def usage(code: str, now=None):
    """What the tape currently holds: bytes, segments, and how far back it
    reaches.

    `free_bytes` is the disk the recordings live on, which is the number that
    decides whether recording can continue at all."""
    segments = list_segments(code, now=now, probe=False)
    total = sum(e["bytes"] for e in segments)
    where = root() if root().exists() else root().parent
    try:
        free = shutil.disk_usage(str(where)).free
    except OSError:
        free = None
    return {
        "segments": len(segments),
        "bytes": total,
        "oldest": segments[0]["started_at"] if segments else None,
        "newest": segments[-1]["started_at"] if segments else None,
        "free_bytes": free,
    }


def segment_path(code: str, name: str):
    """The file `name` names inside `code`'s directory, or None.

    Both halves are validated and the result is re-checked against the
    directory after resolution, so a name that walks out ("..%2f..") cannot
    reach a file this is not meant to serve. Nothing outside this module should
    build a recordings path out of a request."""
    if started_at(name) is None:
        return None
    directory = camera_dir(code)
    try:
        path = (directory / name).resolve()
        path.relative_to(directory.resolve())
    except (ValueError, OSError):
        return None
    return path if path.is_file() else None


def sweep(code: str, retention_days: int, max_gb: float, now=None, keep_newest=True):
    """Delete the oldest segments until the tape is inside BOTH bounds.

    Returns (deleted, freed_bytes, reason), where reason names the bound that
    actually bit -- for the log line, because "the disk budget" and "the
    retention window" call for different answers from an operator and a count
    alone cannot tell them apart.

    The newest segment is never deleted: it is the one ffmpeg has open, and
    pulling that file out from under the muxer loses the last ten minutes AND
    the next ten. A tape so small that the newest file alone breaks the budget
    is a misconfiguration; the recorder logs it rather than deleting what it is
    in the middle of recording.
    """
    segments = list_segments(code, now=now, probe=False)
    total = sum(e["bytes"] for e in segments)
    candidates = segments[:-1] if (keep_newest and segments) else list(segments)
    cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(
        days=max(1, int(retention_days)))
    budget = int(float(max_gb) * (1 << 30))

    doomed, reasons = [], set()
    for e in candidates:
        if e["started_at"] < cutoff:
            doomed.append(e)
            total -= e["bytes"]
            reasons.add("the retention window")
    if budget > 0:
        for e in candidates:
            if total <= budget:
                break
            if e in doomed:
                continue
            doomed.append(e)
            total -= e["bytes"]
            reasons.add("the disk budget")

    freed, deleted = 0, 0
    directory = camera_dir(code)
    for e in doomed:
        try:
            (directory / e["name"]).unlink()
            freed += e["bytes"]
            deleted += 1
        except OSError:
            pass
    return deleted, freed, " and ".join(sorted(reasons))


def discard_unfinished(code: str):
    """Delete leftover segments with no moov atom. Returns their names.

    Only ever the file a previous run was writing when it was killed: every
    other segment was closed properly. Nothing can play it -- the index saying
    where its frames are was never written -- so leaving it would put a file in
    the archive that opens as an error and would be listed as footage the
    barangay does not actually have. Called once at startup, never while
    recording, since the file being written right now fails the same test for
    an entirely innocent reason."""
    gone = []
    directory = camera_dir(code)
    if not directory.is_dir():
        return gone
    for path in sorted(directory.iterdir()):
        if path.is_file() and started_at(path.name) and not is_finished(path):
            try:
                path.unlink()
                gone.append(path.name)
            except OSError:
                pass
    return gone


# ---- the ffmpeg command ---------------------------------------------------


def find_font():
    for candidate in FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


def _drawtext(label: str, font: str, base_time_us: int):
    """The burnt-in clock, in the filter syntax that actually parses.

    Two pieces of ffmpeg escaping are load-bearing, and both were arrived at by
    testing rather than from the documentation. The font path is QUOTED and its
    drive colon escaped ('C\\:/Windows/...'), because unquoted the filtergraph
    parser splits the filter's options at that colon and reports "no option
    name". And the clock uses `expansion=strftime` with `basetime` rather than
    %{localtime:%H:%M:%S}, because the colons inside a %{...} argument are
    eaten as argument separators however many backslashes precede them --
    localtime then refuses the extra arguments. strftime expansion takes the
    format as plain text, so only one level of escaping is needed.

    basetime is the moment this ffmpeg started, in microseconds; the clock
    advances from there with the stream's own timestamps.
    """
    path = font.replace(":", r"\:")
    clock = r"%Y-%m-%d %H\:%M\:%S"
    text = f"{label} {clock}" if label else clock
    return (f"drawtext=fontfile='{path}':expansion=strftime:basetime={int(base_time_us)}"
            f":text='{text}':x=10:y=10:fontsize=24:fontcolor=0x00ff00"
            ":box=1:boxcolor=0x000000@0.65:boxborderw=6")


def ffmpeg_command(source: str, out_pattern, *, mode=MODE_CLIP, segment_seconds=600,
                   label="", font=None, base_time_us=0, rate_limit=False):
    """argv for one continuous recording run.

    `rate_limit` adds -re, for a FILE source: without it ffmpeg reads a clip as
    fast as the disk allows and names a whole evening's segments within the
    same second, which makes the archive's times nonsense. Live sources arrive
    in real time already and must not be throttled.
    """
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-y"]
    if str(source).lower().startswith("rtsp"):
        # TCP, not the default UDP: a 4 MP stream over a barangay's Wi-Fi loses
        # UDP packets, and a torn frame in the archive is worse than the
        # latency TCP costs -- nothing here is live, so that latency buys
        # nothing back.
        cmd += ["-rtsp_transport", "tcp"]
    if rate_limit:
        cmd += ["-re"]
    # -an: no audio, ever. The evidence clips carry none, the detectors use
    # none, and recording conversation on a public street is a different legal
    # question entirely (RA 4200) from recording what a camera can see.
    cmd += ["-i", str(source), "-an"]

    if mode == MODE_SOURCE:
        cmd += ["-c", "copy"]
    else:
        filters = CLIP_SCALE
        if font:
            filters += "," + _drawtext(label, font, base_time_us)
        cmd += ["-vf", filters, *CLIP_H264_ARGS,
                # Pin the keyframes to the segment boundaries. The muxer can
                # only cut on a keyframe, so without this a 10-minute segment
                # overruns by up to the encoder's own 250-frame interval and
                # the name on the NEXT file stops meaning when it starts.
                "-force_key_frames", f"expr:gte(t,n_forced*{int(segment_seconds)})",
                "-g", "50"]
    cmd += [
        "-f", "segment",
        "-segment_time", str(int(segment_seconds)),
        "-reset_timestamps", "1",
        "-strftime", "1",
        "-segment_format", "mp4",
        # Each closed segment gets its moov atom moved to the front: that is
        # what lets a browser start playing (and seeking) without fetching the
        # whole file first, and what is_finished() reads to tell a closed
        # segment from the one being written.
        "-segment_format_options", "movflags=+faststart",
        str(out_pattern),
    ]
    return cmd


def stamp_probe_command(font: str, base_time_us: int, label=""):
    """A one-frame render of the burnt-in clock, to find out whether this
    machine's ffmpeg can draw it before a whole run depends on the answer.

    A missing font or a build without libfreetype must cost the timestamp,
    never the recording."""
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x180:r=1", "-frames:v", "1",
            "-vf", _drawtext(label, font, base_time_us), "-f", "null", "-"]


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def probe_stamp(font: str, base_time_us: int, label="") -> bool:
    try:
        r = subprocess.run(stamp_probe_command(font, base_time_us, label),
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---- who is recording, and how it is asked to stop --------------------------
#
# A pid file rather than a DetectionJob row, which is what the detectors use.
# This is not a detection job: it produces no alerts, fills no run history, and
# should not appear in a list of runs an operator can cancel. What it does need
# is to be findable after the web server restarts -- ffmpeg is a GRANDCHILD of
# that server, and on Windows terminating the manage.py process in between does
# not take ffmpeg with it, which would leave a second recorder holding the
# camera and writing into the same directory as the new one.
#
# Stopping is a FLAG FILE, not a signal, and that is the whole design here.
# Windows has no SIGTERM: os.kill(pid, sig) calls TerminateProcess for every
# signal value, so the recorder's handler never runs, it never gets to send
# ffmpeg its `q`, and ffmpeg dies holding an unwritten moov atom -- which costs
# the whole open segment, up to ten minutes of footage. (os.kill(pid, 0), the
# usual "is it alive" probe, is worse on Windows still: it terminates the
# process with exit code 0. `psutil` is used for both liveness and the
# last-resort kill for exactly that reason.) The recorder polls this flag in
# the same five-second loop it already uses to watch ffmpeg, so a stop is
# noticed promptly and is a clean one on every platform.


def pid_file(code: str) -> Path:
    return camera_dir(code) / ".recorder.pid"


def stop_file(code: str) -> Path:
    return camera_dir(code) / ".stop"


def request_stop(code: str):
    """Ask the recorder to finish the segment it has open and exit."""
    camera_dir(code).mkdir(parents=True, exist_ok=True)
    stop_file(code).write_text(str(int(time.time())), encoding="utf-8")


def stop_requested(code: str) -> bool:
    return stop_file(code).exists()


def clear_stop(code: str):
    try:
        stop_file(code).unlink()
    except OSError:
        pass


def write_pids(code: str, manager_pid: int, ffmpeg_pid=None):
    """Record who is recording: the manager, its ffmpeg, and WHEN the manager
    started.

    The start time is the third line, and it is what makes the file
    trustworthy. A pid alone is not an identity: after a power cut the file
    outlives the process, the operating system reuses the number, and "is that
    pid alive?" then answers yes about somebody else's process entirely --
    which reads as "already recording" and stops a real recorder from ever
    starting. Comparing the start time as well makes a stale file read as
    stale."""
    camera_dir(code).mkdir(parents=True, exist_ok=True)
    pid_file(code).write_text(
        f"{manager_pid}\n{ffmpeg_pid or ''}\n{_started_at(manager_pid) or ''}\n",
        encoding="utf-8")


def _started_at(pid):
    """When `pid` started, as a float, or None if it cannot be read."""
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except Exception:                                            # noqa: BLE001
        return None


def _recorded_start(code: str):
    """The start time the pid file claims, or None for a file written before
    this was recorded."""
    try:
        lines = pid_file(code).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if len(lines) < 3 or not lines[2].strip():
        return None
    try:
        return float(lines[2].strip())
    except ValueError:
        return None


def read_pids(code: str):
    """The pids in the file, manager first. The start-time line is not a pid."""
    try:
        lines = pid_file(code).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [int(p.strip()) for p in lines[:2] if p.strip().isdigit()]


def clear_pids(code: str):
    try:
        pid_file(code).unlink()
    except OSError:
        pass


def terminate_running(code: str, grace=20.0):
    """Stop whatever recorder holds this camera, cleanly if it will.

    Asks first (the flag file) and waits up to `grace` seconds, because a
    recorder that stops on its own finalises the segment it has open and one
    that is killed does not. Only then is each pid in the file killed outright
    -- the manager AND the ffmpeg under it, since ending the manager alone
    orphans an ffmpeg that goes on writing segments nobody is supervising.

    Returns True if it went quietly. Never raises: every pid here may
    legitimately be gone already, and this is called from atexit."""
    if not read_pids(code):
        clear_stop(code)
        return True
    request_stop(code)
    deadline = time.time() + max(0.0, float(grace))
    while time.time() < deadline:
        if not is_running(code):
            clear_pids(code)
            clear_stop(code)
            return True
        time.sleep(0.5)
    for pid in read_pids(code):
        _kill_pid(pid)
    clear_pids(code)
    clear_stop(code)
    return False


def _kill_pid(pid: int):
    try:
        import psutil
        psutil.Process(pid).kill()
    except Exception:                                            # noqa: BLE001
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def is_running(code: str) -> bool:
    """Whether the pid file names a process that still exists.

    Through psutil, never os.kill(pid, 0): on Windows that probe TERMINATES
    the process it is asking about. A pid file that survived a power cut would
    otherwise report a recorder which stopped days ago, and the status the
    dashboard shows has to be about the camera, not about a file left on
    disk."""
    pids = read_pids(code)
    if not pids:
        return False
    try:
        import psutil
        if not psutil.pid_exists(pids[0]):
            return False
        started = _recorded_start(code)
        if started is None:
            return True          # an older file with no start time; trust the pid
        # The same pid AND the same start time, or this is a different process
        # wearing a recycled number.
        actual = _started_at(pids[0])
        return actual is None or abs(actual - started) < 1.0
    except Exception:                                            # noqa: BLE001
        return True      # cannot tell; assume it is there rather than double-start
