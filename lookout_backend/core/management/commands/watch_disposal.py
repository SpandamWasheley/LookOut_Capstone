"""watch_disposal — improper disposal (rubbish dumped and left behind).

Django glue around core/vision/disposal.py. The rule layer and the CV live
there; this file owns the camera, the settings reload, the evidence files and
the Alert row, the same division watch_parking uses.

Why this detector is shaped differently from the other four
-----------------------------------------------------------
The merged model (Bottle / Cigarette / knife) has no garbage class, so there
is nothing to threshold. Rather than retrain for one more violation, disposal
asks two cheaper questions in order: stage 1 finds what arrived and STOPPED
MOVING (dual background subtraction, no training data at all), stage 2 asks an
open-vocabulary model whether that thing is a bag and not a dog.

Like parking, disposal therefore carries its OWN confirmation logic rather
than the shared weighted-sum scoring layer, and for the same reason parking
does: there is no cited cue table to sum. `confidence` on the alert is the
zero-shot bag score and `level` is deliberately left blank, so the alert card
shows Object confidence with no score breakdown -- exactly as parking's does.
Inventing weights here to make it look like the scored detectors would be
presenting a guess as a measurement.

    python manage.py watch_disposal --source <rtsp-url-or-clip.mp4>
    python manage.py watch_disposal --source 0 --debug
"""

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

# Same reason as watch_parking: importing recognition pulls in torch, which is
# seconds of silence before handle() is even called.
with Spinner("Loading PyTorch", tty_only=True):
    from core.vision import debug_view                        # noqa: E402
    from core.vision import disposal as disposal_cv           # noqa: E402
    from core.vision import recognition                       # noqa: E402
    from core.vision import scoring                           # noqa: E402
    from core.vision import trim as trimming                  # noqa: E402

DISPOSAL_CAMERA_CODE = "CAM-SMOKE-01"
SETTINGS_REFRESH_SECONDS = 5


