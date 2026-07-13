import datetime
import os
import time

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import recognition

PARKING_CAMERA_CODE = "CAM-PARKING"
TRACK_GRACE_SECONDS = 2  # tolerate a couple missed frames before dropping a track
SETTINGS_REFRESH_SECONDS = 5  # re-poll SystemSettings this often, not every frame


def _center(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _iou(a, b):
    """Intersection-over-union of two (x1, y1, x2, y2) boxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


class Command(BaseCommand):
    help = (
        "Detects vehicles (car/motorcycle/bus/truck) for illegal-parking / "
        "obstruction monitoring. Use --image PATH to test on a single still "
        "picture, or run with no --image to watch the webcam with a dwell timer."
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
            help="Override the SystemSettings parking confidence (as 0-1). "
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

    def handle(self, *args, **options):
        # ViolationType/Camera aren't created by any migration, so get_or_create
        # here self-heals a fresh DB the same way watch_curfew does.
        self.parking_type, _ = ViolationType.objects.get_or_create(
            code="parking",
            defaults={"label": "Illegal Parking", "color": "#ef4444", "icon": "car"},
        )
        self.camera, _ = Camera.objects.get_or_create(
            code=PARKING_CAMERA_CODE,
            defaults={"name": "Parking Monitor", "status": Camera.Status.ONLINE},
        )
        self.violations_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(self.violations_dir, exist_ok=True)

        self.conf_override = options["confidence"]
        self.dwell_override = options["dwell"]

        cfg = SystemSettings.load()
        if not cfg.parking_enabled:
            self.stdout.write(self.style.WARNING(
                "Parking detection is disabled in Settings (parking_enabled=False). "
                "Enable it in the dashboard, or it won't create alerts."
            ))

        if options["image"]:
            conf = self.conf_override or (cfg.parking_confidence / 100)
            self._run_image(options["image"], conf)
        else:
            self._run_webcam(options["debug"])

    # ---- single-image test mode -------------------------------------------

    def _run_image(self, path, conf):
        frame = recognition.load_image(path)
        if frame is None:
            self.stdout.write(self.style.ERROR(f"Could not read image: {path}"))
            return

        vehicles = recognition.detect_vehicles(frame, conf=conf)
        if not vehicles:
            self.stdout.write(self.style.WARNING(
                f"No vehicles detected above confidence {conf}. "
                "Try lowering --confidence."
            ))
            return

        for (x1, y1, x2, y2, score, label) in vehicles:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 220), 2)
            cv2.putText(frame, f"{label} {score * 100:.0f}%", (x1, max(y1 - 8, 0)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 220), 1)

        # Alert on the highest-confidence vehicle; the annotated frame (all
        # boxes) is saved as evidence.
        best = max(vehicles, key=lambda v: v[4])
        _, _, _, _, best_score, best_label = best
        summary = ", ".join(sorted({v[5] for v in vehicles}))
        alert = self._create_alert(
            best_score, best_label, frame,
            description=(
                f"Illegal parking / obstruction detected on still image: "
                f"{len(vehicles)} vehicle(s) [{summary}]."
            ),
        )
        self.stdout.write(self.style.SUCCESS(
            f"Detected {len(vehicles)} vehicle(s): {summary}. "
            f"ALERT created: {alert.code}"
        ))

    # ---- webcam dwell mode ------------------------------------------------

    def _run_webcam(self, debug):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self.stdout.write(self.style.ERROR("Could not open webcam (index 0)."))
            return

        # Settings are re-polled every few seconds (like watch_curfew) so edits
        # made in the dashboard's Parking config take effect live, without a
        # restart. CLI flags, if given, still win over the stored values.
        cfg = SystemSettings.load()
        cfg_loaded_at = time.time()
        # tracks: id -> {box, anchor, still_since, last_seen, label, score, alerted_at}
        tracks = {}
        next_id = 0

        self.stdout.write(self.style.SUCCESS(
            f"Watching webcam for parked vehicles (dwell {self.dwell_override or cfg.parking_dwell}s, "
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

                if not cfg.parking_enabled:
                    time.sleep(0.5)
                    continue

                conf = self.conf_override or (cfg.parking_confidence / 100)
                dwell_seconds = self.dwell_override or cfg.parking_dwell
                move_tolerance = cfg.parking_move_tolerance
                vehicles = recognition.detect_vehicles(frame, conf=conf)

                # Greedy IoU association of detections to existing tracks — good
                # enough for a stationary parking camera (no ByteTrack needed).
                matched_ids = set()
                for (x1, y1, x2, y2, score, label) in vehicles:
                    box = (x1, y1, x2, y2)
                    best_id, best_iou = None, 0.3
                    for tid, tr in tracks.items():
                        if tid in matched_ids:
                            continue
                        overlap = _iou(box, tr["box"])
                        if overlap > best_iou:
                            best_id, best_iou = tid, overlap

                    if best_id is None:
                        best_id = next_id
                        next_id += 1
                        tracks[best_id] = {"anchor": _center(box), "still_since": now_ts,
                                           "alerted_at": 0}
                    matched_ids.add(best_id)
                    tr = tracks[best_id]
                    tr.update({"box": box, "last_seen": now_ts, "label": label, "score": score})

                    # Movement reset: if the vehicle drifted past the tolerance,
                    # it's moving (not parked) — re-anchor and restart its timer.
                    cx, cy = _center(box)
                    ax, ay = tr["anchor"]
                    if ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5 > move_tolerance:
                        tr["anchor"] = (cx, cy)
                        tr["still_since"] = now_ts

                    parked_for = now_ts - tr["still_since"]
                    if debug:
                        color = (0, 0, 220) if parked_for >= dwell_seconds else (0, 200, 0)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                        cv2.putText(frame, f"{label} {parked_for:.0f}s", (x1, max(y1 - 8, 0)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

                    if parked_for < dwell_seconds:
                        continue
                    if now_ts - tr["alerted_at"] < cfg.alert_cooldown:
                        continue
                    alert = self._create_alert(
                        score, label, frame,
                        description=(
                            f"Illegal parking detected: {label} stationary for "
                            f"{parked_for:.0f}s on parking-monitor feed."
                        ),
                    )
                    tr["alerted_at"] = now_ts
                    self.stdout.write(self.style.SUCCESS(f"ALERT created: {alert.code} ({label})"))

                # drop tracks not seen recently (grace for detector flicker)
                for tid in list(tracks.keys()):
                    if tid in matched_ids:
                        continue
                    if now_ts - tracks[tid]["last_seen"] > TRACK_GRACE_SECONDS:
                        del tracks[tid]

                if debug:
                    cv2.imshow("LookOut - watch_parking (debug)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            cap.release()
            if debug:
                cv2.destroyAllWindows()
            self.stdout.write(self.style.SUCCESS("Stopped."))

    # ---- shared alert creation --------------------------------------------

    def _create_alert(self, score, label, frame, description):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts_label}_parking_{label}.jpg"
        cv2.imwrite(str(self.violations_dir / filename), frame)
        image_url = f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{filename}"

        return Alert.objects.create(
            type=self.parking_type,
            status=Alert.Status.ACTIVE,
            camera=self.camera,
            timestamp=timezone.now(),
            confidence=score,
            description=description,
            image_url=image_url,
            suspect=label,
        )
