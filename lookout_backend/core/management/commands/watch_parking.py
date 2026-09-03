import datetime
import json
import os
import time

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import preprocess as preproc
from core.vision import obstruction as obs
from core.vision import recognition

PARKING_CAMERA_CODE = "CAM-PARKING"
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
        parser.add_argument(
            "--edges",
            default=None,
            help="Path to a JSON file of road-edge lines, switching this command "
                 "to OBSTRUCTION mode: a vehicle is judged by how much of its "
                 "footprint sits past the edge and for how long, instead of by "
                 "dwell alone. Draw the edges once per camera with "
                 "detection_sandbox/obstruction_web.py. Format: "
                 '{"left": {"points": [[x,y],[x,y]], "side": 1}, "right": {...}} '
                 "in the coordinates of the frame as processed.",
        )
        parser.add_argument(
            "--obstruction-pct", type=int, default=50,
            help="Share of the vehicle's footprint that must be past the edge "
                 "(default 50).",
        )
        parser.add_argument(
            "--obstruction-minutes", type=float, default=5.0,
            help="Minutes it must be held before it counts (default 5).",
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
            defaults={"name": "Parking Monitor", "status": Camera.Status.ONLINE},
        )
        self.violations_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(self.violations_dir, exist_ok=True)

        self.conf_override = options["confidence"]
        self.dwell_override = options["dwell"]
        # Both modes by default (far already includes the near whole-frame pass);
        # --fast opts out to the single near pass.
        self.far = not options["fast"]
        self.preprocess = options["preprocess"]
        self.sharpen = options["sharpen"]
        self.monitors = self._load_edges(options)
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

        if options["image"]:
            conf = self.conf_override or (cfg.parking_confidence / 100)
            self._run_image(options["image"], conf)
        else:
            self._run_stream(options["source"], options["debug"])

    # ---- obstruction mode --------------------------------------------------

    def _load_edges(self, options):
        """Builds one ObstructionMonitor per drawn edge, or {} for dwell mode."""
        if not options.get("edges"):
            return {}
        try:
            with open(options["edges"], encoding="utf-8") as fh:
                specs = json.load(fh)
        except (OSError, ValueError) as exc:
            self.stdout.write(self.style.ERROR(f"Could not read --edges: {exc}"))
            return {}

        enter = max(min(options["obstruction_pct"], 90), 10) / 100.0
        obs.ENTER_FRACTION = enter
        obs.EXIT_FRACTION = max(enter - 0.10, 0.05)
        seconds = max(options["obstruction_minutes"], 0.1) * 60

        monitors = {}
        for name, spec in specs.items():
            if len(spec.get("points") or []) < 2:
                continue
            edge = obs.build_edge(spec)
            monitors[name] = (edge, obs.ObstructionMonitor(
                edge, obstruction_seconds=seconds))
        if monitors:
            self.stdout.write(self.style.SUCCESS(
                f"OBSTRUCTION mode: {len(monitors)} edge(s) "
                f"[{', '.join(monitors)}], {enter*100:.0f}% past the line held "
                f"for {seconds/60:.1f} min."
            ))
        return monitors

    def _run_obstruction(self, frame, vehicles, now_ts, cooldown, debug):
        """Judges each vehicle against every edge; alerts once per violation."""
        boxes = [v[:4] for v in vehicles]
        labels = [v[5] for v in vehicles]
        # Pedestrians standing on the road side of an edge next to a stopped
        # vehicle are people who had to walk around it - the most convincing
        # evidence there is that a footpath was actually blocked.
        people = [p[:4] for p in recognition.detect_persons(frame)]

        for name, (edge, monitor) in self.monitors.items():
            edge.draw(frame, (0, 165, 255), 2)
            for state, verdict in monitor.update(
                    boxes, now_ts, labels=labels, frame_shape=frame.shape,
                    pedestrians=people):
                if debug:
                    x1, y1, x2, y2 = state.box
                    colour = ((0, 0, 220) if verdict == obs.OBSTRUCTION
                              else (0, 190, 230) if verdict == obs.WATCHING
                              else (150, 150, 150))
                    cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
                    cv2.putText(frame,
                                f"{name} {state.fraction*100:.0f}% {state.held:.0f}s",
                                (x1, max(y1 - 8, 0)), cv2.FONT_HERSHEY_SIMPLEX,
                                0.5, colour, 1)
                if not state.fresh_alert:
                    continue
                detour = (f" {state.detours} pedestrian(s) forced onto the road."
                          if state.detours else "")
                alert = self._create_alert(
                    state.fraction, name, frame,
                    description=(
                        f"Road-edge obstruction on the {name} edge: vehicle "
                        f"{state.fraction*100:.0f}% past the line, held "
                        f"{state.held/60:.1f} min.{detour}"
                    ),
                )
                self.stdout.write(self.style.SUCCESS(
                    f"ALERT created: {alert.code} [{name}] {state.summary()}"
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
            cv2.putText(frame, f"{label} {score * 100:.0f}%", (x1, max(y1 - 8, 0)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 220), 1)

        # Alert on the highest-confidence vehicle; the annotated frame (all
        # boxes) is saved as evidence.
        best = max(vehicles, key=lambda v: v[4])
        _, _, _, _, best_score, best_label = best
        summary = ", ".join(sorted({v[5] for v in vehicles}))
        alert = self._create_alert(
            best_score, best_label, frame,
            description=(
                f"Illegal parking / obstruction detected on still image: "
                f"{len(vehicles)} vehicle(s) [{summary}]."
            ),
        )
        self.stdout.write(self.style.SUCCESS(
            f"Detected {len(vehicles)} vehicle(s): {summary}. "
            f"ALERT created: {alert.code}"
        ))

    # ---- live stream dwell mode -------------------------------------------

    def _open_capture(self, source):
        """Opens a webcam index or a stream URL / file path."""
        if source.isdigit():
            return cv2.VideoCapture(int(source))
        cap = cv2.VideoCapture(source)
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
        reader = recognition.LatestFrameReader(cap) if is_live else cap

        # Settings are re-polled every few seconds (like watch_curfew) so edits
        # made in the dashboard's Parking config take effect live, without a
        # restart. CLI flags, if given, still win over the stored values.
        cfg = SystemSettings.load()
        cfg_loaded_at = time.time()
        # tracks: id -> {box, anchor, still_since, last_seen, label, score, alerted_at}
        tracks = {}
        next_id = 0

        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} for parked vehicles (dwell {self.dwell_override or cfg.parking_dwell}s, "
            f"reads live from Settings). Press Ctrl+C to stop."
        ))

        try:
            while True:
                ok, frame = reader.read()
                if not ok:
                    if is_live:
                        time.sleep(0.02)
                        continue
                    self.stdout.write(self.style.WARNING(f"Failed to read frame from {source}."))
                    time.sleep(0.5)
                    continue

                # Enhance dim/noisy frames before detection (daytime bypasses).
                frame = self._preprocess(frame)

                now_ts = time.time()
                if now_ts - cfg_loaded_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_loaded_at = now_ts

                if not cfg.parking_enabled:
                    time.sleep(0.5)
                    continue

                conf = self.conf_override or (cfg.parking_confidence / 100)
                dwell_seconds = self.dwell_override or cfg.parking_dwell
                move_tolerance = cfg.parking_move_tolerance
                vehicles = self._detect(frame, conf)

                # Obstruction mode replaces the plain dwell rule rather than
                # adding to it: "parked here for 60s" and "half over the footpath
                # for 5 minutes" would otherwise both fire on the same vehicle
                # and report the same event twice.
                if self.monitors:
                    self._run_obstruction(frame, vehicles, now_ts,
                                          cfg.alert_cooldown, debug)
                    if debug:
                        cv2.imshow("LookOut - watch_parking (debug)", frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            break
                    continue

                # Greedy IoU association of detections to existing tracks — good
                # enough for a stationary parking camera (no ByteTrack needed).
                matched_ids = set()
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
                        best_id = next_id
                        next_id += 1
                        tracks[best_id] = {"anchor": _center(box), "still_since": now_ts,
                                           "alerted_at": 0}
                    matched_ids.add(best_id)
                    tr = tracks[best_id]
                    tr.update({"box": box, "last_seen": now_ts, "label": label, "score": score})

                    # Movement reset: if the vehicle drifted past the tolerance,
                    # it's moving (not parked) — re-anchor and restart its timer.
                    cx, cy = _center(box)
                    ax, ay = tr["anchor"]
                    if ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5 > move_tolerance:
                        tr["anchor"] = (cx, cy)
                        tr["still_since"] = now_ts

                    parked_for = now_ts - tr["still_since"]
                    if debug:
                        color = (0, 0, 220) if parked_for >= dwell_seconds else (0, 200, 0)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                        cv2.putText(frame, f"{label} {parked_for:.0f}s", (x1, max(y1 - 8, 0)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

                    if parked_for < dwell_seconds:
                        continue
                    if now_ts - tr["alerted_at"] < cfg.alert_cooldown:
                        continue
                    alert = self._create_alert(
                        score, label, frame,
                        description=(
                            f"Illegal parking detected: {label} stationary for "
                            f"{parked_for:.0f}s on parking-monitor feed."
                        ),
                    )
                    tr["alerted_at"] = now_ts
                    self.stdout.write(self.style.SUCCESS(f"ALERT created: {alert.code} ({label})"))

                # drop tracks not seen recently (grace for detector flicker)
                for tid in list(tracks.keys()):
                    if tid in matched_ids:
                        continue
                    if now_ts - tracks[tid]["last_seen"] > TRACK_GRACE_SECONDS:
                        del tracks[tid]

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

    # ---- shared alert creation --------------------------------------------

    def _create_alert(self, score, label, frame, description):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts_label}_parking_{label}.jpg"
        cv2.imwrite(str(self.violations_dir / filename), frame)
        image_url = f"{settings.SITE_BASE_URL}{settings.MEDIA_URL}violations/{filename}"

        return Alert.objects.create(
            type=self.parking_type,
            status=Alert.Status.ACTIVE,
            camera=self.camera,
            timestamp=timezone.now(),
            confidence=score,
            description=description,
            image_url=image_url,
            suspect=label,
        )
