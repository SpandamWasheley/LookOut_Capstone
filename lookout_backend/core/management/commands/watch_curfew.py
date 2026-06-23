import datetime
import os
import time

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import recognition

WEBCAM_CAMERA_CODE = "CAM-WEBCAM"
DWELL_GRACE_SECONDS = 2  # tolerate a couple missed frames before resetting dwell
SETTINGS_REFRESH_SECONDS = 5  # how often to re-poll SystemSettings, not every frame


def _within_curfew(now_time, start, end):
    if start <= end:
        return start <= now_time < end
    return now_time >= start or now_time < end


class Command(BaseCommand):
    help = "Watches the webcam for registered minors detected during curfew hours."

    def add_arguments(self, parser):
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Show a live preview window with detection boxes drawn on it.",
        )

    def handle(self, *args, **options):
        debug = options["debug"]

        face_db = recognition.precompute_face_db(recognition.load_face_db())
        if not face_db:
            self.stdout.write(self.style.WARNING(
                "face_db.json is empty - run `manage.py enroll_faces` first."
            ))

        self.stdout.write(
            "Note: insightface (ArcFace) cosine match scores for a genuine "
            "same-person match typically fall around 35-70 (not 90+). If "
            "alerts never fire, try "
            "lowering SystemSettings.curfew_confidence (e.g. to ~40) instead of "
            "assuming nothing matched."
        )

        camera, _ = Camera.objects.get_or_create(
            code=WEBCAM_CAMERA_CODE,
            defaults={"name": "Demo Webcam", "status": Camera.Status.ONLINE},
        )
        # get_or_create (not .get()) so this self-heals if `seed_demo` was
        # never run — the curfew ViolationType isn't created by any
        # migration, only by seed_demo's fixture data.
        curfew_type, _ = ViolationType.objects.get_or_create(
            code="curfew",
            defaults={"label": "Curfew Violation", "color": "#f59e0b", "icon": "moon"},
        )

        violations_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(violations_dir, exist_ok=True)

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self.stdout.write(self.style.ERROR("Could not open webcam (index 0)."))
            return

        dwell = {}  # code -> {"first_seen": ts, "last_seen": ts}
        last_alert = {}  # code -> ts

        self.stdout.write(self.style.SUCCESS("Watching webcam. Press Ctrl+C to stop."))

        cfg = SystemSettings.load()
        cfg_loaded_at = time.time()

        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    self.stdout.write(self.style.WARNING("Failed to read frame from webcam."))
                    time.sleep(0.5)
                    continue

                now_ts = time.time()
                now_dt = datetime.datetime.now()

                # Re-poll settings every few seconds, not every frame — admins
                # rarely change curfew config mid-run, and SystemSettings.load()
                # is a DB round-trip that doesn't belong in a per-frame hot loop.
                if now_ts - cfg_loaded_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_loaded_at = now_ts

                persons = recognition.detect_persons(frame)
                seen_this_frame = set()

                for (x1, y1, x2, y2, _conf) in persons:
                    crop = frame[max(y1, 0):y2, max(x1, 0):x2]
                    embedding = recognition.compute_face_embedding(crop)
                    match, score_pct = recognition.match_embedding(
                        embedding, face_db, cfg.curfew_confidence
                    )

                    if debug:
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 0), 2)
                        label = f"{match['name']} {score_pct:.0f}%" if match else "person"
                        cv2.putText(frame, label, (x1, max(y1 - 8, 0)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1)

                    if match is None:
                        continue
                    if match["age"] is None or match["age"] >= cfg.curfew_age:
                        continue
                    if not _within_curfew(now_dt.time(), cfg.curfew_start, cfg.curfew_end):
                        continue

                    code = match["code"]
                    seen_this_frame.add(code)
                    entry = dwell.setdefault(code, {"first_seen": now_ts, "last_seen": now_ts})
                    entry["last_seen"] = now_ts

                    if now_ts - entry["first_seen"] < cfg.curfew_dwell:
                        continue

                    if now_ts - last_alert.get(code, 0) < cfg.alert_cooldown:
                        continue

                    self._create_alert(camera, curfew_type, match, score_pct, frame, violations_dir)
                    last_alert[code] = now_ts

                # prune dwell entries not seen recently (grace period for flicker)
                for code in list(dwell.keys()):
                    if code in seen_this_frame:
                        continue
                    if now_ts - dwell[code]["last_seen"] > DWELL_GRACE_SECONDS:
                        del dwell[code]

                if debug:
                    cv2.imshow("LookOut - watch_curfew (debug)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            cap.release()
            if debug:
                cv2.destroyAllWindows()
            self.stdout.write(self.style.SUCCESS("Stopped."))

    def _create_alert(self, camera, curfew_type, match, score_pct, frame, violations_dir):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts_label}_{match['code']}.jpg"
        cv2.imwrite(str(violations_dir / filename), frame)
        image_url = f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{filename}"

        alert = Alert.objects.create(
            type=curfew_type,
            status=Alert.Status.ACTIVE,
            camera=camera,
            timestamp=timezone.now(),
            confidence=score_pct / 100,
            description=(
                f"Curfew violation detected: {match['name']} "
                f"(age {match['age']}) on webcam feed."
            ),
            image_url=image_url,
            suspect=match["name"],
        )
        self.stdout.write(self.style.SUCCESS(f"ALERT created: {alert.code} ({match['name']})"))
