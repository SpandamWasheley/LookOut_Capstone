import datetime
import json
import os
import time
from pathlib import Path

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core import descriptions
from core.media import violation_media_path
from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision.progress import Spinner

# Importing recognition pulls in ultralytics and therefore torch — about four
# seconds of disk on this machine, and it happens before Django has even called
# handle(), so the command would otherwise sit silent from the moment it is
# typed. Spun here so the very first thing on screen is movement.
#
# tty_only: the test suite imports this module too, and the dashboard pipes a
# detector's stdout to a log file. Neither wants two extra lines about PyTorch.
with Spinner("Loading PyTorch", tty_only=True):
    from core.vision import preprocess as preproc           # noqa: E402
    from core.vision import trim as trimming                # noqa: E402
    from core.vision import debug_view                      # noqa: E402
    from core.vision import obstruction as obs              # noqa: E402
    from core.vision import recognition                     # noqa: E402
    from core.vision import scoring                         # noqa: E402
    from core.vision.obstruction_zone import ObstructionZone  # noqa: E402

# Where draw_zone writes the polygon by default.
DEFAULT_ZONE_PATH = settings.BASE_DIR / "core" / "vision" / "zones" / "obstruction_zone.json"

PARKING_CAMERA_CODE = "CAM-SMOKE-01"
# BGR, matched to detection_sandbox/obstruction_web.py's SIDE_COLOURS and the
# dashboard's EdgeCanvas (left orange, right cyan) so the --debug preview
# tells the two edges apart the same way the drawing screen did. Previously
# every edge drew in the same hardcoded orange, so two edges whose paths run
# close together on screen (as they often do - both drawn on the same street)
# were visually indistinguishable from one line.
EDGE_DEBUG_COLOURS = {"left": (0, 165, 255), "right": (255, 190, 0),
                      # A road ZONE is a closed polygon rather than a pair of
                      # kerbs, so it gets a third colour that reads as "this
                      # whole area", not "this boundary".
                      "road": (80, 80, 255)}
# Grace for the PLAIN dwell rule below, whose default dwell is 60s. It is far
# too short for the obstruction rule, whose dwell is minutes: one jeepney
# passing in front would reset a five-minute timer and the alert would never
# fire on a busy street. The obstruction path therefore does NOT use this - it
# runs its own tracker with a 12s grace plus a position-keyed cooldown that
# survives losing the track entirely. See core/vision/obstruction.py.
TRACK_GRACE_SECONDS = 2  # tolerate a couple missed frames before dropping a track
SETTINGS_REFRESH_SECONDS = 5  # re-poll SystemSettings this often, not every frame


