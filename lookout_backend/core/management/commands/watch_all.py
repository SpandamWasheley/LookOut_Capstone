"""Run every violation detector on ONE camera feed, frame by frame.

Instead of four separate processes each opening the camera and each running its
own YOLO pass, this opens the stream once and, per frame:

  * detects people ONCE and shares those boxes with smoking, thief and drinking
    (their most expensive pass, so sharing it is the main saving);
  * runs each detector's own model, tracker, voting, dwell and cooldown by
    reusing the existing watch_<x> Command classes unchanged — so behaviour is
    identical to the standalone watchers, just driven from one loop;
  * detects vehicles for parking with its own movement/dwell logic.

Alerts are attributed to one shared Camera (the real CCTV), each carrying its own
ViolationType, so the dashboard shows "Public Smoking", "Theft / Robbery" etc.
from the single feed.

This is CPU-heavy: four models per frame. On CPU expect ~1-3 FPS, less with
--far. That is the cost of one-feed-all-violations; the per-detector temporal
rules are time-based, so they stay correct at low frame rates (see tracking.py).
"""
import os
import time
from collections import Counter

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import recognition, tracking

from .watch_smoking import Command as SmokingCommand
from .watch_thief import Command as ThiefCommand
from .watch_drinking import Command as DrinkingCommand

SETTINGS_REFRESH_SECONDS = 5
PARKING_TRACK_GRACE = 2.0
PARKING_MATCH_IOU = 0.3


