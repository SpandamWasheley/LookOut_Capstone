import datetime
import json
import math
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
from core.vision import recognition, theft, tracking

THIEF_CAMERA_CODE = "CAM-THIEF"
SETTINGS_REFRESH_SECONDS = 5  # re-poll SystemSettings this often, not every frame
# Tolerate this many clean seconds before resetting dwell. Must stay ABOVE
# knife's stale_scale-derived accrual window (currently 0.5*6.0 = 3.0s — see
# CLASS_POLICY below) or the two-tier "pause without resetting, THEN reset"
# design collapses into one abrupt cutoff: accruing() would already report
# not-active by the time this grace check ever fires, making it redundant.
# Bumped from 2.0 -> 4.0 alongside that override for exactly this reason.
PRESENCE_GRACE_SECONDS = 4

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
#
# ratio_scale/stale_scale multiply tracking.VOTE_MIN_RATIO/ACCRUAL_STALE_SECONDS
# the same way conf_scale/dwell_scale multiply the dashboard's confidence/dwell
# — a per-class override of the shared vote-gate defaults, applied in
# _process_track via track.accruing(target_label=...), so loosening it for one
# class never touches the globals watch_smoking/watch_drinking's own Tracks
# still read at their un-overridden defaults.
#
# knife specifically: measured on real holdup footage (see the calibration
# writeup), a person genuinely holding a visible knife the whole time still
# only produces a raw per-frame hit on 0.3-8% of frames — the object is small,
# handheld, and often motion-blurred or partially occluded by the person's own
# grip. At the shared defaults (ratio 0.4, stale 0.5s) that NEVER clears the
# vote window regardless of how long the person is tracked, and even fully
# disabling the ratio still can't accrue 3s of dwell, because 0.5s is stricter
# than the typical gap between two real knife hits. ratio_scale 0.25 (0.4 ->
# 0.1) and stale_scale 6.0 (0.5s -> 3.0s) were chosen as the smallest loosening
# that let a real incident's dwell clear the bar; checked against
# calibration_null_2026-09-07.csv (footage with no knife at all) replayed
# through the identical vote+dwell state machine, this costs exactly one
# additional false-positive track surviving to a full alert (3/17 vs the
# 2/17 that already survive today at the shared defaults) — not zero, but far
# from the ~374 raw phantom detections the vote gate exists to suppress in the
# first place. Gun is left at the shared defaults: it's a comparably compact,
# well-localised object and hasn't shown the same recall gap.
CLASS_POLICY = {
    "gun":              {"conf_scale": 1.0, "dwell_scale": 1.0, "ratio_scale": 1.0,  "stale_scale": 1.0},
    "knife":            {"conf_scale": 1.0, "dwell_scale": 1.0, "ratio_scale": 0.25, "stale_scale": 6.0},
    "robbery activity": {"conf_scale": 1.6, "dwell_scale": 2.0, "ratio_scale": 1.0,  "stale_scale": 1.0},
    "stealing":         {"conf_scale": 1.6, "dwell_scale": 2.0, "ratio_scale": 1.0,  "stale_scale": 1.0},
}
DEFAULT_POLICY = {"conf_scale": 1.0, "dwell_scale": 1.0, "ratio_scale": 1.0, "stale_scale": 1.0}

