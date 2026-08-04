import datetime
import os
import time
from collections import Counter

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import preprocess as preproc
from core.vision import recognition, tracking

SMOKING_CAMERA_CODE = "CAM-SMOKING"
SETTINGS_REFRESH_SECONDS = 5  # re-poll SystemSettings this often, not every frame
PRESENCE_GRACE_SECONDS = 2    # tolerate a couple smoke-free frames before resetting dwell

# N-of-M temporal voting (per tracked person) turns low, flickery per-frame
# confidence into a stable signal before the dwell timer starts counting. The
# window is time-based, not frame-based — see tracking.VOTE_WINDOW_SECONDS for
# why a frame count meant something different in near vs far mode.

# Tracks die after tracking.TRACK_MAX_GAP seconds unseen and a new track starts
# with a fresh cooldown, so a person flickering out of the person detector would
# defeat alert_cooldown. This keeps the cooldown pinned to a place in the frame.
COOLDOWN_IOU = 0.3

# Ablation switches. Disabling one heuristic stage at a time lets the same
# footage be replayed with a single rule removed, so each rule's contribution to
# the true/false alert counts can be measured rather than asserted. Everything is
# ON by default; this exists for evaluation, not for production tuning.
# ("face" is the same switch as --no-face-check, exposed here for symmetry.)
ABLATABLE = ("class-floor", "face", "vote", "dwell", "cooldown", "puff", "preprocess")

# Per-class policy, same idea as watch_thief's. The model's classes are not
# equally trustworthy: a `cigarette` or `vape` is an object held at the face,
# but bare `smoke` is the false-positive magnet of this whole detector — cooking,
# steam, vehicle exhaust, burning leaves and morning fog all read as smoke, and
# none of them are someone violating a smoking ordinance. So smoke has to clear
# a higher bar and hold for longer. Scales multiply the dashboard values, so
# tuning Settings still works.
# Keys are matched CASE-INSENSITIVELY and cover the class names of every smoking
# model trained so far. Different exports capitalise differently ("cigarette" vs
# "Cigarette") and rename the diffuse class ("smoke" vs "Vapor"); a plain
# dict lookup would miss those and silently fall back to the neutral default,
# quietly removing the very penalty the ambiguous class exists to carry.
CLASS_POLICY = {
    "cigarette": {"conf_scale": 1.0, "dwell_scale": 1.0},
    "smoking":   {"conf_scale": 1.0, "dwell_scale": 1.0},
    "vape":      {"conf_scale": 1.0, "dwell_scale": 1.0},
    "smoke":     {"conf_scale": 1.5, "dwell_scale": 2.0},
    "vapor":     {"conf_scale": 1.5, "dwell_scale": 2.0},
}
DEFAULT_POLICY = {"conf_scale": 1.0, "dwell_scale": 1.0}

# Detections no person box claimed are the least trustworthy of all — smoke with
# nobody attached is almost always cooking or exhaust — so they hold twice as long.
SCENE_DWELL_SCALE = 2.0

# Mouth-proximity rule. Person association alone only asks whether a detection
# falls inside someone's BODY box, so a cigarette detected at knee height counts
# exactly as much as one at the lips. These classes are objects a smoker holds to
# their face, so requiring them near the mouth is a real constraint.
#
# `smoke` is deliberately exempt: smoke DRIFTS. It rises and spreads away from
# the smoker, so demanding it sit near the mouth would reject the true positives,
# not the false ones. Smoke is disciplined by its confidence and dwell scales
# instead.
# Matched case-insensitively, for the same reason as CLASS_POLICY: a model that
# exports "Cigarette" instead of "cigarette" would otherwise skip the rule
# entirely and no longer check the mouth at all.
FACE_ANCHORED_CLASSES = {"cigarette", "vape", "smoking"}
FACE_PROXIMITY = 2.5    # allowed distance from the mouth, in face widths

# Puff-cycle rule: when --require-puff is on, an alert additionally needs the
# person to have shown the hand-to-mouth RHYTHM (cigarette raised to the lips and
# lowered) at least this many times. It makes smoking detection much more
# specific — a poster or someone merely holding a cigarette never produces the
# rhythm — but it needs a decent frame rate and a visible face to observe the
# motion, so it is OPT-IN, not the default.
PUFF_MIN_CYCLES = 1


