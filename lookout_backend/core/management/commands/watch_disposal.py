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

The cue table (scoring.DISPOSAL_WEIGHTS)
----------------------------------------
Disposal is a scored detector like the other four, and reaches its status the
same way: SUM(weight * cue) against 0.55 (Possible) and 0.75 (Likely).

    object     0.40  GATE -- the bag check led every look-alike AND cleared
                     disposal_confidence
    arrival    0.20  this patch of ground changed, more than its surroundings
    person     0.10  somebody was beside it as it appeared
    left       0.25  ... and then walked away (conditional on `person`)
    persisted  0.05  still there when the bag check was re-run later

One difference from the other three tables has to be stated and not glossed:
THESE WEIGHTS ARE OURS. Drinking, smoking and holdup take theirs from
published work; this one is tuned on Barangay Tetuan's own footage. What IS
standard is the cue structure -- "a person brought it, it went static, they
left it" is the abandoned-object formulation the PETS2006 and AVSS/i-LIDS
benchmarks are built around.

Disposal carried no table at all for most of its life, on the argument that
inventing weights to look like the scored detectors would present a guess as a
measurement. What overturned that was trying the alternative: the same
evidence was used as BOOLEAN GATES, and a gate that is wrong is wrong in
silence. A missed person detection -- routine at distance, at night, or on
somebody walking away from the camera -- withheld a real violation for its
entire life, which on screen is indistinguishable from the street being clean.
A weight honestly labelled as tuned is a better instrument than that.

What the table changes, in both directions:

  * a confirmed bag with nothing else known is 0.40, the watchlist, instead of
    an instant Likely;
  * a dump whose carrier was never detected still reaches 0.60 on the object
    and arrival cues -- reported as Possible rather than silently dropped.

No gate cue, no event. A weak bag check (leads but under the floor) fires
nothing, so nothing is written and no evidence files are cut -- the same first
rule every other detector obeys, and what keeps disposal_confidence meaningful.

While the owner is still standing over it the status is CAPPED at Monitoring
(it is not abandoned yet) while the score keeps accruing underneath, so it
reports at full strength the moment they walk away. Same mechanism as
smoking's puff gate.

There is ONE row per dumped object -- `Candidate.alert_pk` carries it -- so a
lead that firms up is upgraded in place rather than filed again, and
`peak_level` never falls so an event a reviewer has seen cannot vanish.

Waste-collection window
-----------------------
Barangay Tetuan allows garbage to be set out only from 04:00 to 07:00, Monday
to Saturday. A bag that stage 2 judges Confirmed or Weak INSIDE that window is
stored at Monitoring (no notification) with a reason on the alert, kept tracked
past 07:00, and re-checked once the window closes: still there and still judged
garbage, it rises to Likely / Possible exactly as it would have outside the
window. Outside the window, and all day Sunday, nothing changes. The time of day
comes from the wall clock (Asia/Manila) live, and from --clock plus the position
in the clip for a file; a file with no --clock skips the rule, with one warning.

    python manage.py watch_disposal --source <rtsp-url-or-clip.mp4>
    python manage.py watch_disposal --source 0 --debug
    python manage.py watch_disposal --source clip.mp4 --clock "2026-10-27 05:00"
