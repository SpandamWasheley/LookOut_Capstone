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
    from core.vision import recognition                     # noqa: E402
    from core.vision import scoring                         # noqa: E402
    from core.vision.obstruction_zone import ObstructionZone  # noqa: E402

# Where draw_zone writes the polygon by default.
DEFAULT_ZONE_PATH = settings.BASE_DIR / "core" / "vision" / "zones" / "obstruction_zone.json"

PARKING_CAMERA_CODE = "CAM-SMOKE-01"
SETTINGS_REFRESH_SECONDS = 5  # re-poll SystemSettings this often, not every frame


class Command(BaseCommand):
    help = (
        "Detects vehicles (car/motorcycle/bus/truck) for illegal-parking / "
        "obstruction monitoring. A vehicle is judged by how much of its ground "
        "footprint sits inside the drawn no-parking area, and for how long. Use "
        "--image PATH to check the area against a still, or run with no --image "
        "to watch --source."
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
                 "parked car keeps one identity; ObstructionZone._absorb_lost "
                 "carries the banked seconds across when it does not.",
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
        self.dry_run = options["dry_run"]
        self.trim = trimming.Trim(options["start"], options["end"])
        # Both modes by default (far already includes the near whole-frame pass);
        # --fast opts out to the single near pass.
        self.far = not options["fast"]
        self.preprocess = options["preprocess"]
        self.sharpen = options["sharpen"]
        self.tracker_name = f"{options['tracker']}.yaml"

        # The zone is the ONLY rule this command runs. There used to be two
        # others — a plain dwell timer ("parked in frame at all for 60s") and
        # the edge monitor now at detection_sandbox/obstruction.py — and which
        # got depended on how the command was started. Both are gone; having no
        # area is now a hard stop rather than a fallback, because falling back
        # to dwell would alert on every vehicle that merely stands still
        # anywhere in frame, which reads as a broken detector and not as a
        # missing polygon.
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
        # "Explicit" means the operator named a file. A caller that passes no
        # --zone at all (watch_merged_all defaults it to None) must fall to the
        # camera record, not be treated as having asked for a path of None.
        requested = options.get("zone") or ""
        explicit = bool(requested) and requested != str(DEFAULT_ZONE_PATH)
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

        zone_path = Path(requested or DEFAULT_ZONE_PATH)
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
                     if isinstance(s, dict) and s.get("type") == "zone"
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
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|analyzeduration;500000|probesize;500000|stimeout;5000000")
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap


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