def _is_face_anchored(label):
    return label.lower() in FACE_ANCHORED_CLASSES

# A face pass costs ~400ms per person — about 4x the person + smoking detectors
# combined — so running it every frame would drop the pipeline under 2 FPS with a
# single smoker in view. The anchor is cached per track and stored relative to
# the person box, so it re-projects as they move; it is only re-detected this
# often, which is frequent enough to follow someone turning their head.
FACE_CACHE_SECONDS = 1.0


class Command(BaseCommand):
    help = (
        "Detects public smoking (cigarette/smoke/vape) using the custom smoking "
        "model. Use --image PATH to test on a single still picture, or run with "
        "no --image to watch the webcam with a dwell timer."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set here, not just in handle(), so the detection helpers can be called
        # directly (tests, a REPL) without tripping over missing state.
        self.stats = Counter()
        self._alert_log = []   # (box, timestamp) — cooldown that survives track churn
        self.dry_run = False
        self.tracker_name = "greedy"
        self.face_check = True
        self.require_puff = False
        self.cascade = False
        self.far = True
        self.preprocess = False
        self.sharpen = False
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
            default=SMOKING_CAMERA_CODE,
            help=f"Camera code to attach alerts to (default {SMOKING_CAMERA_CODE}). "
                 "Give each feed its own code when running one watcher per camera, "
                 "or every alert looks like it came from the same place.",
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
                 "(class floor / face rule / vote / dwell / cooldown) plus "
                 "effective FPS. Use this to tune the thresholds against real "
                 "footage instead of guessing.",
        )
        parser.add_argument(
            "--no-face-check",
            action="store_true",
            help="Disable the mouth-proximity rule, accepting any cigarette/vape "
                 "found anywhere on a person's body. Faster (skips a face pass "
                 "per smoker) and more permissive.",
        )
        parser.add_argument(
            "--require-puff",
            action="store_true",
            help="Only alert after the hand-to-mouth RHYTHM is seen — the "
                 f"cigarette raised to the lips and lowered >= {PUFF_MIN_CYCLES} "
                 "time(s). Much more specific (a poster or a held cigarette won't "
                 "fire), but needs a visible face and enough frame rate to see "
                 "the motion — best with the camera close and on a GPU.",
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
            help="Deprecated / no-op: long-range tiling is now ON by default "
                 "(each frame runs the whole-frame near pass AND the tiling far "
                 "pass, merged). Kept so existing commands don't break.",
        )
        parser.add_argument(
            "--fast",
            action="store_true",
            help="Near mode ONLY — the single whole-frame pass, no tiling. Much "
                 "faster but won't detect small/distant objects like a cigarette. "
                 "Use when the subject is always close to the camera.",
        )
        parser.add_argument(
            "--cascade",
            action="store_true",
            help="Cascade crop at native resolution: detect people, then run the "
                 "cigarette model on each person's crop taken from the FULL-RES "
                 "frame. Best range-per-cost — a 4px cigarette in the full frame "
                 "becomes 30-50px in the crop. Use with the MAIN stream "
                 "(/Streaming/Channels/101); cheaper than --far (no tiling).",
        )
        parser.add_argument(
            "--preprocess",
            action="store_true",
            help="Enhance dim/noisy frames before detection: gamma-brighten, "
                 "denoise, and CLAHE local contrast (daytime frames bypass "
                 "untouched). Helps in low light; adds a little cost per dark "
                 "frame. Add 'preprocess' to --ablate to A/B it.",
        )
        parser.add_argument(
            "--sharpen",
            action="store_true",
            help="With --preprocess, also apply an unsharp kernel (sharper edges "
                 "for small objects, but can amplify noise).",
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
            code=options["camera"],
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
        # Both modes by default: far mode already runs the whole-frame (near)
        # pass and adds tiling on top, so "far" means near+far combined. --fast
        # opts out to the single near pass. --cascade is a third mode (native-res
        # person crops), which overrides the others when set.
        self.cascade = options["cascade"]
        self.far = not options["fast"] and not self.cascade
        self.preprocess = options["preprocess"] and "preprocess" not in self.ablate
        self.sharpen = options["sharpen"]
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

        # --no-face-check and --ablate face are the same switch.
        self.face_check = not options["no_face_check"] and "face" not in self.ablate
        self.require_puff = options["require_puff"] and "puff" not in self.ablate
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
        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                "DRY RUN: evidence images will be saved but no alerts created."
            ))

        if options["image"]:
            conf = self.conf_override or (cfg.smoking_confidence / 100)
            self._run_image(options["image"], conf)
        else:
            self._run_stream(options["source"], options["debug"])

    # ---- detection dispatch -----------------------------------------------

    def _policy(self, label):
        """Per-class scales, or the neutral default when the class-floor stage is
        ablated (both the confidence and dwell multipliers come from here)."""
        if "class-floor" in self.ablate:
            return DEFAULT_POLICY
        return CLASS_POLICY.get(label.lower(), DEFAULT_POLICY)

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

    def _mouth_anchor(self, frame, track, now_ts):
        """Mouth position and face width for a track, in full-frame coordinates.

        Cached per track for FACE_CACHE_SECONDS and held relative to the person
        box, so a moving smoker keeps a valid anchor without paying for a face
        pass every frame. Returns None when no face could be found.
        """
        bx1, by1, bx2, by2 = track.box
        bw, bh = max(bx2 - bx1, 1), max(by2 - by1, 1)

        cached = track.face_anchor
        if cached is not None and now_ts - cached[3] < FACE_CACHE_SECONDS:
            rel_x, rel_y, rel_w, _ = cached
            self.stats["face rule: anchor cache hit"] += 1
            return bx1 + rel_x * bw, by1 + rel_y * bh, rel_w * bw

        found = recognition.find_mouth(frame, track.box)
        if found is None:
            return None
        mx, my, face_w = found
        track.face_anchor = ((mx - bx1) / bw, (my - by1) / bh, face_w / bw, now_ts)
        return mx, my, face_w

    def _apply_face_rule(self, frame, per_track, now_ts):
        """Mouth-proximity rule: a held smoking object must be near the person's
        mouth, not merely somewhere on their body.

        Runs per person and only when that person actually has a held-object
        detection, so the extra face pass costs nothing on empty frames. When no
        face can be found the detection is KEPT, not rejected — see
        recognition.find_mouth for why treating "no face" as "not smoking" would
        quietly switch the whole detector off at CCTV range.
        """
        if not self.face_check:
            return per_track

        for track, dets in per_track.items():
            if track.is_scene or not dets:
                continue
            if not any(_is_face_anchored(d[5]) for d in dets):
                continue

            anchor = self._mouth_anchor(frame, track, now_ts)
            if anchor is None:
                self.stats["face rule: no face found, kept"] += 1
                continue

            mx, my, face_w = anchor
            limit = face_w * FACE_PROXIMITY
            kept = []
            nearest_ratio = None   # closest cigarette->mouth distance, in face-widths
            for d in dets:
                if not _is_face_anchored(d[5]):
                    kept.append(d)
                    continue
                cx, cy = (d[0] + d[2]) / 2, (d[1] + d[3]) / 2
                dist = ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5
                ratio = dist / max(face_w, 1)
                if nearest_ratio is None or ratio < nearest_ratio:
                    nearest_ratio = ratio
                if dist <= limit:
                    kept.append(d)
                else:
                    self.stats[f"cut by face rule:{d[5]}"] += 1
            per_track[track] = kept

            # Feed the closest object's mouth distance into the puff-cycle state
            # machine so the rhythm (raise-lower-raise) can be counted per person.
            if nearest_ratio is not None:
                n = track.update_puff(nearest_ratio, now_ts)
                if n:
                    self.stats[f"puffs observed (person #{track.id})"] = n
        return per_track

    def _detect_persons(self, frame):
        """Person boxes + (optionally) external track ids for the chosen tracker."""
        if self.tracker_name == "greedy":
            return recognition.detect_persons(frame), None
        return recognition.detect_persons_tracked(
            frame, tracker=f"{self.tracker_name}.yaml",
        )

    def _dwell_for(self, label, base_dwell, is_scene):
        """Dwell seconds required for this class, scaled up for the ambiguous
        `smoke` class and again for unattributed detections."""
        dwell = base_dwell * self._policy(label)["dwell_scale"]
        if is_scene:
            dwell *= SCENE_DWELL_SCALE
        return dwell

    def _detect(self, frame, conf, persons=None):
        """Runs the smoking detector. --cascade = native-res person crops (best
        range/cost); --far = tiling + upscaled crops; else the fast single pass.
        Per-class confidence floors are applied to whatever comes back."""
        if self.cascade:
            if persons is None:
                persons = recognition.detect_persons(frame)
            dets = recognition.detect_smoking_cascade(
                frame, persons, conf=conf, crop_imgsz=recognition.NEAR_IMGSZ,
            )
        elif not self.far:
            dets = recognition.detect_smoking(frame, conf=conf)
        else:
            if persons is None:
                persons = recognition.detect_persons(frame)
            dets = recognition.detect_smoking_far(
                frame, conf=conf, tiles=self.tiles, person_boxes=persons,
            )
        return self._apply_class_floors(dets, conf)

    # ---- single-image test mode -------------------------------------------

    def _run_image(self, path, conf):
        frame = recognition.load_image(path)
        if frame is None:
            self.stdout.write(self.style.ERROR(f"Could not read image: {path}"))
            return

        smokes = self._detect(frame, conf)
        if not smokes:
            self.stdout.write(self.style.WARNING(
                f"No smoking detected above confidence {conf} (after per-class "
                "floors). Try lowering --confidence."
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
            + (f"ALERT created: {alert.code}" if alert else "No alert (dry run).")
        ))

    # ---- webcam dwell mode ------------------------------------------------

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
        # never falls behind the stream — the lag you'd otherwise see is stale
        # buffered frames, not detection speed. A file is read directly.
        is_live = source.isdigit() or "://" in source
        reader = recognition.LatestFrameReader(cap) if is_live else cap

        # Settings are re-polled every few seconds (like watch_curfew/watch_parking)
        # so edits made in the dashboard's Smoking config take effect live, without
        # a restart. CLI flags, if given, still win over the stored values.
        cfg = SystemSettings.load()
        cfg_loaded_at = time.time()

        # Per-person tracking: the cigarette itself is too small/transient to
        # track, but the PERSON holding it isn't — person boxes are matched
        # across frames (IoU, with a center-proximity fallback for far mode's
        # low frame rate), and each track keeps its own vote window, dwell timer
        # and alert cooldown, so two smokers in frame are confirmed and alerted
        # independently. Detections no person box claims fall back to the
        # tracker's scene pseudo-track.
        tracker = tracking.PersonTracker()

        # Rolling 10s buffer of annotated frames — on an alert it's written out as
        # the evidence clip, so the card shows the cigarette being detected with
        # its box, not just a still.
        # 30-second evidence clip: the ~27s approach to the violation plus the
        # dwell, ending at the alert. Raise toward 60 for more context (costs
        # more memory — it buffers every processed frame for the window).
        self.clip = recognition.ClipRecorder(seconds=30, label=self.camera.code)

        mode = f"FAR {self.tiles[0]}x{self.tiles[1]} tiling + person-crop" if self.far else "near"
        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} for public smoking "
            f"[{mode} mode, {self.tracker_name} tracker, "
            f"face check {'on' if self.face_check else 'off'}] "
            f"(dwell {self.dwell_override or cfg.smoking_dwell}s, "
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
                    # A file source that stops yielding frames has reached its
                    # end — finish rather than looping forever on a test video.
                    self.stdout.write(self.style.SUCCESS(
                        f"End of {source} — done."
                    ))
                    break

                # Enhance dim/noisy frames before detection (daytime bypasses).
                if self.preprocess:
                    frame = preproc.preprocess(
                        frame, mode="cascade" if self.cascade else "near",
                        sharpen=self.sharpen,
                    )

                now_ts = time.time()
                if now_ts - cfg_loaded_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_loaded_at = now_ts

                if not cfg.smoking_enabled:
                    time.sleep(0.5)
                    continue

                self.stats["frames"] += 1
                conf = self.conf_override or (cfg.smoking_confidence / 100)
                dwell_seconds = self.dwell_override or cfg.smoking_dwell

                # Person detection runs every frame (it's the tracking anchor);
                # far mode reuses the same boxes for its person-crop pass.
                persons, ids = self._detect_persons(frame)
                smokes = self._detect(frame, conf, persons=persons)

                tracks = tracker.update(persons, now_ts, ids=ids)
                per_track = self._apply_face_rule(
                    frame, tracker.assign(smokes, now_ts), now_ts,
                )

                # Draw person boxes ALWAYS (not just in debug) so the evidence
                # clip and snapshot show the context, not only the debug window.
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

                # Buffer this annotated frame for the evidence clip.
                self.clip.add(frame, now_ts)

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
                            "a detection. Use fewer --tiles, a smaller frame, or "
                            "--no-face-check."
                        ))

                if debug:
                    cv2.imshow("LookOut - watch_smoking (debug)", frame)
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

        Time-based N-of-M voting + dwell + grace, per person: each track's
        votes/timers/cooldown are its own, so one smoker's alert doesn't mask
        or reset another's.
        """
        track.vote(dets, now_ts)
        # Ablating the vote removes temporal confirmation entirely: a detection
        # in THIS frame is taken at face value, which is the no-heuristics
        # baseline the evaluation compares against.
        active = bool(dets) if "vote" in self.ablate else track.accruing(now_ts)
        present_for = track.tick(now_ts, active)

        if not active:
            # Clear the dwell only when the person is visibly standing there NOT
            # smoking any more. If the track wasn't matched this frame they are
            # out of view, not innocent — hold the progress and let the tombstone
            # hand it back when they reappear.
            if track.seen_at(now_ts) and now_ts - track.last_threat_seen > PRESENCE_GRACE_SECONDS:
                track.reset_dwell()
            return

        # The class that held up across the window, not whichever spiked
        # highest in a single frame.
        best = track.best_detection()
        if best is None:
            return
        _, _, _, _, best_score, best_label = best

        required = 0 if "dwell" in self.ablate else self._dwell_for(
            best_label, dwell_seconds, track.is_scene,
        )

        # Draw the violation boxes ALWAYS (green while building, orange once the
        # dwell is met) so the evidence clip shows the cigarette being detected.
        for (x1, y1, x2, y2, score, label) in dets:
            color = (0, 165, 245) if present_for >= required else (0, 200, 0)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"{label} {score * 100:.0f}% {present_for:.0f}/{required:.0f}s",
                        (x1, max(y1 - 8, 0)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        if present_for < required:
            self.stats[f"held back: dwell not met:{best_label}"] += 1
            return

        # Puff-cycle gate (opt-in): require the hand-to-mouth rhythm as well as
        # the dwell. A scene track has no face/mouth to measure against, so the
        # rule only applies to real person tracks.
        if self.require_puff and not track.is_scene:
            puffs = track.puff_count(now_ts)
            if puffs < PUFF_MIN_CYCLES:
                self.stats[f"held back: no puff rhythm (person #{track.id})"] += 1
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
        puff_note = ""
        if not track.is_scene:
            puffs = track.puff_count(now_ts)
            if puffs:
                puff_note = f", {puffs} puff cycle(s) observed"
        self.stats[f"ALERTS:{best_label}"] += 1
        alert = self._create_alert(
            best_score, best_label, frame,
            description=(
                f"Public smoking detected: {summary} on {who}, present "
                f"for {present_for:.0f}s{puff_note} on {self.camera.code} feed."
            ),
        )
        track.last_alerted_at = now_ts
        self._alert_log.append((tuple(box), now_ts))
        self.stdout.write(self.style.SUCCESS(
            (f"ALERT created: {alert.code}" if alert else "ALERT suppressed (dry run)")
            + f" ({best_label}, {who}, held {present_for:.0f}s{puff_note})"
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
        filename = f"{ts_label}_smoking_{label}.jpg"
        cv2.imwrite(str(self.violations_dir / filename), frame)
        image_url = f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{filename}"

        # Write the ~10s evidence clip (annotated frames leading up to the alert).
        video_url = ""
        clip = getattr(self, "clip", None)
        if clip is not None:
            video_name = f"{ts_label}_smoking_{label}.mp4"
            if clip.save(self.violations_dir / video_name):
                video_url = f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{video_name}"

        if self.dry_run:
            return None

        return Alert.objects.create(
            type=self.smoking_type,
            status=Alert.Status.ACTIVE,
            camera=self.camera,
            timestamp=timezone.now(),
            confidence=score,
            description=description,
            image_url=image_url,
            video_url=video_url,
            suspect=label,
        )
