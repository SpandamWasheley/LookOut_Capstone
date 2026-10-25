"""trace_zone — propose the parking no-parking polygon from footage.

The automatic sibling of draw_zone, which asks an operator to click the
carriageway by hand. This watches where traffic actually drives and hulls those
wheel-contact points into a polygon (see core/vision/zone_autodetect.py for why
traffic and not pixels).

    python manage.py trace_zone --source clip.mp4
    python manage.py trace_zone --source clip.mp4 --save      # onto the camera
    python manage.py trace_zone --source clip.mp4 --points    # just the numbers

It always writes a preview image with the proposed shape drawn on it, because
the only honest way to judge a traced zone is to look at it over the frame it
was traced from. --save is opt-in for the same reason: a polygon nobody has
looked at should not become the rule a vehicle is reported under.

The dashboard reaches the same code through CameraViewSet.autodetect_zone; this
command exists for calibration (it can print the whole report and draw the point
cloud, which the endpoint has no place for) and so the feature is usable on a
box with no browser.
"""

import json
from pathlib import Path

import cv2
from django.core.management.base import BaseCommand, CommandError

from core.models import Camera
from core.vision.progress import Spinner

# Same reason as watch_parking: importing recognition pulls in torch, which is
# seconds of silence before handle() is even called.
with Spinner("Loading PyTorch", tty_only=True):
    from core.vision import recognition                       # noqa: E402
    from core.vision import zone_autodetect as autodetect     # noqa: E402

DEFAULT_CAMERA_CODE = "CAM-SMOKE-01"


class Command(BaseCommand):
    help = ("Traces the parking obstruction polygon from where traffic drives "
            "in a clip, writes a preview image, and optionally saves it onto "
            "the camera.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--source", required=True,
            help="Video file to trace from. A clip with traffic passing "
                 "through — a still street gives nothing to trace.")
        parser.add_argument(
            "--camera", default=DEFAULT_CAMERA_CODE,
            help=f"Camera to save onto with --save (default {DEFAULT_CAMERA_CODE}).")
        parser.add_argument(
            "--save", action="store_true",
            help="Write the traced polygon to the camera's edges, replacing "
                 "whatever zone is saved there. Off by default.")
        parser.add_argument(
            "--out", default="",
            help="Where to write the preview image (default: alongside the "
                 "source, as <source>_zone.jpg).")
        parser.add_argument(
            "--cloud", action="store_true",
            help="Also draw the ground points the polygon was traced from — "
                 "the fastest way to see WHY a zone came out the shape it did.")
        parser.add_argument(
            "--points", action="store_true",
            help="Print the polygon as JSON, for piping somewhere else.")
        parser.add_argument(
            "--stride", type=float, default=autodetect.SAMPLE_STRIDE_SECONDS,
            help=f"Seconds between sampled frames (default "
                 f"{autodetect.SAMPLE_STRIDE_SECONDS}). Lower sees more "
                 f"traffic and costs more model passes.")
        parser.add_argument(
            "--max-samples", type=int, default=autodetect.MAX_SAMPLES,
            help=f"Cap on sampled frames (default {autodetect.MAX_SAMPLES}), "
                 f"so an hour of footage costs the same as a minute.")
        parser.add_argument(
            "--conf", type=float, default=autodetect.VEHICLE_CONF,
            help=f"Vehicle detection confidence floor (default "
                 f"{autodetect.VEHICLE_CONF}).")
        parser.add_argument(
            "--imgsz", type=int, default=autodetect.TRACE_IMGSZ,
            help=f"Inference size for the trace (default "
                 f"{autodetect.TRACE_IMGSZ}). Raising it towards the detection "
                 f"run's 1280 costs well over twice the time for a few percent "
                 f"of polygon.")
        parser.add_argument(
            "--pad", type=float, default=autodetect.PAD_FRACTION,
            help=f"How far to grow the polygon past the wheel-contact hull, as "
                 f"a fraction (default {autodetect.PAD_FRACTION}).")

    def handle(self, *args, **options):
        source = Path(options["source"])
        if not source.is_file():
            raise CommandError(f"No such video: {source}")

        camera = None
        if options["save"]:
            # Resolved BEFORE the expensive part: a typo'd camera code should
            # fail in a second, not after two minutes of inference.
            try:
                camera = Camera.objects.get(code=options["camera"])
            except Camera.DoesNotExist:
                raise CommandError(
                    f"No camera with code {options['camera']!r}.") from None

        self.stdout.write(
            f"Tracing from {source.name} — sampling every "
            f"{options['stride']}s, up to {options['max_samples']} frames.")

        def detect(frame):
            return recognition.detect_vehicles(
                frame, conf=options["conf"], imgsz=options["imgsz"])

        points, report, preview = autodetect.trace_clip(
            source, detect,
            stride_seconds=options["stride"],
            max_samples=options["max_samples"],
            pad_fraction=options["pad"],
        )

        self.stdout.write(
            f"  {report['samples']} frame(s) sampled, "
            f"{report['detections']} vehicle detection(s), "
            f"{report['tracks']} track(s), of which "
            f"{report['moving_tracks']} moved — "
            f"{report['ground_points']} ground point(s) to trace from.")

        if points is None:
            # A refusal with a reason, not a stack trace: "nothing was
            # detected" and "everything was parked" call for completely
            # different footage next time.
            self.stdout.write(self.style.WARNING(f"No zone: {report['reason']}"))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Traced a {len(points)}-point zone."))

        # The cloud is what makes a wrong shape diagnosable: a zone that
        # bulges into a forecourt looks like a bug until the points show a
        # vehicle actually drove through it.
        cloud = report["cloud"] if options["cloud"] else ()
        out_path = (Path(options["out"]) if options["out"]
                    else source.with_name(f"{source.stem}_zone.jpg"))
        image = autodetect.overlay(preview, points, cloud)
        if cv2.imwrite(str(out_path), image):
            self.stdout.write(f"Preview written to {out_path}")
        else:
            self.stdout.write(self.style.WARNING(
                f"Could not write a preview to {out_path}."))

        if options["points"]:
            self.stdout.write(json.dumps(
                {"points": [[round(x, 1), round(y, 1)] for x, y in points],
                 "frame_size": [preview.shape[1], preview.shape[0]]}))

        if camera is not None:
            height, width = preview.shape[:2]
            camera.edges = autodetect.to_spec(points)
            camera.edges_width = width
            camera.edges_height = height
            camera.save(update_fields=["edges", "edges_width", "edges_height"])
            self.stdout.write(self.style.SUCCESS(
                f"Saved onto {camera.code} at {width}x{height}. "
                "Check it in Live Feeds → Edge Zones before relying on it."))
        else:
            self.stdout.write(
                "Not saved (pass --save to write it onto the camera).")
