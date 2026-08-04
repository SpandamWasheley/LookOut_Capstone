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

DRINKING_CAMERA_CODE = "CAM-DRINKING"
SETTINGS_REFRESH_SECONDS = 5  # re-poll SystemSettings this often, not every frame
PRESENCE_GRACE_SECONDS = 2    # tolerate a couple bottle-free frames before resetting dwell

COOLDOWN_IOU = 0.3

# --- The product/behaviour gap -------------------------------------------
# This detector has ONE class, "Red Horse": it recognises a beer brand, which is
# an object, not an act. A bottle in a sari-sari store, in a shopping bag, empty
# in a bin, or held by a bystander all look identical to one being drunk from.
# Public-drinking ordinances concern CONSUMPTION, so the heuristics below carry
# the whole distance between "a bottle is visible" and "someone is drinking".
#
# The mechanism is posture: where the bottle sits relative to the person decides
# how long it must persist before it counts. Raising a bottle to the face is the
# act itself and alerts at the configured dwell; a bottle merely held is weaker
# evidence and must persist twice as long; a bottle with no person at all is
# weakest and must persist three times as long.
#
# Note this is an ESCALATION, not a rejection — unlike the smoking detector,
# which rejects a cigarette far from the mouth outright. A cigarette at knee
# height is meaningless, whereas an open bottle in someone's hand is genuine
# evidence for this ordinance, merely weaker than one at their lips.
POSTURE_DWELL = {
    "at-mouth": 1.0,     # raised to the face — consumption
    "held": 2.0,         # on the person, not raised — possession
    "unattended": 3.0,   # no person associated — litter, display, or a miss
}

# --- Generic vessels (--include-generic) ---------------------------------
# The branded model sees one product, so a gin session, a different beer, or a
# drink poured into a glass produces nothing at all. The COCO classes bottle /
# wine glass / cup come free from the person detector's own pass and close that
# gap — but they say nothing about CONTENTS: a water bottle is indistinguishable
# from a beer.
#
# They are therefore admitted on stricter terms than a branded detection:
#   * only when RAISED TO THE MOUTH — a generic bottle at someone's hip is
#     almost certainly innocuous, whereas one repeatedly lifted to the face in a
#     group is meaningful whatever the label says;
#   * at twice the dwell, since the inference is weaker.
# A branded detection on the same person always takes precedence.
GENERIC_LABELS = set(recognition.VESSEL_CLASS_IDS.values())
GENERIC_DWELL_SCALE = 2.0

# Distance from the mouth, in face widths, within which a bottle counts as
# raised. More generous than smoking's equivalent: a bottle is held further from
# the face than a cigarette and is a much larger object.
MOUTH_PROXIMITY = 3.0

# A face pass costs ~400ms per person, so the anchor is cached per track and
# stored relative to the person box, re-projecting as they move.
FACE_CACHE_SECONDS = 1.0

# Ablation switches — see HEURISTIC_RULES.md. Everything ON by default.
ABLATABLE = ("posture", "vote", "dwell", "cooldown", "hours", "zones")


def _within_window(now_time, start, end):
    """True if now_time falls in [start, end), handling windows that wrap
    midnight. Same semantics as watch_curfew's curfew hours."""
    if start <= end:
        return start <= now_time < end
    return now_time >= start or now_time < end


