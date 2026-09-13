import datetime
import json
import os
import time

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.media import violation_media_path
from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import preprocess as preproc
from core.vision import obstruction as obs
from core.vision import recognition

PARKING_CAMERA_CODE = "CAM-PARKING"
# BGR, matched to detection_sandbox/obstruction_web.py's SIDE_COLOURS and the
# dashboard's EdgeCanvas (left orange, right cyan) so the --debug preview
# tells the two edges apart the same way the drawing screen did. Previously
# every edge drew in the same hardcoded orange, so two edges whose paths run
# close together on screen (as they often do - both drawn on the same street)
# were visually indistinguishable from one line.
EDGE_DEBUG_COLOURS = {"left": (0, 165, 255), "right": (255, 190, 0)}
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
        parser.add_argument(
            "--edges",
            default=None,
            help="Path to a JSON file of road-edge lines, switching this command "
                 "to OBSTRUCTION mode: a vehicle is judged by how much of its "
                 "footprint sits past the edge and for how long, instead of by "
                 "dwell alone. Omit to use the edges stored on the --camera "
                 "record instead (drawn via the dashboard or "
                 "detection_sandbox/obstruction_web.py). Format: "
                 '{"left": {"points": [[x,y],[x,y]], "side": 1}, "right": {...}} '
                 "in the coordinates of the frame as processed — a file passed "
                 "here is used exactly as given, with no resolution scaling.",
        )
        parser.add_argument(
            "--obstruction-pct", type=int, default=None,
            help="Share of the vehicle's footprint that must be past the edge. "
                 "Omit to use the camera record's value (default 50).",
        )
        parser.add_argument(
            "--obstruction-minutes", type=float, default=None,
            help="Minutes it must be held before it counts. Omit to use the "
                 "camera record's value (default 5).",
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
        # Both modes by default (far already includes the near whole-frame pass);
        # --fast opts out to the single near pass.
        self.far = not options["fast"]
        self.preprocess = options["preprocess"]
        self.sharpen = options["sharpen"]
        self.obstruction_mode = self._load_edges(options)
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

    # ---- obstruction mode --------------------------------------------------

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
            edge = obs.build_edge({"points": points, "side": spec.get("side", 1)})
            monitors[name] = (edge, obs.ObstructionMonitor(
                edge, obstruction_seconds=self._obstruction_seconds))

        self.monitors = monitors
        if monitors:
            note = f" (scaled from {src_w}x{src_h} to {w}x{h})" if rescale else ""
            self.stdout.write(self.style.SUCCESS(
                f"OBSTRUCTION mode: {len(monitors)} edge(s) "
                f"[{', '.join(monitors)}], {self._enter_fraction*100:.0f}% past "
                f"the line held for {self._obstruction_seconds/60:.1f} min{note}."
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
                    description=(
                        f"Road-edge obstruction on the {name} edge: vehicle "
                        f"{state.fraction*100:.0f}% past the line, held "
                        f"{state.held/60:.1f} min.{detour}"
                    ),
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
            + (f"ALERT created: {alert.code}" if alert else "ALERT suppressed (dry run)")
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
                description=(
                    f"Illegal parking detected: {label} stationary for "
                    f"{parked_for:.0f}s on parking-monitor feed."
                ),
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
            f"Watching {source} for parked vehicles (dwell {self.dwell_override or cfg.parking_dwell}s, "
            f"reads live from Settings). Press Ctrl+C to stop."
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

                conf = self.conf_override or (cfg.parking_confidence / 100)
                vehicles = self._detect(frame, conf)

                # Obstruction mode replaces the plain dwell rule rather than
                # adding to it: "parked here for 60s" and "half over the footpath
                # for 5 minutes" would otherwise both fire on the same vehicle
                # and report the same event twice.
                if self.obstruction_mode:
                    if self.monitors is None:
                        self._build_monitors(frame.shape)
                    self._run_obstruction(frame, vehicles, now_ts,
                                          cfg.alert_cooldown, debug)
                else:
                    self._run_dwell(frame, vehicles, now_ts, cfg, debug)

                # Buffer this annotated frame (boxes/edges already drawn above)
                # for the evidence clip.
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
