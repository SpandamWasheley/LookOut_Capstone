"""Run a detector over part of a clip instead of all of it.

A full pass costs roughly 0.2-0.5s per frame on CPU, so a five-minute clip is
twenty-plus minutes of waiting to find out whether a zone or a threshold is
right. Almost all of that is footage nobody is testing. Trimming to the thirty
seconds that matter turns the same question around in two minutes.

BY SEEKING, NOT BY CUTTING. The alternative was to cut a real file with ffmpeg
before launching the detector, which is simpler to wire but writes a second
copy of the clip — and the machine this runs on has been at 98% disk. Seeking
costs nothing, needs no re-encode, and starts instantly.

TIME IS FOOTAGE TIME. `start`/`end` are seconds into the video, the same clock
the watchers already use for dwell and duration (CAP_PROP_POS_MSEC), so a trim
lines up exactly with what a person scrubbing the clip in a browser selected.
Ignored for a live source, which has no position to seek to.
"""


def add_cli_flags(parser):
    """Registers --start/--end on a watch_<x> command.

    Declared once here rather than copy-pasted per command, same as
    preprocess.add_cli_flags.
    """
    parser.add_argument(
        "--start", type=float, default=0.0,
        help="Begin at this many seconds into the clip (default 0). Seeks "
             "rather than decoding and discarding, so a late start costs "
             "nothing. Ignored for a live source.",
    )
    parser.add_argument(
        "--end", type=float, default=0.0,
        help="Stop at this many seconds into the clip (default 0 = run to the "
             "end). Reported as a normal end-of-clip finish, so a trimmed run "
             "and a whole one look the same to the job watcher.",
    )


class Trim:
    """The chosen span, and the two decisions it drives.

    Deliberately tiny and stateless beyond its two numbers: every watcher has
    its own capture loop, and the only things they need to agree on are "where
    do I start" and "have I gone past the end".
    """

    def __init__(self, start=0.0, end=0.0):
        self.start = max(float(start or 0.0), 0.0)
        # 0 (or anything at/below start) means "to the end of the clip".
        end = float(end or 0.0)
        self.end = end if end > self.start else None

    def __bool__(self):
        return self.start > 0 or self.end is not None

    def describe(self):
        if not self:
            return "whole clip"
        end = f"{self.end:.0f}s" if self.end is not None else "end"
        return f"{self.start:.0f}s to {end}"

    def seek(self, cap, is_live):
        """Jump the capture to the start. No-op for a live source or 0."""
        if is_live or self.start <= 0 or cap is None:
            return False
        import cv2

        return bool(cap.set(cv2.CAP_PROP_POS_MSEC, self.start * 1000.0))

    def past_end(self, pos_seconds, is_live):
        """True once the clip position has run past the chosen end.

        `pos_seconds` is the watcher's own content clock, so this stays correct
        at any processing rate. A live source is never past its end.
        """
        if is_live or self.end is None or pos_seconds is None:
            return False
        return pos_seconds >= self.end
