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
zero-shot bag score, and `cues` stays empty, so the alert card shows Object
confidence with no score breakdown -- exactly as parking's does. Inventing
weights here to make it look like the scored detectors would be presenting a
guess as a measurement.

What disposal DOES borrow from scoring is the two status names, because an
operator has to be told about a maybe:

    Possible (warning)    the bag check leads but is under the floor
    Likely   (violation)  it leads AND clears the floor

Both are reported. A weak lead used to be held back from the dashboard
entirely, which meant the one thing the detector was most unsure about was
also the one thing nobody could look at -- and a reviewer with the footage in
front of them settles a maybe in seconds, which is the whole point of putting
a human on the end of this. The precision argument the floor exists to serve
is kept by SAYING SO rather than by silence: a weak lead is badged amber
Possible, and `disposal_confidence` is still what decides which side of that
line a detection falls on. There is ONE row per dumped object either way --
`Candidate.alert_pk` carries it -- so a Possible that later firms up is
upgraded in place to Likely rather than filed again.

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
from core import timeline
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
# Seconds of lead-in before the object APPEARED that the raw evidence clip
# starts at. Deliberately not recognition.RAW_CLIP_PRE_SECONDS, which is
# measured back from the alert: with an owner hold the alert can be a minute
# after the drop, and a pre-roll anchored there would miss the only moment
# worth watching.
RAW_CLIP_PRE_DROP_SECONDS = 5.0


