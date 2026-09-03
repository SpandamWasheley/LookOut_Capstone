"""Long-range public-smoking detection by the HAND-TO-MOUTH GESTURE (pose), for
distances where the cigarette itself is too small to detect.

The object detector in `watch_smoking` needs to see the cigarette — a few pixels
at 5-10m, so it fails there. This command instead reads body pose (YOLOv8-pose)
and watches for the repeated hand-to-mouth rhythm: a wrist rising to the face and
dropping, over and over. Wrist and nose keypoints stay resolvable at range long
after an 85mm cigarette becomes 2 pixels, so this reaches much further.

HONEST TRADE-OFF: a hand going to the face is also eating, drinking, phoning,
scratching. So this is a PRESENCE INDICATOR, not proof — alerts are flagged
"needs review", carry a modest confidence, and require several cycles. Pair it
with the object-based watch_smoking up close (high confidence) and use this only
for the far range where nothing else works.

Reuses the shared PersonTracker (distance-invariant, occlusion recovery) and the
ClipRecorder evidence clip. No cigarette model here — pose only.
"""
import datetime
import time

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import preprocess as preproc
from core.vision import recognition, tracking

SETTINGS_REFRESH_SECONDS = 5
COOLDOWN_IOU = 0.3
# How many complete hand-to-mouth cycles (within the gesture window) before we
# raise a "possible smoking" alert. 2 is a deliberate floor: one hand-to-face is
# almost anything; a repeated rhythm is more smoking-like.
GESTURE_MIN_CYCLES = 2


