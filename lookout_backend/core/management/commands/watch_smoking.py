import datetime
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
from core.vision import recognition, scoring, tracking, ai_checker
from ._incidents import IncidentMixin

SMOKING_CAMERA_CODE = "CAM-SMOKE-01"
SETTINGS_REFRESH_SECONDS = 5  # re-poll SystemSettings this often, not every frame
PRESENCE_GRACE_SECONDS = 2    # tolerate a couple smoke-free frames before resetting dwell

# N-of-M temporal voting (per tracked person) turns low, flickery per-frame
# confidence into a stable signal before the dwell timer starts counting. The
# window is time-based, not frame-based — see tracking.VOTE_WINDOW_SECONDS for
# why a frame count meant something different in near vs far mode.

# Tracks die after tracking.TRACK_MAX_GAP seconds unseen and a new track starts
# with a fresh cooldown, so a person flickering out of the person detector would
# defeat alert_cooldown. This keeps the cooldown pinned to a place in the frame.
#
# Measured center-to-center and scaled by mean box size (tracking._center_proximity,
# same formula TOMBSTONE_MATCH_DIST uses) rather than IoU: IoU goes to zero the
# moment two boxes stop touching at all, which a person shifting position between
# alerts routinely does — especially over this check's full cooldown window (up
# to minutes), not just a single frame gap. A size-relative radius keeps "same
# spot" distance-aware (a near person's box spans more pixels for the same real
# shift than a far person's does) instead of requiring literal overlap.
# More generous than TOMBSTONE_MATCH_DIST (1.2) since this spans the whole
# cooldown, not just the ~10s tombstone gap — a person has more time to move.
COOLDOWN_CENTER_DIST = 1.5

# Ablation switches. Disabling one heuristic stage at a time lets the same
# footage be replayed with a single rule removed, so each rule's contribution to
# the true/false alert counts can be measured rather than asserted. Everything is
# ON by default; this exists for evaluation, not for production tuning.
# ("mouth" is the same switch as --no-mouth-check, exposed here for symmetry.)
ABLATABLE = ("class-floor", "mouth", "vote", "dwell", "cooldown", "puff", "preprocess",
             "pose", "scoring", "vlm")

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
MOUTH_ANCHORED_CLASSES = {"cigarette", "vape", "smoking"}
MOUTH_PROXIMITY = 2.5    # allowed distance from the mouth, in face widths

# How recently an at-the-mouth sighting must have happened for the `near_mouth`
# cue to still count at alert time. Matched to the vote window's order of
# magnitude: the cue describes this incident, not something seen a minute ago.
MOUTH_CUE_MAX_AGE = 5.0
# Minimum IoU for a pose box to be attributed to an existing person track.
POSE_MATCH_IOU = 0.3

# Puff-cycle rule: when --require-puff is on, an alert additionally needs the
# person to have shown the hand-to-mouth RHYTHM (cigarette raised to the lips and
# lowered) at least this many times. It makes smoking detection much more
# specific — a poster or someone merely holding a cigarette never produces the
# rhythm — but it needs a decent frame rate and a visible face to observe the
# motion, so it is OPT-IN, not the default.
PUFF_MIN_CYCLES = 1
# Spec v6 section 6: 1 puff 20, 2+ puffs 20, 3+ puffs within 5 minutes 15 -- all
# counted by the POSE hand-to-mouth counter (tracking.Track.update_gesture).
PUFF_PATTERN_COUNT = 3
# Path 2 (a puff but no item detected): for this long after the first gesture the
# cigarette detector runs on a native-resolution crop of that person's head and
# hands. Found -> the normal path; not found -> the puff is only logged.
HIRES_SECONDS = 4.0
HIRES_BOX_FRACTION = 0.6      # top share of the person box = head + hands region
# Pose runs on every processed frame by default; set to 2 to run it every other
# frame if the cost proves too high (the gesture counter is time-based).
POSE_EVERY_N_FRAMES = 1


def _is_mouth_anchored(label):
    return label.lower() in MOUTH_ANCHORED_CLASSES