# Spatial gating for weapon classes on a person: no rule anywhere previously
# constrained WHERE on a person a weapon detection could sit, so a box drawn
# around someone's head/shoulders counted exactly the same as one at their
# hand — geometrically impossible for a knife, but nothing rejected it.
# Mirrors watch_smoking._apply_face_rule's placement: gated on the vote
# input (per_track), before track.vote()/tick() ever sees the detection, so
# a geometrically implausible box can't build dwell at all — not just a
# check at the moment of alerting.
SPATIALLY_GATED_CLASSES = {"knife"}
# A held knife is a hand/forearm-level object — the top ~15% of a
# person's box (head only) is anatomically off-limits for one. Chest-height
# holds (0.30 previously) are geometrically plausible and were being cut.
KNIFE_HEAD_EXCLUSION_FRAC = 0.15
# A knife spanning more than a quarter of the person's OWN height is not
# knife-sized relative to that person — more likely a mis-localized box
# (forearm, sleeve, shadow) than an actual blade.
KNIFE_MAX_HEIGHT_FRAC = 0.25
# A knife box that isn't actually near a HAND is not "this person is holding
# a knife" no matter how confident or how plausibly placed vertically —
# neither the head/shoulders nor the too-large check catches this, since both
# only look at the box's OWN geometry, never whether it's anywhere near where
# a held object would actually be.
#
# Two rectangle-only approaches were tried and rejected here first: excluding
# the head/shoulders band alone left the whole rest of the person's own
# bounding rectangle open, and requiring the knife box to sit 70%+ inside
# that rectangle (checked in an earlier revision of this file) still passed a
# knife box floating in the empty space beside a person's torso or bag —
# real footage on Aug24_16 - TrimHoldupBldg.mp4 scored that case 1.0 (fully
# "contained") despite sitting nowhere near her body. A rectangle spanning
# head-to-feet has a lot of empty space in it; "inside the rectangle" and "on
# the person" are different claims. The actual constraint - a held knife
# has to be near a HAND - needs a hand, not a box.
#
# recognition.detect_pose() already exists for exactly this in
# watch_smoking_pose.py, unused everywhere else in a `watch_merged` run
# (watch_merged.py drives this class's own _apply_weapon_region_rule() via
# its shared per-frame detection pass, never through this file's own
# _run_stream/_detect_scene). Called here lazily, once per frame and only
# when a spatially-gated class is actually present that frame, at ~40ms/call
# measured against detect_persons' own ~49ms/call on this project's own
# footage - not free, but far cheaper than the ~190ms a single tiled
# detection pass already costs in this pipeline.
KNIFE_MAX_WRIST_DIST_FRAC = 0.22


def _is_spatially_gated(label):
    return label.lower() in SPATIALLY_GATED_CLASSES


def _iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    return inter / ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)


def _confident_wrists(person_box, poses):
    """(x, y) of each confidently-visible wrist keypoint (see
    recognition.KP_MIN_CONF) on whichever pose detection best matches
    `person_box` — pose runs its own independent person detector, so this
    can't just reuse the tracker's own identity. Empty list (not a fallback
    to something looser) when no pose matched or neither wrist resolved:
    an unresolvable case is rejected outright, not waved through — see
    KNIFE_MAX_WRIST_DIST_FRAC's caller."""
    best_kpts, best_iou = None, 0.2
    for pbox, kpts in poses:
        iou = _iou(person_box, pbox)
        if iou > best_iou:
            best_kpts, best_iou = kpts, iou
    if best_kpts is None:
        return []
    wrists = []
    for idx in (recognition.KP_LWRIST, recognition.KP_RWRIST):
        x, y, c = best_kpts[idx]
        if c >= recognition.KP_MIN_CONF:
            wrists.append((float(x), float(y)))
    return wrists


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
ABLATABLE = (
    ("class-floor", "spatial", "vote", "dwell", "cooldown", "preprocess", "layer-e")
    # Layer E is ablatable per RULE as well as wholesale: E.9 of the spec calls
    # for per-cue removal so each weight can be revised against measured
    # precision instead of asserted. `--ablate e9` drops just the custody cue.
    + tuple(f"e{i}" for i in range(1, 30))
)

# Local hours the nocturnal amplifier (E20) applies to. Wraps midnight.
NIGHT_START, NIGHT_END = datetime.time(22, 0), datetime.time(5, 0)