class Command(BaseCommand):
    help = (
        "Long-range smoking detection via the hand-to-mouth gesture (pose), for "
        "when the cigarette is too small to detect. Flags POSSIBLE smoking for "
        "human review — less specific than the object-based watch_smoking."
    )

    def add_arguments(self, parser):
        parser.add_argument("--source", default="0",
                            help="Webcam index or RTSP/stream URL / video file.")
        parser.add_argument("--camera", default="CAM-SMOKE-01",
                            help="Camera code alerts attach to.")
        parser.add_argument("--dwell", type=int, default=None,
                            help="Seconds the gesture rhythm must persist before "
                                 "alerting. Omit to use the smoking dwell setting.")
        parser.add_argument("--cycles", type=int, default=GESTURE_MIN_CYCLES,
                            help=f"Hand-to-mouth cycles required (default {GESTURE_MIN_CYCLES}). "
                                 "Raise to cut false positives, lower to catch more.")
        parser.add_argument("--debug", action="store_true",
                            help="Preview window with pose skeleton points and gesture count.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Detect but write no Alert rows.")
        preproc.add_cli_flags(parser, ablatable=False)

    def handle(self, *args, **options):
        self.smoking_type, _ = ViolationType.objects.get_or_create(
            code="smoking",
            defaults={"label": "Public Smoking", "color": "#f59e0b", "icon": "cigarette"},
        )
        self.camera, _ = Camera.objects.get_or_create(
            code=options["camera"],
            defaults={"name": "Smoking Monitor", "status": Camera.Status.ONLINE},
        )
        self.violations_dir = settings.MEDIA_ROOT / "violations"
        import os
        os.makedirs(self.violations_dir, exist_ok=True)

        self.dwell_override = options["dwell"]
        self.min_cycles = max(1, options["cycles"])
        self.dry_run = options["dry_run"]
        self.preprocess = options["preprocess"]
        self.sharpen = options["sharpen"]
        self._alert_log = []

        self._run(options["source"], options["debug"])

    def _open(self, source):
        if source.isdigit():
            return cv2.VideoCapture(int(source))
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self, source, debug):
        cap = self._open(source)
        if not cap.isOpened():
            self.stdout.write(self.style.ERROR(f"Could not open video source: {source}"))
            return
        is_live = source.isdigit() or "://" in source
        reader = recognition.LatestFrameReader(cap) if is_live else cap

        cfg = SystemSettings.load()
        cfg_at = time.time()
        tracker = tracking.PersonTracker()
        self.clip = recognition.ClipRecorder(seconds=30, label=self.camera.code)

        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} for the hand-to-mouth SMOKING GESTURE "
            f"(pose, needs {self.min_cycles} cycles; flags possible smoking for "
            f"review). Ctrl+C to stop."
        ))

        try:
            while True:
                ok, frame = reader.read()
                if not ok:
                    time.sleep(0.02 if is_live else 0.5)
                    continue

                # Enhance dim/noisy frames before pose estimation — keypoints at
                # range are the first thing lost in low light (daytime bypasses).
                if self.preprocess:
                    frame = preproc.preprocess(frame, mode="near", sharpen=self.sharpen)

                now = time.time()
                if now - cfg_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_at = now
                if not cfg.smoking_enabled:
                    time.sleep(0.5)
                    continue
                dwell_seconds = self.dwell_override or cfg.smoking_dwell

                poses = recognition.detect_pose(frame)
                person_boxes = [(b[0], b[1], b[2], b[3], 0.9) for b, _ in poses]
                tracks = tracker.update(person_boxes, now)

                for track in tracks:
                    kpts = self._kpts_for(track, poses)
                    if kpts is None:
                        continue
                    ratio = recognition.hand_to_mouth_ratio(kpts)
                    if ratio is None:
                        continue
                    cycles = track.update_gesture(ratio, now)
                    self._maybe_alert(track, cycles, now, dwell_seconds, cfg, frame, debug)

                # Always-drawn boxes so the evidence clip shows the person + count.
                for track in tracks:
                    x1, y1, x2, y2 = track.box
                    g = track.gesture_count(now)
                    col = (0, 165, 245) if g >= self.min_cycles else (180, 180, 180)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), col, 2)
                    cv2.putText(frame, f"person #{track.id}  hand-to-mouth x{g}",
                                (x1, max(y1 - 6, 0)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
                self.clip.add(frame, now)

                if debug:
                    cv2.imshow("LookOut - watch_smoking_pose (debug)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            reader.stop() if is_live else cap.release()
            if debug:
                cv2.destroyAllWindows()
            self.stdout.write(self.style.SUCCESS("Stopped."))

    def _kpts_for(self, track, poses):
        """Keypoints of the pose whose box best overlaps this track."""
        best, best_iou = None, 0.2
        for box, kpts in poses:
            iou = recognition._iou(track.box, box)
            if iou > best_iou:
                best, best_iou = kpts, iou
        return best

    def _maybe_alert(self, track, cycles, now, dwell_seconds, cfg, frame, debug):
        if cycles < self.min_cycles:
            return
        # First frame the threshold is met starts the dwell; require the rhythm to
        # hold for the dwell so a brief flurry of hand movement isn't enough.
        if track.threat_since is None:
            track.threat_since = now
        if now - track.threat_since < dwell_seconds:
            return
        if track.in_cooldown(now, cfg.alert_cooldown):
            return
        box = track.box
        if self._cooldown_blocks(box, now, cfg.alert_cooldown):
            return

        # Confidence scales gently with how many cycles were seen (more rhythm =
        # more smoking-like), capped modest because this is a review-grade cue.
        confidence = min(0.4 + 0.1 * cycles, 0.75)
        alert = self._create_alert(
            confidence, "hand-to-mouth gesture", frame,
            description=(
                f"POSSIBLE public smoking (pose gesture) — person #{track.id} made "
                f"{cycles} hand-to-mouth motions over ~{dwell_seconds}s on "
                f"{self.camera.code}. Long-range cue, NEEDS REVIEW: could be eating/"
                f"drinking/phone."
            ),
        )
        track.last_alerted_at = now
        self._alert_log.append((tuple(box), now))
        self.stdout.write(self.style.SUCCESS(
            (f"ALERT created: {alert.code}" if alert else "ALERT (dry run)")
            + f" (hand-to-mouth x{cycles}, person #{track.id}, NEEDS REVIEW)"
        ))

    def _cooldown_blocks(self, box, now, cooldown):
        self._alert_log = [(b, ts) for b, ts in self._alert_log if now - ts < cooldown]
        return any(recognition._iou(box, b) >= COOLDOWN_IOU for b, _ in self._alert_log)

    def _create_alert(self, score, label, frame, description):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts_label}_smoking_pose.jpg"
        cv2.imwrite(str(self.violations_dir / filename), frame)
        image_url = f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{filename}"

        video_url = ""
        clip = getattr(self, "clip", None)
        if clip is not None:
            vname = f"{ts_label}_smoking_pose.mp4"
            if clip.save(self.violations_dir / vname):
                video_url = f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{vname}"

        if self.dry_run:
            return None
        return Alert.objects.create(
            type=self.smoking_type, status=Alert.Status.ACTIVE, camera=self.camera,
            timestamp=timezone.now(), confidence=score, description=description,
            image_url=image_url, video_url=video_url, suspect=label,
        )