def _center(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _iou(a, b):
    """Intersection-over-union of two (x1, y1, x2, y2) boxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


class Command(BaseCommand):
    help = (
        "Detects vehicles (car/motorcycle/bus/truck) for illegal-parking / "
        "obstruction monitoring. Use --image PATH to test on a single still "
        "picture, or run with no --image to watch the webcam with a dwell timer."
    )

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
                 "e.g. rtsp://user:pass@192.168.1.64:554/Streaming/Channels/102",
        )
        parser.add_argument(
            "--camera",
            default=PARKING_CAMERA_CODE,
            help=f"Camera code to attach alerts to (default {PARKING_CAMERA_CODE}). "
                 "Give each feed its own code when running one watcher per camera.",
        )
        parser.add_argument(
            "--confidence",
            type=float,
            default=None,
            help="Override the SystemSettings parking confidence (as 0-1). "
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
            "--dry-run",
            action="store_true",
            help="Detect and save evidence images but write no Alert rows.",
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
            help="Near mode ONLY — the single whole-frame pass at imgsz 1280, no "
                 "tiling. Faster but weaker on distant vehicles.",
        )
        parser.add_argument(
            "--tiles",
            default="2x2",
            help="Far mode only: tiling grid as ROWSxCOLS (e.g. 2x2, 3x3). More "
                 "tiles reach further but cost more inference per frame.",
        )
        preproc.add_cli_flags(parser, ablatable=False)
        trimming.add_cli_flags(parser)
        parser.add_argument(
            "--edges",
            default=None,
            help="Path to a JSON file marking the no-parking area, switching "
                 "this command to OBSTRUCTION mode: a vehicle is judged by how "
                 "much of its footprint sits in that area and for how long, "
                 "instead of by dwell alone. Omit to use the area stored on the "
                 "--camera record instead (drawn via the dashboard or "
                 "detection_sandbox/obstruction_web.py). Two shapes, and they "
                 "can be mixed in one file. ZONE - a closed polygon around the "
                 "road itself, anything standing inside it is an obstruction: "
                 '{"road": {"type": "zone", "points": [[x,y],[x,y],[x,y],...]}}. '
                 "EDGE - an open kerb line plus the side the footpath is on: "
                 '{"left": {"points": [[x,y],[x,y]], "side": 1}, "right": {...}}. '
                 "Omitting \"type\" means edge, so files written before zones "
                 "existed are read exactly as before. Coordinates are in the "
                 "frame as processed — a file passed here is used exactly as "
                 "given, with no resolution scaling.",
        )
        parser.add_argument(
            "--obstruction-pct", type=int, default=None,
            help="Share of the vehicle's footprint that must be past the edge. "
                 "Omit to use the camera record's value (default 50).",
        )
        parser.add_argument(
            "--obstruction-minutes", type=float, default=None,
            help="Minutes it must be held before it counts. Omit to use the "
                 "camera record's value (default 5). Read by the EDGE/ZONE "
                 "rule in core/vision/obstruction.py, which this command no "
                 "longer runs itself — see --zone. Kept because "
                 "watch_merged_all drives that rule through this class.",
        )
        # ---- single-polygon zone (core/vision/obstruction_zone.py) ---------
        parser.add_argument(
            "--zone", default=str(DEFAULT_ZONE_PATH),
            help="Path to the obstruction polygon JSON written by "
                 "`python manage.py draw_zone`. This is the rule this command "
                 "runs: a vehicle whose ground point sits inside the polygon "
                 "accrues seconds, and alerts once it reaches --alert-score.",
        )
        parser.add_argument(
            "--alert-score", type=float, default=None,
            help="Seconds inside the zone before a vehicle alerts. Omit to use "
                 "the camera's own value (the 'for N minutes' box in the zone "
                 "editor, x60; default 5 minutes). Time is counted in FOOTAGE "
                 "seconds for a file source, so a 30s clip can never reach a "
                 "300s score.",
        )
        parser.add_argument(
            "--inside-pct", type=int, default=None,
            help="Share of the vehicle's ground footprint that must be inside "
                 "the polygon for it to count as in the area, 5-100. Omit to "
                 "use the camera's own value (the 'Inside the road %%' box in "
                 "the zone editor; default 50). A share of the VEHICLE, so it "
                 "means the same thing on a motorcycle at the kerb and a truck "
                 "down the block.",
        )
        parser.add_argument(
            "--moving-weight", type=float, default=0.0,
            help="How fast the score grows while the vehicle is still MOVING, "
                 "as a fraction of the stopped rate. Default 0: only stopped "
                 "time counts, because a moving vehicle is not obstructing "
                 "anything. Raise it to give a crawling or stop-start vehicle "
                 "partial credit; 1.0 makes passing traffic accrue as fast as "
                 "a parked car, which on a busy road reaches any threshold "
                 "eventually.",
        )
        parser.add_argument(
            "--tracker", default="bytetrack", choices=["bytetrack", "botsort"],
            help="Vehicle association method (default bytetrack). The zone "
                 "rule is keyed on track id, so this is what decides whether a "
                 "parked car keeps one identity; ObstructionZone._adopt_or_new "
                 "covers the churn when it does not.",
        )

    def handle(self, *args, **options):
        # ViolationType/Camera aren't created by any migration, so get_or_create
        # here self-heals a fresh DB the same way watch_curfew does.
        self.parking_type, _ = ViolationType.objects.get_or_create(
            code="parking",
            defaults={"label": "Illegal Parking", "color": "#ef4444", "icon": "car"},
        )
        self.camera, _ = Camera.objects.get_or_create(
            code=options["camera"],
            defaults={"name": "Hikvision DS-2CD1047G2", "status": Camera.Status.ONLINE},
        )
        self.violations_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(self.violations_dir, exist_ok=True)

        # Set here so _create_alert never AttributeErrors regardless of path —
        # --image test mode never runs _run_stream's setup below, which is the
        # only place these get their real values. Same pattern as
        # watch_drinking.py/watch_smoking.py.
        self.clip = None
        self._source_path = None
        self._video_pos_sec = None
        self._raw_buffer = None

        self.conf_override = options["confidence"]
        self.dwell_override = options["dwell"]
        self.dry_run = options["dry_run"]
        self.trim = trimming.Trim(options["start"], options["end"])
        # Both modes by default (far already includes the near whole-frame pass);
        # --fast opts out to the single near pass.
        self.far = not options["fast"]
        self.preprocess = options["preprocess"]
        self.sharpen = options["sharpen"]
        # _load_edges / _build_monitors / _run_obstruction are NOT called here
        # any more — the single-polygon zone below is this command's only
        # trigger. They stay on the class because watch_merged_all drives them
        # directly as its fourth engine (see its _setup_extra), with its own
        # --edges / --obstruction-pct / --obstruction-minutes flags.
        self.tracker_name = f"{options['tracker']}.yaml"

        # The zone IS the rule this command runs, so having none is a hard stop
        # rather than a fallback. Falling back to the old dwell rule would mean
        # every vehicle that merely stands still for 60s alerts, anywhere in
        # frame — which reads as a broken detector, not as a missing zone.
        #
        # The CAMERA RECORD comes first: that is what the dashboard's Edge
        # Zones editor writes, so an operator who traces the road in the
        # browser gets the detector they just configured. The JSON file is the
        # fallback for a terminal-only setup (draw_zone), and --zone forces it.
        self.zone, where = self._load_zone(options)
        if self.zone is None:
            return
        self.stdout.write(self.style.SUCCESS(
            f"Obstruction zone from {where} "
            f"({self.zone.enter_fraction * 100:.0f}% of the vehicle inside, "
            f"{self.zone.alert_score:.0f}s to alert)."))

        # Reading yolov8n.pt off disk is the other four seconds, and it is
        # lazy — without this it would happen on the first frame instead, i.e.
        # silently, after the "Watching ..." line has already claimed the
        # detector is running. Pulled forward so the wait is where the user
        # expects it and has something moving on screen.
        with Spinner("Loading the vehicle model"):
            recognition.load_yolo()

        # The Run Detection page's live view. Every OTHER watcher gets this
        # from IncidentMixin._frame_start; this command is a plain BaseCommand
        # (it has no incidents — a vehicle either is in the zone or is not), so
        # it was the one detector that published nothing and left the page
        # saying "Waiting for the detector to start..." for the whole run.
        # None unless LOOKOUT_DEBUG_DIR is set, i.e. unless launched by the page.
        self.debug_pub = debug_view.DebugPublisher.from_env()
        self._area_sent = False     # the zone outline, published once
        try:
            rows, cols = (int(v) for v in options["tiles"].lower().split("x"))
            self.tiles = (rows, cols)
        except (ValueError, AttributeError):
            self.stdout.write(self.style.ERROR(
                f"Invalid --tiles {options['tiles']!r}; expected ROWSxCOLS like 2x2."
            ))
            return

        cfg = SystemSettings.load()
        if not cfg.parking_enabled:
            self.stdout.write(self.style.WARNING(
                "Parking detection is disabled in Settings (parking_enabled=False). "
                "Enable it in the dashboard, or it won't create alerts."
            ))
        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                "DRY RUN: evidence images will be saved but no alerts created."))

        if options["image"]:
            conf = self.conf_override or (cfg.parking_confidence / 100)
            self._run_image(options["image"], conf)
        else:
            self._run_stream(options["source"], options["debug"])

    # ---- the single-polygon zone -------------------------------------------

    def _load_zone(self, options):
        """(ObstructionZone, where-it-came-from), or (None, "") after printing why.

        Two sources, camera record first:

          CAMERA  self.camera.edges, written by the dashboard's Edge Zones
                  editor (and staged onto the -TEST camera by an upload run).
                  Stored in the PIXELS of the frame it was drawn on, with that
                  frame's size alongside, so it is normalised here.
          FILE    the JSON draw_zone writes, already normalised 0-1. Used when
                  the camera has nothing, or whenever --zone is given
                  explicitly — a flag the operator typed outranks a stored
                  record.
        """
        explicit = options["zone"] != str(DEFAULT_ZONE_PATH)
        # How much of the vehicle has to be in the area. --inside-pct wins;
        # otherwise the camera's own obstruction_pct, which IS the "Inside the
        # road %" box in the dashboard's zone editor — so that field drives the
        # rule again instead of being stored and ignored.
        pct = options["inside_pct"]
        if pct is None:
            pct = self.camera.obstruction_pct or 50
        # ...and how long it must hold it. The editor asks for MINUTES, the
        # zone counts SECONDS, so this is the one conversion between them.
        # Both boxes in that editor now drive the rule; before this the
        # minutes value was stored, staged onto the test camera by Run
        # Detection, and then read by nobody — every run used the CLI default
        # of 60s no matter what was typed.
        score = options["alert_score"]
        if score is None:
            score = (self.camera.obstruction_minutes or 5) * 60.0
        kwargs = {"alert_score": score,
                  "moving_weight": options["moving_weight"],
                  "enter_fraction": pct / 100.0}

        if not explicit:
            points = self._zone_points_from_camera()
            if points is not None:
                try:
                    return ObstructionZone(points, **kwargs), f"camera {self.camera.code}"
                except ValueError as exc:
                    self.stdout.write(self.style.ERROR(
                        f"The area saved on {self.camera.code} is unusable ({exc}). "
                        "Retrace it in Live Feeds -> Edge Zones."))
                    return None, ""

        zone_path = Path(options["zone"])
        if not zone_path.is_file():
            self.stdout.write(self.style.ERROR(
                f"No no-parking area for {self.camera.code}, and no zone file at "
                f"{zone_path}.\n"
                "Trace the road either way:\n"
                "  * in the dashboard — Live Feeds -> Edge Zones (saved on the camera), or\n"
                f"  * on this machine — python manage.py draw_zone --source <rtsp-url-or-clip.mp4>\n"
                "(LEFT click adds a point, S saves, Q quits.) Then re-run this command."
            ))
            return None, ""
        try:
            return ObstructionZone.load(zone_path, **kwargs), str(zone_path)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.stdout.write(self.style.ERROR(
                f"{zone_path} is not a usable zone ({exc}). Redraw it with "
                "`python manage.py draw_zone`."))
            return None, ""

    def _zone_points_from_camera(self):
        """The camera's road polygon as normalised (0-1) points, or None.

        camera.edges may still hold the old kerb lines (open paths keyed
        left/right, no "type"). Those are not convertible — a half-plane
        running off to infinity is not a closed shape — so they read as "no
        zone" and the caller falls through to the file. The dashboard says the
        same thing to the operator when it opens such a camera.
        """
        stored = self.camera.edges or {}
        spec = next((s for s in stored.values()
                     if isinstance(s, dict) and s.get("type") == obs.ZONE
                     and len(s.get("points") or []) >= 3), None)
        if spec is None:
            return None
        width = self.camera.edges_width
        height = self.camera.edges_height
        if not (width and height):
            # Without the frame it was drawn on, the pixels cannot be turned
            # into fractions and would be read as if the frame were 1x1.
            self.stdout.write(self.style.WARNING(
                f"{self.camera.code} has a saved area but no record of the frame size it "
                "was drawn at, so it cannot be scaled. Retrace it in Edge Zones."))
            return None
        return [[x / width, y / height] for x, y in spec["points"]]

    # ---- obstruction mode (watch_merged_all's fourth engine) ---------------

    def _load_edges(self, options):
        """Resolves the edge specs, their source resolution, and the pct/minutes
        thresholds — CLI flags override the camera record, same pattern as
        --confidence/--dwell. Returns True if this puts the command into
        OBSTRUCTION mode. The actual ObstructionMonitors are built lazily by
        _build_monitors() once a real frame size is known, since --edges (a
        file) and the camera record (self.camera.edges) may have been drawn
        against a different resolution than the live source turns out to be."""
        if options.get("edges"):
            try:
                with open(options["edges"], encoding="utf-8") as fh:
                    specs = json.load(fh)
            except (OSError, ValueError) as exc:
                self.stdout.write(self.style.ERROR(f"Could not read --edges: {exc}"))
                specs = {}
            # A file passed via --edges is documented as already being "in the
            # coordinates of the frame as processed" — no resolution recorded,
            # so no scaling is applied to it.
            source_size = None
        else:
            specs = self.camera.edges or {}
            source_size = (
                (self.camera.edges_width, self.camera.edges_height)
                if self.camera.edges_width and self.camera.edges_height
                else None
            )

        self._edge_specs = {
            name: spec for name, spec in specs.items()
            if len(spec.get("points") or []) >= 2
        }
        self._edge_source_size = source_size

        pct = options["obstruction_pct"]
        if pct is None:
            pct = self.camera.obstruction_pct
        minutes = options["obstruction_minutes"]
        if minutes is None:
            minutes = self.camera.obstruction_minutes

        enter = max(min(pct, 90), 10) / 100.0
        obs.ENTER_FRACTION = enter
        obs.EXIT_FRACTION = max(enter - 0.10, 0.05)
        self._enter_fraction = enter
        self._obstruction_seconds = max(minutes, 0.1) * 60

        self.monitors = None  # built once the first real frame size is known
        return bool(self._edge_specs)

    def _build_monitors(self, frame_shape):
        """Builds one ObstructionMonitor per edge, scaling stored points from
        the resolution they were drawn at (self._edge_source_size) to this
        camera's actual capture resolution — drawing tools and live streams
        are not guaranteed to agree on frame size, and a raw pixel mismatch
        would silently judge vehicles against the wrong line."""
        h, w = frame_shape[:2]
        src_w, src_h = self._edge_source_size or (None, None)
        rescale = bool(src_w and src_h and (src_w != w or src_h != h))

        monitors = {}
        for name, spec in self._edge_specs.items():
            points = spec["points"]
            if rescale:
                sx, sy = w / src_w, h / src_h
                points = [[x * sx, y * sy] for x, y in points]
            # "type" MUST be forwarded. Without it a zone spec falls through
            # to build_edge's edge default and gets read as an open path along
            # the polygon's outline - which still builds, still runs, and
            # silently judges vehicles against something that is not the area
            # the operator drew.
            edge = obs.build_edge({
                "type": spec.get("type", obs.EDGE),
                "points": points,
                "side": spec.get("side", 1),
            })
            monitors[name] = (edge, obs.ObstructionMonitor(
                edge, obstruction_seconds=self._obstruction_seconds))

        self.monitors = monitors
        if monitors:
            note = f" (scaled from {src_w}x{src_h} to {w}x{h})" if rescale else ""
            zones = sum(1 for spec in self._edge_specs.values()
                        if spec.get("type") == obs.ZONE)
            # The threshold means the same thing either way - a share of the
            # vehicle's own footprint - but "past the line" and "inside the
            # road" are very different sentences to an operator reading a log,
            # so say the one that matches what they actually drew.
            if zones and zones == len(monitors):
                shape, where = "zone(s)", "inside the road"
            elif zones:
                shape, where = "area(s)", "inside the marked area"
            else:
                shape, where = "edge(s)", "past the line"
            self.stdout.write(self.style.SUCCESS(
                f"OBSTRUCTION mode: {len(monitors)} {shape} "
                f"[{', '.join(monitors)}], {self._enter_fraction*100:.0f}% "
                f"{where} held for {self._obstruction_seconds/60:.1f} min{note}."
            ))

    def _run_obstruction(self, frame, vehicles, now_ts, cooldown, debug):
        """Judges each vehicle against every edge; alerts once per violation."""
        boxes = [v[:4] for v in vehicles]
        labels = [v[5] for v in vehicles]
        # Pedestrians standing on the road side of an edge next to a stopped
        # vehicle are people who had to walk around it - the most convincing
        # evidence there is that a footpath was actually blocked.
        people = [p[:4] for p in recognition.detect_persons(frame)]

        for name, (edge, monitor) in self.monitors.items():
            edge.draw(frame, EDGE_DEBUG_COLOURS.get(name, (0, 165, 255)), 2)
            for state, verdict in monitor.update(
                    boxes, now_ts, labels=labels, frame_shape=frame.shape,
                    pedestrians=people):
                # Draw ALWAYS, not just in --debug, so the evidence clip shows
                # the vehicle being judged against the line — matches
                # watch_smoking/watch_drinking/watch_thief.
                x1, y1, x2, y2 = state.box
                colour = ((0, 0, 220) if verdict == obs.OBSTRUCTION
                          else (0, 190, 230) if verdict == obs.WATCHING
                          else (150, 150, 150))
                cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
                recognition.draw_label(
                    frame, f"{name} {state.fraction*100:.0f}% {state.held:.0f}s",
                    x1, max(y1 - 8, 0), colour)
                if not state.fresh_alert:
                    continue
                detour = (f" {state.detours} pedestrian(s) forced onto the road."
                          if state.detours else "")
                # state.label is the DETECTOR's class for this vehicle
                # (car/motorcycle/bus/truck) — `name` is the EDGE's name
                # (left/right) and must not be used as the alert's "what was
                # detected" value, only in the description text below.
                alert = self._create_alert(
                    state.fraction, state.label or "vehicle", frame,
                    description=descriptions.road_edge(name, state.fraction, state.held / 60),
                    now=now_ts,
                )
                self.stdout.write(self.style.SUCCESS(
                    (f"ALERT created: {alert.code}" if alert else "ALERT suppressed (dry run)")
                    + f" [{name}] {state.summary()}"
                ))

    # ---- detection dispatch -----------------------------------------------

    def _preprocess(self, frame):
        """Enhance a dim/noisy frame before detection (no-op unless --preprocess,
        and daytime frames bypass inside preprocess() itself)."""
        if not self.preprocess:
            return frame
        return preproc.preprocess(frame, mode="near", sharpen=self.sharpen)

    def _detect(self, frame, conf):
        """Runs vehicle detection, using the long-range tiling cascade when
        --far is set, else the fast single (upsized) pass."""
        if self.far:
            return recognition.detect_vehicles_far(frame, conf=conf, tiles=self.tiles)
        return recognition.detect_vehicles(frame, conf=conf)

    # ---- single-image test mode -------------------------------------------

    def _run_image(self, path, conf):
        frame = recognition.load_image(path)
        if frame is None:
            self.stdout.write(self.style.ERROR(f"Could not read image: {path}"))
            return
        frame = self._preprocess(frame)

        vehicles = self._detect(frame, conf)
        if not vehicles:
            self.stdout.write(self.style.WARNING(
                f"No vehicles detected above confidence {conf}. "
                "Try lowering --confidence."
            ))
            return

        for (x1, y1, x2, y2, score, label) in vehicles:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 220), 2)
            recognition.draw_label(frame, f"{label} {score * 100:.0f}%",
                                   x1, max(y1 - 8, 0), (0, 0, 220))

        # No alert from a still. The rule is "inside the zone for N seconds",
        # and one frame carries no seconds — the old behaviour here alerted on
        # whichever vehicle scored highest, anywhere in frame, which is exactly
        # the trigger logic the zone replaced. Reported instead, so --image
        # stays useful for checking the polygon against a real frame.
        summary = ", ".join(sorted({v[5] for v in vehicles}))
        inside = [v for v in vehicles
                  if self.zone.fraction_inside(v[:4], frame.shape) >= self.zone.enter_fraction]
        self.zone.draw(frame)
        self.stdout.write(self.style.SUCCESS(
            f"Detected {len(vehicles)} vehicle(s): {summary}. "
            f"{len(inside)} inside the zone. No alert — the zone rule needs "
            f"{self.zone.alert_score:.0f}s and a still has none."
        ))

    # ---- live stream dwell mode -------------------------------------------

    def _open_capture(self, source):
        """Opens a webcam index or a stream URL / file path."""
        if source.isdigit():
            return cv2.VideoCapture(int(source))
        cap = cv2.VideoCapture(source)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run_dwell(self, frame, vehicles, now_ts, cfg, debug):
        """Plain dwell-timer parking rule (no edges configured on the camera):
        track vehicles across frames by IoU, alert once each has sat still
        past the dwell threshold. Split out of _run_stream so a caller that
        shares this camera across multiple detectors (e.g. watch_merged) can
        drive it directly per-frame, exactly as _run_obstruction already
        could — see watch_merged.py's _run_parking_frame.
        """
        dwell_seconds = self.dwell_override or cfg.parking_dwell
        move_tolerance = cfg.parking_move_tolerance
        tracks = self._dwell_tracks

        # Greedy IoU association of detections to existing tracks — good
        # enough for a stationary parking camera (no ByteTrack needed).
        matched_ids = set()
        assigned = []  # (box, score, label, track_id), in vehicles' order
        for (x1, y1, x2, y2, score, label) in vehicles:
            box = (x1, y1, x2, y2)
            best_id, best_iou = None, 0.3
            for tid, tr in tracks.items():
                if tid in matched_ids:
                    continue
                overlap = _iou(box, tr["box"])
                if overlap > best_iou:
                    best_id, best_iou = tid, overlap

            if best_id is None:
                best_id = self._dwell_next_id
                self._dwell_next_id += 1
                tracks[best_id] = {"anchor": _center(box), "still_since": now_ts,
                                   "alerted_at": 0}
            matched_ids.add(best_id)
            assigned.append((box, score, label, best_id))

        # Merge duplicate tracks: the near whole-frame pass and the far
        # tiling pass can each land a box for the SAME car too far apart
        # (IoU under the 0.3 match bar above) to land on one track in a
        # single shot — especially across frames where only one of the
        # two passes fires — so each anchors its own track. Two tracks
        # whose THIS-FRAME boxes now overlap this heavily (the same bar
        # detect_vehicles_far()'s own NMS already trusts to mean "same
        # object") can't be two real vehicles parked in the same spot.
        # Fold the younger one into the older, which has the more
        # trustworthy dwell timer.
        frame_box = {tid: box for box, _, _, tid in assigned}
        merge_into = {}
        ids_this_frame = list(matched_ids)
        for i, tid_a in enumerate(ids_this_frame):
            if tid_a in merge_into:
                continue
            for tid_b in ids_this_frame[i + 1:]:
                if tid_b in merge_into:
                    continue
                if _iou(frame_box[tid_a], frame_box[tid_b]) < 0.5:
                    continue
                older, younger = (
                    (tid_a, tid_b)
                    if tracks[tid_a]["still_since"] <= tracks[tid_b]["still_since"]
                    else (tid_b, tid_a)
                )
                merge_into[younger] = older
        for younger in merge_into:
            matched_ids.discard(younger)
            del tracks[younger]

        seen_ids = set()
        for box, score, label, tid in assigned:
            tid = merge_into.get(tid, tid)
            if tid in seen_ids:
                continue  # the merged-away duplicate of a track already handled this frame
            seen_ids.add(tid)
            x1, y1, x2, y2 = box
            tr = tracks[tid]
            tr.update({"box": box, "last_seen": now_ts, "label": label, "score": score})

            # Movement reset: if the vehicle drifted past the tolerance,
            # it's moving (not parked) — re-anchor and restart its timer.
            cx, cy = _center(box)
            ax, ay = tr["anchor"]
            if ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5 > move_tolerance:
                tr["anchor"] = (cx, cy)
                tr["still_since"] = now_ts

            parked_for = now_ts - tr["still_since"]
            # Draw ALWAYS, not just in --debug, so the evidence clip shows the
            # vehicle being timed — matches watch_smoking/watch_drinking/
            # watch_thief and _run_obstruction above.
            color = (0, 0, 220) if parked_for >= dwell_seconds else (0, 200, 0)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            recognition.draw_label(frame, f"{label} {score * 100:.0f}% {parked_for:.0f}s",
                                   x1, max(y1 - 8, 0), color)

            if parked_for < dwell_seconds:
                continue
            if now_ts - tr["alerted_at"] < cfg.alert_cooldown:
                continue
            alert = self._create_alert(
                score, label, frame,
                description=descriptions.parking(label, parked_for),
                now=now_ts,
            )
            tr["alerted_at"] = now_ts
            self.stdout.write(self.style.SUCCESS(
                (f"ALERT created: {alert.code}" if alert else "ALERT suppressed (dry run)")
                + f" ({label})"
            ))

        # drop tracks not seen recently (grace for detector flicker)
        for tid in list(tracks.keys()):
            if tid in matched_ids:
                continue
            if now_ts - tracks[tid]["last_seen"] > TRACK_GRACE_SECONDS:
                del tracks[tid]

    def _run_stream(self, source, debug):
        cap = self._open_capture(source)
        if not cap.isOpened():
            self.stdout.write(self.style.ERROR(f"Could not open video source: {source}"))
            return

        # Live sources get the always-latest reader so slow far-mode processing
        # never falls behind the stream; a file is read directly.
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

        # Settings are re-polled every few seconds (like watch_curfew) so edits
        # made in the dashboard's Parking config take effect live, without a
        # restart. CLI flags, if given, still win over the stored values.
        cfg = SystemSettings.load()
        cfg_loaded_at = time.time()
        # _run_dwell's per-track state: id -> {box, anchor, still_since,
        # last_seen, label, score, alerted_at}
        self._dwell_tracks = {}
        self._dwell_next_id = 0

        # Rolling buffer of annotated frames — on an alert it's written out as
        # the evidence clip, so the card shows the vehicle's box (and, in
        # obstruction mode, the edge line) building up to the violation,
        # not just a single still. Same pattern as watch_drinking/watch_smoking.
        self.clip = recognition.ClipRecorder(seconds=30, label=self.camera.code)

        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} [zone: {len(self.zone.points_norm)} points, "
            f"{self.zone.alert_score:.0f}s to alert, moving weight "
            f"{self.zone.moving_weight:g}, {self.tracker_name}]. "
            f"Time is {'wall clock' if is_live else 'footage position'}. "
            "Press Ctrl+C to stop."
        ))

        if debug:
            cv2.namedWindow("LookOut - watch_parking (debug)", cv2.WINDOW_NORMAL)

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

                # Buffer the frame RAW, before any drawing touches it — see
                # RawFrameRecorder.
                if self._raw_buffer is not None:
                    self._raw_buffer.add(frame, time.time())

                # Enhance dim/noisy frames before detection (daytime bypasses).
                frame = self._preprocess(frame)

                wall_now = time.time()
                if wall_now - cfg_loaded_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_loaded_at = wall_now

                if not cfg.parking_enabled:
                    time.sleep(0.5)
                    continue

                # Content-time clock: video position for a file source (not
                # wall clock) so the parked-duration dwell measures the same
                # seconds a human watching the clip would see — even though
                # processing routinely runs far slower than real-time in
                # --far mode. Wall-clock for a live source, where video time
                # and wall-clock time are the same thing by definition.
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

                # Clean pixels for the live view, taken before _run_zone draws
                # the polygon and the boxes on this frame — the page draws its
                # own labels, and baked-in ones would double up.
                if self.debug_pub is not None:
                    self.debug_pub.stash(frame)
                    # Once, on the first real frame: the page cannot show which
                    # area a vehicle is being judged against unless it is told.
                    if not self._area_sent:
                        h, w = frame.shape[:2]
                        self.debug_pub.set_area(
                            [(x * w, y * h) for x, y in self.zone.points_norm])
                        self._area_sent = True

                conf = self.conf_override or (cfg.parking_confidence / 100)
                self._run_zone(frame, now_ts, conf)

                if self.debug_pub is not None:
                    self.debug_pub.commit(now_ts)

                # Buffer this annotated frame (the zone polygon and the vehicle
                # boxes are already drawn on it by _run_zone) for the evidence
                # clip.
                self.clip.add(frame, now_ts)

                if debug:
                    cv2.imshow("LookOut - watch_parking (debug)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            reader.stop() if is_live else cap.release()
            if debug:
                cv2.destroyAllWindows()
            self.stdout.write(self.style.SUCCESS("Stopped."))

    # ---- the zone rule -----------------------------------------------------

    def _run_zone(self, frame, now_ts, conf):
        """One pass of the single-polygon rule: detect, score, draw, alert.

        Drawing is unconditional, not gated behind --debug: the annotated frame
        goes straight into the evidence clip, so the operator reviewing an alert
        sees the polygon the vehicle was judged against and the box colour that
        says why. A clip of an unmarked street is not evidence of an
        obstruction.
        """
        tracked = recognition.detect_vehicles_tracked(
            frame, conf=conf, tracker=self.tracker_name)
        # ObstructionZone wants (track_id, box, label, conf); labels are already
        # the lowercase COCO names (car/motorcycle/bus/truck) it filters on, so
        # no mapping is needed. Boxes with no id yet are passed through and
        # dropped inside update() — nothing can be scored without an identity.
        dets = [(tid, (x1, y1, x2, y2), label, score)
                for (x1, y1, x2, y2, score, label, tid) in tracked]

        hits = self.zone.update(dets, now_ts, frame.shape)
        self._publish_subjects(now_ts, dets, frame.shape)
        self.zone.draw(frame)

        for hit in hits:
            seconds = hit["score"]
            alert = self._create_alert(
                hit["conf"], hit["label"], frame,
                description=f"Obstruction: {hit['label']} stopped in zone {seconds:.0f}s",
                now=now_ts,
            )
            self.stdout.write(self.style.SUCCESS(
                (f"ALERT created: {alert.code}" if alert else "ALERT suppressed (dry run)")
                + f" [zone] #{hit['track_id']} {hit['label']} {seconds:.0f}s"
                  f" ({hit['conf'] * 100:.0f}%)"
            ))

    def _publish_subjects(self, now_ts, dets=(), frame_shape=None):
        """Tell the live view what each vehicle is doing this frame.

        Statuses are the DISPLAY names the other detectors publish via
        debug_view.status_for — "Likely" / "Possible" / "Monitoring" / "Below
        Monitoring", not the internal level codes. ProcessingView prints
        `status` verbatim and looks its colour up by that exact string, so
        publishing "warning" here showed the word "warning" on screen with no
        colour at all.

        The status follows PROGRESS toward the threshold, not mere presence:

            alerted .................... Likely      (the threshold was reached)
            >= SCORE_WARNING of the way  Possible    (well on its way)
            anything else ............. Monitoring   (seen; little or nothing banked)

        Keyed to the score rather than to "is it in the zone" because the
        latter put a vehicle on Possible the instant it entered, with zero
        seconds banked — so the badge jumped past Monitoring entirely and said
        "Possible" about a car that had just driven in. Every other detector
        earns Possible by accumulating; this now does too, against the same
        0.55 bar the scoring model uses, so the word means the same thing on
        every card in the system.

        `score` is the share of the way to the threshold, which is the thing an
        operator is actually waiting on.
        """
        if self.debug_pub is None:
            return
        for state in self.zone.states.values():
            if state.box is None or state.last_seen != now_ts:
                continue
            # Only what is actually being judged. `score > 0` keeps a vehicle
            # on screen through a frame where its box slips over the line, so
            # it does not flicker in and out of the list while its banked
            # seconds are still being held.
            if not state.inside and state.score <= 0:
                continue
            share = state.score / max(self.zone.alert_score, 1e-6)
            if state.alerted:
                status = scoring.label_of(scoring.VIOLATION)      # "Likely"
            elif state.inside and share >= scoring.SCORE_WARNING:
                status = scoring.label_of(scoring.WARNING)        # "Possible"
            else:
                status = scoring.label_of(scoring.MONITORING)     # "Monitoring"
            pct = min(100, round(state.score / max(self.zone.alert_score, 1e-6) * 100))
            # [{"name", "points"}], the shape debug_view.indicator_list produces
            # and ProcessingView's testing view reads as `${i.name} ${i.points}`.
            # Plain strings render as "undefined undefined".
            indicators = [
                {"name": "% of vehicle in zone",
                 "points": int(round(state.fraction * 100))},
                {"name": "seconds banked", "points": int(round(state.score))},
                {"name": "stopped" if state.stationary else "moving",
                 "points": int(round(state.score / max(self.zone.alert_score, 1e-6) * 100))},
            ]
            self.debug_pub.note(
                key=("parking", state.track_id), violation="parking",
                ident=state.track_id, box=state.box, status=status, score_pct=pct,
                indicators=indicators, multipliers={}, momentum=round(state.score, 1),
                kind="vehicle",
            )

        # Vehicles the rule is NOT judging, published as "Below Monitoring".
        #
        # They have no zone state — a vehicle outside the polygon accrues
        # nothing and can never alert, which is the behaviour asked for. But
        # dropping them from the view entirely made a zone drawn over the wrong
        # strip of road look exactly like a detector that had gone blind: a
        # motorcycle parked a metre outside the area was found by YOLO at 0.6
        # confidence every single frame and simply never appeared on screen.
        #
        # ProcessingView already draws this status dashed and faded, and the
        # Panel view filters it out — so an operator still sees only what is
        # being judged, while the Testing view shows what was seen and passed
        # over. Which is the difference between "not watched" and "not found".
        watched = {st.track_id for st in self.zone.states.values()}
        for tid, box, label, conf in dets:
            if tid is None or tid in watched:
                continue
            # The actual share, not a flat zero. "outside the zone 0" answered
            # the question nobody was asking: what an operator needs to know is
            # HOW FAR outside — 48% reads as "nudge the polygon", 5% reads as
            # "that vehicle is nowhere near it". Cheap: one point-in-polygon
            # test per sample on a box we already have.
            share = (self.zone.fraction_inside(box, frame_shape) * 100
                     if frame_shape is not None else 0)
            self.debug_pub.note(
                key=("parking", tid), violation="parking", ident=tid, box=box,
                status=debug_view.BELOW, score_pct=0,
                indicators=[
                    {"name": "% of vehicle in zone", "points": int(round(share))},
                    # The label renders as "<name> <points>", so the bar has to
                    # be the NUMBER to read as "needs 50" rather than "needs 50% 0".
                    {"name": "needs", "points": int(round(self.zone.enter_fraction * 100))},
                ],
                multipliers={}, momentum=0.0, kind="vehicle",
            )

    # ---- shared alert creation --------------------------------------------

    def _create_alert(self, score, label, frame, description, now=None):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = label.replace(" ", "_")
        filename = f"{ts_label}_parking_{tag}.jpg"
        cv2.imwrite(str(self.violations_dir / filename), frame)
        image_url = violation_media_path(filename)

        # Write the ~30s evidence clip (annotated frames leading up to the
        # alert) — same pattern as watch_drinking/watch_smoking/watch_thief.
        # self.clip stays None for --image test mode, which never runs
        # _run_stream's setup and has no timeline of frames to buffer anyway.
        video_url = ""
        if self.clip is not None:
            self.clip.add(frame, now if now is not None else time.time())
            video_name = f"{ts_label}_parking_{tag}.mp4"
            if self.clip.save(self.violations_dir / video_name):
                video_url = violation_media_path(video_name)

        # RAW (unannotated, full source frame rate/resolution) clip. File
        # sources cut straight from the source file (best quality, real fps);
        # live sources fall back to the rolling RawFrameRecorder buffer.
        raw_video_url = ""
        raw_name = f"{ts_label}_parking_{tag}_raw.mp4"
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
            type=self.parking_type,
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
