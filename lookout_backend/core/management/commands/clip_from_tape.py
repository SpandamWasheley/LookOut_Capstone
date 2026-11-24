"""Cut a raw clip from the continuous CCTV tape for an alert that has only a
still image — i.e. the detector's rolling buffer was not warm when the alert
fired (the process had just started, or the source was live but no RawFrameRecorder
was attached).

    python manage.py clip_from_tape ALT-0869
    python manage.py clip_from_tape ALT-0869 ALT-0870  # several at once
    python manage.py clip_from_tape ALT-0869 --dry-run  # print what it would do

The command finds the tape segment whose start time is <= the alert's timestamp,
seeks to (alert_offset - PRE_SECONDS) inside it, cuts a clip of
(PRE + POST) seconds with stream-copy (no re-encode), saves it under
MEDIA_ROOT/violations/, and writes raw_video_url + clip_offset on the alert.

Nothing is written when:
  - the alert already has a raw (or annotated) clip
  - no tape segment covers the alert's timestamp
  - the offset inside the segment is shorter than PRE_SECONDS (the alert fired
    less than PRE_SECONDS after a recording gap)
  - ffmpeg returns a non-zero exit code
"""
import datetime
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core import recording
from core.media import violation_media_path
from core.models import Alert
from core.vision import recognition


class Command(BaseCommand):
    help = "Cut a raw evidence clip from the CCTV tape for screenshot-only alerts."

    def add_arguments(self, parser):
        parser.add_argument("codes", nargs="+", help="Alert codes, e.g. ALT-0869")
        parser.add_argument("--pre", type=float, default=float(recognition.RAW_CLIP_PRE_SECONDS),
                            help="Seconds of tape before the alert moment (default %(default)s)")
        parser.add_argument("--post", type=float, default=float(recognition.RAW_CLIP_POST_SECONDS),
                            help="Seconds of tape after the alert moment (default %(default)s)")
        parser.add_argument("--overwrite", action="store_true",
                            help="Replace an existing raw clip (default: skip if one exists)")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print what would be done without cutting or saving anything")

    def handle(self, *args, **opts):
        pre = opts["pre"]
        post = opts["post"]
        dry_run = opts["dry_run"]
        overwrite = opts["overwrite"]

        for code in opts["codes"]:
            try:
                alert = Alert.objects.select_related("camera").get(code=code.upper())
            except Alert.DoesNotExist:
                raise CommandError(f"Alert {code!r} not found.")

            self.stdout.write(f"\n{alert.code}  ({alert.type}, {alert.timestamp:%Y-%m-%d %H:%M:%S %Z})")

            if alert.raw_video_url and not overwrite:
                self.stdout.write(self.style.WARNING("  already has a raw clip — skipping (--overwrite to replace)"))
                continue
            if alert.video_url and not overwrite:
                self.stdout.write(self.style.WARNING("  already has an annotated clip — skipping (--overwrite to replace)"))
                continue

            cam_code = alert.camera.code
            ts = alert.timestamp  # tz-aware UTC

            # Find the tape segment whose start is <= ts and end is > ts.
            segments = recording.list_segments(cam_code, probe=False)
            if not segments:
                self.stdout.write(self.style.ERROR(f"  no tape found for camera {cam_code}"))
                continue

            covering = None
            for seg in segments:
                seg_start = seg["started_at"]
                # astimezone() gives us an aware local datetime; compare as UTC
                seg_start_utc = seg_start.astimezone(datetime.timezone.utc)
                seg_end_utc = seg_start_utc + datetime.timedelta(seconds=seg["seconds"])
                if seg_start_utc <= ts < seg_end_utc:
                    covering = seg
                    break

            if covering is None:
                self.stdout.write(self.style.ERROR("  no tape segment covers this timestamp"))
                continue

            seg_start_utc = covering["started_at"].astimezone(datetime.timezone.utc)
            alert_offset = (ts - seg_start_utc).total_seconds()
            clip_start = max(0.0, alert_offset - pre)
            if clip_start < 0.001:
                self.stdout.write(self.style.WARNING(
                    f"  alert is only {alert_offset:.1f}s into segment — pre-roll trimmed to {alert_offset:.1f}s"
                ))

            clip_duration = (alert_offset - clip_start) + post
            clip_offset = alert_offset - clip_start  # where in the output clip the alert moment lands

            seg_path = recording.camera_dir(cam_code) / covering["name"]
            ts_str = ts.astimezone().strftime("%Y%m%d_%H%M%S")
            out_name = f"{ts_str}_{alert.code.replace('-', '').lower()}_tape_raw.mp4"
            out_path = settings.MEDIA_ROOT / "violations" / out_name
            media_url = violation_media_path(out_name)

            self.stdout.write(
                f"  segment : {covering['name']}\n"
                f"  offset  : {alert_offset:.1f}s into segment\n"
                f"  clip    : {clip_start:.1f}s → {clip_start + clip_duration:.1f}s  "
                f"({clip_duration:.0f}s total, marker at {clip_offset:.1f}s)\n"
                f"  output  : {out_path}"
            )

            if dry_run:
                self.stdout.write(self.style.WARNING("  [dry-run] nothing written"))
                continue

            os.makedirs(out_path.parent, exist_ok=True)
            ok = recognition.cut_raw_clip(str(seg_path), clip_start, clip_duration, str(out_path))
            if not ok:
                self.stdout.write(self.style.ERROR("  ffmpeg failed — see output above"))
                continue

            alert.raw_video_url = media_url
            alert.clip_offset = clip_offset
            alert.save(update_fields=["raw_video_url", "clip_offset"])
            self.stdout.write(self.style.SUCCESS(f"  saved → {media_url}"))