class Command(BaseCommand):
    help = (
        "Detects public drinking using the custom drinking model. NOTE: the "
        "model detects a beer brand (an object), not the act of drinking; "
        "posture and dwell heuristics carry that gap. Use --image PATH to test "
        "on a still picture, or run with no --image to watch a live source."
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
        self.include_generic = False
        self.zones = []
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
                 "/ video file path. Use the RTSP URL for a real CCTV camera.",
        )
        parser.add_argument(
            "--camera",
            default=DRINKING_CAMERA_CODE,
            help=f"Camera code to attach alerts to (default {DRINKING_CAMERA_CODE}). "
                 "Give each feed its own code when running one watcher per camera.",
        )
        parser.add_argument(
            "--confidence",
            type=float,
            default=None,
            help="Override the SystemSettings drinking confidence (as 0-1). "
                 "Omit to use the dashboard 'Detection confidence' value.",
        )
        parser.add_argument(
            "--dwell",
            type=int,
            default=None,
            help="Override the SystemSettings dwell seconds. This is the "
                 "at-mouth dwell; held and unattended scale up from it.",
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Show a live preview window with boxes and posture drawn on it.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Detect and save evidence images but never write Alert rows.",
        )
        parser.add_argument(
            "--tracker",
            default="greedy",
            choices=["greedy", "bytetrack", "botsort"],
            help="Person-association method. 'greedy' (default) is the built-in "
                 "IoU + proximity matcher; the others are ultralytics' Kalman "
                 "trackers, which assume a steady frame rate.",
        )
        parser.add_argument(
            "--stats",
            action="store_true",
            help="On exit, print how many detections each stage discarded and "
                 "the posture mix, plus effective FPS.",
        )
        parser.add_argument(
            "--no-face-check",
            action="store_true",
            help="Disable posture classification: every bottle on a person is "
                 "treated as 'held'. Faster (skips a face pass per person).",
        )
        parser.add_argument(
            "--ablate",
            default="",
            help="Evaluation only: comma-separated stages to DISABLE, from "
                 f"{'/'.join(ABLATABLE)}. Combine with --dry-run and --stats.",
        )
        parser.add_argument(
            "--include-generic",
            action="store_true",
            help="Also treat COCO bottle/cup/wine-glass detections as evidence, "
                 "covering brands and glassware the custom model never saw. Free "
                 "(same pass as person detection) but contents-blind, so these "
                 "only count when raised to the mouth, at double the dwell.",
        )
        parser.add_argument(
            "--zones",
            help="Path to a zone JSON file ([{name, points:[[x,y],...]}, ...], "
                 "same format as detection_sandbox/zones.example.json). "
                 "Detections whose centre falls outside every zone are ignored — "
                 "use it to exclude private frontage or a licensed venue from a "
                 "camera that also covers public street.",
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
                 "but won't detect small/distant bottles.",
        )
        parser.add_argument(
            "--tiles",
            default="2x2",
            help="Far mode only: tiling grid as ROWSxCOLS (e.g. 2x2, 3x3).",
        )

    def handle(self, *args, **options):
        # ViolationType/Camera aren't created by any migration, so get_or_create
        # here self-heals a fresh DB the same way the other watchers do.
        self.drinking_type, _ = ViolationType.objects.get_or_create(
            code="drinking",
            defaults={"label": "Public Drinking", "color": "#8b5cf6", "icon": "beer"},
        )
        self.camera, _ = Camera.objects.get_or_create(
            code=options["camera"],
            defaults={"name": "Drinking Monitor", "status": Camera.Status.ONLINE},
        )
        self.violations_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(self.violations_dir, exist_ok=True)

        if not recognition.drinking_model_available():
            self.stdout.write(self.style.ERROR(
                f"Drinking model not found at {recognition.DRINKING_MODEL_PATH}. "
                "Copy the trained best.pt there, or set the DRINKING_MODEL env var."
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

        # --no-face-check and --ablate posture are the same switch.
        self.face_check = not options["no_face_check"] and "posture" not in self.ablate
        self.include_generic = options["include_generic"]

        self.zones = []
        if options["zones"] and "zones" not in self.ablate:
            try:
                import json

                import numpy as np

                with open(options["zones"], encoding="utf-8") as fh:
                    for z in json.load(fh):
                        self.zones.append((
                            z.get("name", "zone"),
                            np.array(z["points"], dtype=np.int32),
                        ))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.stdout.write(self.style.ERROR(
                    f"Could not read --zones {options['zones']!r}: {exc}"
                ))
                return
            self.stdout.write(self.style.SUCCESS(
                f"Restricting detection to {len(self.zones)} zone(s): "
                + ", ".join(n for n, _ in self.zones)
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
        if not cfg.drinking_enabled:
            self.stdout.write(self.style.WARNING(
                "Drinking detection is disabled in Settings (drinking_enabled=False). "
                "Enable it in the dashboard, or it won't create alerts."
            ))
        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                "DRY RUN: evidence images will be saved but no alerts created."
            ))

        if options["image"]:
            conf = self.conf_override or (cfg.drinking_confidence / 100)
            self._run_image(options["image"], conf)
        else:
            self._run_stream(options["source"], options["debug"])

    # ---- detection dispatch -----------------------------------------------

    def _detect_persons(self, frame):
        """Person boxes, generic vessels, and (optionally) external track ids.

        With --include-generic the vessels come out of the SAME yolov8n pass as
        the persons, so the extra coverage is free.
        """
        if self.tracker_name != "greedy":
            persons, ids = recognition.detect_persons_tracked(
                frame, tracker=f"{self.tracker_name}.yaml",
            )
            vessels = []
            if self.include_generic:
                # The tracking call filters to persons, so vessels need their own
                # pass here — the one case where --include-generic isn't free.
                _, vessels = recognition.detect_persons_and_vessels(frame)
            return persons, vessels, ids
        if self.include_generic:
            persons, vessels = recognition.detect_persons_and_vessels(frame)
            return persons, vessels, None
        return recognition.detect_persons(frame), [], None

    def _in_zone(self, det):
        """True if the detection's centre falls inside any configured zone."""
        if not self.zones:
            return True
        import cv2 as _cv2

        cx, cy = (det[0] + det[2]) / 2, (det[1] + det[3]) / 2
        return any(_cv2.pointPolygonTest(pts, (float(cx), float(cy)), False) >= 0
                   for _, pts in self.zones)

    def _detect(self, frame, conf, persons=None, vessels=None):
        """Runs the drinking detector, using the long-range cascade when --far is
        set (tiling + upscaled person crops), else the fast single-pass detector.
        Generic vessels, if enabled, are merged in; zones filter the result."""
        if not self.far:
            dets = recognition.detect_drinking(frame, conf=conf)
        else:
            if persons is None:
                persons = recognition.detect_persons(frame)
            dets = recognition.detect_drinking_far(
                frame, conf=conf, tiles=self.tiles, person_boxes=persons,
            )
        if self.include_generic and vessels:
            dets = dets + list(vessels)

        kept = []
        for d in dets:
            self.stats[f"detected:{d[5]}"] += 1
            if self._in_zone(d):
                kept.append(d)
            else:
                self.stats[f"cut by zone:{d[5]}"] += 1
        return kept

    # ---- posture classification -------------------------------------------

    def _mouth_anchor(self, frame, track, now_ts):
        """Mouth position and face width for a track, in full-frame coordinates.

        Cached per track and held relative to the person box, so a moving subject
        keeps a valid anchor without paying for a face pass every frame. Returns
        None when no face could be found.
        """
        bx1, by1, bx2, by2 = track.box
        bw, bh = max(bx2 - bx1, 1), max(by2 - by1, 1)

        cached = track.face_anchor
        if cached is not None and now_ts - cached[3] < FACE_CACHE_SECONDS:
            rel_x, rel_y, rel_w, _ = cached
            self.stats["posture: anchor cache hit"] += 1
            return bx1 + rel_x * bw, by1 + rel_y * bh, rel_w * bw

        found = recognition.find_mouth(frame, track.box)
        if found is None:
            return None
        mx, my, face_w = found
        track.face_anchor = ((mx - bx1) / bw, (my - by1) / bh, face_w / bw, now_ts)
        return mx, my, face_w

    def _posture(self, frame, track, dets, now_ts):
        """Classifies where the bottle sits relative to the person.

        Returns 'unattended', 'held' or 'at-mouth'. When posture checking is off,
        or no face can be resolved, the result is 'held' — the conservative
        middle: not treated as consumption, but not dismissed either. Reading an
        unresolvable face as 'not drinking' would disable the escalation at
        exactly the CCTV distances where faces stop being detectable.
        """
        if track.is_scene:
            return "unattended"
        if not self.face_check or not dets:
            return "held"

        anchor = self._mouth_anchor(frame, track, now_ts)
        if anchor is None:
            self.stats["posture: no face found, treated as held"] += 1
            return "held"

        mx, my, face_w = anchor
        limit = face_w * MOUTH_PROXIMITY
        for d in dets:
            cx, cy = (d[0] + d[2]) / 2, (d[1] + d[3]) / 2
            if ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5 <= limit:
                return "at-mouth"
        return "held"

    def _dwell_for(self, posture, base_dwell, is_generic=False):
        """Dwell seconds required, or None if this evidence can never alert.

        A generic vessel only counts while raised to the mouth (see
        GENERIC_LABELS); anywhere else it is contents-unknown clutter.
        """
        if is_generic:
            if posture != "at-mouth":
                return None
            return base_dwell * GENERIC_DWELL_SCALE
        return base_dwell * POSTURE_DWELL.get(posture, 2.0)

    # ---- single-image test mode -------------------------------------------

    def _run_image(self, path, conf):
        frame = recognition.load_image(path)
        if frame is None:
            self.stdout.write(self.style.ERROR(f"Could not read image: {path}"))
            return

        vessels = []
        if self.include_generic:
            _, vessels = recognition.detect_persons_and_vessels(frame)
        drinks = self._detect(frame, conf, vessels=vessels)
        if not drinks:
            self.stdout.write(self.style.WARNING(
                f"No drinking indicators detected above confidence {conf}. "
                "Try lowering --confidence."
            ))
            return

        for (x1, y1, x2, y2, score, label) in drinks:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (200, 80, 160), 2)
            cv2.putText(frame, f"{label} {score * 100:.0f}%", (x1, max(y1 - 8, 0)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 80, 160), 1)

        # A still image has no temporal signal and no posture history, so this
        # path is a far weaker bar than the live one — use --dry-run for tuning.
        best = max(drinks, key=lambda s: s[4])
        _, _, _, _, best_score, best_label = best
        summary = ", ".join(sorted({s[5] for s in drinks}))
        alert = self._create_alert(
            best_score, best_label, frame,
            description=(
                f"Public drinking indicator detected on still image: "
                f"{len(drinks)} detection(s) [{summary}]."
            ),
        )
        self.stdout.write(self.style.SUCCESS(
            f"Detected {len(drinks)} indicator(s): {summary}. "
            + (f"ALERT created: {alert.code}" if alert else "No alert (dry run).")
        ))

    # ---- live stream dwell mode -------------------------------------------

    def _open_capture(self, source):
        """Opens a webcam index or a stream URL / file path."""
        if source.isdigit():
            return cv2.VideoCapture(int(source))
        cap = cv2.VideoCapture(source)
        # Far mode is slower than an RTSP stream's frame rate; a 1-frame buffer
        # keeps detection on what the camera sees now, not a stale backlog.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run_stream(self, source, debug):
        cap = self._open_capture(source)
        if not cap.isOpened():
            self.stdout.write(self.style.ERROR(f"Could not open video source: {source}"))
            return

        # Live sources get the always-latest reader so slow far-mode processing
        # never falls behind the stream — the delay you'd otherwise see is stale
        # buffered frames, not detection speed. A file is read directly.
        is_live = source.isdigit() or "://" in source
        reader = recognition.LatestFrameReader(cap) if is_live else cap

        cfg = SystemSettings.load()
        cfg_loaded_at = time.time()

        # Per-person tracking: the bottle is what's detected, but the PERSON
        # holding it is what must be timed. Each track keeps its own vote window,
        # dwell timer and cooldown, so two drinkers are confirmed independently.
        tracker = tracking.PersonTracker()

        mode = f"FAR {self.tiles[0]}x{self.tiles[1]} tiling + person-crop" if self.far else "near"
        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} for public drinking "
            f"[{mode} mode, {self.tracker_name} tracker, "
            f"posture check {'on' if self.face_check else 'off'}"
            + (", +generic vessels" if self.include_generic else "")
            + (f", zones {len(self.zones)}" if self.zones else "")
            + "] "
            f"(dwell {self.dwell_override or cfg.drinking_dwell}s at-mouth, "
            + (f"hours {cfg.drinking_start}-{cfg.drinking_end}, "
               if cfg.drinking_hours_enabled else "")
            + "reads live from Settings). Press Ctrl+C to stop."
        ))

        started_at = time.time()
        fps_warned = False
        try:
            while True:
                ok, frame = reader.read()
                if not ok:
                    if is_live:
                        time.sleep(0.02)  # reader has no frame yet
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

                if not cfg.drinking_enabled:
                    time.sleep(0.5)
                    continue

                # Ordinance hours, when configured: outside the window public
                # drinking isn't an offence, so don't accumulate toward one.
                if (cfg.drinking_hours_enabled and "hours" not in self.ablate
                        and not _within_window(timezone.localtime().time(),
                                               cfg.drinking_start, cfg.drinking_end)):
                    self.stats["skipped: outside ordinance hours"] += 1
                    time.sleep(0.5)
                    continue

                self.stats["frames"] += 1
                conf = self.conf_override or (cfg.drinking_confidence / 100)
                dwell_seconds = self.dwell_override or cfg.drinking_dwell

                persons, vessels, ids = self._detect_persons(frame)
                drinks = self._detect(frame, conf, persons=persons, vessels=vessels)

                tracks = tracker.update(persons, now_ts, ids=ids)
                per_track = tracker.assign(drinks, now_ts)

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

                if not fps_warned and self.stats["frames"] >= 30:
                    fps = self.stats["frames"] / max(now_ts - started_at, 1e-6)
                    if fps < 2:
                        fps_warned = True
                        self.stdout.write(self.style.WARNING(
                            f"Running at {fps:.1f} FPS — below ~2 FPS it takes "
                            f"{tracking.VOTE_MIN_FRAMES / fps:.0f}s just to confirm "
                            "a detection. Use fewer --tiles or --no-face-check."
                        ))

                if debug:
                    cv2.imshow("LookOut - watch_drinking (debug)", frame)
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
            "8s at 15 FPS is ~120)"
        )

    # ---- per-track temporal confirmation ----------------------------------

    def _process_track(self, track, dets, now_ts, dwell_seconds, cooldown,
                       frame, debug):
        """Votes, dwell-times and (maybe) alerts ONE track for this frame."""
        track.vote(dets, now_ts)
        active = bool(dets) if "vote" in self.ablate else track.accruing(now_ts)
        present_for = track.tick(now_ts, active)

        if not active:
            # Clear the dwell only when the person is visibly still there without
            # a bottle. If the track wasn't matched this frame they are out of
            # view, not innocent — hold the progress for the tombstone.
            if track.seen_at(now_ts) and now_ts - track.last_threat_seen > PRESENCE_GRACE_SECONDS:
                track.reset_dwell()
            return

        # A branded detection always outranks a generic vessel on the same
        # person: "Red Horse" is known alcohol, "bottle" merely might be.
        branded = [d for d in track.dets if d[5] not in GENERIC_LABELS]
        best = max(branded, key=lambda d: d[4]) if branded else track.best_detection()
        if best is None:
            return
        _, _, _, _, best_score, best_label = best
        is_generic = best_label in GENERIC_LABELS

        # Posture describes the object being alerted on, not the person in
        # general. Judging it from every detection would let a glass raised to
        # the mouth grant the consumption dwell to a bottle sitting at the hip.
        own = [d for d in dets if d[5] == best_label] or dets
        posture = self._posture(frame, track, own, now_ts)
        self.stats[f"posture:{posture}"] += 1

        if "dwell" in self.ablate:
            required = 0
        else:
            required = self._dwell_for(posture, dwell_seconds, is_generic)
            if required is None:
                self.stats[f"held back: generic vessel not raised:{best_label}"] += 1
                return

        if debug:
            for (x1, y1, x2, y2, score, label) in dets:
                color = (200, 80, 160) if present_for >= required else (0, 200, 0)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, f"{posture} {present_for:.0f}/{required:.0f}s",
                            (x1, max(y1 - 8, 0)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        if present_for < required:
            self.stats[f"held back: dwell not met:{posture}"] += 1
            return

        box = track.box or best[:4]
        if "cooldown" not in self.ablate:
            if track.in_cooldown(now_ts, cooldown):
                self.stats["suppressed: track cooldown"] += 1
                return
            if self._cooldown_blocks(box, now_ts, cooldown):
                self.stats["suppressed: recent alert at same spot"] += 1
                return

        who = track.display
        self.stats[f"ALERTS:{posture}"] += 1
        alert = self._create_alert(
            best_score, best_label, frame,
            description=(
                f"Public drinking detected: {best_label} ({posture}) on {who}, "
                f"present for {present_for:.0f}s on {self.camera.code} feed."
            ),
        )
        track.last_alerted_at = now_ts
        self._alert_log.append((tuple(box), now_ts))
        self.stdout.write(self.style.SUCCESS(
            (f"ALERT created: {alert.code}" if alert else "ALERT suppressed (dry run)")
            + f" ({best_label}, {posture}, {who}, held {present_for:.0f}s)"
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
        filename = f"{ts_label}_drinking_{safe_label}.jpg"
        cv2.imwrite(str(self.violations_dir / filename), frame)
        image_url = f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{filename}"

        if self.dry_run:
            return None

        return Alert.objects.create(
            type=self.drinking_type,
            status=Alert.Status.ACTIVE,
            camera=self.camera,
            timestamp=timezone.now(),
            confidence=score,
            description=description,
            image_url=image_url,
            suspect=label,
        )
