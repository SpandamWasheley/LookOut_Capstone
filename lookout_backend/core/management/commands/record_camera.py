"""Continuously record a camera's feed to disk while it is connected.

DVR-style: as long as the camera is reachable, this writes the raw feed (what
you see on the live wall) to timestamped segment files. If the stream drops it
retries until the camera comes back, then resumes — so recording lasts exactly
as long as the CCTV is connected.

Segments are short files (default 10 min) rather than one giant file, so:
  * a crash or power loss only loses the current segment, not everything;
  * old footage can be pruned a segment at a time.

Files land in <project>/cctv records/<camera_code>/<YYYY-MM-DD_HH-MM-SS>.mp4
(override the folder with --out-dir).

Storage: the 640x360 sub-stream is ~0.5-1 GB/hour. Continuous 24/7 recording
fills a disk in days, so segments older than --retention-days are deleted
automatically (default 7). Raise it if you have the disk; set 0 to keep forever
(you must then prune manually).
"""
import datetime
import os
import time
from pathlib import Path

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import Camera

SEGMENT_SECONDS_DEFAULT = 600   # 10-minute files
RECONNECT_WAIT = 3              # seconds between reconnect attempts


class Command(BaseCommand):
    help = (
        "Continuously records a camera's raw feed to disk for as long as it is "
        "connected, in timestamped segment files, pruning footage older than the "
        "retention window. Use --source for the RTSP URL."
    )

    def add_arguments(self, parser):
        parser.add_argument("--source", default="0",
                            help="Webcam index or RTSP/stream URL to record.")
        parser.add_argument("--camera", default="CAM-REC",
                            help="Camera code — footage goes to recordings/<code>/.")
        parser.add_argument("--segment", type=int, default=SEGMENT_SECONDS_DEFAULT,
                            help=f"Seconds per segment file (default {SEGMENT_SECONDS_DEFAULT}).")
        parser.add_argument("--retention-days", type=int, default=7,
                            help="Delete segments older than this many days (default 7). "
                                 "0 = keep forever (prune manually).")
        parser.add_argument("--out-dir", default="",
                            help="Folder to record into. Default: the project's "
                                 "'cctv records' folder.")
        parser.add_argument("--fps", type=float, default=0,
                            help="Force a recording frame rate. Default 0 = auto-detect "
                                 "from the camera, falling back to 15.")
        parser.add_argument("--stop-file", default="",
                            help="Path to a sentinel file: when it appears, the recorder "
                                 "finishes the current segment cleanly and exits. Lets the "
                                 "dashboard stop recording gracefully (finalising the MP4) "
                                 "instead of hard-killing it, which leaves an unplayable file.")

    def handle(self, *args, **options):
        source = options["source"]
        self.segment = max(30, options["segment"])
        self.retention_days = options["retention_days"]
        self.force_fps = options["fps"]
        self.stop_file = Path(options["stop_file"]) if options["stop_file"] else None

        camera, _ = Camera.objects.get_or_create(
            code=options["camera"],
            defaults={"name": "Recorder", "status": Camera.Status.ONLINE},
        )
        # Default to the project's "cctv records" folder (sibling of the backend),
        # a per-camera subfolder inside it. --out-dir overrides.
        base = Path(options["out_dir"]) if options["out_dir"] else settings.BASE_DIR.parent / "cctv records"
        self.out_dir = base / camera.code
        os.makedirs(self.out_dir, exist_ok=True)

        self.stdout.write(self.style.SUCCESS(
            f"Recording {source} -> {self.out_dir} "
            f"({self.segment}s segments, "
            + (f"keep {self.retention_days}d" if self.retention_days else "keep forever")
            + "). Records while the camera is connected. Ctrl+C to stop."
        ))

        try:
            while not self._should_stop():
                cap = self._open(source)
                if not cap.isOpened():
                    self.stdout.write(self.style.WARNING(
                        f"Camera not reachable — retrying in {RECONNECT_WAIT}s."))
                    cap.release()
                    time.sleep(RECONNECT_WAIT)
                    continue
                self._record_until_drop(cap, source)
                cap.release()
                if self._should_stop():
                    break
                # Dropped out of the record loop => stream ended; loop reconnects.
                self.stdout.write(self.style.WARNING(
                    f"Stream dropped — reconnecting in {RECONNECT_WAIT}s."))
                time.sleep(RECONNECT_WAIT)
        except KeyboardInterrupt:
            pass
        finally:
            # Clear the stop sentinel so the next run starts clean.
            if self.stop_file and self.stop_file.exists():
                try:
                    self.stop_file.unlink()
                except OSError:
                    pass
            self.stdout.write(self.style.SUCCESS("Recording stopped."))

    def _should_stop(self):
        """True once the dashboard has asked us to stop (sentinel file present)."""
        return self.stop_file is not None and self.stop_file.exists()

    def _open(self, source):
        if source.isdigit():
            return cv2.VideoCapture(int(source))
        # No 1-frame buffer here: recording wants EVERY frame in order, not just
        # the latest, so the footage is smooth and continuous.
        return cv2.VideoCapture(source, cv2.CAP_FFMPEG)

    def _fps_for(self, cap):
        if self.force_fps > 0:
            return self.force_fps
        fps = cap.get(cv2.CAP_PROP_FPS)
        # Cameras sometimes report 0 or nonsense; fall back to a sane default.
        return fps if 1 <= fps <= 60 else 15.0

    def _record_until_drop(self, cap, source):
        """Reads and writes segments until the stream returns no frames."""
        fps = self._fps_for(cap)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 360
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")

        writer = None
        seg_started = 0.0
        seg_path = None
        fails = 0
        try:
            while True:
                if self._should_stop():
                    break   # finally below releases the writer -> finalises the MP4
                ok, frame = cap.read()
                if not ok:
                    fails += 1
                    if fails > 30:      # ~sustained failure => stream really gone
                        break
                    time.sleep(0.05)
                    continue
                fails = 0
                now = time.time()

                # Roll over to a new segment file on interval (or first frame).
                if writer is None or now - seg_started >= self.segment:
                    if writer is not None:
                        writer.release()
                        self.stdout.write(f"  saved {seg_path.name}")
                    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    seg_path = self.out_dir / f"{ts}.mp4"
                    writer = cv2.VideoWriter(str(seg_path), fourcc, fps, (w, h))
                    seg_started = now
                    self._prune()

                writer.write(frame)
        finally:
            if writer is not None:
                writer.release()
                if seg_path:
                    self.stdout.write(f"  saved {seg_path.name}")

    def _prune(self):
        """Deletes segment files older than the retention window."""
        if self.retention_days <= 0:
            return
        cutoff = time.time() - self.retention_days * 86400
        removed = 0
        for f in self.out_dir.glob("*.mp4"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        if removed:
            self.stdout.write(f"  pruned {removed} segment(s) older than "
                              f"{self.retention_days}d")
