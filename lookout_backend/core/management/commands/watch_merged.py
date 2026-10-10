"""Runs the merged Bottle/Cigarette/knife model ONCE per frame and routes each
detected class to its own existing rule layer — smoking's B4 mouth-proximity/
vote/dwell/cooldown for Cigarette, drinking's Path A (solo) + Path B
(gathering, cluster tracking, tombstone revival) for Bottle, thief's rules for
knife — so a single clip can produce smoking, drinking and theft alerts
together, at the cost of ONE custom-model inference pass per frame instead of
three.

Reuses watch_smoking.py/watch_drinking.py/watch_thief.py's Command classes
UNCHANGED for their rule layers (temporal voting, dwell, cooldown, alert
creation) — only detection differs: instead of each command running its OWN
model, this splits the merged model's output by class name and hands each
engine its own slice, then drives each engine's existing _process_track/
_process_cluster machinery exactly as its standalone command would.

Class routing is NAME-based (see recognition._smoking_boxes_from_result):
labels come from the merged model's own names dict, so its class INDEX order
(0=Bottle, 1=Cigarette, 2=knife per its data.yaml) is irrelevant here — only
the strings "Bottle"/"Cigarette"/"knife" matter, matched case-insensitively
for routing (see ROUTE_ENGINE). Downstream, each engine's own rule layer also
matches by name (watch_smoking lowercases, watch_thief does not — "knife"
already matches either way). watch_drinking has no branded-vs-generic
distinction any more either (Phase B3 follow-up removed it) — a merged-model
"Bottle" detection is judged by the exact same posture/dwell logic as every
other bottle-class label, so there is no tier for a class-name spelling to
accidentally fall into or out of.

A DIFFERENT name-matching hazard remains, though: ROUTE_ENGINE itself is a
name lookup, so if a future retrain renames a class (e.g. "Bottle" ->
"Beer Bottle"), ROUTE_ENGINE silently stops matching it and that engine gets
NO detections at all — nothing looks broken, the other two engines keep
alerting normally, this one just goes quiet. See _check_route_coverage,
checked at startup.

Unlike watch_all.py (which runs THREE separate custom models per frame, one
per detector, sharing only the person-detection pass), this genuinely is one
model doing all the detection — so the usual persist=True/bytetrack
corruption concern doesn't apply here: there is only ONE call per frame to
the shared person-tracking model, not three interleaved ones, so bytetrack
is safe and used by default for person tracking (see --tracker).

Note: watch_all.py's own reuse of SmokingCommand/DrinkingCommand has two gaps
this command does NOT repeat — it calls _process_track with too few
positional arguments for smoking/drinking (a missing argument, which
raises TypeError on the first frame with any person in view) and never
drives drinking's Path B (GroupTracker/_process_cluster) at all.
"""
import csv
import os
import time
from collections import Counter

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import Camera, SystemSettings, ViolationType
from core.vision import clock as vclock
from core.vision import recognition, tracking
from core.vision import trim as trimming

from core.vision import debug_view

from ._incidents import ai_setup

from .watch_smoking import Command as SmokingCommand
from .watch_thief import Command as ThiefCommand
from .watch_drinking import Command as DrinkingCommand

MERGED_CAMERA_CODE = "CAM-SMOKE-01"
SETTINGS_REFRESH_SECONDS = 5

# Merged model class name -> which rule engine owns it. Matched
# case-insensitively; anything the model returns that isn't one of these
# three is dropped (and counted), never silently misrouted to the wrong
# engine.
ROUTE_ENGINE = {"cigarette": "smoking", "bottle": "drinking", "knife": "thief"}


