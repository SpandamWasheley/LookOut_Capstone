import datetime
import os
import time

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import recognition, tracking

SMOKING_CAMERA_CODE = "CAM-SMOKING"
SETTINGS_REFRESH_SECONDS = 5  # re-poll SystemSettings this often, not every frame
PRESENCE_GRACE_SECONDS = 2    # tolerate a couple smoke-free frames before resetting dwell

# N-of-M temporal voting (per tracked person): a person's frame counts as
# "smoking present" only if at least VOTE_MIN of their last VOTE_WINDOW frames
# were positive. This turns low, flickery per-frame confidence into a stable
# signal before the dwell timer starts counting.
VOTE_WINDOW = 15
VOTE_MIN = 6


class Command(BaseCommand):
    help = (
        "Detects public smoking (cigarette/smoke/vape) using the custom smoking "
        "model. Use --image PATH to test on a single still picture, or run with "
        "no --image to watch the webcam with a dwell timer."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--image",
            help="Path to a still image to run detection on once (test mode). "
                 "Without this, watches the webcam (index 0) continuously.",
        )
        parser.add_argument(
            "--confidence",
            type=float,
            default=None,
            help="Override the SystemSettings smoking confidence (as 0-1). "
                 "Omit to use the dashboard 'Detection confidence' value.",
        )
        parser.add_argument(
            "--dwell",
            type=int,
            default=None,
            help="Override the SystemSettings dwell seconds. Omit to use the "
                 "dashboard 'Dwell time before alert' value.",
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Webcam mode: show a live preview window with boxes drawn on it.",
        )
        parser.add_argument(
            "--far",
            action="store_true",
            help="Long-range CCTV mode: split the frame into tiles and run the "
                 "detector on upscaled person crops so a distant cigarette still "
                 "lands on enough pixels. Slower per frame; adds N-of-M temporal "
                 "voting to keep the extra tiling hits from firing false alerts.",
        )
        parser.add_argument(
            "--tiles",
            default="2x2",
            help="Far mode only: tiling grid as ROWSxCOLS (e.g. 2x2, 3x3). More "
                 "tiles reach further but cost more inference per frame.",
        )

    def handle(self, *args, **options):
        # ViolationType/Camera aren't created by any migration, so get_or_create
        # here self-heals a fresh DB the same way watch_curfew/watch_parking do.
        self.smoking_type, _ = ViolationType.objects.get_or_create(
            code="smoking",
            defaults={"label": "Public Smoking", "color": "#f59e0b", "icon": "cigarette"},
        )
        self.camera, _ = Camera.objects.get_or_create(
            code=SMOKING_CAMERA_CODE,
            defaults={"name": "Smoking Monitor", "status": Camera.Status.ONLINE},
        )
        self.violations_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(self.violations_dir, exist_ok=True)

        if not recognition.smoking_model_available():
            self.stdout.write(self.style.ERROR(
                f"Smoking model not found at {recognition.SMOKING_MODEL_PATH}. "
                "Train one with detection_sandbox/train_smoking.py and copy best.pt "
                "to core/vision/smoking.pt (or set the SMOKING_MODEL env var)."
            ))
            return

        self.conf_override = options["confidence"]
        self.dwell_override = options["dwell"]
        self.far = options["far"]
        try:
            rows, cols = (int(v) for v in options["tiles"].lower().split("x"))
            self.tiles = (rows, cols)
        except (ValueError, AttributeError):
            self.stdout.write(self.style.ERROR(
                f"Invalid --tiles {options['tiles']!r}; expected ROWSxCOLS like 2x2."
            ))
            return

        cfg = SystemSettings.load()
        if not cfg.smoking_enabled:
            self.stdout.write(self.style.WARNING(
                "Smoking detection is disabled in Settings (smoking_enabled=False). "
                "Enable it in the dashboard, or it won't create alerts."
            ))

        if options["image"]:
            conf = self.conf_override or (cfg.smoking_confidence / 100)
            self._run_image(options["image"], conf)
        else:
            self._run_webcam(options["debug"])

    # ---- detection dispatch -----------------------------------------------

    def _detect(self, frame, conf, persons=None):
        """Runs the smoking detector, using the long-range cascade when --far is
        set (tiling + upscaled person crops), else the fast single-pass detector."""
        if not self.far:
            return recognition.detect_smoking(frame, conf=conf)
        if persons is None:
            persons = recognition.detect_persons(frame)
        return recognition.detect_smoking_far(
            frame, conf=conf, tiles=self.tiles, person_boxes=persons,
        )

    # ---- single-image test mode -------------------------------------------

    def _run_image(self, path, conf):
        frame = recognition.load_image(path)
        if frame is None:
            self.stdout.write(self.style.ERROR(f"Could not read image: {path}"))
            return

        smokes = self._detect(frame, conf)
        if not smokes:
            self.stdout.write(self.style.WARNING(
                f"No smoking detected above confidence {conf}. "
                "Try lowering --confidence."
            ))
            return

        for (x1, y1, x2, y2, score, label) in smokes:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 165, 245), 2)
            cv2.putText(frame, f"{label} {score * 100:.0f}%", (x1, max(y1 - 8, 0)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 245), 1)

        # Alert on the highest-confidence detection; the annotated frame (all
        # boxes) is saved as evidence.
        best = max(smokes, key=lambda s: s[4])
        _, _, _, _, best_score, best_label = best
        summary = ", ".join(sorted({s[5] for s in smokes}))
        alert = self._create_alert(
            best_score, best_label, frame,
            description=(
                f"Public smoking detected on still image: "
                f"{len(smokes)} detection(s) [{summary}]."
            ),
        )
        self.stdout.write(self.style.SUCCESS(
            f"Detected {len(smokes)} smoking indicator(s): {summary}. "
            f"ALERT created: {alert.code}"
        ))

    # ---- webcam dwell mode ------------------------------------------------

    def _run_webcam(self, debug):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self.stdout.write(self.style.ERROR("Could not open webcam (index 0)."))
            return

        # Settings are re-polled every few seconds (like watch_curfew/watch_parking)
        # so edits made in the dashboard's Smoking config take effect live, without
        # a restart. CLI flags, if given, still win over the stored values.
        cfg = SystemSettings.load()
        cfg_loaded_at = time.time()

        # Per-person tracking: the cigarette itself is too small/transient to
        # track, but the PERSON holding it isn't — person boxes are matched
        # across frames (greedy IoU), and each track keeps its own vote window,
        # dwell timer and alert cooldown, so two smokers in frame are confirmed
        # and alerted independently. Detections no person box claims fall back
        # to the tracker's scene pseudo-track.
        tracker = tracking.PersonTracker(vote_window=VOTE_WINDOW)

        mode = f"FAR {self.tiles[0]}x{self.tiles[1]} tiling + person-crop" if self.far else "near"
        self.stdout.write(self.style.SUCCESS(
            f"Watching webcam for public smoking [{mode} mode] "
            f"(dwell {self.dwell_override or cfg.smoking_dwell}s, "
            f"reads live from Settings). Press Ctrl+C to stop."
        ))

        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    self.stdout.write(self.style.WARNING("Failed to read frame from webcam."))
                    time.sleep(0.5)
                    continue

                now_ts = time.time()
                if now_ts - cfg_loaded_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_loaded_at = now_ts

                if not cfg.smoking_enabled:
                    smoking_since = None
                    time.sleep(0.5)
                    continue

                conf = self.conf_override or (cfg.smoking_confidence / 100)
                dwell_seconds = self.dwell_override or cfg.smoking_dwell

                # Person detection runs every frame (it's the tracking anchor);
                # far mode reuses the same boxes for its person-crop pass.
                persons = recognition.detect_persons(frame)
                smokes = self._detect(frame, conf, persons=persons)

                tracks = tracker.update(persons, now_ts)
                per_track, leftovers = tracker.assign(smokes)

                if debug:
                    for t in tracks:
                        x1, y1, x2, y2 = t.box
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (180, 180, 180), 1)
                        cv2.putText(frame, f"person #{t.id}", (x1, max(y1 - 6, 0)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)

                for track, dets in list(per_track.items()) + [(tracker.scene, leftovers)]:
                    self._process_track(
                        track, dets, now_ts, dwell_seconds, cfg.alert_cooldown,
                        frame, debug,
                    )

                if debug:
                    cv2.imshow("LookOut - watch_smoking (debug)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            cap.release()
            if debug:
                cv2.destroyAllWindows()
            self.stdout.write(self.style.SUCCESS("Stopped."))

    # ---- per-track temporal confirmation ----------------------------------

    def _process_track(self, track, dets, now_ts, dwell_seconds, cooldown,
                       frame, debug):
        """Votes, dwell-times and (maybe) alerts ONE track for this frame.

        Same N-of-M + dwell + grace logic as before, but per person: each
        track's votes/timers/cooldown are its own, so one smoker's alert
        doesn't mask or reset another's.
        """
        track.votes.append(1 if dets else 0)
        if dets:
            track.dets = dets
            track.last_threat_seen = now_ts
        present = sum(track.votes) >= VOTE_MIN

        if present and track.dets:
            if track.threat_since is None:
                track.threat_since = now_ts
            present_for = now_ts - track.threat_since
            best = max(track.dets, key=lambda s: s[4])
            _, _, _, _, best_score, best_label = best

            if debug:
                for (x1, y1, x2, y2, score, label) in dets:
                    color = (0, 165, 245) if present_for >= dwell_seconds else (0, 200, 0)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(frame, f"{label} {present_for:.0f}s", (x1, max(y1 - 8, 0)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

            if present_for >= dwell_seconds and now_ts - track.last_alerted_at >= cooldown:
                summary = ", ".join(sorted({s[5] for s in track.dets}))
                who = f"person #{track.id}" if track.id else "unattributed detection"
                alert = self._create_alert(
                    best_score, best_label, frame,
                    description=(
                        f"Public smoking detected: {summary} on {who}, present "
                        f"for {present_for:.0f}s on smoking-monitor feed."
                    ),
                )
                track.last_alerted_at = now_ts
                self.stdout.write(self.style.SUCCESS(
                    f"ALERT created: {alert.code} ({best_label}, {who})"
                ))
        else:
            # not present: reset the dwell timer only after a short grace
            # period, so brief detector flicker doesn't restart it.
            if track.threat_since is not None and now_ts - track.last_threat_seen > PRESENCE_GRACE_SECONDS:
                track.threat_since = None

    # ---- shared alert creation --------------------------------------------

    def _create_alert(self, score, label, frame, description):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts_label}_smoking_{label}.jpg"
        cv2.imwrite(str(self.violations_dir / filename), frame)
        image_url = f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{filename}"

        return Alert.objects.create(
            type=self.smoking_type,
            status=Alert.Status.ACTIVE,
            camera=self.camera,
            timestamp=timezone.now(),
            confidence=score,
            description=description,
            image_url=image_url,
            suspect=label,
        )
