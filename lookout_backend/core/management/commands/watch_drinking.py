import datetime
import os
import time
from collections import Counter

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core import descriptions
from core.media import violation_media_path
from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import preprocess as preproc
from core.vision import trim as trimming
from core.vision import clock as vclock
from core.vision import ai_checker, momentum, recognition, scoring, tracking
from ._incidents import IncidentMixin

DRINKING_CAMERA_CODE = "CAM-SMOKE-01"
SETTINGS_REFRESH_SECONDS = 5  # re-poll SystemSettings this often, not every frame
PRESENCE_GRACE_SECONDS = 2    # tolerate a couple bottle-free frames before resetting dwell

# Track-agnostic spatial cooldown, measured center-to-center and scaled by mean
# box size (tracking._center_proximity, same formula TOMBSTONE_MATCH_DIST uses)
# rather than IoU: IoU goes to zero the moment two boxes stop touching at all,
# which a person shifting position between alerts routinely does — especially
# over this check's full cooldown window (up to minutes), not just a single
# frame gap. A size-relative radius keeps "same spot" distance-aware (a near
# person's box spans more pixels for the same real shift than a far person's
# does) instead of requiring literal overlap.
# More generous than TOMBSTONE_MATCH_DIST (1.2) since this spans the whole
# cooldown, not just the ~10s tombstone gap — a person has more time to move.
# Now a SystemSettings field (drinking_cooldown_center_dist) rather than a
# constant — see _cooldown_blocks' cooldown_center_dist param.

# --- The product/behaviour gap -------------------------------------------
# This detector uses the shared merged_v2 model's "Bottle" class: it recognises
# a bottle, which is an object, not an act. A bottle in a sari-sari store, in a shopping bag, empty
# in a bin, or held by a bystander all look identical to one being drunk from.
# Public-drinking ordinances concern CONSUMPTION, so the heuristics below carry
# the whole distance between "a bottle is visible" and "someone is drinking".
#
# The mechanism is posture: where the bottle sits relative to the person decides
# how long it must persist before it counts. Raising a bottle to the face is the
# act itself and alerts at the configured (at-mouth) dwell; a bottle merely
# held — including when no face could be resolved to check posture at all,
# common at CCTV range — is weaker evidence and must persist for the
# separately-configured, longer `drinking_held_dwell` instead. Every bottle
# class is judged identically here (Phase B3 follow-up) — there is no
# branded-vs-generic distinction; every "Bottle" detection goes through the
# exact same posture/dwell logic. A bottle with no
# person at all (a "scene" track) isn't scored by posture at all — see
# _process_track — it's discarded outright, the same as watch_smoking treats
# an unattributed detection.
#
# Note this is an ESCALATION, not a rejection — unlike the smoking detector,
# which rejects a cigarette far from the mouth outright. A cigarette at knee
# height is meaningless, whereas an open bottle in someone's hand is genuine
# evidence for this ordinance, merely weaker than one at their lips.

# --- Generic vessels (--include-generic) ---------------------------------
# The branded model sees one product, so a gin session, a different beer, or a
# drink poured into a glass produces nothing at all. The COCO classes bottle /
# wine glass / cup come free from the person detector's own pass and close
# that gap — but they say nothing about CONTENTS: a water bottle is
# indistinguishable from a beer. That used to buy them stricter admission
# terms than a "branded" detection; Phase B3 removed that distinction (a
# merged-model "Bottle" detection turned out to be exactly the class most
# likely to be spurious, so exempting it from the raised-to-mouth/longer-dwell
# treatment was backwards) — every bottle-class label is judged the same way
# now, so GENERIC_LABELS no longer gates anything; --include-generic still
# controls whether these COCO boxes are added as detection candidates at all.
GENERIC_LABELS = set(recognition.VESSEL_CLASS_IDS.values())

# A pose pass costs ~35ms per person (insightface was ~150-400ms), so the anchor is cached per track and
# stored relative to the person box, re-projecting as they move.
MOUTH_CACHE_SECONDS = 1.0

# Ablation switches — see HEURISTIC_RULES.md. Everything ON by default.
ABLATABLE = (
    "posture", "vote", "dwell", "cooldown", "hours", "zones",
    "stationary", "gathering", "preprocess", "scoring", "vlm",
)

# --- Path A (solo) vs Path B (gathering / "inuman") -----------------------
# Near the camera an individual's bottle is verifiable on its own (Path A);
# at range it usually isn't, but a sustained GATHERING still is (Path B) — so
# the evidence standard scales with what the camera can actually establish,
# rather than one fixed per-person rule. See tracking.py's Cluster/GroupTracker
# for how a gathering is identified and timed.
#
# Path A additionally requires the person to be STATIONARY for the dwell
# they've accrued — held-and-stationary is enough, at-mouth is not required,
# but someone merely passing through frame with a bottle in hand shouldn't
# accrue toward an alert the way someone actually stopped does.
#
# Suppression: a fired gathering alert supersedes solo alerts for its members
# going forward (not retroactively) by sharing the SAME spatial cooldown log
# Path A already checks — see _cooldown_blocks and _process_cluster.
GATHERING_ALERT_COLOR = (200, 40, 160)