class Command(BaseCommand):
    help = (
        "Runs the merged Bottle/Cigarette/knife model once per frame and "
        "routes each class to smoking/drinking/thief's own existing rule "
        "layers, so one clip can produce alerts of all three types. Use "
        "--source for an RTSP/CCTV URL or video file."
    )

    # Engines that do NOT come from the merged model. It has three classes
    # (Bottle/Cigarette/knife) and no vehicle class, so a fourth violation has
    # to bring its own detector and its own rule layer; that is what an extra
    # engine is. Empty here, so this command keeps running exactly the three it
    # always has — watch_merged_all.py sets it to ("parking",).
    EXTRA_ENGINES = ()

    # ---- extra-engine hooks (no-ops unless EXTRA_ENGINES is non-empty) ------
    #
    # Deliberately hooks rather than four entries in self.engines: everything
    # in self.engines shares ONE merged-model pass and is filtered out of it by
    # class name, and several places here are written around that (the shared
    # conf_floor taken as the minimum of the active engines' confidences, the
    # per-class floors, ROUTE_ENGINE). A vehicle engine satisfies none of it,
    # so folding it in would quietly make those lines wrong — e.g. a parking
    # confidence of 35 dragging the merged model's inference floor down for
    # smoking and drinking too.

    def _setup_extra(self, options):
        """Build the extra engines' state. Called once, after the camera,
        evidence dir and AI checker exist and before the capture opens. Return
        an error message to refuse the run (same contract as
        _check_route_coverage), or None to proceed."""
        return None

    def _extra_source(self, source, is_live):
        """Tell the extra engines where frames are coming from, so they can cut
        raw evidence clips the same way the merged engines do."""

    def _extra_frame(self, name, frame, now, cfg, debug):
        """One extra engine's whole per-frame pass: its own detection AND its
        own rule layer. Called before the merged pass — see _process_frame."""

    def add_arguments(self, parser):
        parser.add_argument("--source", default="0",
                            help="Webcam index or RTSP/stream URL / video file.")
        parser.add_argument("--camera", default=MERGED_CAMERA_CODE,
                            help=f"Camera code all alerts attach to (default {MERGED_CAMERA_CODE}).")
        parser.add_argument("--only", default="",
                            help="Comma-separated subset to run from "
                                 "smoking/drinking/thief. Default: all enabled in Settings.")
        parser.add_argument("--tracker", default="bytetrack",
                            choices=["greedy", "bytetrack", "botsort"],
                            help="Person-association method for the SHARED person pass. "
                                 "'bytetrack' (default) is safe here — unlike watch_all, only "
                                 "ONE model does detection each frame, so there's no "
                                 "interleaved-call corruption of ultralytics' persist=True "
                                 "tracker state to worry about.")
        parser.add_argument("--fast", action="store_true",
                            help="Near mode ONLY for the merged detector — single "
                                 "whole-frame pass, no tiling. Faster but misses small or "
                                 "distant objects.")
        parser.add_argument("--tiles", default="2x2",
                            help="Far-mode tiling grid ROWSxCOLS (default 2x2).")
        parser.add_argument(
            "--clock", default="",
            help="Footage start time for an uploaded / test clip, e.g. "
                 "\"2026-08-18 19:30\". Drives the holdup time block and the "
                 "drinking evening band (position in the video is added to it). "
                 "Ignored for live streams; without it the wall clock is used.",
        )
        parser.add_argument(
            "--no-cascade", action="store_true",
            help="Skip the extra native-resolution person-crop pass for Cigarette "
                 "(it is on by default: about 0.04 s/frame, finds cigarettes the "
                 "downscaled passes miss).",
        )
        parser.add_argument("--dry-run", action="store_true",
                            help="Detect and save evidence but write no Alert rows.")
        trimming.add_cli_flags(parser)
        parser.add_argument("--debug", action="store_true",
                            help="Show a preview window with every engine's boxes.")
        parser.add_argument("--stats", action="store_true",
                            help="On exit, print each engine's own detection-stage stats "
                                 "(same --stats output each standalone watcher prints).")
        parser.add_argument("--calibration-csv", default="",
                            help="Phase-0 instrumentation: write every raw merged-model "
                                 "detection to this CSV path, one row per detection per "
                                 "frame, BEFORE any per-engine confidence floor, heuristic, "
                                 "or alert logic runs. Columns: frame_idx, timestamp, "
                                 "camera_id, class_name, confidence, x1, y1, x2, y2, "
                                 "box_area_px, track_id. While active, the merged model's "
                                 "own inference floor is dropped to 0.01 (instead of the "
                                 "lowest active engine's configured confidence) so the CSV "
                                 "captures the full confidence distribution, including "
                                 "everything that would normally be discarded before any "
                                 "engine gets a look — alerting behavior is unaffected, "
                                 "since each engine's own floor is still applied downstream "
                                 "exactly as without this flag.")

    def handle(self, *args, **options):
        if not recognition.merged_model_available():
            self.stdout.write(self.style.ERROR(
                f"Merged model not found at {recognition.MERGED_MODEL_PATH}. "
                "Copy the trained best.pt there, or set the MERGED_MODEL env var."
            ))
            return

        # Load eagerly (normally lazy, on first detection call) so this check
        # runs — and can fail loudly — before opening a camera or touching the
        # DB. See the module docstring and _check_route_coverage: a renamed
        # class silently drops an entire engine's detections with nothing
        # looking broken, so this is caught here instead of discovered later
        # as "why did knife never fire on this run".
        model = recognition.load_merged_model()
        uncovered = self._check_route_coverage(model)
        if uncovered:
            self.stdout.write(self.style.ERROR(uncovered))
            return

        self.camera, _ = Camera.objects.get_or_create(
            code=options["camera"],
            defaults={"name": "Hikvision DS-2CD1047G2", "status": Camera.Status.ONLINE},
        )
        self.violations_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(self.violations_dir, exist_ok=True)

        # One AI checker (client + full-res frame ring) shared by all three engines.
        self.ai_state = ai_setup(self.stdout)
        self.debug_pub = debug_view.DebugPublisher.from_env()    # None unless LOOKOUT_DEBUG_DIR is set

        self.far = not options["fast"]
        self.dry_run = options["dry_run"]
        self.trim = trimming.Trim(options["start"], options["end"])
        self.cascade = not options["no_cascade"]
        self.clock_start = vclock.parse_clock(options.get("clock"))
        self.tracker_name = options["tracker"]
        self.show_stats = options["stats"]
        try:
            rows, cols = (int(v) for v in options["tiles"].lower().split("x"))
            self.tiles = (rows, cols)
        except (ValueError, AttributeError):
            self.stdout.write(self.style.ERROR(
                f"Invalid --tiles {options['tiles']!r}; expected ROWSxCOLS like 2x2."))
            return

        only = {s.strip() for s in options["only"].split(",") if s.strip()}
        valid = {"smoking", "drinking", "thief"} | set(self.EXTRA_ENGINES)
        if only - valid:
            self.stdout.write(self.style.ERROR(
                f"--only: unknown {', '.join(only - valid)}. Valid: {', '.join(valid)}."))
            return
        self.enabled = only or None  # None => decide per-frame from Settings

        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                "DRY RUN: evidence images will be saved but no alerts created."))

        self.calibration_csv_path = options["calibration_csv"]
        self.calibration_file = None
        self.calibration_writer = None

        self.engines = {}
        for name, cls in (("smoking", SmokingCommand),
                          ("drinking", DrinkingCommand),
                          ("thief", ThiefCommand)):
            cmd = cls()
            self._setup_engine(name, cmd)
            self.engines[name] = {"cmd": cmd, "tracker": tracking.PersonTracker()}
        self.engines["drinking"]["group_tracker"] = tracking.GroupTracker()

        refused = self._setup_extra(options)
        if refused:
            self.stdout.write(self.style.ERROR(refused))
            return

        self._run(options["source"], options["debug"])

    # ---- sub-command wiring -------------------------------------------------

    def _setup_engine(self, name, cmd):
        """Gives a reused watch_<x> Command the state its rule-layer methods
        expect, pointed at OUR shared camera/evidence dir and configured from
        our flags — mirrors what its own handle() would set up. Detection
        itself is never delegated to `cmd` (see _process_frame) — only the
        confirmation/alerting machinery is reused."""
        cmd.camera = self.camera
        cmd.violations_dir = self.violations_dir
        cmd.dry_run = self.dry_run
        cmd.clock_start = self.clock_start     # drinking evening band, holdup time block
        cmd.far = self.far
        cmd.tiles = self.tiles
        cmd.conf_override = None
        cmd.dwell_override = None
        cmd.tracker_name = "greedy"  # never consulted — we own person detection here
        cmd.show_stats = False
        cmd.ablate = set()
        # ONE ring and one client shared by all three engines (same camera, same frames).
        cmd.__dict__.update(self.ai_state)
        cmd.debug_pub = self.debug_pub        # one live-view publisher for all three engines
        cmd._debug_checked = True
        cmd.stats = Counter()
        cmd._alert_log = []
        cmd.stdout = self.stdout
        cmd.style = self.style
        # Every engine's own _create_alert references all three violation
        # types indirectly through shared helpers in some code paths, so all
        # three are created once here (get_or_create, so this is idempotent
        # with each standalone command's own handle()).
        cmd.smoking_type = self._vtype("smoking", "Public Smoking", "#f59e0b", "cigarette")
        # code="theft" (not "thief") — matches watch_thief.py's own fix; see
        # migration 0026 for why the two codes must never diverge again.
        cmd.thief_type = self._vtype("theft", "Holdup in Public Area", "#ef4444", "siren")
        cmd.drinking_type = self._vtype("drinking", "Public Drinking", "#8b5cf6", "beer")
        # Every engine gets its OWN evidence clip buffer (smoking, drinking
        # AND thief — thief's _create_alert didn't build a video_url at all
        # until watch_thief.py grew a ClipRecorder alongside this command).
        # watch_all.py skips this (getattr(self, "clip", None) silently
        # no-ops in _create_alert), which quietly drops evidence video for
        # anything run through it — don't repeat that here.
        cmd.clip = recognition.ClipRecorder(seconds=30, label=self.camera.code)
        if name == "smoking":
            cmd.mouth_check = True
        elif name == "drinking":
            cmd.mouth_check = True
            cmd.include_generic = False  # skipped for v1 — see watch_merged's design notes
            cmd.zones = []
            cmd.min_group_override = None
            cmd.group_duration_override = None

    def _vtype(self, code, label, color, icon):
        vt, _ = ViolationType.objects.get_or_create(
            code=code, defaults={"label": label, "color": color, "icon": icon})
        return vt

    def _check_route_coverage(self, model):
        """None if safe, else an error message: does every rule engine
        (smoking/drinking/thief) have at least one merged-model class routed
        to it via ROUTE_ENGINE?

        ROUTE_ENGINE matches by name (see the module docstring), so if a
        future retrain renames a class — "Bottle" to "Beer Bottle", say — the
        rename silently stops matching any ROUTE_ENGINE key and that engine
        gets NO detections at all for the rest of the run. Nothing looks
        broken: the process starts fine, the other two engines keep alerting
        normally, this one just never fires. That reads as "the model got
        worse" or "nothing happening tonight", not a wiring bug — exactly the
        kind of failure that should be loud at startup instead of discovered
        hours into a review. Checked here so it can't happen unnoticed.
        """
        names = model.names.values() if isinstance(model.names, dict) else model.names
        covered = {ROUTE_ENGINE[label.lower()] for label in names
                  if label.lower() in ROUTE_ENGINE}
        missing = set(ROUTE_ENGINE.values()) - covered
        if missing:
            return (
                f"Merged model's classes {sorted(names)!r} route (via ROUTE_ENGINE) to "
                f"{sorted(covered)!r}, leaving {sorted(missing)!r} with NO class at all. "
                "That engine would silently never produce a detection for the rest of "
                "the run — refusing to start rather than run one or two engines short "
                "with no visible error. Update ROUTE_ENGINE to match the model's actual "
                "class names, or re-export the model with the expected ones."
            )
        return None

    def _active(self, name, cfg):
        if self.enabled is not None:
            return name in self.enabled
        return getattr(cfg, f"{name}_enabled", True)

    # ---- capture -------------------------------------------------------------

    def _open(self, source):
        if source.isdigit():
            return cv2.VideoCapture(int(source))
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|analyzeduration;500000|probesize;500000|stimeout;5000000")
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self, source, debug):
        cap = self._open(source)
        if not cap.isOpened():
            self.stdout.write(self.style.ERROR(f"Could not open video source: {source}"))
            return

        is_live = source.isdigit() or "://" in source
        reader = recognition.LatestFrameReader(
            cap, open_fn=lambda: self._open(source),
            log=lambda m: self.stdout.write(self.style.WARNING(m)),
        ) if is_live else cap

        # A file source is seekable, so each engine can cut raw evidence clips
        # straight from it later; a live source gets its own rolling raw-frame
        # buffer instead — same setup each standalone command does in its own
        # _run_stream.
        for name in ("smoking", "drinking", "thief"):
            cmd = self.engines[name]["cmd"]
            cmd._source_path = None if is_live else source
            cmd._raw_buffer = recognition.RawFrameRecorder() if is_live else None
        self._extra_source(source, is_live)
        # Jump straight to the chosen start rather than decoding and throwing
        # away everything before it — on CPU that discarded prefix costs the
        # same per frame as the part being tested.
        if self.trim.seek(cap, is_live):
            self.stdout.write(self.style.SUCCESS(
                f"Trimmed to {self.trim.describe()} of the clip."))

        cfg = SystemSettings.load()
        cfg_at = time.time()
        mode = f"FAR {self.tiles[0]}x{self.tiles[1]}" if self.far else "near"
        names = ", ".join(list(self.engines) + list(self.EXTRA_ENGINES))
        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} [{mode}, {self.tracker_name} tracker] for: {names}. "
            "Ctrl+C to stop."
        ))

        if self.calibration_csv_path:
            self.calibration_file = open(
                self.calibration_csv_path, "w", newline="", encoding="utf-8")
            self.calibration_writer = csv.writer(self.calibration_file)
            self.calibration_writer.writerow([
                "frame_idx", "timestamp", "camera_id", "class_name", "confidence",
                "x1", "y1", "x2", "y2", "box_area_px", "track_id",
            ])
            self.stdout.write(self.style.WARNING(
                f"CALIBRATION MODE: raw detections logging to {self.calibration_csv_path} "
                "(merged-model inference floor forced to 0.01)."))

        if debug:
            cv2.namedWindow("LookOut - watch_merged (debug)", cv2.WINDOW_NORMAL)

        started = time.time()
        frames = 0
        fps_warned = False
        try:
            while True:
                ok, frame = reader.read()
                if not ok:
                    if is_live:
                        time.sleep(0.02)
                        continue
                    self.stdout.write(self.style.SUCCESS(f"End of {source} — done."))
                    break

                for name in ("smoking", "drinking", "thief"):
                    cmd = self.engines[name]["cmd"]
                    if cmd._raw_buffer is not None:
                        cmd._raw_buffer.add(frame, time.time())
                if self.ai_state["ai_ring"] is not None:
                    self.ai_state["ai_ring"].stash(frame)     # clean pixels for the AI checker's crops
                if self.debug_pub is not None:
                    self.debug_pub.stash(frame)               # and for the live processing view

                wall_now = time.time()
                if wall_now - cfg_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_at = wall_now
                frames += 1

                active = [n for n in self.engines if self._active(n, cfg)]
                extra = [n for n in self.EXTRA_ENGINES if self._active(n, cfg)]
                if not active and not extra:
                    continue

                # Content-time clock: video position for a file source (not
                # wall clock) so vote/dwell/duration/cooldown measure the
                # same seconds a human watching the clip would see, and a
                # raw clip cut later lines up with what the detector just
                # saw — even though processing routinely runs far slower
                # than real-time in --far mode. Wall-clock for a live
                # source, where video time and wall-clock time are the same
                # thing by definition. See the false-positive-suppression
                # brief's timing-bug finding: wall-clock badly overstated
                # every dwell/duration figure against an uploaded file (a
                # gathering reading "33/25s" on a 19s clip).
                now = wall_now if is_live else reader.get(cv2.CAP_PROP_POS_MSEC) / 1000.0

                # Reported as an ordinary end-of-clip finish: a trimmed run and
                # a whole one must look identical to _watch_detection_job,
                # which only sees the exit code.
                if self.trim.past_end(now, is_live):
                    self.stdout.write(self.style.SUCCESS(
                        f"End of {source} ({self.trim.describe()}) — done."))
                    break

                for name in ("smoking", "drinking", "thief"):
                    cmd = self.engines[name]["cmd"]
                    if cmd._source_path is not None:
                        cmd._video_pos_sec = now

                timestamp = now if not is_live else wall_now - started
                self._process_frame(frame, now, cfg, active, debug, frames,
                                    timestamp, extra)

                if not fps_warned and frames >= 20:
                    fps = frames / max(wall_now - started, 1e-6)
                    if fps < 1.5:
                        fps_warned = True
                        self.stdout.write(self.style.WARNING(
                            f"Running at {fps:.1f} FPS — the merged model + person pass "
                            "on CPU is not free. Drop --far or use a GPU."))

                if debug:
                    cv2.imshow("LookOut - watch_merged (debug)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            reader.stop() if is_live else cap.release()
            if self.calibration_file is not None:
                self.calibration_file.close()
                self.stdout.write(self.style.SUCCESS(
                    f"Calibration CSV written: {self.calibration_csv_path}"))
            if debug:
                cv2.destroyAllWindows()
            if self.show_stats:
                elapsed = time.time() - started
                for eng in self.engines.values():
                    eng["cmd"]._print_stats(elapsed)
            self.stdout.write(self.style.SUCCESS("Stopped."))

    # ---- per-frame: one merged pass, routed to each engine ------------------

    def _process_frame(self, frame, now, cfg, active, debug, frame_idx=None,
                       timestamp=None, extra=()):
        """Extra engines first, then the merged pass.

        The order matters for evidence: an extra engine does its own drawing
        (the parking engine marks the road edge and the vehicle it is judging),
        and running it first means those marks are already on the frame when
        the merged engines buffer it into their own evidence clips. The other
        way round, every clip but parking's would show the street with no line
        on it.
        """
        for name in extra:
            self._extra_frame(name, frame, now, cfg, debug)
        if active:
            self._merged_pass(frame, now, cfg, active, debug, frame_idx, timestamp)

    def _merged_pass(self, frame, now, cfg, active, debug, frame_idx=None, timestamp=None):
        for _name in active:
            self.engines[_name]["cmd"].apply_spec_settings(cfg)
        # One shared person pass per frame. bytetrack is safe here — see the
        # module docstring for why this differs from watch_all's hardcoded
        # greedy matcher.
        if self.tracker_name == "greedy":
            persons, ids = recognition.detect_persons(frame), None
        else:
            persons, ids = recognition.detect_persons_tracked(
                frame, tracker=f"{self.tracker_name}.yaml")

        # The merged model's own inference floor must be at or below every
        # active engine's configured confidence — otherwise a detection a
        # more permissive engine would have kept could be discarded before
        # engine-specific class floors ever see it. Each engine's own floor
        # is still applied afterward, per class, exactly as it would run
        # standalone (see _apply_engine_floor).
        conf_floor = min(getattr(cfg, f"{n}_confidence") for n in active) / 100
        if self.calibration_writer is not None:
            # Calibration wants the FULL raw confidence distribution, not
            # just what the engines would have kept — drop the inference
            # floor to (near) zero. Alerting is unaffected: _apply_engine_floor
            # still filters each engine's slice against the real cfg
            # confidence below, exactly as it would without this flag.
            conf_floor = min(conf_floor, 0.01)
        if self.far:
            dets = recognition.detect_merged_far(
                frame, conf=conf_floor, tiles=self.tiles, person_boxes=persons)
        else:
            dets = recognition.detect_merged(frame, conf=conf_floor)

        # Cigarette recall: a native-resolution pass over each person's crop. A
        # cigarette is ~24 px on the full frame; the whole-frame and tile passes
        # shrink it, the crop pass does not.
        if self.cascade and "smoking" in active and persons:
            casc = recognition.detect_smoking_cascade(
                frame, persons, conf=cfg.smoking_confidence / 100,
                crop_imgsz=recognition.NEAR_IMGSZ)
            if casc:
                dets = recognition._nms(list(dets) + casc)
                self.stats_cascade = getattr(self, "stats_cascade", 0) + len(casc)

        if self.calibration_writer is not None:
            self._log_calibration_rows(dets, persons, ids, frame_idx, timestamp)

        routed = {"smoking": [], "drinking": [], "thief": []}
        for d in dets:
            engine = ROUTE_ENGINE.get(d[5].lower())
            if engine is not None:
                routed[engine].append(d)

        # Raw merged-model output ALWAYS, thin yellow, before any per-engine
        # confidence floor / spatial rule / vote / dwell gating touches it —
        # so a detection that never survives gating is still visible for
        # sanity checking, not just each engine's own green/red confirmed
        # boxes drawn later in _process_track.
        for (x1, y1, x2, y2, score, label) in dets:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 220), 1)
            recognition.draw_label(frame, f"{label} {score * 100:.0f}%",
                                   x1, max(y1 - 8, 0), (0, 220, 220), scale=0.5)

        # Person boxes drawn once, shared — context for the evidence clip and
        # debug window regardless of which engine(s) end up alerting. Each
        # engine still keeps its own independent tracker/track ids beneath
        # this; the shared boxes are cosmetic only.
        for (x1, y1, x2, y2, _score) in persons:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (180, 180, 180), 1)

        for name in active:
            eng = self.engines[name]
            cmd, tracker = eng["cmd"], eng["tracker"]
            conf = getattr(cfg, f"{name}_confidence") / 100
            dwell = getattr(cfg, f"{name}_dwell")

            filtered = self._apply_engine_floor(name, cmd, routed[name], conf)
            tracks = tracker.update(persons, now, ids=ids)
            per_track = tracker.assign(filtered, now)
            if name == "smoking":
                cmd._feed_pose(frame, tracks, now)          # pose hand-to-mouth counter
                per_track = cmd._hires_check(frame, per_track, now)
                per_track = cmd._apply_mouth_cue(frame, per_track, now)
            elif name == "thief":
                cmd._frame_tracks = tracks
                per_track = cmd._apply_weapon_region_rule(per_track, frame)

            if name == "drinking":
                self._process_drinking_frame(
                    cmd, eng, tracks, per_track, now, dwell, cfg, frame, debug)
            elif name == "smoking":
                for track, tdets in per_track.items():
                    cmd._process_track(
                        track, tdets, now, dwell, cfg.alert_cooldown,
                        frame, debug)
            else:  # thief
                for track, tdets in per_track.items():
                    cmd._process_track(track, tdets, now, dwell, cfg.alert_cooldown, frame, debug)

        for name in active:
            cmd = self.engines[name]["cmd"]
            cmd.clip.add(frame, now)
            cmd._incident_gc(now, frame)        # close incidents whose object is gone

    def _log_calibration_rows(self, dets, persons, ids, frame_idx, timestamp):
        """Writes one CSV row per raw detection, before routing, per-engine
        confidence floors, or any rule-layer/alert logic. `track_id` is a
        best-effort association to the shared person pass (which person's box
        the detection's center falls inside, per that person's bytetrack id)
        — purely geometric, not a rule-layer decision, so it doesn't
        contradict "before any heuristic" — and only available when
        --tracker is bytetrack/botsort (ids is None under the default greedy
        person matcher, which this command never uses for its own tracks)."""
        for (x1, y1, x2, y2, score, label) in dets:
            track_id = self._nearest_person_track_id((x1, y1, x2, y2), persons, ids)
            self.calibration_writer.writerow([
                frame_idx, f"{timestamp:.3f}" if timestamp is not None else "",
                self.camera.id, label, f"{score:.4f}",
                x1, y1, x2, y2, (x2 - x1) * (y2 - y1), track_id,
            ])

    @staticmethod
    def _nearest_person_track_id(box, persons, ids):
        if not persons or ids is None:
            return ""
        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        for (px1, py1, px2, py2, _score), pid in zip(persons, ids):
            if px1 <= cx <= px2 and py1 <= cy <= py2:
                return pid
        return ""

    def _apply_engine_floor(self, name, cmd, dets, conf):
        """Each engine's own per-class confidence floor, applied to its
        routed slice of the merged model's output — the same stage each
        standalone command's own _detect() runs internally."""
        if name == "drinking":
            # watch_drinking has no CLASS_POLICY (its branded model was
            # always single-class) — just its own configured floor.
            kept = []
            for d in dets:
                cmd.stats[f"detected:{d[5]}"] += 1
                if d[4] >= conf:
                    kept.append(d)
                else:
                    cmd.stats[f"cut by confidence:{d[5]}"] += 1
            return kept
        return cmd._apply_class_floors(dets, conf)  # smoking / thief

    def _process_drinking_frame(self, cmd, eng, tracks, per_track, now, dwell,
                                cfg, frame, debug):
        """Path B (gathering) evaluated BEFORE Path A, so a cluster alert
        that fires this frame lands in _alert_log in time to suppress its
        members' solo alerts later in the same frame — mirrors
        watch_drinking.py's own _run_stream ordering exactly."""
        group_tracker = eng["group_tracker"]
        min_group = cfg.drinking_min_group
        group_duration = cfg.drinking_group_duration
        clusters = group_tracker.update(tracks, now, min_group)
        cmd.note_clusters(clusters, min_group, group_duration)
        for cluster in clusters:
            member_dets = [d for t, dets in per_track.items()
                           for d in dets
                           if t.id in cluster.member_ids and not t.is_scene]
            # a bottle on the table the group sits around is a scene detection
            member_dets += cmd._scene_dets_near(cluster, per_track)
            best = max(member_dets, key=lambda d: d[4]) if member_dets else None
            cluster.frame_conf = best[4] if best else 0.0
            if best:
                cluster.note_evidence(best, now)
            cmd._process_cluster(
                cluster, now, min_group, group_duration, cfg.alert_cooldown,
                frame, debug,
                evidence_max_age=cfg.drinking_evidence_max_age,
                cooldown_center_dist=cfg.drinking_cooldown_center_dist,
            )

        for track, dets in per_track.items():
            cmd._process_track(
                track, dets, now, dwell, cfg.alert_cooldown,
                frame, debug,
                held_dwell_seconds=cfg.drinking_held_dwell,
                mouth_proximity=cfg.drinking_mouth_proximity,
                cooldown_center_dist=cfg.drinking_cooldown_center_dist,
            )