# A pose pass costs ~35ms per person (insightface was ~150-400ms) — about 4x the person + smoking detectors
# combined — so running it every frame would drop the pipeline under 2 FPS with a
# single smoker in view. The anchor is cached per track and stored relative to
# the person box, so it re-projects as they move; it is only re-detected this
# often, which is frequent enough to follow someone turning their head.
MOUTH_CACHE_SECONDS = 1.0


class Command(IncidentMixin, BaseCommand):
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
        self.mouth_check = True
        self.require_puff = False
        self.pose = True
        self.cascade_extra = False     # add a native-res person-crop pass to --far (watch_all sets it)
        self.cascade = False
        self.far = True
        self.preprocess = False
        self.sharpen = False
        self.ablate = set()
        # Set only for a file source (see _run_stream) — lets _create_alert cut
        # a RAW evidence clip straight from the source instead of the sparser
        # annotated-frame buffer. Both stay None for webcam/RTSP sources.
        self._source_path = None
        self._video_pos_sec = None
        # Set for a live source in handle() (see watch_drinking.py's mirror of
        # this) — stays None for --image test mode and file sources, both of
        # which _create_alert's raw-clip fallback already guards for.
        self._raw_buffer = None

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
            default="bytetrack",
            choices=["greedy", "bytetrack", "botsort"],
            help="Person-association method. 'bytetrack' (default) uses "
                 "ultralytics' Kalman tracker, which holds identity across brief "
                 "gaps far better than greedy — but assumes a roughly steady "
                 "frame rate, which --far's tiling (~0.5-0.7 FPS) and file-source "
                 "replay routinely violate. Pass --tracker greedy to fall back "
                 "to the plain IoU/proximity matcher if a given camera's "
                 "footage benchmarks worse under bytetrack. Never used inside "
                 "watch_all (hardcoded to greedy there — shared YOLO model "
                 "state across its three interleaved detectors would corrupt "
                 "persist=True tracking).",
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
                 "(class floor / mouth rule / vote / dwell / cooldown) plus "
                 "effective FPS. Use this to tune the thresholds against real "
                 "footage instead of guessing.",
        )
        parser.add_argument(
            "--no-mouth-check", "--no-face-check",
            dest="no_mouth_check",
            action="store_true",
            help="Disable the mouth-proximity rule, accepting any cigarette/vape "
                 "found anywhere on a person's body. Faster (skips a pose pass "
                 "per smoker) and more permissive.",
        )
        parser.add_argument(
            "--pose",
            action="store_true",
            help="Deprecated / no-op: the pose hand-to-mouth counter is ALWAYS on "
                 "now (spec v6: puffs come from the pose gesture counter).",
        )
        parser.add_argument(
            "--no-pose",
            action="store_true",
            help="Turn the pose gesture counter off (ablation / speed tests only).",
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
        preproc.add_cli_flags(parser)
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
            defaults={"name": "Hikvision DS-2CD1047G2", "status": Camera.Status.ONLINE},
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

        # --no-mouth-check and --ablate mouth are the same switch.
        self.mouth_check = not options["no_mouth_check"] and "mouth" not in self.ablate
        self.require_puff = options["require_puff"] and "puff" not in self.ablate
        self.pose = not options.get("no_pose") and "pose" not in self.ablate

        # AI checker (spec v6 section 8): local Qwen3-VL, display-only, asynchronous.
        self._ai_setup(off="vlm" in self.ablate)

        if self.pose:
            self.stdout.write(
                "Pose gesture cue: ON (fifth model — expect a lower frame rate)"
            )

        # Must stay AFTER --ablate is parsed above, or 'preprocess' in --ablate
        # would be read against the empty default set and silently ignored.
        self.preprocess = options["preprocess"] and "preprocess" not in self.ablate
        self.sharpen = options["sharpen"]
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

    def _preprocess(self, frame):
        """Enhance a dim/noisy frame before detection (no-op unless --preprocess,
        and daytime frames bypass inside preprocess() itself)."""
        if not self.preprocess:
            return frame
        return preproc.preprocess(
            frame, mode="cascade" if self.cascade else "near", sharpen=self.sharpen,
        )

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

        Cached per track for MOUTH_CACHE_SECONDS and held relative to the person
        box, so a moving smoker keeps a valid anchor without paying for a pose
        pass every frame. Returns None when no mouth anchor could be found.
        """
        bx1, by1, bx2, by2 = track.box
        bw, bh = max(bx2 - bx1, 1), max(by2 - by1, 1)

        cached = track.mouth_anchor
        if cached is not None and now_ts - cached[3] < MOUTH_CACHE_SECONDS:
            rel_x, rel_y, rel_w, _ = cached
            self.stats["mouth rule: anchor cache hit"] += 1
            return bx1 + rel_x * bw, by1 + rel_y * bh, rel_w * bw

        # An unknown anchor (person facing away / too far) is also cached, briefly,
        # so pose is not re-run on the same track every frame just to fail again.
        if now_ts < track.mouth_miss_until:
            return None
        found = recognition.find_mouth_pose(frame, track.box)
        if found is None:
            track.mouth_miss_until = now_ts + recognition.MOUTH_MISS_CACHE_SECONDS
            return None
        mx, my, face_w = found
        track.mouth_anchor = ((mx - bx1) / bw, (my - by1) / bh, face_w / bw, now_ts)
        return mx, my, face_w

    def _apply_mouth_cue(self, frame, per_track, now_ts):
        """Item-at-the-mouth CUE (spec v6 section 6, +15): records how close a held
        smoking item is to the person's mouth, in face-widths.

        This used to be a hard REJECT of items far from the mouth. It no longer
        removes anything: a cigarette held at the side is still a cigarette (it
        reaches scoring and shows as Monitoring); only `near_mouth` is withheld.
        An unknown mouth anchor (person facing away / too far) is "unknown", never
        "not near".
        """
        if not self.mouth_check:
            return per_track

        for track, dets in per_track.items():
            if track.is_scene or not dets:
                continue
            if not any(_is_mouth_anchored(d[5]) for d in dets):
                continue

            anchor = self._mouth_anchor(frame, track, now_ts)
            if anchor is None:
                self.stats["mouth cue: no mouth anchor found"] += 1
                recognition.log_mouth(kind="smoking", t=now_ts, track=track.id, anchor=None)
                continue

            mx, my, face_w = anchor
            nearest_ratio = None   # closest item->mouth distance, in face-widths
            for d in dets:
                if not _is_mouth_anchored(d[5]):
                    continue
                cx, cy = (d[0] + d[2]) / 2, (d[1] + d[3]) / 2
                ratio = ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5 / max(face_w, 1)
                if nearest_ratio is None or ratio < nearest_ratio:
                    nearest_ratio = ratio
            recognition.log_mouth(kind="smoking", t=now_ts, track=track.id, anchor="pose",
                                  face_w=face_w, ratio=nearest_ratio, kept=len(dets), of=len(dets))
            if nearest_ratio is not None:
                # Stamped on the track so scoring can tell "at the mouth" from
                # "no anchor". Only the first is evidence.
                track.last_mouth_ratio = nearest_ratio
                track.last_mouth_seen = now_ts
                self.stats["mouth cue: near" if nearest_ratio <= MOUTH_PROXIMITY
                           else "mouth cue: away from mouth (kept)"] += 1
        return per_track

    def _hires_check(self, frame, per_track, now_ts):
        """Path 2 (spec v6 section 6): a puff but no cigarette detected.

        For HIRES_SECONDS after the first hand-to-mouth gesture, run the cigarette
        detector on a native-resolution crop of that person's head-and-hands
        region. Found -> the detection joins the normal path (item + puff = 60,
        Possible). Not found -> nothing changes: the puff is logged only.
        """
        for track, dets in per_track.items():
            if track.is_scene or track.box is None or now_ts >= track.hires_until or dets:
                continue
            x1, y1, x2, y2 = track.box
            top = (x1, y1, x2, int(y1 + (y2 - y1) * HIRES_BOX_FRACTION))
            try:
                found = recognition.detect_smoking_cascade(frame, [top], conf=0.30)
            except Exception as exc:                      # never let a check stop the loop
                self.stats[f"hires check unavailable: {type(exc).__name__}"] += 1
                track.hires_until = 0.0
                continue
            self.stats["hires check ran"] += 1
            if found:
                per_track[track] = list(found)
                self.stats["hires check: cigarette found"] += 1
        return per_track

    def _feed_pose(self, frame, tracks, now_ts):
        """Feed the POSE hand-to-mouth gesture into each track's rhythm state.

        This is what brings watch_smoking_pose's "Strong" gesture indicator into
        the production pipeline. It lived only in that standalone command, which
        watch_all does not run -- so in a real deployment the indicator scored
        nothing at all.

        Always on (spec v6: puffs come from this counter). It costs one
        full-frame pose pass per processed frame; POSE_EVERY_N_FRAMES = 2 halves
        that if needed, since the counter is time-based.

        Wrist and nose keypoints stay resolvable long after an 85mm cigarette
        becomes two pixels, so this is the only smoking indicator that reaches
        past object range.
        """
        if not self.pose or not tracks:
            return
        self._pose_tick = getattr(self, "_pose_tick", -1) + 1
        if POSE_EVERY_N_FRAMES > 1 and self._pose_tick % POSE_EVERY_N_FRAMES:
            return
        try:
            poses = recognition.detect_pose(frame)
        except Exception as exc:
            self.stats[f"pose unavailable: {type(exc).__name__}"] += 1
            self.pose = False   # don't retry every frame once it's broken
            self.stdout.write(self.style.WARNING(
                f"Pose model failed, continuing without the gesture cue: {exc}"
            ))
            return

        for box, kpts in poses:
            ratio = recognition.hand_to_mouth_ratio(kpts)
            if ratio is None:
                continue
            # Greedy IoU match, same association rule the rest of this command
            # uses. A pose with no matching person track is dropped rather than
            # creating one -- person detection owns track creation.
            best, best_iou = None, 0.0
            for track in tracks:
                if track.is_scene or track.box is None:
                    continue
                iou = recognition._iou(track.box, box)
                if iou > best_iou:
                    best, best_iou = track, iou
            if best is not None and best_iou >= POSE_MATCH_IOU:
                n = best.update_gesture(ratio, now_ts)
                if n:
                    self.stats[f"hand-to-mouth gestures (person #{best.id})"] = n
                # A NEW puff starts the high-resolution cigarette check (path 2).
                if n > getattr(best, "gestures_seen", 0):
                    best.hires_until = now_ts + HIRES_SECONDS
                best.gestures_seen = n

    def _detect_persons(self, frame):
        """Person boxes + (optionally) external track ids for the chosen tracker."""
        if self.tracker_name == "greedy":
            return recognition.detect_persons(frame), None
        return recognition.detect_persons_tracked(
            frame, tracker=f"{self.tracker_name}.yaml",
        )

    def _dwell_for(self, label, base_dwell):
        """Dwell seconds required for this class, scaled up for the ambiguous
        `smoke` class."""
        return base_dwell * self._policy(label)["dwell_scale"]

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
        if self.cascade_extra and not self.cascade and persons:
            extra = recognition.detect_smoking_cascade(
                frame, persons, conf=conf, crop_imgsz=recognition.NEAR_IMGSZ)
            if extra:
                dets = recognition._nms(list(dets) + extra)
        return self._apply_class_floors(dets, conf)

    # ---- single-image test mode -------------------------------------------

    def _run_image(self, path, conf):
        frame = recognition.load_image(path)
        if frame is None:
            self.stdout.write(self.style.ERROR(f"Could not read image: {path}"))
            return
        frame = self._preprocess(frame)

        smokes = self._detect(frame, conf)
        if not smokes:
            self.stdout.write(self.style.WARNING(
                f"No smoking detected above confidence {conf} (after per-class "
                "floors). Try lowering --confidence."
            ))
            return

        for (x1, y1, x2, y2, score, label) in smokes:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 165, 245), 2)
            recognition.draw_label(frame, f"{label} {score * 100:.0f}%",
                                   x1, max(y1 - 8, 0), (0, 165, 245))

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
        reader = recognition.LatestFrameReader(
            cap, open_fn=lambda: self._open_capture(source),
            log=lambda m: self.stdout.write(self.style.WARNING(m)),
        ) if is_live else cap
        # A file source is seekable, so raw evidence clips can be cut straight
        # from it later (see _create_alert) instead of relying only on the
        # annotated buffer's sparser processed frames.
        self._source_path = None if is_live else source
        # Live sources can't be seeked backwards, so a live source gets its own
        # rolling buffer of RAW (unannotated) frames to cut a raw clip from
        # instead (see RawFrameRecorder's docstring).
        self._raw_buffer = recognition.RawFrameRecorder() if is_live else None

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
            f"mouth check {'on' if self.mouth_check else 'off'}] "
            f"(dwell {self.dwell_override or cfg.smoking_dwell}s, "
            f"reads live from Settings). Press Ctrl+C to stop."
        ))

        if debug:
            cv2.namedWindow("LookOut - watch_smoking (debug)", cv2.WINDOW_NORMAL)

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

                # Buffer the frame RAW, before preprocessing or any drawing
                # touches it — see RawFrameRecorder.
                if self._raw_buffer is not None:
                    self._raw_buffer.add(frame, time.time())
                if self.ai_ring is not None:
                    self.ai_ring.stash(frame)     # clean pixels for the AI checker's crops

                # Enhance dim/noisy frames before detection (daytime bypasses).
                frame = self._preprocess(frame)

                wall_now = time.time()
                if wall_now - cfg_loaded_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_loaded_at = wall_now

                if not cfg.smoking_enabled:
                    time.sleep(0.5)
                    continue

                # Content-time clock: video position for a file source (not
                # wall clock) so vote/dwell/cooldown measure the same seconds
                # a human watching the clip would see, and a raw clip cut
                # later lines up with what the detector just saw — even
                # though processing routinely runs far slower than
                # real-time in --far mode. Wall-clock for a live source,
                # where video time and wall-clock time are the same thing
                # by definition. See the false-positive-suppression brief's
                # timing-bug finding: wall-clock badly overstated every
                # dwell/duration figure against an uploaded file.
                if self._source_path is not None:
                    self._video_pos_sec = reader.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                    now_ts = self._video_pos_sec
                else:
                    now_ts = wall_now

                self.stats["frames"] += 1
                conf = self.conf_override or (cfg.smoking_confidence / 100)
                dwell_seconds = self.dwell_override or cfg.smoking_dwell

                # Person detection runs every frame (it's the tracking anchor);
                # far mode reuses the same boxes for its person-crop pass.
                persons, ids = self._detect_persons(frame)
                smokes = self._detect(frame, conf, persons=persons)

                tracks = tracker.update(persons, now_ts, ids=ids)
                self._feed_pose(frame, tracks, now_ts)
                per_track = self._hires_check(
                    frame, tracker.assign(smokes, now_ts), now_ts)
                per_track = self._apply_mouth_cue(frame, per_track, now_ts)

                # Draw person boxes ALWAYS (not just in debug) so the evidence
                # clip and snapshot show the context, not only the debug window.
                for t in tracks:
                    x1, y1, x2, y2 = t.box
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (180, 180, 180), 1)
                    recognition.draw_label(frame, f"person #{t.id}", x1, max(y1 - 6, 0),
                                           (180, 180, 180), scale=0.6)

                for track, dets in per_track.items():
                    self._process_track(
                        track, dets, now_ts, dwell_seconds, cfg.alert_cooldown,
                        frame, debug,
                    )

                # Buffer this annotated frame for the evidence clip.
                self.clip.add(frame, now_ts)
                self._incident_gc(now_ts, frame)   # close incidents whose object is gone

                # Confirmation is time-based, but VOTE_MIN_FRAMES still needs a
                # few frames to land inside the window — below ~2 FPS that floor,
                # not the dwell, is what decides how fast anything can alert.
                if not fps_warned and self.stats["frames"] >= 30:
                    fps = self.stats["frames"] / max(wall_now - started_at, 1e-6)
                    if fps < 2:
                        fps_warned = True
                        self.stdout.write(self.style.WARNING(
                            f"Running at {fps:.1f} FPS — below ~2 FPS it takes "
                            f"{tracking.VOTE_MIN_FRAMES / fps:.0f}s just to confirm "
                            "a detection. Use fewer --tiles, a smaller frame, or "
                            "--no-mouth-check."
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
        """Scores ONE person's smoking evidence for this frame and keeps their
        incident (Monitoring -> Possible -> Likely) up to date.

        Object cue: the smoking item held long enough (vote + dwell; momentum
        replaces this gate in the next phase). Pose cues: the hand-to-mouth
        counter (1 puff, 2+ puffs, 3+ within 5 minutes) -- these need no item,
        which is what makes the capped puff-only path possible.
        """
        # Smoking is committed by a person. An unattributed ("scene") detection
        # (steam, exhaust, cooking) has no person behind it and is discarded.
        if track.is_scene:
            self.stats["discarded: no person (scene)"] += 1
            return

        track.vote(dets, now_ts)       # keeps the label history that track.dets draws from
        # Object cue: momentum per (track, class) -- replaces the vote + dwell gate.
        cue = self._object_cue(track, dets, now_ts)
        object_on, best_label, best_score = cue.on, cue.label, cue.conf
        self._draw_object_cue(frame, track, dets, cue, color_on=(0, 165, 245))
        if not object_on and dets:
            self.stats["held back: momentum below ON"] += 1

        # --- the cues (spec v6 section 6) ---
        puffs = track.gesture_count(now_ts)          # pose counter, 5-minute window
        cues = set()
        if object_on:
            cues.add("cigarette")
            if self._near_mouth(track, now_ts):
                cues.add("near_mouth")
        if puffs >= 1:
            cues.add("gesture")
        if puffs >= 2:
            cues.add("puffs")
        if puffs >= PUFF_PATTERN_COUNT:
            cues.add("puff_pattern")

        key = ("smoke", track.id)
        score = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, cues,
                              previous_level=self._incident_level(key), object_on=object_on)
        if not score.stored:
            if puffs and not object_on:
                self.stats[f"puffs logged only ({puffs})"] += 1
            self._incident_sync(key, score, now_ts, create=None)   # closes an open incident's state
            return

        box = track.box
        summary = best_label or ", ".join(sorted({s[5] for s in track.dets})) or "hand-to-mouth movement"
        who = track.display

        def describe(sc):
            text = (f"Public smoking: {summary} on {who}, {puffs} puff(s) seen, "
                    f"status {scoring.label_of(sc.level)} on {self.camera.code} feed.")
            return f"{text} {sc.tag}." if sc.tag else text

        def create(level, with_clip):
            return self._create_alert(
                score.score, best_label or "Puff-only", frame, description=describe(score),
                box=box, now=now_ts, score_obj=score, object_confidence=best_score,
                with_clip=with_clip)

        self._incident_sync(
            key, score, now_ts, create=create, describe=describe, frame=frame, box=box,
            blocked=lambda: "cooldown" not in self.ablate and self._cooldown_blocks(box, now_ts, cooldown),
            ai={"kind": "smoking", "keys": [("track", track.id)],
                "note": ai_checker.system_note("smoking", puff_only=score.puff_only, puffs=puffs)},
            extra_cues={"puffs_seen": puffs, "momentum": cue.snapshot,
                        "object_confidence": None if best_score is None else round(best_score, 3)},
        )

    def _near_mouth(self, track, now_ts):
        """True when this track had a face-anchored object AT the mouth recently.

        Deliberately not "the mouth rule didn't reject it" -- a detection kept
        because no face could be resolved is not evidence that anything was
        near a mouth.
        """
        seen = getattr(track, "last_mouth_seen", None)
        ratio = getattr(track, "last_mouth_ratio", None)
        if seen is None or ratio is None:
            return False
        return (now_ts - seen) <= MOUTH_CUE_MAX_AGE and ratio <= MOUTH_PROXIMITY

    def _cooldown_blocks(self, box, now, cooldown):
        """True if we already alerted near roughly this spot inside the
        cooldown, regardless of which track id it was at the time."""
        self._alert_log = [
            (b, ts) for b, ts in self._alert_log if now - ts < cooldown
        ]
        return any(tracking._center_proximity(box, b, max_frac=COOLDOWN_CENTER_DIST) > 0
                   for b, _ in self._alert_log)

    # ---- shared alert creation --------------------------------------------


    def _save_clips(self, base, frame, now):
        """Write (or rewrite) the annotated and raw evidence clips named `base`.
        Returns (video_url, raw_video_url); either may be empty on failure."""
        # Write the ~10s evidence clip (annotated frames leading up to the alert).
        video_url = ""
        clip = getattr(self, "clip", None)
        if clip is not None:
            # Add the fully-annotated current frame (with colored alert box) to
            # the buffer at the moment the alert fires, so the evidence clip
            # includes it. MUST be the same clock the caller's been using for
            # every other frame added this run (`now`, passed in by
            # _process_track) — ClipRecorder.add() trims its buffer by
            # comparing timestamps, so mixing wall-clock time.time() in here
            # against a run using video-position time (file sources, see the
            # timing-bug fix) makes this one frame look tens of years newer
            # than everything already buffered, which the cutoff logic reads
            # as "evict all of it." That's the exact regression this
            # parameter fixes: default only covers a caller with no timeline
            # of its own (--image test mode, which never touches self.clip
            # anyway).
            self.clip.add(frame, now if now is not None else time.time())
            video_name = f"{base}.mp4"
            if clip.save(self.violations_dir / video_name):
                video_url = violation_media_path(video_name)

        # RAW (unannotated, full source frame rate/resolution) clip. File
        # sources cut straight from the source file (best quality, real fps).
        # Live sources can't be seeked, so they fall back to the rolling
        # RawFrameRecorder buffer of raw frames instead (see its docstring).
        raw_video_url = ""
        raw_name = f"{base}_raw.mp4"
        raw_path = self.violations_dir / raw_name
        if self._source_path is not None and self._video_pos_sec is not None:
            start = max(0.0, self._video_pos_sec - recognition.RAW_CLIP_PRE_SECONDS)
            duration = recognition.RAW_CLIP_PRE_SECONDS + recognition.RAW_CLIP_POST_SECONDS
            cut_ok = recognition.cut_raw_clip(self._source_path, start, duration, raw_path)
        elif self._raw_buffer is not None:
            cut_ok = self._raw_buffer.save(raw_path)
        else:
            cut_ok = False
        if cut_ok:
            raw_video_url = violation_media_path(raw_name)
            self.stdout.write(self.style.SUCCESS(f"  raw clip: {raw_name}"))
        else:
            self.stdout.write(self.style.WARNING(
                "  raw clip not produced (see ffmpeg log above if one was attempted)"
            ))
        return video_url, raw_video_url

    def _create_alert(self, score, label, frame, description, box=None,
                      now=None, score_obj=None, object_confidence=None, with_clip=True):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{ts_label}_smoking_{label}"
        self._last_clip_base = base
        filename = f"{base}.jpg"
        cv2.imwrite(str(self.violations_dir / filename), frame)
        image_url = violation_media_path(filename)

        # A Monitoring event gets ONE image and no clip; the clip is written when
        # the event reaches Possible / Likely (and refreshed while it continues).
        video_url = raw_video_url = ""
        if with_clip:
            video_url, raw_video_url = self._save_clips(base, frame, now)

        # Optional run log ($LOOKOUT_MOUTH_LOG): what fired and with which cues,
        # even in --dry-run, so before/after comparisons need no database rows.
        recognition.log_mouth(kind="alert", engine="smoking", label=label, score=score,
                              level=getattr(score_obj, "level", ""),
                              cues=sorted(getattr(score_obj, "cues", None) or []))
        if self.dry_run:
            return None

        return Alert.objects.create(
            type=self.smoking_type,
            status=Alert.Status.ACTIVE,
            camera=self.camera,
            timestamp=timezone.now(),
            # Now the weighted violation likelihood, not the YOLO box score.
            confidence=score,
            description=description,
            image_url=image_url,
            video_url=video_url,
            raw_video_url=raw_video_url,
            last_seen_at=timezone.now(),
            suspect=label,
            level=score_obj.level if score_obj is not None else "",
            cues=score_obj.as_dict() if score_obj is not None else {},
            # The DETECTOR's own confidence in the anchoring box, kept apart
            # from `confidence` (the violation likelihood). See Alert.object_confidence.
            object_confidence=object_confidence,
        )
