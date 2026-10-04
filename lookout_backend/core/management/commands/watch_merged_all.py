"""watch_merged plus road-edge obstruction: all four violations from one feed.

watch_merged already runs smoking, drinking and theft from a single pass of the
merged Bottle/Cigarette/knife model. This adds the fourth, illegal parking, as
an EXTRA ENGINE (see watch_merged.Command.EXTRA_ENGINES) — the merged model has
no vehicle class, so parking brings its own detector and its own rule layer.

Nothing about the obstruction rule is reimplemented here. watch_parking's own
Command is instantiated and its _load_edges / _build_monitors / _run_obstruction
/ _create_alert are called directly, exactly as its own handle() would, so the
share-of-footprint threshold, the hysteresis, the dwell timer, the
position-keyed cooldown and the pedestrian-detour evidence are the same code
running here as on a live parking camera. The same holds for the other three:
their rule layers come from watch_smoking/watch_drinking/watch_thief unchanged.

OBSTRUCTION ONLY — THE AREA MUST BE DRAWN FIRST
-----------------------------------------------
watch_parking has two rules and picks between them: with an area marked it
judges "how much of the vehicle is in it, for how long"; with none it falls back
to plain dwell, "parked in frame at all for 60 seconds". This command does NOT
inherit that fallback. With nothing drawn it refuses to start.

That is deliberate. The fallback is reasonable in watch_parking, where the
operator chose the parking detector and knows which of its two rules they set
up. Here parking is one of four things running at once, and a silent fallback
would mean every jeepney that stops at a corner for a minute raises an "illegal
parking" alert in the middle of a smoking/drinking/holdup run — which reads as
the detector being broken, not as a missing line. Refusing is the loud version
of the same information. Draw the area (Run Detection draws it on the clip's
first frame; a live camera uses Live Feeds -> Edge Zones) and the obstruction
rule is what runs.
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
        "first, on the --camera record or via --edges; this command runs the "
        "obstruction rule only, never watch_parking's plain dwell fallback."
    )

    EXTRA_ENGINES = ("parking",)

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--edges",
            default=None,
            help="Path to a JSON file marking the no-parking area, in the "
                 "coordinates of the frame as processed (no rescaling is "
                 "applied to a file given here). Omit to use the area stored "
                 "on the --camera record, which IS rescaled to whatever the "
                 "source decodes at. Same two shapes watch_parking accepts, "
                 "mixable in one file. ZONE - a closed polygon around the road "
                 'itself: {"road": {"type": "zone", "points": [[x,y],...]}}. '
                 "EDGE - an open kerb line plus the side the footpath is on: "
                 '{"left": {"points": [[x,y],[x,y]], "side": 1}}.',
        )
        parser.add_argument(
            "--obstruction-pct", type=int, default=None,
            help="Share of the vehicle's footprint that must be inside the "
                 "marked area. Omit to use the camera record's value (50).",
        )
        parser.add_argument(
            "--obstruction-minutes", type=float, default=None,
            help="Minutes it must be held before it counts. Omit to use the "
                 "camera record's value (5).",
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
        # Plain-dwell state. Never used (see the module docstring - this
        # command refuses to run that rule), but _create_alert and the class's
        # own methods are shared with that path, so leaving the attributes
        # absent would turn a future wiring mistake into an AttributeError
        # several minutes into a run instead of a wrong-but-visible alert.
        cmd._dwell_tracks = {}
        cmd._dwell_next_id = 0

        # Resolves --edges / camera.edges and the pct+minutes thresholds, and
        # returns whether that amounts to an area at all.
        if not cmd._load_edges(options):
            where = ("the --edges file" if options.get("edges")
                     else f"camera {self.camera.code}")
            return (
                f"No no-parking area is marked on {where}, so there is nothing "
                "to judge a vehicle against. This command runs the obstruction "
                "rule only and will not fall back to watch_parking's plain "
                "dwell rule (see this command's module docstring for why) - "
                "draw the area first, then start the run. Run Detection draws "
                "it on the clip's first frame; a live camera uses Live Feeds "
                "-> Edge Zones. Use `watch_merged` for the three person-based "
                "violations without parking."
            )

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
        """One obstruction pass: detect vehicles, judge each against every
        marked area, alert on the ones that cross the threshold and hold it."""
        cmd = self.parking
        # Frames reach this hook before anything has drawn on them (see
        # watch_merged._process_frame), which is what the raw buffer wants -
        # clean pixels, not the annotated ones.
        if cmd._raw_buffer is not None:
            cmd._raw_buffer.add(frame, time.time())
        if cmd._source_path is not None:
            cmd._video_pos_sec = now

        conf = cmd.conf_override or (cfg.parking_confidence / 100)
        vehicles = cmd._detect(frame, conf)

        # Built on the first real frame, not at setup: the area was drawn
        # against some other resolution (a staged still, a different stream
        # profile) and _build_monitors rescales it to what this source
        # actually decodes at. Judging raw pixel coordinates across a
        # resolution change would silently use the wrong line.
        if cmd.monitors is None:
            cmd._build_monitors(frame.shape)

        cmd._run_obstruction(frame, vehicles, now, cfg.alert_cooldown, debug)
        cmd.clip.add(frame, now)
