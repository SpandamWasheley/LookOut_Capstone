"""watch_merged plus road-edge obstruction: all four violations from one feed.

watch_merged already runs smoking, drinking and theft from a single pass of the
merged Bottle/Cigarette/knife model. This adds the fourth, illegal parking, as
an EXTRA ENGINE (see watch_merged.Command.EXTRA_ENGINES) — the merged model has
no vehicle class, so parking brings its own detector and its own rule layer.

Nothing about the obstruction rule is reimplemented here. watch_parking's own
Command is instantiated and its _load_zone / _run_zone / _create_alert are
called directly, exactly as its own handle() would, so the footprint fraction,
its hysteresis, the stationary gate, the identity recovery and the alert
cooldown are the same code running here as on a live parking camera. The same
holds for the other three: their rule layers come from
watch_smoking/watch_drinking/watch_thief unchanged.

This used to drive the OLDER edge rule (now detection_sandbox/obstruction.py),
meant parking behaved differently depending on which command you started — the
live monitor ran watch_parking's zone, this ran the edge monitor, and only the
former got the fixes that came out of testing on real footage (moving vehicles
no longer accrue, status follows progress, the polygon is drawn on the
processing view, a passing vehicle cannot steal a parked one's banked
seconds). One rule now, everywhere.

THE AREA MUST BE DRAWN FIRST
----------------------------
With nothing drawn this refuses to start rather than watching the whole frame.
Parking is one of four things running at once here, so a silent fallback would
mean every jeepney that stops at a corner raises an "illegal parking" alert in
the middle of a smoking/drinking/holdup run — which reads as the detector being
broken, not as a missing polygon. Refusing is the loud version of the same
information. Draw the area (Run Detection draws it on the clip's first frame; a
live camera uses Live Feeds -> Edge Zones) and the rule runs.
"""
import time

from core.vision import recognition

from .watch_merged import Command as MergedCommand
from .watch_parking import Command as ParkingCommand


class Command(MergedCommand):
    help = (
        "Runs smoking, drinking and theft off one merged-model pass AND "
        "road-edge obstruction (illegal parking) on the same feed, so one clip "
        "can produce alerts of all four types. A no-parking area must be marked "
        "first, on the --camera record or via --zone."
    )

    EXTRA_ENGINES = ("parking",)

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--zone", default=None,
            help="Path to the obstruction polygon JSON written by "
                 "`python manage.py draw_zone`. Omit to use the area stored on "
                 "the --camera record, which is what the dashboard's Edge "
                 "Zones editor writes.",
        )
        parser.add_argument(
            "--inside-pct", type=int, default=None,
            help="Share of the vehicle's ground footprint that must be inside "
                 "the polygon. Omit to use the camera's own value.",
        )
        parser.add_argument(
            "--alert-score", type=float, default=None,
            help="Seconds inside the zone before a vehicle alerts. Omit to use "
                 "the camera's own 'for N minutes' value.",
        )
        parser.add_argument(
            "--moving-weight", type=float, default=0.0,
            help="Score rate while the vehicle is still moving, as a fraction "
                 "of the stopped rate. Default 0: only stopped time counts.",
        )
        parser.add_argument(
            "--parking-confidence", type=float, default=None,
            help="Override the SystemSettings parking confidence, as 0-1. The "
                 "three merged engines keep their own configured floors - this "
                 "is a separate model and a separate threshold.",
        )

    # ---- the fourth engine --------------------------------------------------

    def _setup_extra(self, options):
        """Gives a reused watch_parking Command the state its obstruction
        methods expect, pointed at OUR shared camera and evidence dir - the
        same wiring _setup_engine does for the other three. Detection and the
        rule layer both stay watch_parking's; only the loop driving them is
        ours."""
        cmd = ParkingCommand()
        cmd.stdout = self.stdout
        cmd.style = self.style
        cmd.camera = self.camera
        cmd.violations_dir = self.violations_dir
        cmd.dry_run = self.dry_run
        cmd.far = self.far
        cmd.tiles = self.tiles
        cmd.conf_override = options["parking_confidence"]
        cmd.dwell_override = None
        cmd.tracker_name = f"{options['tracker']}.yaml"
        cmd.debug_pub = self.debug_pub      # one live-view publisher for all four
        cmd._area_sent = False
        # watch_merged runs no frame enhancement for its own three engines, so
        # parking sees the same pixels they do rather than a separately
        # brightened copy of the frame they were judged on.
        cmd.preprocess = False
        cmd.sharpen = False
        cmd.parking_type = self._vtype("parking", "Illegal Parking", "#ef4444", "car")
        # The 30s annotated evidence clip, plus the raw-clip plumbing
        # _create_alert reads. _extra_source fills the last three in once the
        # source is known; set here so a failure before that can't AttributeError.
        cmd.clip = recognition.ClipRecorder(seconds=30, label=self.camera.code)
        cmd._source_path = None
        cmd._video_pos_sec = None
        cmd._raw_buffer = None
        # The polygon and its two thresholds, resolved exactly as watch_parking
        # resolves them: the camera record first, then a --zone file. Returns
        # None and prints why when there is no area to judge against, which
        # refuses the whole run — see this command's module docstring.
        zone, where = cmd._load_zone(options)
        if zone is None:
            return ("No no-parking area, so there is nothing to judge a vehicle "
                    "against. Draw one first (Live Feeds -> Edge Zones, or "
                    "`python manage.py draw_zone`), or use `watch_merged` for "
                    "the three person-based violations without parking.")
        cmd.zone = zone
        self.stdout.write(self.style.SUCCESS(
            f"Obstruction zone from {where} "
            f"({zone.enter_fraction * 100:.0f}% of the vehicle inside, "
            f"{zone.alert_score:.0f}s to alert)."))

        self.parking = cmd
        return None

    def _extra_source(self, source, is_live):
        """A file source is seekable, so raw evidence clips are cut straight
        from it; a live source gets a rolling raw-frame buffer instead. The
        same split watch_parking._run_stream makes, and the merged engines."""
        self.parking._source_path = None if is_live else source
        self.parking._raw_buffer = (recognition.RawFrameRecorder()
                                    if is_live else None)

    def _extra_frame(self, name, frame, now, cfg, debug):
        """One obstruction pass, run by watch_parking's own _run_zone."""
        cmd = self.parking
        # Frames reach this hook before anything has drawn on them (see
        # watch_merged._process_frame), which is what the raw buffer and the
        # live view both want - clean pixels, not the annotated ones.
        if cmd._raw_buffer is not None:
            cmd._raw_buffer.add(frame, time.time())
        if cmd._source_path is not None:
            cmd._video_pos_sec = now
        if cmd.debug_pub is not None and not cmd._area_sent:
            h, w = frame.shape[:2]
            cmd.debug_pub.set_area([(x * w, y * h) for x, y in cmd.zone.points_norm])
            cmd._area_sent = True

        conf = cmd.conf_override or (cfg.parking_confidence / 100)
        cmd._run_zone(frame, now, conf)
        cmd.clip.add(frame, now)
