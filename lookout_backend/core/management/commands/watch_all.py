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

from core.media import violation_media_path
from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import preprocess as preproc
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
        preproc.add_cli_flags(parser, ablatable=False)

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
        self.preprocess = options["preprocess"]
        self.sharpen = options["sharpen"]
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
            if name == "drinking":
                # Path B (gathering) needs its own group-level tracker,
                # driven alongside Path A in _run_person_detector.
                self.engines[name]["group_tracker"] = tracking.GroupTracker()

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
        # OFF on the sub-commands on purpose: we enhance the shared frame ONCE
        # per frame in _run() and hand the same enhanced frame to every detector,
        # so letting each one preprocess again would re-denoise/re-CLAHE an
        # already-enhanced image three times over.
        cmd.preprocess = False
        cmd.sharpen = False
        # Layer E (theft patterns, E1-E29) drives itself from watch_thief's own
        # frame loop, which this combined runner bypasses — it calls each
        # detector's _detect/_process_track directly. Turned off explicitly so
        # the sub-command isn't left half-initialised; run `watch_thief` for the
        # pattern rules.
        if hasattr(cmd, "layer_e"):
            cmd.layer_e = False
            cmd.layer_e_only = False
        cmd.ablate = set()
        cmd.stats = Counter()
        cmd._alert_log = []
        cmd.stdout = self.stdout
        cmd.style = self.style
        # Every engine gets its own evidence clip buffer (smoking, drinking
        # AND thief) — without this, _create_alert's getattr(self, "clip",
        # None) silently no-ops and every alert through this command loses
        # its evidence video.
        cmd.clip = recognition.ClipRecorder(seconds=30, label=self.camera.code)
        # detector-specific extras
        if hasattr(cmd, "face_check"):
            cmd.face_check = True
        if hasattr(cmd, "include_generic"):
            cmd.include_generic = False
            cmd.zones = []
            cmd.min_group_override = None
            cmd.group_duration_override = None
        # violation types each command's _create_alert references
        cmd.smoking_type = self._vtype("smoking", "Public Smoking", "#f59e0b", "cigarette")
        # code="theft" (not "thief") — matches watch_thief.py's own fix; see
        # migration 0026 for why the two codes must never diverge again.
        cmd.thief_type = self._vtype("theft", "Holdup in Public Area", "#ef4444", "siren")
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

        if debug:
            cv2.namedWindow("LookOut - watch_all (debug)", cv2.WINDOW_NORMAL)

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

                # One enhancement pass for the whole frame, before the shared
                # person detection — every detector this frame sees the same
                # enhanced pixels (daytime frames bypass untouched).
                if self.preprocess:
                    frame = preproc.preprocess(frame, mode="near", sharpen=self.sharpen)

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
                # Snapshot before any engine draws a violation box on `frame` —
                # face recognition (smoking/drinking's citation-prefill match)
                # must run against a clean copy, same reasoning as each
                # standalone command's own loop.
                clean_frame = frame.copy() if need_persons else None

                for name, eng in self.engines.items():
                    if name in due:
                        self._run_person_detector(name, eng, frame, persons, now, cfg, debug, clean_frame)

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

    def _run_person_detector(self, name, eng, frame, persons, now, cfg, debug, clean_frame):
        cmd, tracker = eng["cmd"], eng["tracker"]
        conf = getattr(cfg, f"{name}_confidence") / 100
        dwell = getattr(cfg, f"{name}_dwell")

        if name == "drinking":
            dets = cmd._detect(frame, conf, persons=persons, vessels=[])
        else:
            dets = cmd._detect(frame, conf, persons=persons)

        tracks = tracker.update(persons, now)
        per_track = tracker.assign(dets, now)
        if name == "smoking":
            per_track = cmd._apply_face_rule(frame, per_track, now)

        if name == "drinking":
            self._run_gathering(eng, tracks, per_track, now, cfg, frame, debug, clean_frame)
            for track, td in per_track.items():
                cmd._process_track(track, td, now, dwell, cfg.alert_cooldown,
                                   frame, debug, cfg.curfew_confidence, clean_frame)
        elif name == "smoking":
            for track, td in per_track.items():
                cmd._process_track(track, td, now, dwell, cfg.alert_cooldown,
                                   frame, debug, cfg.curfew_confidence, clean_frame)
        else:  # thief — no face_threshold/clean_frame param on this one
            for track, td in per_track.items():
                cmd._process_track(track, td, now, dwell, cfg.alert_cooldown, frame, debug)

        # Buffer this annotated frame into the engine's own evidence clip —
        # cmd.clip is created in _share_setup but nothing else fills it;
        # without this call it stays effectively empty and every alert's
        # clip is a near-blank few-hundred-ms stub, not the ~30s of context
        # the standalone commands' own _run_stream loops buffer every frame.
        cmd.clip.add(frame, now)

    def _run_gathering(self, eng, tracks, per_track, now, cfg, frame, debug, clean_frame):
        """Drinking's Path B (gathering) — previously never invoked here, so a
        sustained group with no single confirmed solo drinker never alerted
        when run through this command. Evaluated BEFORE Path A's per-track
        loop, so a cluster alert that fires this frame lands in
        cmd._alert_log in time to suppress its members' solo alerts later in
        the same frame — mirrors watch_drinking.py's own _run_stream."""
        cmd, group_tracker = eng["cmd"], eng["group_tracker"]
        min_group = cfg.drinking_min_group
        group_duration = cfg.drinking_group_duration
        clusters = group_tracker.update(tracks, now, min_group)
        detected_ids = {t.id for t, dets in per_track.items() if dets and not t.is_scene}
        for cluster in clusters:
            if cluster.member_ids & detected_ids:
                member_dets = [d for t, dets in per_track.items()
                              for d in dets
                              if t.id in cluster.member_ids and not t.is_scene]
                if member_dets:
                    best = max(member_dets, key=lambda d: d[4])
                    if cluster.evidence is None or best[4] > cluster.evidence[4]:
                        cluster.evidence = best
            cmd._process_cluster(
                cluster, now, min_group, group_duration, cfg.alert_cooldown,
                frame, debug, cfg.curfew_confidence, clean_frame,
            )

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
                recognition.draw_label(frame, f"{label} {score * 100:.0f}% {parked_for:.0f}s",
                                       x1, max(y1 - 8, 0), col)

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
        url = violation_media_path(fn)
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