class Command(BaseCommand):
    help = (
        "Detects improper disposal: an object that appears, stops moving and is "
        "then confirmed to be a garbage/plastic bag by a zero-shot check. Needs "
        "no training data for the bag class."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source", default="0",
            help="Video source: a webcam index (default 0), a stream URL or a "
                 "video file path. Use the RTSP URL for a real CCTV camera.",
        )
        parser.add_argument(
            "--camera", default=DISPOSAL_CAMERA_CODE,
            help=f"Camera code to attach alerts to (default {DISPOSAL_CAMERA_CODE}).",
        )
        parser.add_argument(
            "--model", default=disposal_cv.DEFAULT_WORLD_MODEL,
            help="YOLO-World weights for the bag check (auto-downloads).",
        )
        parser.add_argument(
            "--confidence", type=float, default=None,
            help="Override the SystemSettings disposal confidence (as 0-1).",
        )
        parser.add_argument(
            "--static-seconds", type=float, default=None,
            help="Override how long an object must sit still before it is checked.",
        )
        parser.add_argument(
            "--stride", type=int, default=1,
            help="Process every Nth frame (default 1). Stage 1 is cheap; raise "
                 "this only on a slow CPU.",
        )
        parser.add_argument(
            "--zone", default=None,
            help="Path to a draw_zone JSON limiting WHERE rubbish is watched for. "
                 "Optional: without one (and without an area saved on the camera) "
                 "the whole frame is watched, which is the old behaviour.",
        )
        parser.add_argument("--start", default=None, help="Trim: start at this position.")
        parser.add_argument("--end", default=None, help="Trim: stop at this position.")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Detect and log, but write no Alert rows.",
        )
        parser.add_argument("--debug", action="store_true", help="Show an annotated window.")
        parser.add_argument(
            "--show-mask", action="store_true",
            help="With --debug, also show the static-object mask (stage 1's output).",
        )

    def handle(self, *args, **options):
        # No migration creates these, so get_or_create self-heals a fresh DB —
        # the same reason seed_core exists for split deployments.
        self.disposal_type, _ = ViolationType.objects.get_or_create(
            code="disposal",
            defaults={"label": "Improper Disposal", "color": "#22c55e", "icon": "trash"},
        )
        self.camera, _ = Camera.objects.get_or_create(
            code=options["camera"],
            defaults={"name": "Hikvision DS-2CD1047G2", "status": Camera.Status.ONLINE},
        )
        self.violations_dir = settings.MEDIA_ROOT / "violations"
        os.makedirs(self.violations_dir, exist_ok=True)

        # Set unconditionally so _create_alert never AttributeErrors on a path
        # that skipped _run_stream's setup. Same guard as watch_parking.
        self.clip = None
        self._source_path = None
        self._video_pos_sec = None
        self._raw_buffer = None
        # None unless LOOKOUT_DEBUG_DIR is set, i.e. unless the page launched
        # us. Set here rather than in _run_stream for the same reason as the
        # four above: _create_alert and _publish_view must never AttributeError
        # on a path that skipped the stream setup.
        self.debug_pub = debug_view.DebugPublisher.from_env()
        self._movers = []
        self._movers_at = -1e9
        self._mover_warned = False

        self.dry_run = options["dry_run"]
        self.conf_override = options["confidence"]
        self.static_override = options["static_seconds"]
        self.trim = trimming.Trim(options["start"], options["end"])
        # _load_area runs inside _run_stream, after the camera is resolved.
        self._options = options
        self.stride = max(1, options["stride"])

        self.stdout.write(f"Loading zero-shot bag check ({options['model']})...")
        load_started = time.perf_counter()
        try:
            self.world = disposal_cv.load_world_model(
                options["model"], log=lambda msg: self.stdout.write(f"  {msg}"))
        except Exception as exc:                                    # noqa: BLE001
            # A missing/undownloadable open-vocab model is the one dependency
            # this detector cannot degrade around — stage 1 alone would alert
            # on every parked bicycle. Fail loudly rather than quietly badly.
            self.stdout.write(self.style.ERROR(
                f"Could not load the zero-shot model ({options['model']}): {exc}"))
            return
        self.stdout.write(f"  ready in {time.perf_counter() - load_started:.1f}s")
        self.garbage_ids = disposal_cv.garbage_class_ids()

        self._run_stream(options["source"], options["debug"], options["show_mask"])

    # ---- stream ------------------------------------------------------------

    def _open_capture(self, source):
        """Opens a webcam index or a stream URL / file path."""
        if source.isdigit():
            return cv2.VideoCapture(int(source))
        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|"
            "analyzeduration;500000|probesize;500000|stimeout;5000000",
        )
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run_stream(self, source, debug, show_mask):
        cap = self._open_capture(source)
        if not cap.isOpened():
            self.stdout.write(self.style.ERROR(f"Could not open video source: {source}"))
            return

        is_live = source.isdigit() or "://" in source
        reader = recognition.LatestFrameReader(
            cap, open_fn=lambda: self._open_capture(source),
            log=lambda m: self.stdout.write(self.style.WARNING(m)),
        ) if is_live else cap
        self._source_path = None if is_live else source
        self._raw_buffer = recognition.RawFrameRecorder() if is_live else None
        if self.trim.seek(cap, is_live):
            self.stdout.write(self.style.SUCCESS(
                f"Trimmed to {self.trim.describe()} of the clip."))

        cfg = SystemSettings.load()
        cfg_loaded_at = time.time()
        self.clip = recognition.ClipRecorder(seconds=30, label=self.camera.code)

        area, area_from = self._load_area(self._options)
        if area is not None:
            self.stdout.write(self.style.SUCCESS(
                f"Watching only the area drawn on {area_from} "
                f"({len(area.points_norm)} points)."))
        detector = disposal_cv.StaticObjectDetector(
            area=area,
            # Percent in the dashboard, fractions in the CV module.
            min_area_frac=cfg.disposal_min_area / 100.0,
            max_area_frac=cfg.disposal_max_area / 100.0,
            warmup_seconds=float(cfg.disposal_warmup_seconds),
        )
        self._area_sent = False
        cooldown = disposal_cv.SpatialCooldown(
            seconds=self._cooldown_seconds(cfg),
            distance=float(cfg.disposal_cooldown_dist),
        )

        self.stdout.write(self.style.SUCCESS(
            f"Watching {source} for improper disposal "
            f"[{self._static_seconds(cfg):.0f}s still, confirm >= "
            f"{self._confidence(cfg):.2f}, size "
            f"{cfg.disposal_min_area:g}%-{cfg.disposal_max_area:g}% of view]. "
            f"Time is {'wall clock' if is_live else 'footage position'}. "
            f"Learning the background for {disposal_cv.WARMUP_SECONDS:.0f}s first. "
            "Press Ctrl+C to stop."
        ))

        window = "LookOut - watch_disposal (debug)"
        if debug:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)

        frame_idx = 0
        n_alerts = 0
        warned_warm = False
        stats = {"checked": 0, "confirmed": 0, "rejected": 0, "cooldown": 0, "ignored": 0}

        try:
            while True:
                ok, frame = reader.read()
                if not ok:
                    if is_live:
                        time.sleep(0.02)
                        continue
                    self.stdout.write(self.style.SUCCESS(f"End of {source} — done."))
                    break

                # RAW, before any drawing — see RawFrameRecorder.
                if self._raw_buffer is not None:
                    self._raw_buffer.add(frame, time.time())

                frame_idx += 1
                if frame_idx % self.stride:
                    continue

                # NO frame enhancement here, deliberately. The other detectors
                # offer --preprocess to lift dim frames before inference, but
                # background subtraction depends on photometric consistency
                # BETWEEN frames: a per-frame adaptive enhancement changes the
                # background statistics every frame and injects false
                # foreground, which is precisely what stage 1 reads as "a new
                # object appeared". Dim footage costs recall here; enhancing it
                # would cost correctness.

                wall_now = time.time()
                if wall_now - cfg_loaded_at >= SETTINGS_REFRESH_SECONDS:
                    cfg = SystemSettings.load()
                    cfg_loaded_at = wall_now
                    cooldown.seconds = self._cooldown_seconds(cfg)
                    cooldown.distance = float(cfg.disposal_cooldown_dist)
                    # Live, like every other threshold here: an operator
                    # watching a flood of noise candidates can raise the size
                    # floor and see it take effect without restarting the run.
                    #
                    # The warmup is deliberately NOT re-applied — it has
                    # already happened, and resetting it mid-run would blind
                    # the detector again for no reason. It takes effect on the
                    # next start.
                    detector.min_area_frac = cfg.disposal_min_area / 100.0
                    detector.max_area_frac = cfg.disposal_max_area / 100.0

                if not cfg.disposal_enabled:
                    time.sleep(0.5)
                    continue

                # Content-time clock for a file source so "still for 5s" means
                # the five seconds a human watching the clip would see, even
                # though the zero-shot pass makes processing slower than
                # real-time. Wall clock for a live source, where the two are
                # the same thing. Same reasoning as watch_parking's.
                if self._source_path is not None:
                    self._video_pos_sec = reader.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                    now_ts = self._video_pos_sec
                else:
                    now_ts = wall_now

                if self.trim.past_end(now_ts, is_live):
                    self.stdout.write(self.style.SUCCESS(
                        f"End of {source} ({self.trim.describe()}) — done."))
                    break

                # Clean pixels for the live view, taken before anything draws.
                # Nothing in this loop draws on `frame` in place (_annotate
                # copies), but stash() must still be called every frame — it
                # decides internally when a publish is due.
                if self.debug_pub is not None:
                    self.debug_pub.stash(frame)
                    # Once, on the first real frame: without this the page
                    # cannot show WHICH area a candidate is being judged
                    # against, and an area traced over the wrong patch of road
                    # looks identical to a detector that has gone blind.
                    if not self._area_sent and detector.area is not None:
                        h, w = frame.shape[:2]
                        self.debug_pub.set_area(
                            [(x * w, y * h) for x, y in detector.area.points_norm])
                        self._area_sent = True

                ignore = (self._mover_boxes(frame, now_ts)
                          if cfg.disposal_ignore_movers else None)
                candidates = detector.update(frame, now_ts, ignore_boxes=ignore)

                if detector.warming:
                    if not warned_warm:
                        self.stdout.write("  learning the empty scene...")
                        warned_warm = True
                    # Publish during the warmup too, so the page shows the feed
                    # immediately instead of "Waiting for the detector to
                    # start..." for the first five seconds of every run.
                    if self.debug_pub is not None:
                        self.debug_pub.commit(now_ts)
                    if debug:
                        self._show(window, detector, [], now_ts, show_mask)
                    continue

                static_seconds = self._static_seconds(cfg)
                conf = self._confidence(cfg)

                for cand in detector.due_for_check(
                        now_ts, static_seconds,
                        recheck_seconds=float(cfg.disposal_recheck_seconds)):
                    cand.last_check = now_ts
                    stats["checked"] += 1

                    crop = disposal_cv.crop_full_res(frame, cand.box, detector.scale)
                    is_garbage, (g_name, g_conf), (r_name, r_conf) = disposal_cv.confirm(
                        self.world, crop, self.garbage_ids, conf)

                    if not is_garbage:
                        cand.rejected = True
                        cand.label = f"{r_name} {r_conf:.0%}" if r_name else "not garbage"
                        stats["rejected"] += 1
                        continue

                    cand.confirmed, cand.rejected = True, False
                    cand.label = g_name
                    cand.score = g_conf

                    if cooldown.blocks(cand.center, now_ts):
                        stats["cooldown"] += 1
                        self.stdout.write(
                            f"  {g_name} confirmed but inside the "
                            f"{cooldown.seconds:.0f}s same-spot cooldown — no alert.")
                        continue

                    cooldown.remember(cand.center, now_ts)
                    stats["confirmed"] += 1
                    n_alerts += 1

                    still = cand.still_for(now_ts)
                    description = descriptions.disposal(g_name, still)
                    # Draw the box BEFORE the frame becomes evidence: the still
                    # and the clip both come from this array, and an evidence
                    # photo that does not show WHERE the rubbish was leaves the
                    # tanod hunting for it in the frame. watch_parking gets this
                    # for free because its pipeline already drew on the frame;
                    # nothing draws on ours, so it has to happen here.
                    evidence = self._annotate(frame, cand, detector.scale,
                                              g_name, g_conf, still)
                    alert = self._create_alert(
                        g_conf, g_name, evidence, description, now=now_ts)
                    prefix = "ALERT created" if alert else "ALERT suppressed (dry run)"
                    self.stdout.write(self.style.SUCCESS(
                        f"{prefix} [disposal] {g_name} {g_conf:.0%} "
                        f"after {still:.0f}s still"
                        + (f" — {alert.code}" if alert else "")
                    ))

                stats["ignored"] += len(detector.ignored)
                self._publish_view(detector, candidates, now_ts, static_seconds)

                if debug:
                    if self._show(window, detector, candidates, now_ts, show_mask):
                        break
        except KeyboardInterrupt:
            self.stdout.write("\nStopped.")
        finally:
            if hasattr(reader, "stop"):
                reader.stop()
            else:
                cap.release()
            if debug:
                cv2.destroyAllWindows()

        self.stdout.write(self.style.SUCCESS(
            f"Checked {stats['checked']} static object(s): "
            f"{stats['confirmed']} alerted, {stats['rejected']} rejected, "
            f"{stats['cooldown']} inside cooldown. "
            # Counted per FRAME, not per object: one person standing still is
            # suppressed on every frame they are in shot, so this is "how much
            # work the filter saved", not "how many people there were".
            f"Suppressed {stats['ignored']} blob-frame(s) as people/vehicles."
        ))

    # ---- people and vehicles to ignore -------------------------------------

    # One stock-yolov8n pass every this many seconds, not every frame. A person
    # who is STANDING STILL is the whole problem here, and a still person's box
    # is still valid half a second later — so the throttle costs nothing in
    # accuracy and keeps the detector near its stage-1-only frame rate. Anyone
    # moving fast enough for a stale box to matter is already filtered by the
    # dual-background rule.
    MOVER_CHECK_SECONDS = 0.5
    MOVER_IMGSZ = 640

    def _mover_boxes(self, frame, now):
        """(x, y, w, h) boxes of people and vehicles, in full-res coordinates.

        Cached between passes. Returns the previous result when a pass is not
        due, so the caller can hand this to the detector every frame.
        """
        if now - self._movers_at < self.MOVER_CHECK_SECONDS:
            return self._movers
        self._movers_at = now
        try:
            persons, _ids, _carriables, vehicles = recognition.detect_scene(
                frame, imgsz=self.MOVER_IMGSZ)
        except Exception as exc:                                    # noqa: BLE001
            # Never fatal: losing the filter means noisier candidates, which
            # stage 2 still rejects. Losing the detector means no disposal at
            # all. Warn once and carry on without it.
            if not self._mover_warned:
                self._mover_warned = True
                self.stdout.write(self.style.WARNING(
                    f"Person/vehicle filter unavailable ({exc}); "
                    "watching without it."))
            self._movers = []
            return self._movers
        self._movers = [(x1, y1, x2 - x1, y2 - y1)
                        for x1, y1, x2, y2, *_rest in list(persons) + list(vehicles)]
        return self._movers

    # ---- watched area ------------------------------------------------------

    def _load_area(self, options):
        """(Area, where-it-came-from), or (None, "") for the whole frame.

        Unlike parking's, this area is OPTIONAL at every level — no area means
        watch the whole frame, which is exactly what disposal did before this
        existed. So an unusable or missing one is a warning, never a refusal:
        the detector still works, it just works everywhere.

        Two sources, same order and same stored shape as watch_parking:
        the camera record written by the dashboard's Edge Zones editor (pixels
        plus the frame size they were drawn at), then a draw_zone file.
        """
        requested = options.get("zone") or ""
        if not requested:
            points = self._area_points_from_camera()
            if points is None:
                return None, ""
            try:
                return disposal_cv.Area(points), f"camera {self.camera.code}"
            except ValueError as exc:
                self.stdout.write(self.style.WARNING(
                    f"The area saved on {self.camera.code} is unusable ({exc}); "
                    "watching the whole frame. Retrace it in Live Feeds -> Edge Zones."))
                return None, ""

        path = Path(requested)
        if not path.is_file():
            self.stdout.write(self.style.WARNING(
                f"No zone file at {path}; watching the whole frame."))
            return None, ""
        try:
            data = json.loads(path.read_text())
            return disposal_cv.Area(data["points"]), str(path)
        except (ValueError, KeyError, OSError) as exc:
            self.stdout.write(self.style.WARNING(
                f"{path} is not a usable zone ({exc}); watching the whole frame."))
            return None, ""

    def _area_points_from_camera(self):
        """The camera's drawn polygon as normalised (0-1) points, or None.

        Reads the same "zone" spec parking does, so one area drawn in Edge
        Zones serves both rules rather than asking an operator to trace the
        same patch of road twice. Older kerb-line edges (open paths, no "type")
        are not closed shapes and read as "no area".
        """
        stored = self.camera.edges or {}
        spec = next((s for s in stored.values()
                     if isinstance(s, dict) and s.get("type") == "zone"
                     and len(s.get("points") or []) >= 3), None)
        if spec is None:
            return None
        width, height = self.camera.edges_width, self.camera.edges_height
        if not (width and height):
            self.stdout.write(self.style.WARNING(
                f"{self.camera.code} has a saved area but no record of the frame size "
                "it was drawn at, so it cannot be scaled; watching the whole frame."))
            return None
        return [[x / width, y / height] for x, y in spec["points"]]

    # ---- settings resolution ----------------------------------------------
    # CLI flags win over stored settings, so a calibration run doesn't need a
    # dashboard visit (and doesn't leave the dashboard changed afterwards).

    def _confidence(self, cfg):
        if self.conf_override is not None:
            return self.conf_override
        return cfg.disposal_confidence / 100.0

    def _static_seconds(self, cfg):
        if self.static_override is not None:
            return self.static_override
        return float(cfg.disposal_static_seconds)

    def _cooldown_seconds(self, cfg):
        return float(cfg.disposal_cooldown)

    # ---- live processing view (Run Detection / Live Feeds) -----------------
    # Only active with LOOKOUT_DEBUG_DIR, i.e. only when launched by the page.
    #
    # Disposal publishes the SAME shape as the other detectors even though it
    # has no scoring layer: the statuses below are borrowed from `scoring` for
    # their display vocabulary only — nothing here sums a cue table, and the
    # alert's `level` stays blank. What an operator is waiting on is still-time,
    # so that is what the score bar tracks.

    def _publish_view(self, detector, candidates, now, static_seconds):
        if self.debug_pub is None:
            return
        for cand in candidates:
            # Candidate boxes live in PROC_WIDTH coordinates; the page scales
            # against the published frame's full-resolution dimensions, so they
            # have to be mapped back the same way _annotate does.
            box = [int(v / detector.scale) for v in cand.box]
            still = cand.still_for(now)
            pct = int(min(100, round(still / max(static_seconds, 1e-6) * 100)))
            indicators = [
                {"name": "seconds still", "points": int(round(still))},
                {"name": "needs", "points": int(round(static_seconds))},
            ]
            if cand.confirmed:
                status = scoring.label_of(scoring.VIOLATION)      # "Likely"
                indicators.append({"name": cand.label or "garbage",
                                   "points": int(round(cand.score * 100))})
            elif cand.rejected:
                # Below Monitoring: the page draws these dashed and faded and
                # the Panel view hides them. A look-alike that stage 2 threw
                # out is exactly that — seen, judged, passed over — and showing
                # WHY (the winning reject class) is the whole point, because a
                # run that rejects everything otherwise looks like a blind
                # detector rather than a working one.
                status = debug_view.BELOW
                indicators.append({"name": cand.label or "not garbage", "points": 0})
            else:
                status = scoring.label_of(scoring.MONITORING)     # "Monitoring"
            self.debug_pub.note(
                key=("disposal", cand.id), violation="disposal", ident=cand.id,
                box=box, status=status, score_pct=pct, indicators=indicators,
                multipliers={}, momentum=round(still, 1), kind="object",
            )
        self.debug_pub.commit(now)

    # ---- debug view --------------------------------------------------------

    def _show(self, window, detector, candidates, now, show_mask):
        """Draws the candidate boxes. Returns True when the user pressed q."""
        vis = getattr(detector, "small", None)
        if vis is None:
            return False
        vis = vis.copy()
        if detector.warming:
            cv2.putText(vis, "Learning background...", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        for cand in candidates:
            x, y, w, h = cand.box
            if cand.confirmed:
                color, text = (0, 200, 0), cand.label       # green: confirmed disposal
            elif cand.rejected:
                color, text = (160, 160, 160), cand.label   # grey: a dog, a box, a bike
            else:
                color, text = (0, 255, 255), f"still {cand.still_for(now):.0f}s"
            cv2.rectangle(vis, (x, y), (x + w, y + h), color, 2)
            cv2.putText(vis, text, (x, max(15, y - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        cv2.imshow(window, vis)
        if show_mask and detector.mask is not None:
            cv2.imshow("static mask", detector.mask)
        return (cv2.waitKey(1) & 0xFF) == ord("q")

    # ---- evidence ----------------------------------------------------------

    EVIDENCE_GREEN = (94, 197, 34)   # BGR for #22c55e, the disposal chip colour

    def _annotate(self, frame, cand, scale, label, score, still):
        """A copy of `frame` with the confirmed object boxed and labelled.

        Copied, not drawn in place: the caller's frame goes on to the raw
        buffer and the next iteration's detection, and neither wants our
        rectangle burned into it.
        """
        out = frame.copy()
        x, y, w, h = [int(v / scale) for v in cand.box]
        cv2.rectangle(out, (x, y), (x + w, y + h), self.EVIDENCE_GREEN, 3)
        caption = f"{label} {score:.0%} - still {still:.0f}s"
        cv2.putText(out, caption, (x, max(28, y - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, self.EVIDENCE_GREEN, 2)
        return out

    # ---- alert -------------------------------------------------------------

    def _create_alert(self, score, label, frame, description, now=None):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = str(label).replace(" ", "_")
        filename = f"{ts_label}_disposal_{tag}.jpg"
        cv2.imwrite(str(self.violations_dir / filename), frame)
        image_url = violation_media_path(filename)

        video_url = ""
        if self.clip is not None:
            self.clip.add(frame, now if now is not None else time.time())
            video_name = f"{ts_label}_disposal_{tag}.mp4"
            if self.clip.save(self.violations_dir / video_name):
                video_url = violation_media_path(video_name)

        raw_video_url = ""
        raw_name = f"{ts_label}_disposal_{tag}_raw.mp4"
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

        if self.dry_run:
            return None

        return Alert.objects.create(
            type=self.disposal_type,
            status=Alert.Status.ACTIVE,
            camera=self.camera,
            timestamp=timezone.now(),
            confidence=score,
            # The zero-shot bag score, which is what the card's "Object
            # confidence" box shows. `level` is left blank on purpose — see the
            # module docstring: disposal has no cited cue table to sum, so it
            # shows no score breakdown, exactly like parking.
            object_confidence=score,
            description=description,
            image_url=image_url,
            video_url=video_url,
            raw_video_url=raw_video_url,
            suspect=str(label),
        )