class Command(BaseCommand):
    # None means "save the annotated clip immediately" — correct for any path
    # that does not drain the queue. _run_stream, which does, switches it on.
    pending = None

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
            "--presence-seconds", type=float, default=None,
            help="How often a pinned object (one stage 1 has forgotten) is "
                 "re-asked whether it is still there. Default "
                 f"{disposal_cv.PRESENCE_RECHECK_SECONDS:.0f}s.",
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
        self._persons = []
        self._movers_at = -1e9
        self._mover_warned = False

        self.dry_run = options["dry_run"]
        self.conf_override = options["confidence"]
        self.static_override = options["static_seconds"]
        self.presence_seconds = (options["presence_seconds"]
                                 or disposal_cv.PRESENCE_RECHECK_SECONDS)
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
            owner_radius=int(cfg.disposal_owner_radius),
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
        # Annotated clips are SAVED LATE, so the detection sits in the middle
        # of one instead of on its last frame. See recognition.PendingClips.
        self.pending = recognition.PendingClips()
        stats = {"checked": 0, "confirmed": 0, "rejected": 0, "cooldown": 0,
                 "ignored": 0, "weak": 0, "pinned": 0, "released": 0,
                 # Rows opened at Possible, and rows that later rose from
                 # Possible to Likely. Both are the SAME alert as far as a
                 # reviewer is concerned, which is why they are counted apart
                 # from `confirmed` rather than added into it.
                 "possible": 0, "upgraded": 0,
                 # Candidates that took over the row their place already had
                 # instead of opening a second one for the same bag.
                 "adopted": 0}

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
                    # The owner radius is live for the same reason the size
                    # bounds are: an operator watching a hold that never
                    # releases (radius too wide) or never engages (too narrow)
                    # can correct it without losing the run's learned
                    # background, which a restart would throw away.
                    detector.owner_radius = int(cfg.disposal_owner_radius)

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

                # Rolling evidence buffer. ClipRecorder only holds what it is
                # given, so this has to run on EVERY processed frame (the same
                # place watch_parking does it) — feeding it only at alert time
                # leaves one frame in the deque and writes a clip that is a
                # single frozen image instead of the 30s lead-up. These frames
                # are clean; _create_alert adds the annotated one last, so the
                # clip ends on the boxed frame.
                if self.clip is not None:
                    self.clip.add(frame, now_ts)

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

                # The owner rule needs the person boxes whether or not the
                # ignore rule is switched on, so the pass runs either way; what
                # `disposal_ignore_movers` controls is only whether its result
                # is also used to suppress blobs.
                movers = self._mover_boxes(frame, now_ts)
                ignore = movers if cfg.disposal_ignore_movers else None
                candidates = detector.update(frame, now_ts, ignore_boxes=ignore,
                                             person_boxes=self._persons)

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

                # Hold open the place of every candidate that already owns a
                # row, whether or not it was due for a check this frame. A
                # CONFIRMED candidate is never offered to stage 2 again, so
                # without this its place would fall idle and be released while
                # the rubbish was still plainly sitting there — and the next
                # candidate raised for that same bag would open a second
                # violation for it. The place's clock has to track the object
                # being there, not the detector happening to re-ask about it.
                for live in candidates:
                    if live.alert_pk is not None:
                        cooldown.remember(live.center, now_ts,
                                          live.alert_pk, live.alert_level)

                for cand in detector.due_for_check(
                        now_ts, static_seconds,
                        recheck_seconds=float(cfg.disposal_recheck_seconds),
                        owner_away_seconds=float(cfg.disposal_owner_away_seconds),
                        owner_max_hold=float(cfg.disposal_owner_max_hold)):
                    cand.last_check = now_ts
                    stats["checked"] += 1

                    verdict, (g_name, g_conf), (r_name, r_conf) = self._ask_stage2(
                        frame, cand, detector.scale, conf)

                    if verdict == disposal_cv.REJECTED:
                        cand.rejected, cand.weak = True, False
                        cand.label = f"{r_name} {r_conf:.0%}" if r_name else "not garbage"
                        stats["rejected"] += 1
                        continue

                    if verdict == disposal_cv.WEAK:
                        # Garbage led but fell short of the floor. REPORTED, as
                        # Possible, and not a write-off either: it also stays in
                        # the recheck queue (last_check above schedules the next
                        # look), so the same object can be upgraded to Likely
                        # later without being filed twice.
                        #
                        # disposal_confidence is still a real control -- it is
                        # what decides Possible from Likely, and so what an
                        # operator triages on. What it no longer does is hide
                        # the detector's maybes: a weak lead the dashboard never
                        # showed could not be confirmed or dismissed by the one
                        # thing that settles it, which is a person watching the
                        # clip.
                        cand.weak, cand.rejected = True, False
                        cand.label, cand.score = g_name, g_conf
                        stats["weak"] += 1
                        self.stdout.write(
                            f"  {g_name} leads at {g_conf:.0%} but the floor is "
                            f"{conf:.0%} — Possible, rechecking in "
                            f"{cfg.disposal_recheck_seconds}s.")
                        if self._alert(cand, detector, frame, g_name, g_conf,
                                       now_ts, cfg, cooldown, stats,
                                       scoring.WARNING):
                            n_alerts += 1
                        continue

                    cand.confirmed, cand.rejected, cand.weak = True, False, False
                    cand.label = g_name
                    cand.score = g_conf
                    if self._alert(cand, detector, frame, g_name, g_conf,
                                   now_ts, cfg, cooldown, stats,
                                   scoring.VIOLATION):
                        n_alerts += 1

                # Is it STILL there? Pinned candidates are the ones both
                # background models have absorbed — the rubbish has not moved,
                # stage 1 simply cannot see it any more — so their presence is
                # decided here, on appearance, for as long as the pin lasts.
                for cand in detector.due_for_presence(now_ts, self.presence_seconds):
                    first_look = cand.last_presence_check is None
                    verdict, (g_name, g_conf), (r_name, r_conf) = self._ask_stage2(
                        frame, cand, detector.scale, conf)
                    if verdict != disposal_cv.REJECTED:
                        cand.weak = verdict == disposal_cv.WEAK
                        cand.label, cand.score = g_name, g_conf
                        cand.confirmed = verdict == disposal_cv.CONFIRMED

                    if first_look:
                        stats["pinned"] += 1
                        self.stdout.write(
                            f"  stage 1 lost the blob for #{cand.id} after "
                            f"{cand.still_for(now_ts):.0f}s still — holding it on "
                            f"the bag check every {self.presence_seconds:.0f}s.")

                    if not detector.record_presence(
                            cand, verdict != disposal_cv.REJECTED, now_ts):
                        stats["released"] += 1
                        self.stdout.write(
                            f"  #{cand.id} is gone "
                            f"({r_name or 'nothing'} reads there now) — stopped "
                            f"tracking it after {cand.still_for(now_ts):.0f}s.")
                        continue

                    # A weak match that firmed up WHILE pinned is a real
                    # confirmation and has to be reported, not merely refresh
                    # the hold: before the pin existed this candidate was
                    # dropped before its recheck ever came round, which is a
                    # missed alert rather than a cosmetic problem.
                    #
                    # Both engaged verdicts are sent, not just the firming-up
                    # one, because the row and the candidate's status have to
                    # agree in both directions -- a Likely that reads back as
                    # a weak lead on the next look is downgraded to Possible,
                    # the same way the scored detectors let a status fall.
                    # _alert is a no-op when nothing changed, so this costs one
                    # comparison per presence check.
                    if cand.confirmed or cand.weak:
                        if self._alert(cand, detector, frame, g_name, g_conf,
                                       now_ts, cfg, cooldown, stats,
                                       scoring.VIOLATION if cand.confirmed
                                       else scoring.WARNING):
                            n_alerts += 1

                self._drain_pending(now_ts)

                stats["ignored"] += len(detector.ignored)
                self._publish_view(detector, candidates, now_ts, static_seconds,
                                   float(cfg.disposal_owner_away_seconds),
                                   float(cfg.disposal_owner_max_hold), conf)

                if debug:
                    if self._show(window, detector, candidates, now_ts, show_mask):
                        break
        except KeyboardInterrupt:
            self.stdout.write("\nStopped.")
        finally:
            if not self.dry_run:
                self._drain_pending(self._video_pos_sec or time.time(), final=True)
            if hasattr(reader, "stop"):
                reader.stop()
            else:
                cap.release()
            if debug:
                cv2.destroyAllWindows()

        self.stdout.write(self.style.SUCCESS(
            f"Checked {stats['checked']} static object(s): "
            f"{stats['confirmed']} alerted as Likely, "
            f"{stats['possible']} as Possible "
            f"({stats['upgraded']} later rose to Likely), "
            f"{stats['rejected']} rejected, "
            f"{stats['adopted']} re-joined a row already open at their spot, "
            f"{stats['cooldown']} suppressed as an already-reported spot. "
            # Counted per FRAME, not per object: one person standing still is
            # suppressed on every frame they are in shot, so this is "how much
            # work the filter saved", not "how many people there were".
            f"{stats['weak']} weak verdict(s) returned. "
            # Pinned is how many objects outlived stage 1's ability to see
            # them at all; released is how many of those the bag check later
            # found gone. A run with pins and no releases means every dumped
            # thing was still there when the run ended, which is the normal
            # shape for footage of a street nobody has cleaned.
            f"Pinned {stats['pinned']} object(s) past the background models, "
            f"released {stats['released']}. "
            f"Suppressed {stats['ignored']} blob-frame(s) as people/vehicles."
        ))

    # ---- stage 2 ------------------------------------------------------------

    def _drain_pending(self, now, final=False):
        """Write any annotated clips whose post-roll has played, onto their rows.

        `final` empties the queue regardless: a run that reaches the end of its
        footage still owes every clip it scheduled, and an alert whose evidence
        was never written because the video ended two seconds later is a lost
        alert.
        """
        if self.pending is None:
            return
        ready = (self.pending.flush(now) if final else self.pending.due(now))
        for alert_pk, path, offset in ready:
            fields = {"video_url": violation_media_path(path.name)}
            # Only for a LIVE run. On a file the raw clip is cut from the
            # source and already carries the truer offset; overwriting it with
            # the annotated clip's would mark the wrong clip's moment.
            if offset is not None and self._source_path is None:
                fields["clip_offset"] = offset
            Alert.objects.filter(pk=alert_pk).update(**fields)

    def _ask_stage2(self, frame, cand, scale, conf):
        """One zero-shot pass on this candidate's crop.

        (verdict, (garbage_label, score), (reject_label, score)). Shared by the
        two callers on purpose: the static-object check and the "is it still
        there?" check ask the model exactly the same question about exactly the
        same crop, and only the meaning of the answer differs. Two copies of
        this would be two places for the crop padding or the floor to drift.
        """
        crop = disposal_cv.crop_full_res(frame, cand.box, scale)
        _ok, best_g, best_r = disposal_cv.confirm(
            self.world, crop, self.garbage_ids, conf)
        return disposal_cv.classify(best_g, best_r, conf), best_g, best_r

    def _alert(self, cand, detector, frame, g_name, g_conf, now, cfg, cooldown,
               stats, level):
        """Place gate, evidence and the Alert row. True if one was written.

        `level` is scoring.WARNING (Possible) or scoring.VIOLATION (Likely) —
        which of the two things the bag check concluded.

        ONE ROW PER PLACE. The first verdict at a spot files the alert; every
        later verdict about that spot restates that same row, whether it
        arrives on the same candidate or on a fresh one stage 1 raised after
        forgetting the object. So a lead reported at Possible and confirmed
        thirty seconds later is one alert that changed status, and a bag that
        sits all afternoon is one alert, not one per candidate lifetime.

        Rubbish somewhere ELSE is a different place and gets its own row —
        further than `cooldown.distance` away is a second thing to go and
        clear, and merging the two would under-count the street.

        Extracted so a confirmation reached from the presence loop — a weak
        match that firmed up after stage 1 had already forgotten the object —
        goes through the same gate, the same annotated evidence and the same
        suspect crop as one reached the ordinary way. A second copy of this
        block would be a second place for the evidence to be built differently.
        """
        still = cand.still_for(now)
        away = cand.owner_away_for(now)
        description = descriptions.disposal(g_name, still, away)

        # This candidate does not know a row yet — but its PLACE may already
        # have one. Stage 1 forgets an object a couple of unseen seconds after
        # the background models absorb it and raises a fresh id for the same
        # bag; adopting the row that place holds is what stops one bag becoming
        # a string of violations down the shift.
        if cand.alert_pk is None:
            taken = cooldown.alert_at(cand.center, now)
            if taken is not None:
                cand.alert_pk, cand.alert_level = taken
                stats["adopted"] += 1

        # Filed, by this candidate or by the one before it at this spot. The
        # place is held open first, then the row is brought up to date.
        if cand.alert_pk is not None:
            cooldown.remember(cand.center, now, cand.alert_pk, level)
            return self._restate_alert(cand, detector, frame, g_name, g_conf,
                                       still, description, level, stats)

        # Reported, with no row to adopt: a dry run.
        if cooldown.blocks(cand.center, now):
            stats["cooldown"] += 1
            self.stdout.write(
                f"  {g_name} reads as {scoring.label_of(level)} but this spot "
                f"has already been reported — no alert.")
            return False

        stats["confirmed" if level == scoring.VIOLATION else "possible"] += 1

        # Draw the box BEFORE the frame becomes evidence: the still and the
        # clip both come from this array, and an evidence photo that does not
        # show WHERE the rubbish was leaves the tanod hunting for it in the
        # frame. watch_parking gets this for free because its pipeline already
        # drew on the frame; nothing draws on ours, so it has to happen here.
        evidence = self._annotate(frame, cand, detector.scale, g_name, g_conf, still)
        alert = self._create_alert(
            g_conf, g_name, evidence, description, now=now,
            cand=cand, scale=detector.scale, cfg=cfg, level=level)
        if alert is not None:
            cand.alert_pk, cand.alert_level = alert.pk, level
        # Claimed AFTER the row exists, so the place carries the pk a later
        # candidate for this same bag will adopt. A dry run claims the place
        # with no pk, which blocks rather than adopts — the nearest a run that
        # writes nothing can get to the real behaviour.
        cooldown.remember(cand.center, now,
                          alert.pk if alert is not None else None, level)
        prefix = "ALERT created" if alert else "ALERT suppressed (dry run)"
        self.stdout.write(self.style.SUCCESS(
            f"{prefix} [disposal] {scoring.label_of(level)} · {g_name} "
            f"{g_conf:.0%} after {still:.0f}s still"
            + (f" — {alert.code}" if alert else "")
        ))
        return True

    def _restate_alert(self, cand, detector, frame, g_name, g_conf, still,
                       description, level, stats):
        """Bring this candidate's existing row up to the latest verdict.

        True only when something actually moved, so the caller can keep
        counting "alerts written" and the log stays readable: the presence loop
        re-asks the bag check every few seconds and almost always gets the same
        answer back, which is not news.

        `peak_level` never falls — it is what decides whether the alert is
        LISTED, and an event a reviewer has already been shown must not vanish
        from Potential Violations because the next look was less sure. `level`
        is the live truth and moves both ways. Same split the scored detectors
        use (see IncidentMixin._incident_write).

        Only reachable once a row exists, which a dry run never creates, so
        there is no dry-run branch here.
        """
        if level == cand.alert_level:
            return False
        was = scoring.label_of(cand.alert_level)
        rose = scoring.LEVEL_ORDER[level] > scoring.LEVEL_ORDER[cand.alert_level]
        fields = {"level": level, "confidence": g_conf,
                  "object_confidence": g_conf, "description": description,
                  "suspect": str(g_name), "last_seen_at": timezone.now()}
        if rose:
            fields["peak_level"] = level
            # Re-shoot the still, because its caption carries the score: a card
            # badged Likely over a photo captioned with the weak percentage
            # that opened it reads as a system contradicting itself. The CLIPS
            # are left alone on purpose — they are anchored to the drop, which
            # is the moment worth watching and has not changed.
            image_url = self._save_still(
                self._annotate(frame, cand, detector.scale, g_name, g_conf, still),
                g_name)
            if image_url:
                fields["image_url"] = image_url
        Alert.objects.filter(pk=cand.alert_pk).update(**fields)
        timeline.add(cand.alert_pk, "status", scoring.label_of(level))
        cand.alert_level = level
        # Counted as an upgrade, NOT as another filing: `possible` and
        # `confirmed` are "rows opened at this status", and adding to them here
        # would count one bag twice.
        if rose:
            stats["upgraded"] += 1
        self.stdout.write(self.style.SUCCESS(
            f"ALERT {'raised' if rose else 'lowered'} [disposal] {was} -> "
            f"{scoring.label_of(level)} · {g_name} {g_conf:.0%}"))
        return True

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
            self._persons = []
            return self._movers
        # The PERSON subset is kept separately as well as merged. The ignore
        # rule does not care whether a blob is sitting inside a person or a
        # parked car; the owner rule only ever means a person. One pass, two
        # readings of it — adding a second detector for the owner rule would
        # be paying twice for detections already in hand.
        self._persons = [(x1, y1, x2 - x1, y2 - y1)
                         for x1, y1, x2, y2, *_rest in persons]
        self._movers = self._persons + [(x1, y1, x2 - x1, y2 - y1)
                                        for x1, y1, x2, y2, *_rest in vehicles]
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
    # alert's `cues` stay empty. What an operator is waiting on is still-time,
    # so that is what the score bar tracks. The Possible/Likely the page shows
    # for a candidate and the one on its alert row are now the same word for
    # the same verdict, which is the point of borrowing the vocabulary.

    # An object old enough to check but still being stood over. Not one of
    # scoring's levels -- it is not a confidence at all, it is "the rule is
    # working and deliberately waiting" -- so it is named here and styled with
    # the Monitoring colour the page already has.
    # Score bands, matching the rest of the system so one bar means one thing
    # everywhere: the other detectors' Possible starts at 0.55 and Likely at
    # 0.75, and a bar that filled to 100% on a disposal candidate that had not
    # been judged would read as a stronger claim than the detector is making.
    # Disposal has no weighted score to plot, so the bar instead tracks
    # PROGRESS: still-time across Monitoring, then a flat band for the two
    # WAITING states, then full once a verdict exists.
    #
    MONITORING_MAX_PCT = 54     # 0-54 while the still-timer counts
    POSSIBLE_MIN_PCT = 55       # 55-99 once still but unconfirmed
    POSSIBLE_MAX_PCT = 99

    # Sub-labels for the three ways a candidate can sit at Possible. Shown
    # beside the status, because "Possible" alone cannot distinguish a rule
    # that is waiting from a model that is unsure -- and those call for
    # different things from whoever is watching.
    DETAIL_OWNER = "Owner nearby"
    DETAIL_CHECKING = "Checking"
    # Stage 1 can no longer see this object; the bag check is what is keeping
    # it on screen. Worth saying out loud, because the box no longer moves or
    # resizes at all once it is pinned, and a frozen box is exactly what a
    # stuck detector looks like.
    DETAIL_PINNED = "Still there"

    def _possible_pct(self, cand, conf):
        """Where in the 55-99 band a Possible candidate sits.

        A weak match is placed by how close its score came to the floor, so an
        object the model nearly accepted reads differently from one it barely
        noticed. Waiting states sit at the bottom of the band: no score exists
        yet, and inventing a position for one would be a guess drawn as a
        measurement.
        """
        span = self.POSSIBLE_MAX_PCT - self.POSSIBLE_MIN_PCT
        if not cand.weak or conf <= 0:
            return self.POSSIBLE_MIN_PCT
        return self.POSSIBLE_MIN_PCT + int(round(span * min(1.0, cand.score / conf)))

    def _publish_view(self, detector, candidates, now, static_seconds,
                      owner_away_seconds=None, owner_max_hold=None, conf=None):
        if self.debug_pub is None:
            return
        conf = self._confidence(SystemSettings.load()) if conf is None else conf
        held = set()
        if owner_away_seconds is not None:
            held = {c.id for c in detector.held(now, static_seconds,
                                                owner_away_seconds, owner_max_hold)}
        for cand in candidates:
            # Candidate boxes live in PROC_WIDTH coordinates; the page scales
            # against the published frame's full-resolution dimensions, so they
            # have to be mapped back the same way _annotate does.
            box = [int(v / detector.scale) for v in cand.box]
            still = cand.still_for(now)
            detail = ""
            indicators = [
                {"name": "seconds still", "points": int(round(still))},
                {"name": "needs", "points": int(round(static_seconds))},
            ]
            if cand.confirmed:
                status = scoring.label_of(scoring.VIOLATION)      # "Likely"
                pct = 100
                indicators.append({"name": cand.label or "garbage",
                                   "points": int(round(cand.score * 100))})
                if cand.pinned:
                    detail = self.DETAIL_PINNED
            elif cand.rejected:
                # Below Monitoring: the page draws these dashed and faded and
                # the Panel view hides them. A look-alike that stage 2 threw
                # out is exactly that — seen, judged, passed over — and showing
                # WHY (the winning reject class) is the whole point, because a
                # run that rejects everything otherwise looks like a blind
                # detector rather than a working one.
                status = debug_view.BELOW
                pct = 0
                indicators.append({"name": cand.label or "not garbage", "points": 0})
            elif still < static_seconds:
                # Still counting. The only state where the bar is a real
                # measurement of anything: progress toward the requirement.
                status = scoring.label_of(scoring.MONITORING)     # "Monitoring"
                pct = int(min(self.MONITORING_MAX_PCT,
                              round(still / max(static_seconds, 1e-6)
                                    * self.MONITORING_MAX_PCT)))
            else:
                # Still long enough, not confirmed, not thrown out. Three ways
                # to be here and the sub-label says which: waiting on an owner
                # to leave, waiting on stage 2, or stage 2 said "bag" too
                # quietly to act on. None of them writes an Alert.
                status = scoring.label_of(scoring.WARNING)        # "Possible"
                pct = self._possible_pct(cand, conf)
                if cand.id in held:
                    detail = self.DETAIL_OWNER
                    away = cand.owner_away_for(now)
                    indicators.append({"name": "owner away",
                                       "points": int(round(away)) if away else 0})
                elif cand.weak:
                    detail = (f"{self.DETAIL_PINNED} · weak {cand.score:.0%}"
                              if cand.pinned else f"Weak match {cand.score:.0%}")
                    indicators.append({"name": cand.label or "garbage",
                                       "points": int(round(cand.score * 100))})
                    indicators.append({"name": "needs",
                                       "points": int(round(conf * 100))})
                else:
                    detail = self.DETAIL_CHECKING
            self.debug_pub.note(
                key=("disposal", cand.id), violation="disposal", ident=cand.id,
                box=box, status=status, score_pct=pct, indicators=indicators,
                multipliers={}, momentum=round(still, 1), kind="object",
                detail=detail,
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
                # Green: a row was written for this. The two qualifiers
                # compose rather than overwrite -- a weak match that is also
                # being held is both of those things at once, and the window
                # is the only place a calibration run can see either.
                color = (0, 200, 0)
                text = f"{cand.label} {cand.score:.0%}"
                if cand.pinned:
                    # No blob backs this box any more.
                    text += f" (held {cand.pinned_for(now):.0f}s)"
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

    # ---- suspect crop ------------------------------------------------------

    def _save_suspect(self, cand, scale, ts_label, tag, cfg):
        """(url, status) for the "likely dropped by" crop. Never raises.

        Three outcomes in strict order, and the order is the point:

          person   somebody was detected in the window around the drop
          carrier  nobody was, but something was still MOVING where the object
                   landed — a shape beside the bag beats nothing at all
          none     neither, recorded explicitly so the card can say so

        Evidence capture must never be able to stop an alert being written, so
        every failure here degrades to "none" rather than propagating. An alert
        with no suspect is still a violation; a violation lost to a cropping
        error is not recoverable.
        """
        if cand is None:
            return "", Alert.SuspectStatus.NONE
        try:
            name = f"{ts_label}_disposal_{tag}_suspect.jpg"
            path = self.violations_dir / name
            conf = (cfg.disposal_suspect_confidence / 100.0 if cfg is not None
                    else disposal_cv.SUSPECT_CONFIDENCE)

            frames = [(t, disposal_cv.decode_frame(j)) for t, j in cand.suspect_frames]
            frames = [(t, f) for t, f in frames if f is not None]
            centre = [v / scale for v in cand.center]

            samples = []
            for _t, img in frames:
                try:
                    persons, *_rest = recognition.detect_scene(img, conf=conf)
                except Exception:                               # noqa: BLE001
                    continue
                samples.append((img, [(x1, y1, x2 - x1, y2 - y1)
                                      for x1, y1, x2, y2, *_r in persons]))
            crop, _box = disposal_cv.pick_suspect(samples, centre)
            if crop is not None and crop.size:
                cv2.imwrite(str(path), crop)
                return violation_media_path(name), Alert.SuspectStatus.PERSON

            # Fallback 1 — the moving blob that was there when it landed.
            if cand.carrier_box is not None and frames:
                near = min(frames, key=lambda f: abs(f[0] - cand.first))[1]
                carrier = disposal_cv.crop_full_res(near, cand.carrier_box, scale)
                if carrier is not None and carrier.size:
                    cv2.imwrite(str(path), carrier)
                    return violation_media_path(name), Alert.SuspectStatus.CARRIER
        except Exception as exc:                                # noqa: BLE001
            self.stdout.write(self.style.WARNING(
                f"Could not capture a suspect crop ({exc}); recording none."))
        return "", Alert.SuspectStatus.NONE

    # ---- alert -------------------------------------------------------------

    def _save_still(self, frame, label, ts_label=None):
        """Write the annotated evidence photo; its media URL, or "" off-disk.

        Shared by the first filing and by an upgrade, so one naming convention
        serves both and a re-shot still cannot drift from the original scheme.
        """
        ts_label = ts_label or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{ts_label}_disposal_{str(label).replace(' ', '_')}.jpg"
        cv2.imwrite(str(self.violations_dir / name), frame)
        return violation_media_path(name)

    def _create_alert(self, score, label, frame, description, now=None,
                      cand=None, scale=1.0, cfg=None, level=""):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = str(label).replace(" ", "_")
        image_url = self._save_still(frame, label, ts_label)

        # The annotated clip is NOT saved here. ClipRecorder holds the frames
        # BEFORE now, so saving at this moment yields a clip ending on the
        # detection -- the approach, then nothing. It is scheduled below
        # instead, once the row exists to attach it to.
        video_name = f"{ts_label}_disposal_{tag}.mp4"
        # Empty on the row at first: _drain_pending fills it in once the
        # post-roll has played and the clip actually exists.
        video_url = ""
        if self.clip is not None:
            self.clip.add(frame, now if now is not None else time.time())

        suspect_image_url, suspect_status = self._save_suspect(
            cand, scale, ts_label, tag, cfg)

        raw_video_url = ""
        clip_offset = None
        raw_name = f"{ts_label}_disposal_{tag}_raw.mp4"
        raw_path = self.violations_dir / raw_name
        if self._source_path is not None and self._video_pos_sec is not None:
            # Anchored to the DROP, not to the alert. The alert can be a full
            # owner-hold minute after the object landed, and a pre-roll
            # measured backwards from it would start well after the only part
            # of the footage anybody wants to see — somebody putting it there.
            # The clip therefore runs from just before the object appeared
            # through to the usual post-roll past the alert.
            drop_at = cand.first if cand is not None else self._video_pos_sec
            start = max(0.0, drop_at - RAW_CLIP_PRE_DROP_SECONDS)
            duration = ((self._video_pos_sec - start)
                        + recognition.RAW_CLIP_POST_SECONDS)
            cut_ok = recognition.cut_raw_clip(self._source_path, start, duration, raw_path)
            if cut_ok:
                # Same expression as the other detectors, but `start` is
                # anchored to the DROP rather than to a fixed lead-in, so this
                # is usually well past RAW_CLIP_PRE_SECONDS.
                clip_offset = self._video_pos_sec - start
        elif self._raw_buffer is not None:
            # A live source has no seekable file to reach back into, so this
            # saves whatever the rolling buffer still holds — which on a long
            # hold may no longer reach the drop. Stated, not silently papered
            # over: the annotated clip and the suspect crop both still do.
            cut_ok = self._raw_buffer.save(raw_path)
        else:
            cut_ok = False
        if cut_ok:
            raw_video_url = violation_media_path(raw_name)

        owner_left_at = None
        if cand is not None and now is not None:
            away = cand.owner_away_for(now)
            if away is not None:
                # `away` is measured in the detector's clock — footage position
                # for a clip, wall clock for a stream — but the ELAPSED time is
                # the same quantity in both, so subtracting it from now() gives
                # a wall-clock instant either way.
                owner_left_at = timezone.now() - datetime.timedelta(seconds=away)

        if self.dry_run:
            return None

        alert = Alert.objects.create(
            suspect_image_url=suspect_image_url,
            suspect_status=suspect_status,
            owner_left_at=owner_left_at,
            footage_offset=self._video_pos_sec if self._source_path is not None else None,
            type=self.disposal_type,
            status=Alert.Status.ACTIVE,
            camera=self.camera,
            timestamp=timezone.now(),
            confidence=score,
            # Possible or Likely, per the bag check — see the module docstring.
            # `peak_level` opens equal to it and only ever rises (in
            # _restate_alert), because it is what decides whether the alert is
            # listed at all.
            level=level,
            peak_level=level,
            last_seen_at=timezone.now(),
            # The zero-shot bag score, which is what the card's "Object
            # confidence" box shows. `cues` is left empty on purpose: disposal
            # has no cited cue table to sum, so it shows no score breakdown,
            # exactly like parking.
            object_confidence=score,
            description=description,
            image_url=image_url,
            video_url=video_url,
            raw_video_url=raw_video_url,
            clip_offset=clip_offset,
            suspect=str(label),
        )
        # Filled in by _drain_pending once the post-roll has played. A raw clip
        # cut from a FILE already covers both sides of the detection, so it is
        # written above and only the annotated one waits.
        if self.clip is not None:
            path = self.violations_dir / video_name
            when = now if now is not None else time.time()
            if self.pending is None:
                # No drain wired (nothing does this today, but a caller driving
                # this Command from its own loop would) — save it now rather
                # than schedule a clip nobody will write.
                if self.clip.save(path):
                    Alert.objects.filter(pk=alert.pk).update(
                        video_url=violation_media_path(video_name))
            else:
                self.pending.schedule(alert.pk, self.clip, path, when,
                                      recognition.RAW_CLIP_POST_SECONDS)
        return alert
