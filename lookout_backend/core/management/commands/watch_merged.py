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
already matches either way), EXCEPT one case-sensitive coincidence worth
knowing: watch_drinking.GENERIC_LABELS (the weaker "must be raised to the
mouth, 2x dwell" vessel path) is the set of lowercase COCO names {"bottle",
"wine glass", "cup"} — the merged model's "Bottle" (capital B) does NOT match
it, so a Bottle detection is treated as full branded evidence, the same tier
as watch_drinking's own "Red Horse" class, not degraded generic evidence.
That's the behavior we want, but it depends on the merged model never being
re-exported with a lowercase "bottle" class name — if it ever is, Bottle
detections would silently downgrade to the weaker generic-vessel path.

Unlike watch_all.py (which runs THREE separate custom models per frame, one
per detector, sharing only the person-detection pass), this genuinely is one
model doing all the detection — so the usual persist=True/bytetrack
corruption concern doesn't apply here: there is only ONE call per frame to
the shared person-tracking model, not three interleaved ones, so bytetrack
is safe and used by default for person tracking (see --tracker).

Note: watch_all.py's own reuse of SmokingCommand/DrinkingCommand has two gaps
this command does NOT repeat — it calls _process_track with too few
positional arguments for smoking/drinking (missing face_threshold, which
raises TypeError on the first frame with any person in view) and never
drives drinking's Path B (GroupTracker/_process_cluster) at all.
"""
import os
import time
from collections import Counter

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import Camera, SystemSettings, ViolationType
from core.vision import recognition, tracking

from .watch_smoking import Command as SmokingCommand
from .watch_thief import Command as ThiefCommand
from .watch_drinking import Command as DrinkingCommand, GENERIC_LABELS

MERGED_CAMERA_CODE = "CAM-MERGED-TEST"
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
        parser.add_argument("--dry-run", action="store_true",
                            help="Detect and save evidence but write no Alert rows.")
        parser.add_argument("--debug", action="store_true",
                            help="Show a preview window with every engine's boxes.")
        parser.add_argument("--stats", action="store_true",
                            help="On exit, print each engine's own detection-stage stats "
                                 "(same --stats output each standalone watcher prints).")

    def handle(self, *args, **options):
        if not recognition.merged_model_available():
            self.stdout.write(self.style.ERROR(
                f"Merged model not found at {recognition.MERGED_MODEL_PATH}. "
                "Copy the trained best.pt there, or set the MERGED_MODEL env var."
            ))
            return

        # Load eagerly (normally lazy, on first detection call) so this check
        # runs — and can fail loudly — before opening a camera or touching the
        # DB. See the module docstring: watch_drinking treats a Bottle-routed
        # detection as full branded evidence UNLESS its exact label string
        # collides with GENERIC_LABELS, in which case it silently downgrades
        # to the weak "raised-to-mouth-only, 2x dwell" generic-vessel path —
        # with no error, just a quietly worse true-positive rate. Fail here
        # instead of letting that happen unnoticed.
        model = recognition.load_merged_model()
        collision = self._bottle_generic_collision(model)
        if collision:
            self.stdout.write(self.style.ERROR(collision))
            return

        self.camera, _ = Camera.objects.get_or_create(
            code=options["camera"],
            defaults={"name": "Merged-Model Monitor", "status": Camera.Status.ONLINE},
        )
        self.violations_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(self.violations_dir, exist_ok=True)

        self.far = not options["fast"]
        self.dry_run = options["dry_run"]
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
        valid = {"smoking", "drinking", "thief"}
        if only - valid:
            self.stdout.write(self.style.ERROR(
                f"--only: unknown {', '.join(only - valid)}. Valid: {', '.join(valid)}."))
            return
        self.enabled = only or None  # None => decide per-frame from Settings

        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                "DRY RUN: evidence images will be saved but no alerts created."))

        self.engines = {}
        for name, cls in (("smoking", SmokingCommand),
                          ("drinking", DrinkingCommand),
                          ("thief", ThiefCommand)):
            cmd = cls()
            self._setup_engine(name, cmd)
            self.engines[name] = {"cmd": cmd, "tracker": tracking.PersonTracker()}
        self.engines["drinking"]["group_tracker"] = tracking.GroupTracker()

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
        cmd.far = self.far
        cmd.tiles = self.tiles
        cmd.conf_override = None
        cmd.dwell_override = None
        cmd.tracker_name = "greedy"  # never consulted — we own person detection here
        cmd.show_stats = False
        cmd.ablate = set()
        cmd.stats = Counter()
        cmd._alert_log = []
        cmd.stdout = self.stdout
        cmd.style = self.style
        # Every engine's own _create_alert references all three violation
        # types indirectly through shared helpers in some code paths, so all
        # three are created once here (get_or_create, so this is idempotent
        # with each standalone command's own handle()).
        cmd.smoking_type = self._vtype("smoking", "Public Smoking", "#f59e0b", "cigarette")
        cmd.thief_type = self._vtype("thief", "Theft / Robbery", "#ef4444", "siren")
        cmd.drinking_type = self._vtype("drinking", "Public Drinking", "#8b5cf6", "beer")
        # Every engine gets its OWN evidence clip buffer (smoking, drinking
        # AND thief — thief's _create_alert didn't build a video_url at all
        # until watch_thief.py grew a ClipRecorder alongside this command).
        # watch_all.py skips this (getattr(self, "clip", None) silently
        # no-ops in _create_alert), which quietly drops evidence video for
        # anything run through it — don't repeat that here.
        cmd.clip = recognition.ClipRecorder(seconds=30, label=self.camera.code)
        if name == "smoking":
            cmd.face_check = True
        elif name == "drinking":
            cmd.face_check = True
            cmd.include_generic = False  # skipped for v1 — see watch_merged's design notes
            cmd.zones = []
            cmd.min_group_override = None
            cmd.group_duration_override = None

    def _vtype(self, code, label, color, icon):
        vt, _ = ViolationType.objects.get_or_create(
            code=code, defaults={"label": label, "color": color, "icon": icon})
        return vt

    def _bottle_generic_collision(self, model):
        """None if safe, else an error message: does the merged model's
        Bottle-routed class name (as it will actually appear on a detection,
        e.g. "Bottle") collide with watch_drinking.GENERIC_LABELS — the set
        of lowercase COCO vessel names {"bottle", "wine glass", "cup"}?
        watch_drinking checks that membership with an EXACT, non-lowercased
        compare (`best_label in GENERIC_LABELS`), so this only bites if the
        merged model's class is itself lowercase "bottle" — but if it ever
        is (a re-export, a different training run), every Bottle detection
        would silently take the weak generic-vessel path instead of full
        branded evidence, and nothing would look broken — the detector would
        just alert less than it should, which reads as poor accuracy, not a
        wiring bug. Checked at startup so that failure mode can't happen
        unnoticed.
        """
        names = model.names.values() if isinstance(model.names, dict) else model.names
        for label in names:
            if ROUTE_ENGINE.get(label.lower()) != "drinking":
                continue
            if label in GENERIC_LABELS:
                return (
                    f"Merged model's Bottle class is named {label!r}, which collides "
                    f"with watch_drinking.GENERIC_LABELS {sorted(GENERIC_LABELS)!r}. "
                    "Every Bottle detection would silently be treated as weak generic-"
                    "vessel evidence (must be raised to the mouth, 2x dwell) instead of "
                    "full branded evidence — refusing to start rather than alert less "
                    "than expected with no visible error. Re-export the merged model "
                    "with a non-colliding Bottle class name (e.g. capitalized "
                    "'Bottle'), or if this is intentional, update GENERIC_LABELS in "
                    "watch_drinking.py to exclude it."
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
        cap = cv2.VideoCapture(source)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self, source, debug):
        cap = self._open(source)
        if not cap.isOpened():
            self.stdout.write(self.style.ERROR(f"Could not open video source: {source}"))
            return

        is_live = source.isdigit() or "://" in source
        reader = recognition.LatestFrameReader(cap) if is_live else cap

        # A file source is seekable, so smoking/drinking can cut raw evidence
        # clips straight from it later; a live source gets its own rolling
        # raw-frame buffer instead — same setup each standalone command does
        # in its own _run_stream. Thief doesn't use either (no raw clip).
        for name in ("smoking", "drinking"):
            cmd = self.engines[name]["cmd"]
            cmd._source_path = None if is_live else source
            cmd._raw_buffer = recognition.RawFrameRecorder() if is_live else None

        cfg = SystemSettings.load()
        cfg_at = time.time()
        mode = f"FAR {self.tiles[0]}x{self.tiles[1]}" if self.far else "near"
        names = ", ".join(self.engines)
        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} [{mode}, {self.tracker_name} tracker] for: {names}. "
            "Ctrl+C to stop."
        ))

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

                for name in ("smoking", "drinking"):
                    cmd = self.engines[name]["cmd"]
                    if cmd._raw_buffer is not None:
                        cmd._raw_buffer.add(frame, time.time())

                now = time.time()
                if now - cfg_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_at = now
                frames += 1

                active = [n for n in self.engines if self._active(n, cfg)]
                if not active:
                    continue

                for name in ("smoking", "drinking"):
                    cmd = self.engines[name]["cmd"]
                    if cmd._source_path is not None:
                        cmd._video_pos_sec = reader.get(cv2.CAP_PROP_POS_MSEC) / 1000.0

                self._process_frame(frame, now, cfg, active, debug)

                if not fps_warned and frames >= 20:
                    fps = frames / max(now - started, 1e-6)
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
            if debug:
                cv2.destroyAllWindows()
            if self.show_stats:
                elapsed = time.time() - started
                for eng in self.engines.values():
                    eng["cmd"]._print_stats(elapsed)
            self.stdout.write(self.style.SUCCESS("Stopped."))

    # ---- per-frame: one merged pass, routed to each engine ------------------

    def _process_frame(self, frame, now, cfg, active, debug):
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
        if self.far:
            dets = recognition.detect_merged_far(
                frame, conf=conf_floor, tiles=self.tiles, person_boxes=persons)
        else:
            dets = recognition.detect_merged(frame, conf=conf_floor)

        routed = {"smoking": [], "drinking": [], "thief": []}
        for d in dets:
            engine = ROUTE_ENGINE.get(d[5].lower())
            if engine is not None:
                routed[engine].append(d)

        # Person boxes drawn once, shared — context for the evidence clip and
        # debug window regardless of which engine(s) end up alerting. Each
        # engine still keeps its own independent tracker/track ids beneath
        # this; the shared boxes are cosmetic only.
        for (x1, y1, x2, y2, _score) in persons:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (180, 180, 180), 1)

        # Snapshot before any violation box is drawn — face recognition
        # (smoking/drinking's citation-prefill match) must run against a
        # clean frame, same reasoning as each standalone command's own loop.
        clean_frame = frame.copy()

        for name in active:
            eng = self.engines[name]
            cmd, tracker = eng["cmd"], eng["tracker"]
            conf = getattr(cfg, f"{name}_confidence") / 100
            dwell = getattr(cfg, f"{name}_dwell")

            filtered = self._apply_engine_floor(name, cmd, routed[name], conf)
            tracks = tracker.update(persons, now, ids=ids)
            per_track = tracker.assign(filtered, now)
            if name == "smoking":
                per_track = cmd._apply_face_rule(frame, per_track, now)

            if name == "drinking":
                self._process_drinking_frame(
                    cmd, eng, tracks, per_track, now, dwell, cfg, frame, debug, clean_frame)
            elif name == "smoking":
                for track, tdets in per_track.items():
                    cmd._process_track(
                        track, tdets, now, dwell, cfg.alert_cooldown,
                        frame, debug, cfg.curfew_confidence, clean_frame)
            else:  # thief — no face_threshold/clean_frame param on this one
                for track, tdets in per_track.items():
                    cmd._process_track(track, tdets, now, dwell, cfg.alert_cooldown, frame, debug)

        for name in active:
            self.engines[name]["cmd"].clip.add(frame, now)

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
                                cfg, frame, debug, clean_frame):
        """Path B (gathering) evaluated BEFORE Path A, so a cluster alert
        that fires this frame lands in _alert_log in time to suppress its
        members' solo alerts later in the same frame — mirrors
        watch_drinking.py's own _run_stream ordering exactly."""
        group_tracker = eng["group_tracker"]
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

        for track, dets in per_track.items():
            cmd._process_track(
                track, dets, now, dwell, cfg.alert_cooldown,
                frame, debug, cfg.curfew_confidence, clean_frame,
            )
