"""The "what time of day is it?" source for time-dependent scoring.

Two indicators depend on the clock: the holdup time-block multiplier and the
drinking evening band (16:00-24:00). Live detection uses the wall clock. For an
uploaded or test clip the wall clock is meaningless (a clip filmed at 19:30 and
run at 02:00 would be scored as night), so a footage start time can be given:

    --clock "2026-08-18 19:30"

and the clock then reads `start + position in the video`. A clip run without
--clock falls back to the wall clock, exactly as before. The override applies
to FILE sources only; a live stream always uses the wall clock.
"""
import datetime

FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")


def parse_clock(text):
    """Parse a --clock value; None for an empty value. Raises ValueError if malformed."""
    if not text:
        return None
    for fmt in FORMATS:
        try:
            return datetime.datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised --clock {text!r}; use e.g. \"2026-08-18 19:30\".")


def clock_now(start, media_seconds, is_file):
    """The time of day for the frame at `media_seconds` into the source.

    `start`         the footage start time (datetime) or None
    `media_seconds` position in the video file, in seconds
    `is_file`       True for an uploaded / test clip; live streams ignore `start`
    """
    if start is not None and is_file and media_seconds is not None:
        return start + datetime.timedelta(seconds=float(media_seconds))
    return datetime.datetime.now()
