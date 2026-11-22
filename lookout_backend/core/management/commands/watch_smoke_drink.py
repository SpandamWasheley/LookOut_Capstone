"""Runs the merged model but routes ONLY Cigarette → smoking and Bottle →
drinking, skipping the knife → thief engine entirely.  Useful for evaluating
smoking + drinking together without holdup detection adding noise."""
from .watch_merged import Command as MergedCommand, ROUTE_ENGINE


class Command(MergedCommand):
    help = (
        "Runs the merged model once per frame and routes Cigarette/Bottle to "
        "smoking/drinking only — knife detections are dropped, no holdup "
        "alerts are produced. Use --source for an RTSP/CCTV URL or video file."
    )

    ROUTE_ENGINE = {k: v for k, v in ROUTE_ENGINE.items() if v != "thief"}
