import datetime
import os
import time
from collections import Counter

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import recognition, tracking

THIEF_CAMERA_CODE = "CAM-THIEF"
SETTINGS_REFRESH_SECONDS = 5  # re-poll SystemSettings this often, not every frame
PRESENCE_GRACE_SECONDS = 2    # tolerate a couple clean frames before resetting dwell

# Temporal voting is time-based (see tracking.VOTE_WINDOW_SECONDS): a frame
# counts as "threat present" only if enough of the last few SECONDS of frames
# were positive. Guns/knives are small hand-held objects and "robbery activity"
# /"stealing" are pose-like classes, so single-frame hits flicker; the vote
# turns them into a stable signal before the dwell timer even starts counting.

# Per-class policy. The four classes are not equally trustworthy: gun and knife
# are compact objects a frame-wise detector localises well, while "robbery
# activity" and "stealing" are *actions* being inferred from a single still
# frame — something an object detector is structurally weak at, and the usual
# source of false alerts (someone reaching into a bag, crouching, hugging).
# So the pose-like classes must clear a higher confidence bar and hold for
# longer before they can raise an alert, while a weapon alerts at the settings
# dwell. Scales multiply the dashboard values, so tuning Settings still works.
CLASS_POLICY = {
    "gun":              {"conf_scale": 1.0, "dwell_scale": 1.0},
    "knife":            {"conf_scale": 1.0, "dwell_scale": 1.0},
    "robbery activity": {"conf_scale": 1.6, "dwell_scale": 2.0},
    "stealing":         {"conf_scale": 1.6, "dwell_scale": 2.0},
}
DEFAULT_POLICY = {"conf_scale": 1.0, "dwell_scale": 1.0}

# The scene pseudo-track holds detections no person box claimed. Those are the
# least trustworthy of all — a weapon with nobody holding it is usually a
# poster, a TV, a tool on a bench, or a person the detector missed — so it has
# to hold for twice as long as the same class on a real person.
SCENE_DWELL_SCALE = 2.0

# Tracks are destroyed after tracking.TRACK_MAX_GAP seconds unseen, and a new
# track starts with a fresh cooldown — so a person who flickers out of the
# person detector for two seconds would re-alert immediately, defeating the
# 120s alert_cooldown entirely. This keeps a short registry of where we
# recently alerted so the cooldown sticks to a place in the frame, not to a
# track id that churns.
COOLDOWN_IOU = 0.3

# Ablation switches. Disabling one heuristic stage at a time lets the same
# footage be replayed with a single rule removed, so each rule's contribution to
# the true/false alert counts can be measured rather than asserted. Everything is
# ON by default; this exists for evaluation, not for production tuning.
ABLATABLE = ("class-floor", "vote", "dwell", "cooldown")