class Command(BaseCommand):
    help = (
        "Runs smoking, theft, drinking and illegal-parking detection together on "
        "a single camera feed. Use --source for an RTSP/CCTV URL. Person "
        "detection is shared across the person-based detectors, so this is much "
        "cheaper than four separate processes."
    )

    # Person-based detectors that share the person-detection pass and the
    # PersonTracker/_process_track machinery.
    PERSON_DETECTORS = ("smoking", "thief", "drinking")

    def add_arguments(self, parser):
        parser.add_argument("--source", default="0",
                            help="Webcam index or RTSP/stream URL / video file.")
        parser.add_argument("--camera", default="CAM-ALL",
                            help="Camera code all alerts attach to (default CAM-ALL).")
        parser.add_argument("--far", action="store_true",
                            help="Deprecated / no-op: tiling is now ON by default for "
                                 "every detector (near+far combined). Kept so existing "
                                 "commands don't break.")
        parser.add_argument("--fast", action="store_true",
                            help="Near mode ONLY for every detector — single whole-frame "
                                 "pass, no tiling. Faster but misses small objects.")
        parser.add_argument("--tiles", default="2x2",
                            help="Far-mode tiling grid ROWSxCOLS (default 2x2).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Detect and save evidence but write no Alert rows.")
        parser.add_argument("--debug", action="store_true",
                            help="Show a preview window with every detector's boxes.")
        parser.add_argument("--only", default="",
                            help="Comma-separated subset to run from "
                                 "smoking/thief/drinking/parking. Default: all enabled "
                                 "in Settings.")
        parser.add_argument("--schedule", default="rotate", choices=["rotate", "all"],
                            help="'rotate' (default) runs ONE violation model per frame, "
                                 "cycling through them, so the feed stays responsive on "
                                 "CPU — safe because the temporal rules are time-based, "
                                 "not frame-based, so each detector still confirms within "
                                 "its window. 'all' runs every model every frame "
                                 "(accurate but ~4x slower; use only on a GPU).")

    def handle(self, *args, **options):
        self.camera, _ = Camera.objects.get_or_create(
            code=options["camera"],
            defaults={"name": "All-Violation Monitor", "status": Camera.Status.ONLINE},
        )
        self.violations_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(self.violations_dir, exist_ok=True)

        # Both modes by default (far already includes the near whole-frame pass);
        # --fast opts out to the single near pass.
        self.far = not options["fast"]
        self.dry_run = options["dry_run"]
        self.schedule = options["schedule"]
        try:
            rows, cols = (int(v) for v in options["tiles"].lower().split("x"))
            self.tiles = (rows, cols)
        except (ValueError, AttributeError):
            self.stdout.write(self.style.ERROR(
                f"Invalid --tiles {options['tiles']!r}; expected ROWSxCOLS like 2x2."))
            return

        only = {s.strip() for s in options["only"].split(",") if s.strip()}
        valid = set(self.PERSON_DETECTORS) | {"parking"}
        if only - valid:
            self.stdout.write(self.style.ERROR(
                f"--only: unknown {', '.join(only - valid)}. Valid: {', '.join(valid)}."))
            return
        self.enabled = only or None  # None => decide per-frame from Settings

        # Build and configure a sub-command per person-based detector, reusing
        # its exact detection + confirmation logic. Each gets its own tracker.
        self.engines = {}
        for name, cls in (("smoking", SmokingCommand),
                          ("thief", ThiefCommand),
                          ("drinking", DrinkingCommand)):
            cmd = cls()
            self._share_setup(cmd)
            if not self._model_ok(name, cmd):
                self.stdout.write(self.style.WARNING(
                    f"{name}: model not available, skipping this detector."))
                continue
            self.engines[name] = {"cmd": cmd, "tracker": tracking.PersonTracker()}

        # Parking needs its own violation type; it tracks vehicles, not people.
        self.parking_type, _ = ViolationType.objects.get_or_create(
            code="parking",
            defaults={"label": "Illegal Parking", "color": "#ef4444", "icon": "car"},
        )
        self.parking_tracks = {}
        self.parking_next_id = 0
        self.parking_alert_log = []

        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                "DRY RUN: evidence images will be saved but no alerts created."))

        self._run(options["source"], options["debug"])

    # ---- sub-command wiring ----------------------------------------------

    def _share_setup(self, cmd):
        """Gives a reused watch_<x> Command the state its methods expect, but
        pointed at OUR shared camera and evidence dir, and configured from our
        flags. Mirrors what each command's handle() would set."""
        cmd.camera = self.camera
        cmd.violations_dir = self.violations_dir
        cmd.dry_run = self.dry_run
        cmd.far = self.far
        cmd.tiles = self.tiles
        cmd.conf_override = None
        cmd.dwell_override = None
        cmd.tracker_name = "greedy"
        cmd.show_stats = False
        cmd.ablate = set()
        cmd.stats = Counter()
        cmd._alert_log = []
        cmd.stdout = self.stdout
        cmd.style = self.style
        # detector-specific extras
        if hasattr(cmd, "face_check"):
            cmd.face_check = True
        if hasattr(cmd, "include_generic"):
            cmd.include_generic = False
            cmd.zones = []
        # violation types each command's _create_alert references
        cmd.smoking_type = self._vtype("smoking", "Public Smoking", "#f59e0b", "cigarette")
        cmd.thief_type = self._vtype("thief", "Theft / Robbery", "#ef4444", "siren")
        cmd.drinking_type = self._vtype("drinking", "Public Drinking", "#8b5cf6", "beer")

    def _vtype(self, code, label, color, icon):
        vt, _ = ViolationType.objects.get_or_create(
            code=code, defaults={"label": label, "color": color, "icon": icon})
        return vt

    def _model_ok(self, name, cmd):
        checks = {
            "smoking": recognition.smoking_model_available,
            "thief": recognition.thief_model_available,
            "drinking": recognition.drinking_model_available,
        }
        return checks[name]()

    def _active(self, name, cfg):
        if self.enabled is not None:
            return name in self.enabled
        return getattr(cfg, f"{name}_enabled", True)

    # ---- capture ----------------------------------------------------------

    def _open(self, source):
        if source.isdigit():
            return cv2.VideoCapture(int(source))
        cap = cv2.VideoCapture(source)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self, source, debug):
        cap = self._open(source)
        if not cap.isOpened():
            self.stdout.write(self.style.ERROR(f"Could not open video source: {source}"))
            return

        # Live sources (RTSP / webcam) get the always-latest reader so processing
        # never falls behind the stream; a video file is read directly so every
        # frame is seen.
        is_live = source.isdigit() or "://" in source
        reader = recognition.LatestFrameReader(cap) if is_live else cap

        cfg = SystemSettings.load()
        cfg_at = time.time()
        mode = f"FAR {self.tiles[0]}x{self.tiles[1]}" if self.far else "near"
        names = ", ".join(list(self.engines) + ["parking"])
        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} [{mode}] for: {names}. Ctrl+C to stop."))

        started = time.time()
        frames = 0
        fps_warned = False
        rot = 0
        try:
            while True:
                ok, frame = reader.read()
                if not ok:
                    # Live reader may not have its first frame yet; wait briefly.
                    time.sleep(0.02)
                    continue

                now = time.time()
                if now - cfg_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_at = now
                frames += 1

                # Which detectors run THIS frame. In 'rotate' mode only one heavy
                # model runs per frame (cycled), so the loop stays fast; in 'all'
                # mode they all run. The person pass is shared either way.
                active = [n for n in list(self.engines) + ["parking"] if self._active(n, cfg)]
                if not active:
                    continue
                if self.schedule == "rotate":
                    due = {active[rot % len(active)]}
                    rot += 1
                else:
                    due = set(active)

                # Shared person pass — computed once, fed to whichever person-based
                # detectors are due this frame.
                need_persons = any(n in due for n in self.engines)
                persons = recognition.detect_persons(frame) if need_persons else []

                for name, eng in self.engines.items():
                    if name in due:
                        self._run_person_detector(name, eng, frame, persons, now, cfg, debug)

                if "parking" in due:
                    self._run_parking(frame, now, cfg, debug)

                if not fps_warned and frames >= 20:
                    fps = frames / max(now - started, 1e-6)
                    if fps < 1.5:
                        fps_warned = True
                        self.stdout.write(self.style.WARNING(
                            f"Running at {fps:.1f} FPS — four models per frame on CPU is "
                            "heavy. Use --only to run fewer, drop --far, or use a GPU."))

                if debug:
                    cv2.imshow("LookOut - watch_all (debug)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            reader.stop() if is_live else cap.release()
            if debug:
                cv2.destroyAllWindows()
            self.stdout.write(self.style.SUCCESS("Stopped."))

    # ---- per-detector drivers (reuse each command's own methods) ---------

    def _run_person_detector(self, name, eng, frame, persons, now, cfg, debug):
        cmd, tracker = eng["cmd"], eng["tracker"]
        conf = getattr(cfg, f"{name}_confidence") / 100
        dwell = getattr(cfg, f"{name}_dwell")

        if name == "drinking":
            dets = cmd._detect(frame, conf, persons=persons, vessels=[])
        else:
            dets = cmd._detect(frame, conf, persons=persons)

        tracker.update(persons, now)
        per_track = tracker.assign(dets, now)
        if name == "smoking":
            per_track = cmd._apply_face_rule(frame, per_track, now)

        for track, td in per_track.items():
            cmd._process_track(track, td, now, dwell, cfg.alert_cooldown, frame, debug)

    def _run_parking(self, frame, now, cfg, debug):
        """Vehicle movement/dwell logic, mirroring watch_parking, inline."""
        conf = cfg.parking_confidence / 100
        dwell = cfg.parking_dwell
        tol = cfg.parking_move_tolerance
        if self.far:
            vehicles = recognition.detect_vehicles_far(frame, conf=conf, tiles=self.tiles)
        else:
            vehicles = recognition.detect_vehicles(frame, conf=conf)

        matched = set()
        for (x1, y1, x2, y2, score, label) in vehicles:
            box = (x1, y1, x2, y2)
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            best_id, best_iou = None, PARKING_MATCH_IOU
            for tid, tr in self.parking_tracks.items():
                if tid in matched:
                    continue
                o = recognition._iou(box, tr["box"])
                if o > best_iou:
                    best_id, best_iou = tid, o
            if best_id is None:
                best_id = self.parking_next_id
                self.parking_next_id += 1
                self.parking_tracks[best_id] = {"anchor": (cx, cy), "still_since": now,
                                                "alerted_at": 0}
            matched.add(best_id)
            tr = self.parking_tracks[best_id]
            tr.update({"box": box, "last_seen": now, "label": label, "score": score})

            ax, ay = tr["anchor"]
            if ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5 > tol:
                tr["anchor"] = (cx, cy)
                tr["still_since"] = now
            parked_for = now - tr["still_since"]

            if debug:
                col = (0, 0, 220) if parked_for >= dwell else (0, 200, 0)
                cv2.rectangle(frame, (x1, y1), (x2, y2), col, 2)
                cv2.putText(frame, f"{label} {parked_for:.0f}s", (x1, max(y1 - 8, 0)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)

            if parked_for >= dwell and now - tr["alerted_at"] >= cfg.alert_cooldown:
                self._parking_alert(score, label, frame, parked_for)
                tr["alerted_at"] = now

        for tid in list(self.parking_tracks):
            if tid not in matched and now - self.parking_tracks[tid]["last_seen"] > PARKING_TRACK_GRACE:
                del self.parking_tracks[tid]

    def _parking_alert(self, score, label, frame, parked_for):
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = f"{ts}_parking_{label}.jpg"
        cv2.imwrite(str(self.violations_dir / fn), frame)
        url = f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{fn}"
        if self.dry_run:
            self.stdout.write(self.style.SUCCESS(f"parking: {label} {parked_for:.0f}s (dry run)"))
            return
        a = Alert.objects.create(
            type=self.parking_type, status=Alert.Status.ACTIVE, camera=self.camera,
            timestamp=timezone.now(), confidence=score,
            description=(f"Illegal parking detected: {label} stationary for "
                         f"{parked_for:.0f}s on {self.camera.code} feed."),
            image_url=url, suspect=label)
        self.stdout.write(self.style.SUCCESS(f"ALERT {a.code} (parking, {label})"))