"""

import datetime
import json
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core import descriptions
from core import timeline
from core.media import violation_media_path
from core.models import Alert, Camera, SystemSettings, ViolationType
from core.vision import clock as vclock
from core.vision import spec_settings
from core.vision.progress import Spinner

# Same reason as watch_parking: importing recognition pulls in torch, which is
# seconds of silence before handle() is even called.
with Spinner("Loading PyTorch", tty_only=True):
    from core.vision import debug_view                        # noqa: E402
    from core.vision import disposal as disposal_cv           # noqa: E402
    from core.vision import recognition                       # noqa: E402
    from core.vision import zone_autodetect as autodetect     # noqa: E402
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
# The collection window is a local-time rule, so a live stream is read in
# Manila time explicitly rather than in whatever zone the host is set to.
COLLECTION_TZ = ZoneInfo("Asia/Manila")


class Command(BaseCommand):
    # None means "save the annotated clip immediately" — correct for any path
    # that does not drain the queue. _run_stream, which does, switches it on.
    pending = None
    # --clock footage start; None for live and for a clip run without one.
    # Class-level so a Command built without handle() (the tests) still has them.
    clock_start = None
    _window_warned = False
    presence_seconds = disposal_cv.PRESENCE_RECHECK_SECONDS

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
            "--no-auto-area", action="store_true",
            help="Do not work the watched area out from the footage when none "
                 "is saved. Without this flag, a run with no area traces the "
                 "road and its margins; with it, the whole frame is watched, "
                 "which is what disposal did before the tracer existed.",
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
        parser.add_argument(
            "--clock", default="",
            help="Footage start time for a video FILE, e.g. \"2026-10-27 05:00\" "
                 "(Manila time). Drives the waste-collection window. Ignored for "
                 "live streams; a file without it skips the window rule.",
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
        # None, not [] -- "the person pass has not run / is unavailable", which
        # the owner rule has to be able to tell apart from "it ran and saw
        # nobody". [] would make every candidate owner_known and so withhold
        # every one of them the moment the detector was lost. See
        # disposal.Candidate.owner_known.
        self._persons = self._vehicles = None
        self._movers_at = -1e9
        self._mover_warned = False

        self.dry_run = options["dry_run"]
        self.conf_override = options["confidence"]
        self.static_override = options["static_seconds"]
        self.presence_seconds = (options["presence_seconds"]
                                 or disposal_cv.PRESENCE_RECHECK_SECONDS)
        self.trim = trimming.Trim(options["start"], options["end"])
        try:
            self.clock_start = vclock.parse_clock(options.get("clock"))
        except ValueError as exc:
            self.stdout.write(self.style.ERROR(str(exc)))
            return
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
                # single frozen image instead of the 30s lead-up.
                #
                # BOXED, not clean. These frames used to go in untouched, with
                # a single annotated one appended at the end by _create_alert
                # — so the "annotated" clip was the raw footage plus one
                # marked frame, and once the save was deferred to centre the
                # detection even that frame ended up buried in the middle.
                # The two versions in the player were then indistinguishable.
                # watch_parking has always buffered its drawn frames; this now
                # does too.
                if self.clip is not None:
                    self.clip.add(self._annotate_live(frame, detector), now_ts)

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
                        cand.rejected, cand.weak, cand.confirmed = True, False, False
                        cand.label = f"{r_name} {r_conf:.0%}" if r_name else "not garbage"
                        stats["rejected"] += 1
                        # A rejection is the one verdict that ends a candidate's
                        # chances, and "person 86%" alone does not say whether a
                        # person was IN the crop or the model called the rubbish
                        # a person. Those need opposite fixes, so the numbers
                        # that tell them apart are printed: what was cut, how
                        # many bystanders were trimmed off it, and what the best
                        # GARBAGE prompt managed against the winner.
                        self.stdout.write(
                            f"  #{cand.id} rejected: {r_name} {r_conf:.0%} "
                            f"(best garbage: {g_name or 'none'} {g_conf:.0%}) "
                            f"· {self._crop_note(frame, cand, detector.scale)}")
                        continue

                    # Stage 2 looked and did not say "that is a dog". Counted
                    # before the verdicts split, because the `persisted` cue
                    # is about having been SEEN again, not about how sure the
                    # second look was.
                    cand.confirm_count += 1

                    if verdict == disposal_cv.WEAK:
                        # Garbage led every look-alike but fell short of the
                        # floor, so the GATE cue does not fire -- and with no
                        # gate there is no event, the same first rule every
                        # other detector obeys. Nothing is written and no
                        # evidence files are cut.
                        #
                        # Not a write-off either: it stays in the recheck
                        # queue (last_check above schedules the next look), so
                        # the same object scores properly the moment the bag
                        # check firms up. A zero-shot head ranks nine garbage
                        # synonyms against eleven look-alikes on every crop it
                        # is handed, so SOMETHING leads nearly always; a 9%
                        # lead over an 8% reject is not the model saying
                        # "bag, unsure", it is the model saying nothing.
                        cand.weak, cand.rejected, cand.confirmed = True, False, False
                        cand.label, cand.score = g_name, g_conf
                        stats["weak"] += 1
                        self.stdout.write(
                            f"  {g_name} leads at {g_conf:.0%} but the floor is "
                            f"{conf:.0%} — no object cue, rechecking in "
                            f"{cfg.disposal_recheck_seconds}s.")
                        # An object that ALREADY has a row has to be told its
                        # evidence weakened; one that never had a row stays
                        # unwritten. _alert makes that split.
                        if self._alert(cand, detector, frame, g_name, g_conf,
                                       now_ts, cfg, cooldown, stats):
                            n_alerts += 1
                        continue

                    cand.confirmed, cand.rejected, cand.weak = True, False, False
                    cand.label = g_name
                    cand.score = g_conf
                    if self._alert(cand, detector, frame, g_name, g_conf,
                                   now_ts, cfg, cooldown, stats):
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
                        cand.confirm_count += 1

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
                    # a weak lead on the next look falls, the same way the
                    # scored detectors let a status fall. _alert is a no-op
                    # when nothing changed, so this costs one comparison per
                    # presence check.
                    if cand.confirmed or cand.weak:
                        if self._alert(cand, detector, frame, g_name, g_conf,
                                       now_ts, cfg, cooldown, stats):
                            n_alerts += 1

                # The collection window closed on a bag stage 1 can still see.
                # Nothing else asks a CONFIRMED, unpinned candidate anything
                # again, so without this its row would sit at Monitoring until
                # the bag moved. Pinned candidates are covered by the loop above.
                inside, _window, _when = self._in_collection_window(now_ts, cfg)
                if not inside:
                    for cand in detector.due_for_window_recheck(
                            now_ts, self.presence_seconds):
                        verdict, (g_name, g_conf), _r = self._ask_stage2(
                            frame, cand, detector.scale, conf)
                        present = verdict != disposal_cv.REJECTED
                        if present:
                            cand.weak = verdict == disposal_cv.WEAK
                            cand.label, cand.score = g_name, g_conf
                            cand.confirmed = verdict == disposal_cv.CONFIRMED
                            cand.confirm_count += 1
                            if self._alert(cand, detector, frame, g_name, g_conf,
                                           now_ts, cfg, cooldown, stats):
                                n_alerts += 1
                        if not detector.record_window_recheck(cand, present, now_ts):
                            self.stdout.write(
                                f"  #{cand.id} is no longer there after the "
                                f"collection window closed -- left at Monitoring.")

                self._drain_pending(now_ts)

                stats["ignored"] += len(detector.ignored)
                self._publish_view(detector, candidates, now_ts, static_seconds,
                                   float(cfg.disposal_owner_away_seconds),
                                   float(cfg.disposal_owner_max_hold), conf, cfg)

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
            f"({stats['upgraded']} later rose), "
            f"{stats.get('held', 0)} left on the Monitoring watchlist "
            f"(too little evidence to announce, or the collection window), "
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
            # The ANNOTATED clip's own offset. Its own field because the two
            # clips are different lengths — writing this into clip_offset
            # would mark the raw clip at the wrong frame.
            if offset is not None:
                fields["annotated_offset"] = offset
            # clip_offset belongs to the raw clip, which on a FILE run is cut
            # from the source and already carries the truer number. Only a live
            # run, whose "raw" clip is this same rolling buffer, shares it.
            if offset is not None and self._source_path is None:
                fields["clip_offset"] = offset
            Alert.objects.filter(pk=alert_pk).update(**fields)

    def _crop_note(self, frame, cand, scale):
        """One line describing the crop stage 2 was actually given.

        Reported twice from footage as "why did it not alert", and twice I
        guessed from a screenshot. The detector can simply say.
        """
        avoid = self._movers or []
        wide = disposal_cv.crop_rect_full_res(frame.shape, cand.box, scale)
        tight = disposal_cv.crop_rect_full_res(frame.shape, cand.box, scale,
                                               avoid=avoid)
        box = tuple(int(v / scale) for v in cand.box)
        trimmed = "unchanged" if tight == wide else f"trimmed from {wide}"
        # The arrival numbers ride along because they now decide which reject
        # classes are even allowed to vote, and a rejection that turns on them
        # is unreadable without them.
        arrival = (f"arrival {cand.arrival_inner:.0%} vs ring "
                   f"{cand.arrival_ring:.0%} "
                   f"({'proven' if cand.arrival_proven else 'unproven'})")
        return (f"object={box} crop={tight} ({trimmed}) "
                f"avoid={len(avoid)} box(es) · {arrival} · "
                f"owner={'yes' if cand.had_owner else 'no'}")

    def _ask_stage2(self, frame, cand, scale, conf):
        """One zero-shot pass on this candidate's crop.

        (verdict, (garbage_label, score), (reject_label, score)). Shared by the
        two callers on purpose: the static-object check and the "is it still
        there?" check ask the model exactly the same question about exactly the
        same crop, and only the meaning of the answer differs. Two copies of
        this would be two places for the crop padding or the floor to drift.
        """
        # The people and vehicles this frame, so the padding can be pulled off
        # them. Reported twice from footage: rubbish with bystanders a metre
        # away came back "person 88%" and could never fire the object cue.
        # Same boxes stage 1 already has -- no extra inference. See shrink_away.
        crop = disposal_cv.crop_full_res(frame, cand.box, scale,
                                         avoid=self._movers)
        # What the TRAINED detector says is not here. A zero-shot "person 86%"
        # at a spot yolov8n sees no person at does not get to veto. See
        # disposal.TRUSTED_REJECTS.
        absent = disposal_cv.absent_rejects(
            tuple(v / scale for v in cand.box),
            persons=self._persons, vehicles=self._vehicles)
        # ... plus the one this candidate's own owner makes meaningless. The
        # person standing over rubbish they just put down is the dumper, not a
        # look-alike. See disposal.dumper_rejects.
        absent |= disposal_cv.dumper_rejects(cand)
        # ... and the container classes a PROVEN arrival rules out. A bin that
        # lives here would have been learned as background, never raised, and
        # never measured as a change. See disposal.fixture_rejects.
        absent |= disposal_cv.fixture_rejects(cand)
        _ok, best_g, best_r = disposal_cv.confirm(
            self.world, crop, self.garbage_ids, conf, absent=absent)
        return disposal_cv.classify(best_g, best_r, conf), best_g, best_r

    def _alert(self, cand, detector, frame, g_name, g_conf, now, cfg, cooldown,
               stats):
        """Score, place gate, evidence, Alert row. True if one was written.

        The level is WORKED OUT here, from `_score`, rather than handed in.
        Every caller used to decide it for itself from the stage-2 verdict
        alone, which is exactly the design this replaced: a confirmed bag went
        straight to Likely however little else was known about it, and a weak
        one was a flat watchlist row. The cue table (scoring.DISPOSAL_WEIGHTS)
        weighs the arrival and owner evidence alongside the bag check instead.

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
        scored = self._score(cand, now, cfg,
                             held=self._held_by_owner(cand, now, cfg))
        level = scored.level

        # No gate cue, so no event -- the first rule in the spec, and the one
        # that keeps the confidence floor meaningful. A candidate with no row
        # is simply not written. One that ALREADY has a row cannot vanish from
        # the dashboard because a later look was weaker, so it falls to the
        # watchlist floor instead; `peak_level` is what keeps it listed.
        if level == scoring.NONE:
            if cand.alert_pk is None and cooldown.alert_at(cand.center, now) is None:
                return False
            level = scoring.MONITORING

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

        # Inside the collection window the verdict is stored, not announced.
        # Decided AFTER the row is adopted because the rule applies to a row
        # that is new or already parked at Monitoring, never to one that has
        # reached Possible / Likely: a window opening at 04:00 must not walk an
        # existing Likely back down.
        level, window_label = self._apply_collection_window(cand, now, cfg, level)
        description = descriptions.disposal(g_name, still, away, window=window_label)

        # Filed, by this candidate or by the one before it at this spot. The
        # place is held open first, then the row is brought up to date.
        if cand.alert_pk is not None:
            cooldown.remember(cand.center, now, cand.alert_pk, level)
            changed = self._restate_alert(cand, detector, frame, g_name, g_conf,
                                          still, description, level, stats, scored)
            cand.note_level(level, now)
            return changed

        # Reported, with no row to adopt: a dry run.
        if cooldown.blocks(cand.center, now):
            stats["cooldown"] += 1
            self.stdout.write(
                f"  {g_name} reads as {scoring.label_of(level)} but this spot "
                f"has already been reported — no alert.")
            return False

        if level == scoring.MONITORING:
            stats["held"] = stats.get("held", 0) + 1
        else:
            stats["confirmed" if level == scoring.VIOLATION else "possible"] += 1

        # Draw the box BEFORE the frame becomes evidence: the still and the
        # clip both come from this array, and an evidence photo that does not
        # show WHERE the rubbish was leaves the tanod hunting for it in the
        # frame. watch_parking gets this for free because its pipeline already
        # drew on the frame; nothing draws on ours, so it has to happen here.
        evidence = self._annotate(frame, cand, detector.scale, g_name, g_conf, still)
        alert = self._create_alert(
            g_conf, g_name, evidence, description, now=now,
            cand=cand, scale=detector.scale, cfg=cfg, level=level, scored=scored)
        if alert is not None:
            cand.alert_pk, cand.alert_level = alert.pk, level
            cand.note_level(level, now)
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

    # ---- waste-collection window -------------------------------------------

    def _collection_clock(self, now):
        """Local time of day for the collection rule, or None if it is unknown.

        Same source as the holdup E20 rule: a live stream reads the wall clock
        (in Manila time); a file reads --clock plus its position in the clip; a
        file with no --clock has no time of day -- the wall clock would be when
        it was PROCESSED, not filmed -- so the rule is skipped, with one warning.
        """
        if self._source_path is not None:
            if self.clock_start is None:
                if not self._window_warned:
                    self._window_warned = True
                    self.stdout.write(self.style.WARNING(
                        "Collection window skipped: no clip time "
                        "(pass --clock to apply it to this file)."))
                return None
            return vclock.clock_now(self.clock_start, now, True)
        return datetime.datetime.now(COLLECTION_TZ)

    def _in_collection_window(self, now, cfg):
        """(inside, window, when). `window` is None when the rule is off or unusable."""
        try:
            window = disposal_cv.CollectionWindow.from_settings(cfg)
        except ValueError as exc:
            if not getattr(self, "_window_bad_logged", False):
                self._window_bad_logged = True
                self.stdout.write(self.style.WARNING(
                    f"Collection window ignored ({exc})"))
            return False, None, None
        if window is None:
            return False, None, None
        when = self._collection_clock(now)
        if when is None:
            return False, None, None
        return window.contains(when), window, when

    def _apply_collection_window(self, cand, now, cfg, level):
        """(level to store, window label or None) for a Possible / Likely verdict.

        Inside the window the verdict is stored at Monitoring and the candidate
        is marked `window_held`, with `hold_until` pushed past the close plus two
        presence checks so it is still tracked -- pinned past its 30 minute cap
        if need be -- when the window shuts. Once the window is closed a held
        candidate is released here, and the unchanged `level` then raises its row
        to the real status through the ordinary restate path. When the hold ends
        the pin's own 30 minute clock restarts, so a bag raised at 07:00 gets
        the same tracking life any other pinned bag has.
        """
        if level not in (scoring.WARNING, scoring.VIOLATION):
            return level, None
        inside, window, when = self._in_collection_window(now, cfg)
        if inside and (cand.alert_pk is None or cand.alert_level == scoring.MONITORING):
            cand.window_held = True
            cand.window_misses = 0
            cand.hold_until = (now + window.seconds_until_close(when)
                               + 2 * self.presence_seconds)
            return scoring.MONITORING, window.label()
        if cand.window_held and not inside:
            cand.window_held = False
            cand.hold_until = None
            if cand.pinned:
                cand.pinned_since = now
        return level, None

    def _restate_alert(self, cand, detector, frame, g_name, g_conf, still,
                       description, level, stats, scored=None):
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
        if scored is not None:
            # The breakdown has to move WITH the status, or the card shows a
            # Likely badge over the cue list that only justified a Possible.
            row = Alert.objects.filter(pk=cand.alert_pk).values("cues").first() or {}
            fields["cues"] = {**(row.get("cues") or {}), **scored.as_dict()}
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
        # Counted as an upgrade, NOT as another filing: `confirmed` and
        # `held` are "rows opened at this status", and adding to them here
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
            self._persons = self._vehicles = None
            return self._movers
        # The PERSON subset is kept separately as well as merged. The ignore
        # rule does not care whether a blob is sitting inside a person or a
        # parked car; the owner rule only ever means a person. One pass, two
        # readings of it — adding a second detector for the owner rule would
        # be paying twice for detections already in hand.
        self._vehicles = [(x1, y1, x2 - x1, y2 - y1)
                          for x1, y1, x2, y2, *_rest in vehicles]
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

        Two sources: the camera's own disposal area, written by the dashboard's
        Edge Zones editor (pixels plus the frame size they were drawn at), then
        a draw_zone file. It is NOT parking's no-parking polygon -- that covers
        the carriageway, and rubbish lies beside it.
        """
        requested = options.get("zone") or ""
        if not requested:
            points = self._area_points_from_camera()
            if points is None:
                # Nothing saved — work one out from the footage rather than
                # falling straight back to the whole frame, which on a 4MP
                # image raises a flood of noise blobs from trees and sky.
                return self._auto_area(options)
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

    def _auto_area(self, options):
        """(Area, description) traced from the footage, or (None, "") for the
        whole frame.

        Built on the same tracer parking uses, but PADDED FAR WIDER, and that
        difference is the whole point. Parking asks "is the vehicle on the
        carriageway", so the traffic hull is the answer. Disposal asks "where
        might rubbish be left" — and rubbish is left exactly where vehicles do
        not drive: at the kerb, against a wall, on the verge. Handed the bare
        hull, disposal would watch the one strip of ground nobody dumps on.
        So the road is traced the same way and then grown past its edges to
        take in the margins either side (DISPOSAL_PAD_FRACTION).

        NEVER SAVED onto the camera, unlike parking's. Both detectors read the
        same "zone" entry in Camera.edges, so persisting this wide area would
        silently replace the no-parking polygon with one reaching across the
        pavement — and parking would start reporting vehicles that were never
        on the road. It is traced per run and thrown away.

        Never fatal: every failure here returns "no area", which means watch
        the whole frame, which is exactly what disposal did before this
        existed.
        """
        if options.get("no_auto_area"):
            return None, ""

        source = options.get("source")
        if not source:
            return None, ""

        self.stdout.write(
            f"No area saved for {self.camera.code} — working one out from the "
            f"footage (the road and its margins)...")
        try:
            points, report, frame = autodetect.trace_clip(
                source,
                lambda f: recognition.detect_vehicles(
                    f, imgsz=autodetect.TRACE_IMGSZ),
                max_samples=autodetect.MAX_SAMPLES,
                pad_fraction=autodetect.DISPOSAL_PAD_FRACTION,
            )
        except Exception as exc:                                # noqa: BLE001
            self.stdout.write(self.style.WARNING(
                f"Could not trace an area ({exc}); watching the whole frame."))
            return None, ""

        if points is None:
            self.stdout.write(self.style.WARNING(
                f"  {report['reason']} Watching the whole frame."))
            return None, ""

        height, width = frame.shape[:2]
        try:
            area = disposal_cv.Area([[x / width, y / height] for x, y in points])
        except ValueError as exc:
            self.stdout.write(self.style.WARNING(
                f"  The traced area is unusable ({exc}); watching the whole frame."))
            return None, ""

        self.stdout.write(self.style.SUCCESS(
            f"  Traced a {len(points)}-point area from "
            f"{report['ground_points']} sighting(s) of moving vehicles. "
            "Not saved — it is this run's only."))
        return area, "the footage (auto-traced)"

    def _area_points_from_camera(self):
        """The camera's drawn polygon as normalised (0-1) points, or None.

        Reads Camera.disposal_edges only. It used to share parking's `edges`,
        which silently confined disposal to the road the moment a no-parking
        zone (or a traced one) was saved.
        """
        stored = self.camera.edges or {}

        def pick(kind):
            return next((s for s in stored.values()
                         if isinstance(s, dict) and s.get("type") == kind
                         and len(s.get("points") or []) >= 3), None)

        # A "disposal" area is this detector's own and wins outright. Falling
        # back to the no-parking "zone" keeps every camera drawn before the two
        # were distinguishable working exactly as it did -- but note what that
        # fallback IS: the carriageway, which is where rubbish is least likely
        # to be left. Drawing a disposal area, or letting this trace its own
        # (padded far wider), is the better answer on any camera that has one.
        spec = pick("disposal") or pick("zone")
        if spec is None:
            return None
        width = self.camera.edges_width
        height = self.camera.edges_height
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

    # The owner is still standing over it, so it is not abandoned YET. A cap
    # rather than a withholding: the score keeps accruing and the status is
    # held down, so the moment they walk away it reports at its real strength.
    # Same mechanism smoking's puff gate uses.
    OWNER_CAP_REASON = "owner still beside it"

    def _score(self, cand, now, cfg, *, held=False):
        """This candidate's scored verdict. The ONLY place disposal scores.

        Both the alert path and the live processing view come through here, so
        the status on the page and the status on the row cannot disagree --
        which they could, and did, while each worked the level out for itself.
        """
        cues = disposal_cv.cues_for(
            cand, cand.confirmed, now,
            away_seconds=float(cfg.disposal_owner_away_seconds))
        # The behaviour-only path. Without it the object cue is an absolute
        # veto: a bag check one point under the floor meant nothing else could
        # count, however completely the dumping itself had been observed.
        behaviour = disposal_cv.behaviour_only(cand, cand.confirmed, cues)
        cap, reason = None, ""
        if held:
            cap, reason = scoring.MONITORING, self.OWNER_CAP_REASON
        elif behaviour:
            cap, reason = scoring.WARNING, scoring.DISPOSAL_BEHAVIOUR_TAG
        return scoring.Score(
            "disposal", scoring.DISPOSAL_WEIGHTS, cues,
            previous_level=cand.alert_level or None,
            # `object_on` is what admits an event at all. The behaviour path
            # opens it without the gate cue, exactly as the puff-only path does
            # for smoking, and the cap above is what keeps it honest.
            object_on=True if behaviour else None,
            cap=cap, cap_reason=reason,
            detail=cand.label or "")

    def _held_by_owner(self, cand, now, cfg):
        return cand.held_by_owner(now,
                                  float(cfg.disposal_owner_away_seconds),
                                  float(cfg.disposal_owner_max_hold))

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
    # The bar is the REAL score now, on the same 0-100 scale as every other
    # detector, so 55 means Possible here exactly as it does on a smoking
    # card. The one exception is a candidate stage 2 has not been asked about
    # yet: no cue has had the chance to fire, so a score of 0 would say
    # "nothing here" when the truth is "not asked yet". Those show the
    # still-timer's progress instead, capped below the Possible line.
    STILL_MAX_PCT = 54          # 0-54 while the still-timer counts

    # Sub-labels. Shown beside the status, because a status alone cannot
    # distinguish a rule that is deliberately waiting from a model that is
    # unsure -- and those call for different things from whoever is watching.
    DETAIL_OWNER = "Owner nearby"
    DETAIL_CHECKING = "Checking"
    # Stage 1 can no longer see this object; the bag check is what is keeping
    # it on screen. Worth saying out loud, because the box no longer moves or
    # resizes at all once it is pinned, and a frozen box is exactly what a
    # stuck detector looks like.
    DETAIL_PINNED = "Still there"

    def _publish_view(self, detector, candidates, now, static_seconds,
                      owner_away_seconds=None, owner_max_hold=None, conf=None,
                      cfg=None):
        if self.debug_pub is None:
            return
        cfg = SystemSettings.load() if cfg is None else cfg
        conf = self._confidence(cfg) if conf is None else conf
        # Asked per candidate rather than read off detector.held(), which
        # excludes CONFIRMED ones -- it was built to answer "withheld from the
        # bag check", and a confirmed bag is never re-offered. As the source of
        # the owner CAP that exclusion is exactly backwards: a confirmed bag
        # with its owner still standing over it is the one case the cap is for.
        away_seconds = (float(cfg.disposal_owner_away_seconds)
                        if owner_away_seconds is None else owner_away_seconds)
        max_hold = (float(cfg.disposal_owner_max_hold)
                    if owner_max_hold is None else owner_max_hold)
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
            if cand.rejected:
                # Below Monitoring: the page draws these dashed and faded and
                # the Panel view hides them. A look-alike that stage 2 threw
                # out is exactly that — seen, judged, passed over — and showing
                # WHY (the winning reject class) is the whole point, because a
                # run that rejects everything otherwise looks like a blind
                # detector rather than a working one.
                status = debug_view.BELOW
                pct = 0
                indicators.append({"name": cand.label or "not garbage", "points": 0})
            elif cand.last_check is None:
                # Never been asked. No cue has had its chance, so the bar
                # tracks the still-timer rather than reporting a score of zero
                # as though the evidence had been weighed and found wanting.
                status = scoring.label_of(scoring.MONITORING)     # "Monitoring"
                pct = int(min(self.STILL_MAX_PCT,
                              round(still / max(static_seconds, 1e-6)
                                    * self.STILL_MAX_PCT)))
                detail = self.DETAIL_CHECKING
            else:
                # Scored, through the SAME call the alert path uses — so what
                # the page shows and what the row says cannot disagree.
                scored = self._score(
                    cand, now, cfg,
                    held=cand.held_by_owner(now, away_seconds, max_hold))
                # NONE means no gate cue -- nothing is written for it. On the
                # page it still has to read as something being watched, or a
                # candidate the detector is actively re-checking every few
                # seconds would be labelled "Not shown" while visibly boxed on
                # the frame. Below Monitoring stays reserved for a candidate
                # stage 2 actually threw out.
                level = (scoring.MONITORING if scored.level == scoring.NONE
                         else scored.level)
                status = scoring.label_of(level)
                pct = int(round(scored.score * 100))
                indicators += debug_view.indicator_list(scored)
                if scored.capped:
                    detail = self.DETAIL_OWNER
                    away = cand.owner_away_for(now)
                    indicators.append({"name": "owner away",
                                       "points": int(round(away)) if away else 0})
                elif cand.weak:
                    # No object cue: the bag check leads but is under the
                    # floor. Said plainly, with the floor beside it, because
                    # "Monitoring" alone does not tell an operator that the
                    # one number they can tune is what is holding it back.
                    detail = (f"{self.DETAIL_PINNED} · weak {cand.score:.0%}"
                              if cand.pinned else f"Weak match {cand.score:.0%}")
                    indicators.append({"name": cand.label or "garbage",
                                       "points": int(round(cand.score * 100))})
                    indicators.append({"name": "needs",
                                       "points": int(round(conf * 100))})
                elif cand.pinned:
                    detail = self.DETAIL_PINNED
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

    # Candidate colours for the rolling clip, BGR. Deliberately the same
    # vocabulary the live processing view uses, so a reviewer who has watched
    # a run recognises them: green is a confirmed bag, amber a weak lead,
    # grey something the bag check threw out.
    LIVE_CONFIRMED = (94, 197, 34)    # #22c55e
    LIVE_WEAK = (11, 158, 245)        # #f59e0b
    LIVE_REJECTED = (150, 150, 150)

    def _annotate_live(self, frame, detector):
        """A copy of `frame` with the watched area and every candidate drawn.

        Copied, never in place: the caller's frame goes on to the debug view,
        the stage-2 crops and the next iteration's detection, and none of them
        want our rectangles burned in.

        Cheap next to the inference it sits beside — a frame copy and a
        handful of rectangles — and it is what makes the annotated clip worth
        having as a separate thing from the raw one.
        """
        out = frame.copy()
        height, width = out.shape[:2]

        if detector.area is not None:
            points = np.array([[int(x * width), int(y * height)]
                               for x, y in detector.area.points_norm],
                              dtype=np.int32)
            cv2.polylines(out, [points], True, self.LIVE_CONFIRMED, 2)

        for cand in detector.candidates:
            if cand.rejected:
                colour, tag = self.LIVE_REJECTED, cand.label or "not garbage"
            elif cand.confirmed:
                colour, tag = self.LIVE_CONFIRMED, f"{cand.label} {cand.score:.0%}"
            elif cand.weak:
                colour, tag = self.LIVE_WEAK, f"{cand.label} {cand.score:.0%}?"
            else:
                colour, tag = self.LIVE_WEAK, "checking"
            x, y, w, h = [int(v / detector.scale) for v in cand.box]
            cv2.rectangle(out, (x, y), (x + w, y + h), colour, 2)
            cv2.putText(out, f"#{cand.id} {tag}", (x, max(20, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
        return out

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
                      cand=None, scale=1.0, cfg=None, level="", scored=None):
        ts_label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = str(label).replace(" ", "_")
        image_url = self._save_still(frame, label, ts_label)

        # The annotated clip is NOT saved here. ClipRecorder holds the frames
        # BEFORE now, so saving at this moment yields a clip ending on the
        # detection -- the approach, then nothing. It is scheduled below
        # instead, once the row exists to attach it to.
        video_name = f"{ts_label}_disposal_{tag}.mp4"
        # Saved BEFORE the row is written, so an alert never exists without
        # its annotated clip. Saved again later, centred on the detection —
        # see watch_parking._schedule_clip for why both.
        video_url = ""
        annotated_offset = None
        if self.clip is not None:
            self.clip.add(frame, now if now is not None else time.time())
            if self.clip.save(self.violations_dir / video_name):
                video_url = violation_media_path(video_name)
                # Pre-roll only for now, so the detection is the last frame.
                # _drain_pending moves it when the centred copy lands.
                annotated_offset = self.clip.span()

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
            # Only the settings in force, as the scored detectors log them. No
            # `cues` vector inside it: disposal still has no score breakdown.
            # The cue vector, same shape every other detector writes, so the
            # alert card renders disposal's breakdown through the existing
            # checklist path with no client change. `settings` rides alongside
            # it as the audit snapshot, as it does elsewhere.
            cues={**(scored.as_dict() if scored is not None else {}),
                  **({"settings": spec_settings.snapshot(cfg)} if cfg is not None else {})},
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
            annotated_offset=annotated_offset,
            suspect=str(label),
        )
        # Filled in by _drain_pending once the post-roll has played. A raw clip
        # cut from a FILE already covers both sides of the detection, so it is
        # written above and only the annotated one waits.
        # The clip is already written (above); this only queues the centred
        # replacement. Losing it costs centring, never the evidence.
        if self.clip is not None and self.pending is not None:
            self.pending.schedule(
                alert.pk, self.clip, self.violations_dir / video_name,
                now if now is not None else time.time(),
                recognition.RAW_CLIP_POST_SECONDS)
        return alert