class Command(BaseCommand):
    help = (
        "Detects theft/robbery indicators (gun/knife/robbery activity/stealing) "
        "using the custom thief model. Use --image PATH to test on a single "
        "still picture, or run with no --image to watch a live source with a "
        "dwell timer."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set here, not just in handle(), so the detection helpers can be called
        # directly (tests, a REPL) without tripping over missing state.
        self.stats = Counter()
        self._alert_log = []   # (box, timestamp) — cooldown that survives track churn
        self.dry_run = False
        self.tracker_name = "greedy"
        self.ablate = set()

    def add_arguments(self, parser):
        parser.add_argument(
            "--image",
            help="Path to a still image to run detection on once (test mode). "
                 "Without this, watches --source continuously.",
        )
        parser.add_argument(
            "--source",
            default="0",
            help="Live video source: a webcam index (default 0) or a stream URL "
                 "/ video file path. Use the RTSP URL for a real CCTV camera, "
                 "e.g. rtsp://user:pass@192.168.1.50:554/stream1",
        )
        parser.add_argument(
            "--camera",
            default=THIEF_CAMERA_CODE,
            help=f"Camera code to attach alerts to (default {THIEF_CAMERA_CODE}). "
                 "Give each feed its own code when running one watcher per camera, "
                 "or every alert looks like it came from the same place.",
        )
        parser.add_argument(
            "--confidence",
            type=float,
            default=None,
            help="Override the SystemSettings thief confidence (as 0-1). "
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
            help="Show a live preview window with boxes drawn on it.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Detect and save evidence images but never write Alert rows. "
                 "Use this for tuning against a real feed without filling the "
                 "dispatcher's alert queue with test alerts.",
        )
        parser.add_argument(
            "--tracker",
            default="greedy",
            choices=["greedy", "bytetrack", "botsort"],
            help="Person-association method. 'greedy' (default) is the built-in "
                 "IoU + proximity matcher. 'bytetrack'/'botsort' use ultralytics' "
                 "Kalman trackers, which keep identities apart when two people "
                 "cross but assume a steady frame rate — benchmark before using "
                 "them with --far.",
        )
        parser.add_argument(
            "--ablate",
            default="",
            help="Evaluation only: comma-separated heuristic stages to DISABLE, "
                 f"from {'/'.join(ABLATABLE)}. Replay one recording per setting "
                 "and compare the alert counts to measure what each rule "
                 "contributes. Combine with --dry-run and --stats.",
        )
        parser.add_argument(
            "--stats",
            action="store_true",
            help="On exit, print how many detections each stage discarded "
                 "(class floor / vote / dwell / cooldown) plus effective FPS. "
                 "Use this to tune CLASS_POLICY against real footage instead of "
                 "guessing.",
        )
        parser.add_argument(
            "--far",
            action="store_true",
            help="Deprecated / no-op: long-range tiling is now ON by default "
                 "(whole-frame near pass AND tiling far pass every frame, merged). "
                 "Kept so existing commands don't break.",
        )
        parser.add_argument(
            "--fast",
            action="store_true",
            help="Near mode ONLY — the single whole-frame pass, no tiling. Faster "
                 "but won't detect small/distant objects like a gun or knife.",
        )
        parser.add_argument(
            "--tiles",
            default="2x2",
            help="Far mode only: tiling grid as ROWSxCOLS (e.g. 2x2, 3x3). More "
                 "tiles reach further but cost more inference per frame.",
        )

    def handle(self, *args, **options):
        # ViolationType/Camera aren't created by any migration, so get_or_create
        # here self-heals a fresh DB the same way watch_curfew/watch_smoking do.
        self.thief_type, _ = ViolationType.objects.get_or_create(
            code="thief",
            defaults={"label": "Theft / Robbery", "color": "#ef4444", "icon": "siren"},
        )
        self.camera, _ = Camera.objects.get_or_create(
            code=options["camera"],
            defaults={"name": "Thief Monitor", "status": Camera.Status.ONLINE},
        )
        self.violations_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(self.violations_dir, exist_ok=True)

        if not recognition.thief_model_available():
            self.stdout.write(self.style.ERROR(
                f"Thief model not found at {recognition.THIEF_MODEL_PATH}. "
                "Train one with detection_sandbox/train_thief.py and copy best.pt "
                "to core/vision/thief.pt (or set the THIEF_MODEL env var)."
            ))
            return

        self.conf_override = options["confidence"]
        self.dwell_override = options["dwell"]
        # Both modes by default (far already includes the near whole-frame pass);
        # --fast opts out to the single near pass.
        self.far = not options["fast"]
        self.dry_run = options["dry_run"]
        self.tracker_name = options["tracker"]
        self.show_stats = options["stats"]

        self.ablate = {s.strip() for s in options["ablate"].split(",") if s.strip()}
        unknown = self.ablate - set(ABLATABLE)
        if unknown:
            self.stdout.write(self.style.ERROR(
                f"Unknown --ablate stage(s): {', '.join(sorted(unknown))}. "
                f"Valid: {', '.join(ABLATABLE)}."
            ))
            return
        if self.ablate:
            self.stdout.write(self.style.WARNING(
                f"ABLATION: {', '.join(sorted(self.ablate))} DISABLED — "
                "measurement run, not a production configuration."
            ))
        try:
            rows, cols = (int(v) for v in options["tiles"].lower().split("x"))
            self.tiles = (rows, cols)
        except (ValueError, AttributeError):
            self.stdout.write(self.style.ERROR(
                f"Invalid --tiles {options['tiles']!r}; expected ROWSxCOLS like 2x2."
            ))
            return

        cfg = SystemSettings.load()
        if not cfg.thief_enabled:
            self.stdout.write(self.style.WARNING(
                "Thief detection is disabled in Settings (thief_enabled=False). "
                "Enable it in the dashboard, or it won't create alerts."
            ))
        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                "DRY RUN: evidence images will be saved but no alerts created."
            ))

        if options["image"]:
            conf = self.conf_override or (cfg.thief_confidence / 100)
            self._run_image(options["image"], conf)
        else:
            self._run_stream(options["source"], options["debug"])

    # ---- per-class policy --------------------------------------------------

    def _policy(self, label):
        """Per-class scales, or the neutral default when the class-floor stage is
        ablated (both the confidence and dwell multipliers come from here)."""
        if "class-floor" in self.ablate:
            return DEFAULT_POLICY
        return CLASS_POLICY.get(label, DEFAULT_POLICY)

    def _apply_class_floors(self, dets, conf):
        """Drops detections that clear the global confidence floor but not their
        own class's stricter one (see CLASS_POLICY)."""
        kept = []
        for d in dets:
            self.stats[f"detected:{d[5]}"] += 1
            if d[4] >= min(conf * self._policy(d[5])["conf_scale"], 1.0):
                kept.append(d)
            else:
                self.stats[f"cut by class floor:{d[5]}"] += 1
        return kept

    def _dwell_for(self, label, base_dwell, is_scene):
        """Dwell seconds required for this class, scaled up for the weak
        pose-like classes and again for unattributed detections."""
        dwell = base_dwell * self._policy(label)["dwell_scale"]
        if is_scene:
            dwell *= SCENE_DWELL_SCALE
        return dwell

    # ---- detection dispatch -----------------------------------------------

    def _detect_persons(self, frame):
        """Person boxes + (optionally) external track ids for the chosen tracker."""
        if self.tracker_name == "greedy":
            return recognition.detect_persons(frame), None
        return recognition.detect_persons_tracked(
            frame, tracker=f"{self.tracker_name}.yaml",
        )

    def _detect(self, frame, conf, persons=None):
        """Runs the thief detector, using the long-range cascade when --far is
        set (tiling + upscaled person crops), else the fast single-pass detector.
        Per-class confidence floors are applied to whatever comes back."""
        if not self.far:
            dets = recognition.detect_thief(frame, conf=conf)
        else:
            if persons is None:
                persons = recognition.detect_persons(frame)
            dets = recognition.detect_thief_far(
                frame, conf=conf, tiles=self.tiles, person_boxes=persons,
            )
        return self._apply_class_floors(dets, conf)

    # ---- single-image test mode -------------------------------------------

    def _run_image(self, path, conf):
        frame = recognition.load_image(path)
        if frame is None:
            self.stdout.write(self.style.ERROR(f"Could not read image: {path}"))
            return

        threats = self._detect(frame, conf)
        if not threats:
            self.stdout.write(self.style.WARNING(
                f"No theft/robbery indicators detected above confidence {conf} "
                "(after per-class floors). Try lowering --confidence."
            ))
            return

        for (x1, y1, x2, y2, score, label) in threats:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 220), 2)
            cv2.putText(frame, f"{label} {score * 100:.0f}%", (x1, max(y1 - 8, 0)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 220), 1)

        # A still image has no temporal signal at all — no voting, no dwell — so
        # this path is a much weaker bar than the live one. That's fine for
        # eyeballing the model, but it is why --dry-run exists.
        best = max(threats, key=lambda s: s[4])
        _, _, _, _, best_score, best_label = best
        summary = ", ".join(sorted({s[5] for s in threats}))
        alert = self._create_alert(
            best_score, best_label, frame,
            description=(
                f"Theft/robbery indicator detected on still image: "
                f"{len(threats)} detection(s) [{summary}]."
            ),
        )
        self.stdout.write(self.style.SUCCESS(
            f"Detected {len(threats)} indicator(s): {summary}. "
            + (f"ALERT created: {alert.code}" if alert else "No alert (dry run).")
        ))

    # ---- live stream dwell mode -------------------------------------------

    def _open_capture(self, source):
        """Opens a webcam index or a stream URL / file path."""
        if source.isdigit():
            return cv2.VideoCapture(int(source))
        cap = cv2.VideoCapture(source)
        # Far mode is far slower than an RTSP stream's frame rate, and OpenCV
        # would queue the backlog and hand us frames that are minutes stale.
        # A 1-frame buffer keeps detection on what the camera sees *now*.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run_stream(self, source, debug):
        cap = self._open_capture(source)
        if not cap.isOpened():
            self.stdout.write(self.style.ERROR(f"Could not open video source: {source}"))
            return

        # Live sources get the always-latest reader so slow far-mode processing
        # never falls behind the stream — the lag is stale buffered frames, not
        # detection speed. A file is read directly.
        is_live = source.isdigit() or "://" in source
        reader = recognition.LatestFrameReader(cap) if is_live else cap

        # Settings are re-polled every few seconds (like watch_curfew/watch_smoking)
        # so edits made in the dashboard take effect live, without a restart.
        # CLI flags, if given, still win over the stored values.
        cfg = SystemSettings.load()
        cfg_loaded_at = time.time()

        # Per-person tracking: person boxes are matched across frames (IoU, with
        # a center-proximity fallback for far mode's low frame rate), and each
        # track keeps its OWN vote window, dwell timer and alert cooldown — two
        # people in frame are confirmed and alerted independently, instead of one
        # scene-wide presence timer. Detections no person box claims fall back to
        # the tracker's scene pseudo-track.
        tracker = tracking.PersonTracker()

        mode = f"FAR {self.tiles[0]}x{self.tiles[1]} tiling + person-crop" if self.far else "near"
        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} for theft/robbery indicators "
            f"[{mode} mode, {self.tracker_name} tracker] "
            f"(dwell {self.dwell_override or cfg.thief_dwell}s, "
            f"reads live from Settings). Press Ctrl+C to stop."
        ))

        started_at = time.time()
        fps_warned = False
        try:
            while True:
                ok, frame = reader.read()
                if not ok:
                    if is_live:
                        time.sleep(0.02)
                        continue
                    self.stdout.write(self.style.WARNING(
                        f"Failed to read frame from {source}."
                    ))
                    time.sleep(0.5)
                    continue

                now_ts = time.time()
                if now_ts - cfg_loaded_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_loaded_at = now_ts

                if not cfg.thief_enabled:
                    time.sleep(0.5)
                    continue

                self.stats["frames"] += 1
                conf = self.conf_override or (cfg.thief_confidence / 100)
                dwell_seconds = self.dwell_override or cfg.thief_dwell

                # Person detection runs every frame (it's the tracking anchor);
                # far mode reuses the same boxes for its person-crop pass.
                persons, ids = self._detect_persons(frame)
                threats = self._detect(frame, conf, persons=persons)

                tracks = tracker.update(persons, now_ts, ids=ids)
                per_track = tracker.assign(threats, now_ts)

                if debug:
                    for t in tracks:
                        x1, y1, x2, y2 = t.box
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (180, 180, 180), 1)
                        cv2.putText(frame, f"person #{t.id}", (x1, max(y1 - 6, 0)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)

                for track, dets in per_track.items():
                    self._process_track(
                        track, dets, now_ts, dwell_seconds, cfg.alert_cooldown,
                        frame, debug,
                    )

                # Confirmation is time-based, but VOTE_MIN_FRAMES still needs a
                # few frames to land inside the window — below ~2 FPS that floor,
                # not the dwell, is what decides how fast anything can alert.
                if not fps_warned and self.stats["frames"] >= 30:
                    fps = self.stats["frames"] / max(now_ts - started_at, 1e-6)
                    if fps < 2:
                        fps_warned = True
                        self.stdout.write(self.style.WARNING(
                            f"Running at {fps:.1f} FPS — below ~2 FPS it takes "
                            f"{tracking.VOTE_MIN_FRAMES / fps:.0f}s just to confirm "
                            "a detection. Use fewer --tiles or a smaller frame."
                        ))

                if debug:
                    cv2.imshow("LookOut - watch_thief (debug)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            reader.stop() if is_live else cap.release()
            if debug:
                cv2.destroyAllWindows()
            if self.show_stats:
                self._print_stats(time.time() - started_at)
            self.stdout.write(self.style.SUCCESS("Stopped."))

    def _print_stats(self, elapsed):
        """Where every detection ended up, so the thresholds can be tuned from
        numbers instead of guesses."""
        frames = self.stats["frames"]
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Detection stats"))
        self.stdout.write(
            f"  {frames} frames in {elapsed:.0f}s "
            f"({frames / max(elapsed, 1e-6):.1f} FPS effective)"
        )
        if not any(k != "frames" for k in self.stats):
            self.stdout.write("  no detections at all — lower --confidence?")
            return
        for key in sorted(k for k in self.stats if k != "frames"):
            stage, _, label = key.partition(":")
            name = f"{stage} [{label}]" if label else stage
            self.stdout.write(f"  {self.stats[key]:>7}  {name}")
        self.stdout.write(
            "  (counts are detection-frames, not incidents: one person held for "
            "3s at 15 FPS is ~45)"
        )

    # ---- per-track temporal confirmation ----------------------------------

    def _process_track(self, track, dets, now_ts, dwell_seconds, cooldown,
                       frame, debug):
        """Votes, dwell-times and (maybe) alerts ONE track for this frame.

        Time-based N-of-M voting + a per-class dwell, per person: each track's
        votes/timers/cooldown are its own, so one person's alert doesn't mask or
        reset another's.
        """
        track.vote(dets, now_ts)
        # Ablating the vote removes temporal confirmation entirely: a detection
        # in THIS frame is taken at face value, which is the no-heuristics
        # baseline the evaluation compares against.
        active = bool(dets) if "vote" in self.ablate else track.accruing(now_ts)
        present_for = track.tick(now_ts, active)

        if not active:
            if dets:
                self.stats["held back: not enough votes yet"] += 1
            # Clear the dwell only when the person is visibly standing there
            # NOT doing it any more. If the track wasn't matched this frame they
            # are out of view, not innocent — hold the progress and let the
            # tombstone hand it back when they reappear.
            if track.seen_at(now_ts) and now_ts - track.last_threat_seen > PRESENCE_GRACE_SECONDS:
                track.reset_dwell()
            return

        # The class that held up across the window, not whichever spiked highest
        # in one frame.
        best = track.best_detection()
        if best is None:
            return
        _, _, _, _, best_score, best_label = best

        required = 0 if "dwell" in self.ablate else self._dwell_for(
            best_label, dwell_seconds, track.is_scene,
        )

        if debug:
            for (x1, y1, x2, y2, score, label) in dets:
                color = (0, 0, 220) if present_for >= required else (0, 200, 0)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, f"{label} {present_for:.0f}/{required:.0f}s",
                            (x1, max(y1 - 8, 0)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        if present_for < required:
            self.stats[f"held back: dwell not met:{best_label}"] += 1
            return

        box = track.box or best[:4]
        if "cooldown" not in self.ablate:
            if track.in_cooldown(now_ts, cooldown):
                self.stats["suppressed: track cooldown"] += 1
                return
            if self._cooldown_blocks(box, now_ts, cooldown):
                self.stats["suppressed: recent alert at same spot"] += 1
                return

        summary = ", ".join(sorted({s[5] for s in track.dets}))
        who = track.display
        self.stats[f"ALERTS:{best_label}"] += 1
        alert = self._create_alert(
            best_score, best_label, frame,
            description=(
                f"Theft/robbery indicator detected: {summary} on {who}, "
                f"present for {present_for:.0f}s on {self.camera.code} feed."
            ),
        )
        track.last_alerted_at = now_ts
        self._alert_log.append((tuple(box), now_ts))
        self.stdout.write(self.style.SUCCESS(
            (f"ALERT created: {alert.code}" if alert else "ALERT suppressed (dry run)")
            + f" ({best_label}, {who}, held {present_for:.0f}s)"
        ))

    def _cooldown_blocks(self, box, now, cooldown):
        """True if we already alerted on roughly this part of the frame inside
        the cooldown, regardless of which track id it was at the time."""
        self._alert_log = [
            (b, ts) for b, ts in self._alert_log if now - ts < cooldown
        ]
        return any(recognition._iou(box, b) >= COOLDOWN_IOU
                   for b, _ in self._alert_log)

    # ---- shared alert creation --------------------------------------------

    def _create_alert(self, score, label, frame, description):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = label.replace(" ", "_")
        filename = f"{ts_label}_thief_{safe_label}.jpg"
        cv2.imwrite(str(self.violations_dir / filename), frame)
        image_url = f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{filename}"

        if self.dry_run:
            return None

        return Alert.objects.create(
            type=self.thief_type,
            status=Alert.Status.ACTIVE,
            camera=self.camera,
            timestamp=timezone.now(),
            confidence=score,
            description=description,
            image_url=image_url,
            suspect=label,
        )