def _within_window(now_time, start, end):
    """True if now_time falls in [start, end), handling windows that wrap
    midnight. Same semantics as watch_curfew's curfew hours."""
    if start <= end:
        return start <= now_time < end
    return now_time >= start or now_time < end


class Command(IncidentMixin, BaseCommand):
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
        # Gathering (Path B) funnel: DISTINCT cluster ids that ever reached each
        # stage, so --stats reports "how many gatherings got this far" rather
        # than a per-frame hit count that just scales with clip length/FPS —
        # much easier to see which single stage is the actual blocker.
        self._gathering_funnel = {
            "formed": set(), "min_group": set(), "duration": set(),
            "stationary": set(), "evidence": set(),
        }
        # Highest duration_held each distinct cluster id ever reached, BEFORE
        # any reset (grace-period expiry or identity churn to a new cluster
        # object). Distinguishes "got close to group_duration then reset" from
        # "never accumulated more than a few seconds" — the funnel above can't
        # tell those apart since both just show up as "duration not met".
        self._cluster_peak_duration = {}
        # Per-cluster-id centroid samples, as (x_frac, y_frac) of frame size —
        # so a short-lived cluster's location can be checked against the
        # others': repeatedly the same one or two spots points at leftover
        # identity churn; scattered across the frame points at genuinely
        # separate brief encounters, not a bug.
        self._cluster_centroids = {}
        self.dry_run = False
        self._cluster_of = {}                 # track id -> its Cluster this frame
        self.clock_start = None               # --clock footage start (file sources only)
        self._gathering_params = (2, 600)     # (min_group, group_duration)
        self.tracker_name = "bytetrack"
        self.mouth_check = True
        self.include_generic = False
        self.zones = []
        self.preprocess = False
        self.sharpen = False
        self.ablate = set()
        # Set only for a file source (see _run_stream) — lets _create_alert cut
        # a RAW evidence clip straight from the source instead of the sparser
        # annotated-frame buffer. Both stay None for webcam/RTSP sources.
        self._source_path = None
        self._video_pos_sec = None
        # Not set by watch_smoking.py's __init__ either (only inside
        # _run_stream) — declared here so _create_alert doesn't AttributeError
        # if ever reached via _run_image (--image test mode), which never runs
        # _run_stream's setup. The real upload path always uses --source.
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
                 "at-mouth dwell; held scales up from it.",
        )
        parser.add_argument(
            "--min-group",
            type=int,
            default=None,
            help="Override SystemSettings.drinking_min_group — persons "
                 "required for a gathering (Path B). Omit to use the "
                 "dashboard value.",
        )
        parser.add_argument(
            "--group-duration",
            type=int,
            default=None,
            help="Override SystemSettings.drinking_group_duration — seconds "
                 "a gathering must persist before alerting. Omit to use the "
                 "dashboard value.",
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
            "--clock", default="",
            help="Footage start time for an uploaded / test clip, e.g. "
                 "\"2026-08-18 19:30\". Drives the holdup time block and the "
                 "drinking evening band (position in the video is added to it). "
                 "Ignored for live streams; without it the wall clock is used.",
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
            "--stats",
            action="store_true",
            help="On exit, print how many detections each stage discarded and "
                 "the posture mix, plus effective FPS.",
        )
        parser.add_argument(
            "--no-mouth-check", "--no-face-check",
            dest="no_mouth_check",
            action="store_true",
            help="Disable posture classification: every bottle on a person is "
                 "treated as 'held'. Faster (skips a pose pass per person).",
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
        preproc.add_cli_flags(parser)
        trimming.add_cli_flags(parser)

    def handle(self, *args, **options):
        # ViolationType/Camera aren't created by any migration, so get_or_create
        # here self-heals a fresh DB the same way the other watchers do.
        self.drinking_type, _ = ViolationType.objects.get_or_create(
            code="drinking",
            defaults={"label": "Public Drinking", "color": "#8b5cf6", "icon": "beer"},
        )
        self.camera, _ = Camera.objects.get_or_create(
            code=options["camera"],
            defaults={"name": "Hikvision DS-2CD1047G2", "status": Camera.Status.ONLINE},
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
        self.min_group_override = options["min_group"]
        self.group_duration_override = options["group_duration"]
        # Both modes by default (far already includes the near whole-frame pass);
        # --fast opts out to the single near pass.
        self.far = not options["fast"]
        self.dry_run = options["dry_run"]
        self.trim = trimming.Trim(options["start"], options["end"])
        self.clock_start = vclock.parse_clock(options.get("clock"))
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

        # --no-mouth-check and --ablate posture are the same switch.
        self.mouth_check = not options["no_mouth_check"] and "posture" not in self.ablate

        # AI checker (spec v6 section 8): local Qwen3-VL, display-only, asynchronous.
        self._ai_setup(off="vlm" in self.ablate)


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

        self.apply_spec_settings(cfg)
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
        keeps a valid anchor without paying for a pose pass every frame. Returns
        None when no mouth anchor could be found.
        """
        bx1, by1, bx2, by2 = track.box
        bw, bh = max(bx2 - bx1, 1), max(by2 - by1, 1)

        cached = track.mouth_anchor
        if cached is not None and now_ts - cached[3] < MOUTH_CACHE_SECONDS:
            rel_x, rel_y, rel_w, _ = cached
            self.stats["posture: anchor cache hit"] += 1
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

    def _posture(self, frame, track, dets, now_ts, mouth_proximity):
        """Classifies where the bottle sits relative to the person.

        Returns 'held' or 'at-mouth' — never 'unattended', since scene
        (no-person) tracks are discarded outright in _process_track before
        this is ever called. When posture checking is off, or no mouth anchor can be
        resolved, the result is 'held' — the conservative middle: not treated
        as consumption, but not dismissed either. Reading an unresolvable face
        as 'not drinking' would disable the escalation at exactly the CCTV
        distances where faces stop being detectable.
        """
        if not self.mouth_check or not dets:
            return "held"

        anchor = self._mouth_anchor(frame, track, now_ts)
        if anchor is None:
            self.stats["posture: no mouth anchor found, treated as held"] += 1
            recognition.log_mouth(kind="drinking", t=now_ts, track=track.id, anchor=None)
            return "held"

        mx, my, face_w = anchor
        limit = face_w * mouth_proximity
        nearest = min((((((d[0] + d[2]) / 2 - mx) ** 2 + ((d[1] + d[3]) / 2 - my) ** 2) ** 0.5) / max(face_w, 1)
                       for d in dets), default=None)
        recognition.log_mouth(kind="drinking", t=now_ts, track=track.id, anchor="pose",
                              face_w=face_w, ratio=nearest, limit_ratio=mouth_proximity)
        for d in dets:
            cx, cy = (d[0] + d[2]) / 2, (d[1] + d[3]) / 2
            if ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5 <= limit:
                return "at-mouth"
        return "held"

    def _dwell_for(self, posture, at_mouth_dwell, held_dwell):
        """Dwell seconds required for this posture — every bottle class is
        judged identically now (Phase B3 follow-up, no more branded/generic
        split). A raised bottle escalates at the shorter `at_mouth_dwell`;
        a merely held one (including "no face resolvable to check", the
        common CCTV-range case) still counts, but only after the longer,
        independently-configured `held_dwell` — held-only presence is much
        weaker evidence of ACTUAL drinking than a raised bottle is, not zero
        evidence, so unlike before it is never rejected outright.
        """
        return at_mouth_dwell if posture == "at-mouth" else held_dwell

    # ---- single-image test mode -------------------------------------------

    def _preprocess(self, frame):
        """Enhance a dim/noisy frame before detection (no-op unless --preprocess,
        and daytime frames bypass inside preprocess() itself)."""
        if not self.preprocess:
            return frame
        return preproc.preprocess(frame, mode="near", sharpen=self.sharpen)

    def _run_image(self, path, conf):
        frame = recognition.load_image(path)
        if frame is None:
            self.stdout.write(self.style.ERROR(f"Could not read image: {path}"))
            return
        frame = self._preprocess(frame)

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
            recognition.draw_label(frame, f"{label} {score * 100:.0f}%",
                                   x1, max(y1 - 8, 0), (200, 80, 160))

        # A still image has no temporal signal and no posture history, so this
        # path is a far weaker bar than the live one — use --dry-run for tuning.
        best = max(drinks, key=lambda s: s[4])
        _, _, _, _, best_score, best_label = best
        summary = ", ".join(sorted({s[5] for s in drinks}))
        alert = self._create_alert(
            best_score, best_label, frame,
            description=(
                descriptions.drinking(best_label)
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
        # Jump straight to the chosen start rather than decoding and throwing
        # away everything before it — on CPU that discarded prefix costs the
        # same per frame as the part being tested.
        if self.trim.seek(cap, is_live):
            self.stdout.write(self.style.SUCCESS(
                f"Trimmed to {self.trim.describe()} of the clip."))

        cfg = SystemSettings.load()

        self.apply_spec_settings(cfg)
        cfg_loaded_at = time.time()

        # Per-person tracking: the bottle is what's detected, but the PERSON
        # holding it is what must be timed. Each track keeps its own vote window,
        # dwell timer and cooldown, so two drinkers are confirmed independently.
        tracker = tracking.PersonTracker()
        # Path B: groups this frame's person tracks into persistent gatherings,
        # identified by spatial footprint rather than membership (see
        # tracking.Cluster/GroupTracker).
        group_tracker = tracking.GroupTracker()

        # Rolling 30-second buffer of annotated frames — on an alert it's written
        # out as the evidence clip, so the card shows the bottle being detected
        # with its box, not just a still.
        self.clip = recognition.ClipRecorder(seconds=30, label=self.camera.code)

        mode = f"FAR {self.tiles[0]}x{self.tiles[1]} tiling + person-crop" if self.far else "near"
        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} for public drinking "
            f"[{mode} mode, {self.tracker_name} tracker, "
            f"posture check {'on' if self.mouth_check else 'off'}"
            + (", +generic vessels" if self.include_generic else "")
            + (f", zones {len(self.zones)}" if self.zones else "")
            + "] "
            f"(dwell {self.dwell_override or cfg.drinking_dwell}s at-mouth, "
            + (f"hours {cfg.drinking_start}-{cfg.drinking_end}, "
               if cfg.drinking_hours_enabled else "")
            + (f"gathering: {self.min_group_override or cfg.drinking_min_group}+ persons for "
               f"{self.group_duration_override or cfg.drinking_group_duration}s, "
               if "gathering" not in self.ablate else "gathering: off, ")
            + "reads live from Settings). Press Ctrl+C to stop."
        ))

        if debug:
            cv2.namedWindow("LookOut - watch_drinking (debug)", cv2.WINDOW_NORMAL)

        started_at = time.time()
        fps_warned = False
        try:
            while True:
                ok, frame = reader.read()
                if not ok:
                    if is_live:
                        time.sleep(0.02)  # reader has no frame yet
                        continue
                    # A file source that stops yielding frames has reached its
                    # end — finish rather than looping forever on a test video.
                    self.stdout.write(self.style.SUCCESS(
                        f"End of {source} — done."
                    ))
                    break

                # Buffer the frame RAW, before any drawing touches it — see
                # RawFrameRecorder.
                if self._raw_buffer is not None:
                    self._raw_buffer.add(frame, time.time())
                self._frame_start(frame)      # clean pixels for the AI checker and the live view

                # Enhance dim/noisy frames before detection (daytime bypasses).
                frame = self._preprocess(frame)

                wall_now = time.time()
                if wall_now - cfg_loaded_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    self.apply_spec_settings(cfg)
                    cfg_loaded_at = wall_now

                if not cfg.drinking_enabled:
                    time.sleep(0.5)
                    continue

                # Ordinance hours, when configured: outside the window public
                # drinking isn't an offence, so don't accumulate toward one.
                # Real-world local time-of-day, deliberately wall-clock even
                # for a file source — an uploaded clip is being reviewed NOW,
                # not at whatever hour it was originally recorded.
                if (cfg.drinking_hours_enabled and "hours" not in self.ablate
                        and not _within_window(timezone.localtime().time(),
                                               cfg.drinking_start, cfg.drinking_end)):
                    self.stats["skipped: outside ordinance hours"] += 1
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

                # Reported as an ordinary end-of-clip finish: a trimmed run and
                # a whole one must look identical to _watch_detection_job,
                # which only sees the exit code.
                if self.trim.past_end(now_ts, is_live):
                    self.stdout.write(self.style.SUCCESS(
                        f"End of {source} ({self.trim.describe()}) — done."))
                    break

                self.stats["frames"] += 1
                conf = self.conf_override or (cfg.drinking_confidence / 100)
                dwell_seconds = self.dwell_override or cfg.drinking_dwell

                persons, vessels, ids = self._detect_persons(frame)
                drinks = self._detect(frame, conf, persons=persons, vessels=vessels)

                tracks = tracker.update(persons, now_ts, ids=ids)
                per_track = tracker.assign(drinks, now_ts)

                # Draw person boxes ALWAYS (not just in debug) so the evidence
                # clip and snapshot show the context, not only the debug window.
                for t in tracks:
                    x1, y1, x2, y2 = t.box
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (180, 180, 180), 1)
                    recognition.draw_label(frame, f"person #{t.id}", x1, max(y1 - 6, 0),
                                           (180, 180, 180), scale=0.6)

                # Path B: group this frame's tracks into gatherings and evaluate
                # each BEFORE Path A below, so a cluster alert that fires this
                # frame lands in _alert_log in time to suppress its members'
                # solo alerts later in the same frame (see _cooldown_blocks).
                if "gathering" not in self.ablate:
                    min_group = self.min_group_override or cfg.drinking_min_group
                    group_duration = self.group_duration_override or cfg.drinking_group_duration
                    clusters = group_tracker.update(tracks, now_ts, min_group)
                    self.note_clusters(clusters, min_group, group_duration)
                    for cluster in clusters:
                        # Per-frame member-count tally (not a distinct-cluster
                        # count, unlike the funnel below) — shows the raw
                        # distribution of how many people were actually grouped
                        # together at once, e.g. to check whether a partially
                        # occluded second person ever registers as a 2nd member
                        # or the cluster is stuck at size 1 the whole clip.
                        self.stats[f"gathering: cluster size {len(cluster.member_ids)}"] += 1
                        member_dets = [d for t, dets in per_track.items()
                                       for d in dets
                                       if t.id in cluster.member_ids and not t.is_scene]
                        # A bottle on the table the group sits around is not in
                        # anyone's box (a scene track) but still belongs to it.
                        member_dets += self._scene_dets_near(cluster, per_track)
                        best = max(member_dets, key=lambda d: d[4]) if member_dets else None
                        cluster.frame_conf = best[4] if best else 0.0
                        if best:
                            cluster.note_evidence(best, now_ts)
                        self._process_cluster(
                            cluster, now_ts, min_group, group_duration,
                            cfg.alert_cooldown, frame, debug,
                            evidence_max_age=cfg.drinking_evidence_max_age,
                            cooldown_center_dist=cfg.drinking_cooldown_center_dist,
                        )

                for track, dets in per_track.items():
                    self._process_track(
                        track, dets, now_ts, dwell_seconds, cfg.alert_cooldown,
                        frame, debug,
                        held_dwell_seconds=cfg.drinking_held_dwell,
                        mouth_proximity=cfg.drinking_mouth_proximity,
                        cooldown_center_dist=cfg.drinking_cooldown_center_dist,
                    )

                # Buffer this annotated frame for the evidence clip.
                self.clip.add(frame, now_ts)
                self._incident_gc(now_ts, frame)   # close incidents whose object is gone

                if not fps_warned and self.stats["frames"] >= 30:
                    fps = self.stats["frames"] / max(wall_now - started_at, 1e-6)
                    if fps < 2:
                        fps_warned = True
                        self.stdout.write(self.style.WARNING(
                            f"Running at {fps:.1f} FPS — below ~2 FPS it takes "
                            f"{tracking.VOTE_MIN_FRAMES / fps:.0f}s just to confirm "
                            "a detection. Use fewer --tiles or --no-mouth-check."
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

        # Gathering (Path B) funnel: DISTINCT clusters that ever reached each
        # stage, in order — whichever stage drops sharply from the one above
        # it is where gatherings are actually failing.
        f = self._gathering_funnel
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Gathering (Path B) funnel"))
        self.stdout.write(f"  {len(f['formed']):>7}  clusters formed (any size)")
        self.stdout.write(f"  {len(f['min_group']):>7}  reached min_group")
        self.stdout.write(f"  {len(f['duration']):>7}  reached group_duration")
        self.stdout.write(f"  {len(f['stationary']):>7}  passed stationarity")
        self.stdout.write(f"  {len(f['evidence']):>7}  had bottle evidence (would have alerted)")
        self.stdout.write(
            "  (counts are detection-frames, not incidents: one person held for "
            "8s at 15 FPS is ~120)"
        )

        # Peak duration_held per distinct cluster, bucketed — shows whether
        # clusters that never reached group_duration got CLOSE before resetting
        # (grace period too short) or never accumulated more than a few seconds
        # (cluster identity churn, or genuinely brief encounters).
        peaks = self._cluster_peak_duration
        if peaks:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"Peak duration_held per cluster ({len(peaks)} distinct clusters, "
                f"max {max(peaks.values()):.0f}s)"
            ))
            buckets = Counter()
            for peak in peaks.values():
                buckets[int(peak // 5) * 5] += 1
            for bucket in sorted(buckets):
                self.stdout.write(f"  {buckets[bucket]:>7}  {bucket}-{bucket + 5}s")

        # Per-cluster location (avg centroid, fraction of frame), sorted by
        # peak duration so the survivors are visible at top for reference —
        # do the short-lived ones repeat at the same one or two spots
        # (leftover identity churn) or scatter across the frame (genuinely
        # separate brief encounters)?
        if self._cluster_centroids:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(
                "Cluster locations (avg centroid, fraction of frame)"
            ))
            for cid in sorted(peaks, key=lambda i: -peaks[i]):
                pts = self._cluster_centroids.get(cid, [])
                if not pts:
                    continue
                avg_x = sum(p[0] for p in pts) / len(pts)
                avg_y = sum(p[1] for p in pts) / len(pts)
                self.stdout.write(
                    f"  cluster #{cid:<4} peak {peaks[cid]:>5.1f}s  "
                    f"centroid ({avg_x:.2f}, {avg_y:.2f})  [{len(pts)} samples]"
                )

            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(
                "Cluster locations, grouped (rounded to nearest 10% of frame)"
            ))
            location_counts = Counter()
            for cid, pts in self._cluster_centroids.items():
                avg_x = sum(p[0] for p in pts) / len(pts)
                avg_y = sum(p[1] for p in pts) / len(pts)
                location_counts[(round(avg_x, 1), round(avg_y, 1))] += 1
            for bucket in sorted(location_counts, key=lambda b: -location_counts[b]):
                self.stdout.write(
                    f"  {location_counts[bucket]:>7}  clusters near "
                    f"({bucket[0]:.1f}, {bucket[1]:.1f})"
                )

    # ---- per-track temporal confirmation ----------------------------------

    # ---- weighted scoring (core/vision/scoring.py) -------------------------

    def _time_band_cue(self, now_ts=None):
        """The Omamalin high band (16:00-24:00) as a scored cue, not a gate."""
        start, end = self.drinking_band
        return scoring.in_time_band(
            vclock.clock_now(self.clock_start, now_ts, self._source_path is not None), start, end)

    def note_clusters(self, clusters, min_group, group_duration):
        """Remember this frame's gatherings so a person's own score can include
        the group / duration points when they belong to one (spec v6 section 5)."""
        self._gathering_params = (min_group, group_duration)
        self._cluster_of = {tid: c for c in clusters for tid in c.member_ids}

    def _gathering_cues(self, cluster, now_ts):
        """{'gathering', 'gathering_duration'} earned by being part of `cluster`."""
        min_group, group_duration = self._gathering_params
        cues = set()
        if cluster is None or len(cluster.member_ids) < min_group or "gathering" in self.ablate:
            return cues
        # "Group of 2+ (stationary)": the group has stayed put for the last
        # stretch of its life (up to 30 s), so people passing each other do not count.
        window = min(cluster.duration_held, 30.0)
        if window >= 5.0 and cluster.stationary(now_ts, window):
            cues.add("gathering")
            if (cluster.duration_held >= group_duration
                    and cluster.stationary(now_ts, group_duration)):
                cues.add("gathering_duration")
        return cues

    def _scene_dets_near(self, cluster, per_track):
        """Bottle detections that belong to the SCENE (not inside any person's
        box) but sit within reach of a gathering -- a bottle on the table the
        group is sitting around. They count as that gathering's bottle evidence."""
        x1, y1, x2, y2 = cluster.bbox
        pad = 0.75 * max(y2 - y1, 1)
        found = []
        for t, dets in per_track.items():
            if not getattr(t, "is_scene", False):
                continue
            for d in dets:
                cx, cy = (d[0] + d[2]) / 2, (d[1] + d[3]) / 2
                if x1 - pad <= cx <= x2 + pad and y1 - pad <= cy <= y2 + pad:
                    found.append(d)
        return found

    def _process_track(self, track, dets, now_ts, dwell_seconds, cooldown,
                       frame, debug, *,
                       held_dwell_seconds, mouth_proximity, cooldown_center_dist):
        """Scores ONE person's drinking evidence and keeps their incident
        (Monitoring -> Possible -> Likely) up to date."""
        # Public drinking is committed by a person. A bottle outside every
        # person's box is "scene" here; it still counts for a gathering via
        # _scene_dets_near, but never makes a solo event on its own.
        if track.is_scene:
            self.stats["discarded: no person (scene)"] += 1
            return

        track.vote(dets, now_ts)       # keeps the label history that track.dets draws from
        # Object cue: momentum per (track, class) -- replaces the vote + dwell gate.
        # A bottle carried past is Monitoring too (spec v6 lists it on the quiet
        # watchlist), so there is no stationary requirement.
        cue = self._object_cue(track, dets, now_ts)
        object_on, best_label, best_score = cue.on, cue.label, cue.conf
        posture = "held"
        if object_on:
            # Posture describes the object being scored, not the person in
            # general: a glass raised to the mouth must not credit a bottle
            # sitting at the hip.
            own = [d for d in dets if d[5] == best_label] or dets
            posture = self._posture(frame, track, own, now_ts, mouth_proximity)
            self.stats[f"posture:{posture}"] += 1
        self._draw_object_cue(frame, track, dets, cue, color_on=(200, 80, 160))
        if not object_on and dets:
            self.stats["held back: momentum below ON"] += 1

        # --- the cues (spec v6 section 5) ---
        cues = set()
        if object_on:
            cues.add("bottle")
            at_mouth_now = posture == "at-mouth"
            # Held for `cue_hold` seconds after it last fired (the bottle goes to the lips and away
            # again frame to frame); the group the person belongs to is told as well.
            if self._sticky(("drink", track.id), "at_mouth", at_mouth_now, now_ts):
                cues.add("at_mouth")
            member_of = self._cluster_of.get(track.id)
            if at_mouth_now and member_of is not None:
                self._sticky(("cluster", member_of.id), "at_mouth", True, now_ts)
        # Group and duration points accumulate silently and appear the moment the
        # bottle cue turns ON (30 + 40 = 70 opens at Possible, skipping Monitoring).
        cues |= self._gathering_cues(self._cluster_of.get(track.id), now_ts)
        if self._time_band_cue(now_ts):
            cues.add("time_band")

        key = ("drink", track.id)
        score = scoring.Score("drinking", scoring.DRINKING_WEIGHTS, cues,
                              previous_level=self._incident_level(key), object_on=object_on)
        self._debug_note("Drinking", key, track.id, track.box, score, cue.momentum)
        group = self._cluster_of.get(track.id)
        params = getattr(self, "_gathering_params", None)
        if group is not None and params is not None and len(group.member_ids) >= params[0]:
            # One gathering is ONE event for the whole group (its own row, below). This person's
            # bottle already counts there, so they do not get a second event of their own.
            self.stats["person event folded into the group's event"] += 1
            return
        if not score.stored:
            self._incident_sync(key, score, now_ts, create=None)
            return

        box = track.box
        who = track.display

        def describe(sc):
            return descriptions.drinking(best_label, posture == "at-mouth")

        def create(level, with_clip):
            return self._create_alert(
                score.score, best_label, frame, description=describe(score), box=box,
                now=now_ts, score_obj=score, object_confidence=best_score,
                with_clip=with_clip)

        self._incident_sync(
            key, score, now_ts, create=create, describe=describe, frame=frame, box=box,
            blocked=lambda: "cooldown" not in self.ablate
            and self._cooldown_blocks(box, now_ts, cooldown, cooldown_center_dist),
            ai={"kind": "drinking", "keys": [("track", track.id)], "note": ai_checker.system_note("drinking")},
            extra_cues={"posture": posture, "momentum": cue.snapshot,
                        "object_confidence": None if best_score is None else round(best_score, 3)},
        )

    def _cooldown_blocks(self, box, now, cooldown, cooldown_center_dist):
        """True if we already alerted near roughly this spot inside the
        cooldown, regardless of which track id it was at the time."""
        self._alert_log = [
            (b, ts) for b, ts in self._alert_log if now - ts < cooldown
        ]
        return any(tracking._center_proximity(box, b, max_frac=cooldown_center_dist) > 0
                   for b, _ in self._alert_log)

    # ---- Path B: gathering confirmation ------------------------------------

    def _process_cluster(self, cluster, now_ts, min_group, group_duration, cooldown, frame, debug, *,
                         evidence_max_age, cooldown_center_dist):
        """Scores ONE gathering and keeps its incident up to date.

        The gathering itself is evidence (group of 2+ stationary, stayed 10+
        minutes). The object cue is a bottle seen anywhere around the group --
        in a member's hand OR on the table they sit around -- within
        `evidence_max_age`. Without the bottle the points are logged only.
        """
        n = len(cluster.member_ids)
        self._ai_note(("cluster", cluster.id), cluster.bbox)
        self._gathering_params = (min_group, group_duration)
        self._gathering_funnel["formed"].add(cluster.id)
        self._cluster_peak_duration[cluster.id] = max(
            self._cluster_peak_duration.get(cluster.id, 0.0), cluster.duration_held,
        )
        h, w = frame.shape[:2]
        cx = (cluster.bbox[0] + cluster.bbox[2]) / 2 / w
        cy = (cluster.bbox[1] + cluster.bbox[3]) / 2 / h
        self._cluster_centroids.setdefault(cluster.id, []).append((cx, cy))

        if n < min_group:
            self.stats["held back: gathering below min_group"] += 1
            return
        self._gathering_funnel["min_group"].add(cluster.id)
        if cluster.duration_held >= group_duration:
            self._gathering_funnel["duration"].add(cluster.id)

        cues = self._gathering_cues(cluster, now_ts)
        if "gathering_duration" in cues:
            self._gathering_funnel["stationary"].add(cluster.id)
        # Object cue for the gathering: momentum on the best bottle confidence
        # seen around the group this frame (a member's hand OR the table).
        mkey = ("cluster", cluster.id)
        self._momentum_book()
        self._seen_track_ids.add(mkey)
        frame_conf = getattr(cluster, "frame_conf", 0.0)
        slots = self._momentum_book().step(mkey, {"bottle": frame_conf} if frame_conf > 0 else {}, now_ts)
        slot = slots.get("bottle")
        object_on = momentum.confirmed(slot, now_ts, self.confirm_seconds)
        evidence = cluster.evidence
        if object_on:
            self._gathering_funnel["evidence"].add(cluster.id)
            cues.add("bottle")
            _, _, _, _, ev_score, ev_label = evidence if evidence else (0, 0, 0, 0, frame_conf, "Bottle")
        else:
            ev_score, ev_label = 0.0, "Gathering"
        if object_on and self._sticky(("cluster", cluster.id), "at_mouth", False, now_ts):
            cues.add("at_mouth")           # a member raised the bottle to their mouth (held)
        if self._time_band_cue(now_ts):
            cues.add("time_band")

        key = ("cluster", cluster.id)
        score = scoring.Score("drinking", scoring.DRINKING_WEIGHTS, cues,
                              previous_level=self._incident_level(key), object_on=object_on)
        self._debug_note("Drinking (group)", key, "G%s" % cluster.id, cluster.bbox, score,
                         slot.momentum if slot else 0.0)
        if not score.stored:
            self._incident_sync(key, score, now_ts, create=None)
            return

        box = tuple(int(v) for v in cluster.bbox)
        cv2.rectangle(frame, (box[0], box[1]), (box[2], box[3]), GATHERING_ALERT_COLOR, 3)
        recognition.draw_label(
            frame,
            f"GATHERING {n}p - {ev_label} {ev_score * 100:.0f}% - "
            f"{cluster.duration_held:.0f}/{group_duration:.0f}s",
            box[0], max(box[1] - 10, 0), GATHERING_ALERT_COLOR,
        )

        def describe(sc):
            return descriptions.gathering(n, cluster.duration_held)

        def create(level, with_clip):
            return self._create_alert(
                score.score, ev_label, frame, description=describe(score),
                suspect=f"{ev_label} · Gathering ({n})",
                filename_tag=f"{ev_label.replace(' ', '_')}_gathering",
                box=box, now=now_ts, score_obj=score,
                object_confidence=ev_score or None, with_clip=with_clip)

        self._incident_sync(
            key, score, now_ts, create=create, describe=describe, frame=frame, box=box,
            blocked=lambda: "cooldown" not in self.ablate
            and self._cooldown_blocks(box, now_ts, cooldown, cooldown_center_dist),
            ai={"kind": "drinking", "keys": [("cluster", cluster.id)],
                "note": ai_checker.system_note(
                    "drinking", people=n, minutes=round(cluster.duration_held / 60, 1),
                    stationary="gathering" in cues)},
            extra_cues={"gathering_size": n, "gathering_seconds": round(cluster.duration_held, 1),
                        "momentum": self._momentum_book().snapshot(mkey, "bottle")},
        )


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
            # _process_track/_process_cluster) — ClipRecorder.add() trims
            # its buffer by comparing timestamps, so mixing wall-clock
            # time.time() in here against a run using video-position time
            # (file sources, see the timing-bug fix) makes this one frame
            # look tens of years newer than everything already buffered,
            # which the cutoff logic reads as "evict all of it." Default
            # only covers a caller with no timeline of its own (--image
            # test mode, which never touches self.clip anyway).
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

    def _create_alert(self, score, label, frame, description, suspect=None, filename_tag=None,
                      box=None, now=None,
                      score_obj=None, object_confidence=None, with_clip=True):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = filename_tag or label.replace(" ", "_")
        base = f"{ts_label}_drinking_{tag}"
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
        recognition.log_mouth(kind="alert", engine="drinking", label=label, score=score,
                              level=getattr(score_obj, "level", ""),
                              cues=sorted(getattr(score_obj, "cues", None) or []))
        if self.dry_run:
            return None

        return Alert.objects.create(
            type=self.drinking_type,
            status=Alert.Status.ACTIVE,
            camera=self.camera,
            timestamp=self._event_time(now),
            # Now the weighted violation likelihood, not the YOLO box score.
            confidence=score,
            description=description,
            image_url=image_url,
            video_url=video_url,
            raw_video_url=raw_video_url,
            last_seen_at=self._event_time(now),
            suspect=suspect if suspect is not None else label,
            level=score_obj.level if score_obj is not None else "",
            # Retained even when it barely cleared the bar: this vector is the
            # training data calibrate_weights fits the final weights against.
            cues=score_obj.as_dict() if score_obj is not None else {},
            # The DETECTOR's own confidence in the anchoring box, kept apart
            # from `confidence` (the violation likelihood). See Alert.object_confidence.
            object_confidence=object_confidence,
        )