def _is_night(now_dt):
    """E20 — 22:00 to 05:00 local time."""
    t = now_dt.time()
    return t >= NIGHT_START or t < NIGHT_END


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
        self.preprocess = False
        self.sharpen = False
        self.ablate = set()
        self.layer_e = True
        self.layer_e_only = False
        self.weapon_alone_alerts = False
        self.observe_log = None
        self.engine = None
        # Set only for a file source (see _run_stream) — lets _create_alert cut
        # a RAW evidence clip straight from the source instead of the sparser
        # annotated-frame buffer. Both stay None for webcam/RTSP sources.
        self._source_path = None
        self._video_pos_sec = None
        # Set for a live source in _run_stream — stays None for --image test
        # mode and file sources, both of which _create_alert's raw-clip
        # fallback already guards for. Mirrors watch_smoking/watch_drinking.
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
            help="Evaluation only: comma-separated stages to DISABLE — "
                 "class-floor/vote/dwell/cooldown/preprocess/layer-e, or an "
                 "individual Layer E rule (e6, e9, e14, e23 ...). Replay one "
                 "recording per setting and compare the alert counts to measure "
                 "what each rule contributes. Combine with --dry-run and --stats.",
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
        preproc.add_cli_flags(parser)
        parser.add_argument(
            "--no-layer-e",
            action="store_true",
            help="Disable the Layer E theft pattern rules (E1-E29) and run only "
                 "the per-class detection gate. Same as --ablate layer-e.",
        )
        parser.add_argument(
            "--layer-e-only",
            action="store_true",
            help="Make Layer E the ONLY decision path, per the spec's "
                 "implementation binding ('the scoring function replaces the "
                 "current conjunctive gate'). NOTE: with the written weights a "
                 "lone weapon scores 0.45, below the 0.55 alert band, so a gun "
                 "with no other cue stops raising alerts — pair this with "
                 "--weapon-alone-alerts if that is not what you want.",
        )
        parser.add_argument(
            "--weapon-alone-alerts",
            action="store_true",
            help="Promote any evidence containing the weapon cue (E14) to the "
                 "Candidate band regardless of score. Resolves the spec's own "
                 "conflict between E14's weight (0.45) and its stated rationale "
                 "('sufficient alone to reach the alert band') in favour of the "
                 "rationale.",
        )
        parser.add_argument(
            "--observe-log",
            default=None,
            help="Append Observe-band evidence (E29: 0.35-0.55) to this file as "
                 "JSON lines, with the full cue vector. This is the ablation "
                 "store the spec calibrates the threshold against after the "
                 "field shoot — near-misses accumulate instead of being lost.",
        )

    def handle(self, *args, **options):
        # ViolationType/Camera aren't created by any migration, so get_or_create
        # here self-heals a fresh DB the same way watch_curfew/watch_smoking do.
        # code="theft" (not "thief") — this used to create a SECOND row
        # alongside seed_demo.py's "theft", splitting alerts/citations across
        # two ViolationTypes. See migration 0026 for the one-time merge of
        # whatever already landed on the old "thief" row.
        self.thief_type, _ = ViolationType.objects.get_or_create(
            code="theft",
            defaults={"label": "Holdup in Public Area", "color": "#ef4444", "icon": "siren"},
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
        # Kept after --ablate is parsed, so 'preprocess' in --ablate is honoured.
        self.preprocess = options["preprocess"] and "preprocess" not in self.ablate
        self.sharpen = options["sharpen"]

        # --- Layer E ---
        self.layer_e = not options["no_layer_e"] and "layer-e" not in self.ablate
        self.layer_e_only = options["layer_e_only"] and self.layer_e
        self.weapon_alone_alerts = options["weapon_alone_alerts"]
        self.observe_log = options["observe_log"]
        if self.layer_e:
            # The engine shares self.stats, so --stats reports Layer E's
            # suppressions and cue counts in the same table as the legacy gate's.
            self.engine = theft.TheftEngine(
                ablate=self.ablate, stats=self.stats,
                weapon_alone_alerts=self.weapon_alone_alerts,
            )
        if self.layer_e_only:
            self.stdout.write(self.style.WARNING(
                "LAYER E ONLY: the per-class dwell gate is off. A lone weapon "
                f"scores {theft.WEIGHTS['E14']:.2f}, under the "
                f"{theft.SCORE_ALERT:.2f} alert band, so it will land in "
                "Observe rather than alerting unless --weapon-alone-alerts is set."
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
            if self.layer_e:
                self.stdout.write(self.style.WARNING(
                    "--image runs the per-class gate only: every Layer E rule is "
                    "temporal (approach, dwell, freeze, custody change), so a "
                    "single still frame cannot satisfy any of them."
                ))
            conf = self.conf_override or (cfg.thief_confidence / 100)
            self._run_image(options["image"], conf)
        else:
            self._run_stream(options["source"], options["debug"])

    # ---- per-class policy --------------------------------------------------

    def _preprocess(self, frame):
        """Enhance a dim/noisy frame before detection (no-op unless --preprocess,
        and daytime frames bypass inside preprocess() itself)."""
        if not self.preprocess:
            return frame
        return preproc.preprocess(frame, mode="near", sharpen=self.sharpen)

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

    def _apply_weapon_region_rule(self, per_track, frame):
        """Rejects a spatially-gated weapon detection (currently: knife) that
        sits in the person's head/shoulders region, is too large relative to
        that person's own box to plausibly be that object, or isn't actually
        near a HAND (see KNIFE_MAX_WRIST_DIST_FRAC).

        Mirrors watch_smoking._apply_face_rule's placement exactly: this runs
        on `per_track` right after tracker.assign(), so a rejected detection
        never reaches track.vote()/tick() at all — it can't build dwell, not
        just get blocked at the final alert check. Unlike the face rule,
        there's no "keep it if we can't tell" fallback for the wrist check:
        a frame where neither wrist resolves confidently is rejected, not
        waved through — an unresolvable case is exactly the kind of thing
        that turned into a floating false positive before this rule existed.
        """
        if "spatial" in self.ablate:
            return per_track

        # Lazy and shared across every track this frame: pose only costs
        # anything (~40ms, see KNIFE_MAX_WRIST_DIST_FRAC's comment) on a
        # frame where a spatially-gated class was actually detected, and one
        # call covers every person in frame, not one call per track.
        poses = None

        for track, dets in per_track.items():
            if track.is_scene or not dets:
                continue
            if not any(_is_spatially_gated(d[5]) for d in dets):
                continue

            px1, py1, px2, py2 = track.box
            person_height = max(py2 - py1, 1)
            head_boundary = py1 + KNIFE_HEAD_EXCLUSION_FRAC * person_height
            max_height = KNIFE_MAX_HEIGHT_FRAC * person_height

            if poses is None:
                poses = recognition.detect_pose(frame)
            wrists = _confident_wrists(track.box, poses)

            kept = []
            for d in dets:
                x1, y1, x2, y2, score, label = d
                if not _is_spatially_gated(label):
                    kept.append(d)
                    continue
                cy = (y1 + y2) / 2
                if cy < head_boundary:
                    self.stats[f"cut by spatial rule (head/shoulders):{label}"] += 1
                    continue
                if (y2 - y1) > max_height:
                    self.stats[f"cut by spatial rule (too large):{label}"] += 1
                    continue
                if not wrists:
                    self.stats[f"cut by spatial rule (no confident wrist):{label}"] += 1
                    continue
                kcx, kcy = (x1 + x2) / 2, (y1 + y2) / 2
                dist = min(math.hypot(kcx - wx, kcy - wy) for wx, wy in wrists)
                if dist / person_height > KNIFE_MAX_WRIST_DIST_FRAC:
                    self.stats[f"cut by spatial rule (far from wrist):{label}"] += 1
                    continue
                kept.append(d)
            per_track[track] = kept
        return per_track

    def _dwell_for(self, label, base_dwell):
        """Dwell seconds required for this class, scaled up for the weak
        pose-like classes."""
        return base_dwell * self._policy(label)["dwell_scale"]

    # ---- detection dispatch -----------------------------------------------

    def _detect_persons(self, frame):
        """Person boxes + (optionally) external track ids for the chosen tracker."""
        persons, ids, _, _ = self._detect_scene(frame)
        return persons, ids

    def _detect_scene(self, frame):
        """(persons, ids, carriables, vehicles) from ONE YOLO pass.

        Layer E needs the bags people carry (E4/E9/E21/E22) and the vehicles they
        park (E5/E15-E19) in addition to the person boxes. They are extra class
        filters on the person pass that runs every frame anyway, so the whole
        Layer E object layer costs no additional inference.
        """
        if self.tracker_name == "greedy":
            persons, ids, carriables, vehicles = recognition.detect_scene(frame)
            return persons, None, carriables, vehicles
        return recognition.detect_scene_tracked(
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
        frame = self._preprocess(frame)

        threats = self._detect(frame, conf)
        if not threats:
            self.stdout.write(self.style.WARNING(
                f"No theft/robbery indicators detected above confidence {conf} "
                "(after per-class floors). Try lowering --confidence."
            ))
            return

        for (x1, y1, x2, y2, score, label) in threats:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 220), 2)
            recognition.draw_label(frame, f"{label} {score * 100:.0f}%",
                                   x1, max(y1 - 8, 0), (0, 0, 220))

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
        # A file source is seekable, so raw evidence clips can be cut straight
        # from it later (see _create_alert) instead of relying only on the
        # annotated buffer's sparser processed frames.
        self._source_path = None if is_live else source
        # Live sources can't be seeked backwards, and record_camera's segments
        # aren't safely readable while the current one is still open (see
        # RawFrameRecorder's docstring) — so a live source gets its own rolling
        # buffer of RAW (unannotated) frames to cut a raw clip from instead.
        self._raw_buffer = recognition.RawFrameRecorder() if is_live else None

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

        # Rolling 30-second buffer of annotated frames — on an alert it's
        # written out as the evidence clip, so the card shows the weapon/pose
        # being detected with its box, not just a still. Mirrors
        # watch_smoking/watch_drinking's own ClipRecorder.
        self.clip = recognition.ClipRecorder(seconds=30, label=self.camera.code)

        mode = f"FAR {self.tiles[0]}x{self.tiles[1]} tiling + person-crop" if self.far else "near"
        layer_e = "off"
        if self.layer_e:
            layer_e = "ONLY" if self.layer_e_only else "on"
        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} for theft/robbery indicators "
            f"[{mode} mode, {self.tracker_name} tracker, Layer E {layer_e}] "
            f"(dwell {self.dwell_override or cfg.thief_dwell}s, "
            f"reads live from Settings). Press Ctrl+C to stop."
        ))
        if self.layer_e:
            self.stdout.write(
                "Layer E watches four patterns — snatch (E6-E9), holdup "
                "(E10-E14), carnapping of two-wheelers (E15-E20) and unattended "
                "property (E21-E22). Anchors need time to register: a vehicle "
                f"must sit still {theft.ANCHOR_STATIC_SECONDS}s and then be "
                f"alone {theft.ABSENCE_SECONDS}s before carnapping rules "
                "evaluate at all."
            )

        if debug:
            cv2.namedWindow("LookOut - watch_thief (debug)", cv2.WINDOW_NORMAL)

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

                # Enhance dim/noisy frames before detection (daytime bypasses).
                frame = self._preprocess(frame)

                wall_now = time.time()
                if wall_now - cfg_loaded_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_loaded_at = wall_now

                if not cfg.thief_enabled:
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
                conf = self.conf_override or (cfg.thief_confidence / 100)
                dwell_seconds = self.dwell_override or cfg.thief_dwell

                # Person detection runs every frame (it's the tracking anchor);
                # far mode reuses the same boxes for its person-crop pass. The
                # same pass yields the carriables and vehicles Layer E needs.
                persons, ids, carriables, vehicles = self._detect_scene(frame)
                threats = self._detect(frame, conf, persons=persons)

                # Raw model output ALWAYS, thin yellow, before spatial/vote/dwell
                # gating touches it — so a detection that gets cut (spatial rule)
                # or never confirmed (vote/dwell) is still visible for sanity
                # checking, not just the green/red confirmed boxes below.
                for (x1, y1, x2, y2, score, label) in threats:
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 220), 1)
                    recognition.draw_label(frame, f"{label} {score * 100:.0f}%",
                                           x1, max(y1 - 8, 0), (0, 220, 220), scale=0.5)

                # frame.shape feeds the E25 edge-truncation guard: a box clipped
                # by the frame border has a wrong centroid and height, which
                # corrupts every normalized quantity in E1-E3.
                tracks = tracker.update(persons, now_ts, ids=ids,
                                        frame_shape=frame.shape)
                per_track = tracker.assign(threats, now_ts)
                per_track = self._apply_weapon_region_rule(per_track, frame)

                # Draw person boxes ALWAYS (not just in debug) so the evidence
                # clip shows the context, not only the debug window.
                for t in tracks:
                    x1, y1, x2, y2 = t.box
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (180, 180, 180), 1)
                    recognition.draw_label(frame, f"person #{t.id}", x1, max(y1 - 6, 0),
                                           (180, 180, 180), scale=0.6)

                # Layer E: pattern rules over the tracks, scored and banded.
                if self.layer_e:
                    evidence = self.engine.update(
                        tracks, carriables, vehicles, threats, now_ts,
                        is_night=_is_night(datetime.datetime.now()),
                    )
                    for ev in evidence:
                        self._handle_evidence(ev, frame, now_ts,
                                              cfg.alert_cooldown, debug)

                # The per-class dwell gate. Layer E replaces it under
                # --layer-e-only; by default both run, so a confirmed weapon or
                # action still alerts on its own terms while Layer E adds the
                # pattern-based candidates on top.
                if not self.layer_e_only:
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
                    fps = self.stats["frames"] / max(wall_now - started_at, 1e-6)
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
        # Theft is committed by a person by definition. An unattributed
        # ("scene") detection has no person box behind it at all — a knife on
        # a counter, a poster, a kitchen drawer — and no dwell length makes it
        # actionable (Alert.suspect would name a weapon with nobody to hold
        # accountable). Matches watch_smoking/watch_drinking's identical
        # scene-track discard.
        if track.is_scene:
            self.stats["discarded: no person (scene)"] += 1
            return

        track.vote(dets, now_ts)

        # Which class's confirmation policy applies — the one most represented
        # in the window so far, same resolution best_detection() below uses to
        # pick what to REPORT, resolved here too since a per-class vote/dwell
        # override (CLASS_POLICY's ratio_scale/stale_scale — currently just
        # knife, see its comment) has to be known before the active/accruing
        # check, not after. None when the window is still empty; _policy()
        # falls back to DEFAULT_POLICY (scale 1.0, i.e. the shared default)
        # for that and for any class without its own override.
        label_votes = track.label_votes()
        policy_label = max(label_votes, key=label_votes.get) if label_votes else None
        policy = self._policy(policy_label)
        vote_ratio = tracking.VOTE_MIN_RATIO * policy["ratio_scale"]
        accrual_stale = tracking.ACCRUAL_STALE_SECONDS * policy["stale_scale"]

        # Ablating the vote removes temporal confirmation entirely: a detection
        # in THIS frame is taken at face value, which is the no-heuristics
        # baseline the evaluation compares against.
        active = bool(dets) if "vote" in self.ablate else track.accruing(
            now_ts, min_ratio=vote_ratio, stale_seconds=accrual_stale,
            target_label=policy_label,
        )
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
            best_label, dwell_seconds,
        )

        # Draw the violation boxes ALWAYS (green while building, red once the
        # dwell is met) — not just in debug — so the evidence clip shows the
        # weapon/pose being detected. Matches watch_smoking/watch_drinking.
        # `dets` is only THIS frame's detections, but active/present_for can
        # still be confirmed on a frame with none at all (track.accruing()
        # tolerates brief flicker within the vote window) — draw the
        # track's last KNOWN detections instead so the clip has something to
        # show for a dwell/alert that built up across a gap, dashed and
        # dimmed to mark it as historical, not live this frame.
        draw_dets = dets if dets else track.dets
        is_historical = not dets and bool(track.dets)
        for (x1, y1, x2, y2, score, label) in draw_dets:
            color = (0, 0, 220) if present_for >= required else (0, 200, 0)
            label_text = f"{label} {score * 100:.0f}% {present_for:.0f}/{required:.0f}s"
            if is_historical:
                color = tuple(c // 2 for c in color)
                recognition.draw_dashed_rect(frame, (x1, y1), (x2, y2), color, 2)
                label_text += " (last seen)"
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            recognition.draw_label(frame, label_text, x1, max(y1 - 8, 0), color)

        if present_for < required:
            self.stats[f"held back: dwell not met:{best_label}"] += 1
            return

        box = track.box or best[:4]
        if "cooldown" not in self.ablate:
            # Phase C: a cooldown timing out is not the same thing as this
            # incident ending. See tracking.Track.has_alerted.
            if track.has_alerted:
                self.stats["suppressed: track already alerted (same incident)"] += 1
                return
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
            now=now_ts,
        )
        track.last_alerted_at = now_ts
        track.has_alerted = True
        self._alert_log.append((tuple(box), now_ts))
        self.stdout.write(self.style.SUCCESS(
            (f"ALERT created: {alert.code}" if alert else "ALERT suppressed (dry run)")
            + f" ({best_label}, {who}, held {present_for:.0f}s)"
        ))

    # ---- Layer E evidence handling (E29) ----------------------------------

    def _handle_evidence(self, ev, frame, now_ts, cooldown, debug):
        """E29 — route one scored evidence vector to its band's outcome.

        Discard  (<0.35)      nothing beyond the suppression log
        Observe  (0.35-0.55)  logged with the full cue vector, no alert
        Candidate(>0.55)      an Alert row, entering the existing lifecycle
        """
        if debug:
            x1, y1, x2, y2 = ev.box
            color = {theft.CANDIDATE: (0, 0, 220),
                     theft.OBSERVE: (0, 165, 255)}.get(ev.band, (120, 120, 120))
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            recognition.draw_label(frame, f"{ev.kind} {ev.score:.2f} {ev.band}",
                                   x1, max(y1 - 8, 0), color)

        if ev.band == theft.DISCARD:
            return
        if ev.band == theft.OBSERVE:
            self._log_observe(ev)
            return

        if "cooldown" not in self.ablate and self._cooldown_blocks(
                ev.box, now_ts, cooldown):
            self.stats["suppressed: recent alert at same spot"] += 1
            return

        self.stats[f"ALERTS:{ev.kind}"] += 1
        alert = self._create_alert(
            # Alert.confidence is a 0-1 field, but an E28 score is a weighted
            # sum that can legitimately exceed 1.0 (weapon + custody + night).
            # Clamp for storage; the true score is in the description.
            min(ev.score, 1.0), ev.kind, frame,
            description=(
                f"Theft pattern detected ({ev.kind}): {ev.detail}. "
                f"Layer E score {ev.score:.2f} "
                f"[{', '.join(ev.rules)}] on {self.camera.code} feed."
            ),
            now=now_ts,
        )
        self._alert_log.append((tuple(ev.box), now_ts))
        self.stdout.write(self.style.SUCCESS(
            (f"ALERT created: {alert.code}" if alert else "ALERT suppressed (dry run)")
            + f" [Layer E {ev.kind}] {ev.summary()}"
        ))

    def _log_observe(self, ev):
        """The Observe band: a near miss, kept with its evidence vector intact.

        This is how the decision threshold gets calibrated after the field
        shoot — against real footage rather than against reasoned defaults — so
        these records are the point, not noise.
        """
        self.stats[f"OBSERVE:{ev.kind}"] += 1
        self.stdout.write(self.style.WARNING(f"OBSERVE {ev.summary()}"))
        if not self.observe_log:
            return
        row = {
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
            "camera": self.camera.code,
            "kind": ev.kind,
            "score": round(ev.score, 4),
            "band": ev.band,
            "cues": ev.cues,
            "multipliers": ev.multipliers,
            "abstained": sorted(ev.abstained),
            "tracks": ev.tracks,
            "box": list(ev.box),
            "detail": ev.detail,
        }
        try:
            with open(self.observe_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError as exc:
            self.stdout.write(self.style.ERROR(
                f"Could not write --observe-log {self.observe_log}: {exc}"
            ))
            self.observe_log = None   # don't retry once per frame

    def _cooldown_blocks(self, box, now, cooldown):
        """True if we already alerted on roughly this part of the frame inside
        the cooldown, regardless of which track id it was at the time."""
        self._alert_log = [
            (b, ts) for b, ts in self._alert_log if now - ts < cooldown
        ]
        return any(recognition._iou(box, b) >= COOLDOWN_IOU
                   for b, _ in self._alert_log)

    # ---- shared alert creation --------------------------------------------

    def _create_alert(self, score, label, frame, description, now=None):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = label.replace(" ", "_")
        filename = f"{ts_label}_thief_{safe_label}.jpg"
        cv2.imwrite(str(self.violations_dir / filename), frame)
        image_url = violation_media_path(filename)

        # Write the ~30s evidence clip (annotated frames leading up to the
        # alert). getattr guards --image test mode, which never creates
        # self.clip (mirrors watch_smoking/watch_drinking's own guard).
        video_url = ""
        clip = getattr(self, "clip", None)
        if clip is not None:
            # Add the fully-annotated current frame (with the alert box) to
            # the buffer at the moment the alert fires, so the clip includes
            # it. MUST be the same clock the caller's been using for every
            # other frame added this run (`now`, passed in by
            # _process_track/_handle_evidence) — ClipRecorder.add() trims
            # its buffer by comparing timestamps, so mixing wall-clock
            # time.time() in here against a run using video-position time
            # (file sources, see the timing-bug fix) makes this one frame
            # look tens of years newer than everything already buffered,
            # which the cutoff logic reads as "evict all of it." Default
            # only covers a caller with no timeline of its own (--image
            # test mode, which never touches self.clip anyway).
            clip.add(frame, now if now is not None else time.time())
            video_name = f"{ts_label}_thief_{safe_label}.mp4"
            if clip.save(self.violations_dir / video_name):
                video_url = violation_media_path(video_name)

        # RAW (unannotated, full source frame rate/resolution) clip. File
        # sources cut straight from the source file (best quality, real fps).
        # Live sources can't be seeked, so they fall back to the rolling
        # RawFrameRecorder buffer of raw frames instead (see its docstring for
        # why record_camera's segments aren't usable for this).
        raw_video_url = ""
        raw_name = f"{ts_label}_thief_{safe_label}_raw.mp4"
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
            video_url=video_url,
            raw_video_url=raw_video_url,
            suspect=label,
        )
