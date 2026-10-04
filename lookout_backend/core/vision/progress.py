"""A terminal spinner for the long, silent stretches before a detector runs.

Starting a watcher costs about ten seconds before it looks at a single pixel:
roughly four to import torch, four to read the YOLO weights off disk, and one
more to warm the model up. None of that is avoidable — it is what starting a
Python process with PyTorch costs — but printing nothing for ten seconds makes
a working command look hung, which is how it gets killed and re-run.

TTY-AWARE, which is the part that matters here. A detector launched from the
dashboard has its stdout piped to a log file (see DetectionJobViewSet.run), and
an animation written there would be thousands of carriage returns in a file
somebody later greps for an error. So when stdout is not a terminal this
degrades to one plain line at the start and one at the end — same information,
no animation.

ASCII frames only: this console has already been seen to mangle an em-dash into
a replacement character, and a spinner is not worth a UnicodeEncodeError.
"""
import itertools
import sys
import threading
import time

FRAMES = ("|", "/", "-", "\\")
INTERVAL = 0.1


class Spinner:
    """Context manager: animates `label` until the block exits.

        with Spinner("Loading detector"):
            model = load_yolo()

    Prints "Loading detector... done in 4.4s" on the way out. An exception
    inside the block stops the animation and leaves the line in place rather
    than swallowing it, so a traceback is not printed over a half-drawn frame.
    """

    def __init__(self, label, stream=None, interval=INTERVAL, tty_only=False):
        self.label = label
        # Default resolved at __enter__, not here: Django swaps sys.stdout for
        # its own wrapper while a command runs.
        self._stream = stream
        self.interval = interval
        # tty_only: say nothing at all when piped, instead of degrading to two
        # plain lines. For a spinner that runs at IMPORT time — the test suite
        # imports these modules too, and "Loading PyTorch... done in 4.3s"
        # across 286 tests is noise, not progress.
        self.tty_only = tty_only
        self._stop = threading.Event()
        self._thread = None
        self._started = None
        self._animated = False

    def _write(self, text):
        try:
            self.stream.write(text)
            self.stream.flush()
        except (ValueError, OSError):
            # The stream closed under us (a killed subprocess). Losing the
            # animation is not a reason to take the detector down with it.
            self._stop.set()

    def _spin(self):
        for frame in itertools.cycle(FRAMES):
            if self._stop.is_set():
                return
            elapsed = time.monotonic() - self._started
            self._write(f"\r  {frame} {self.label}... {elapsed:.0f}s")
            if self._stop.wait(self.interval):
                return

    def __enter__(self):
        self.stream = self._stream or sys.stdout
        self._started = time.monotonic()
        self._animated = bool(getattr(self.stream, "isatty", lambda: False)())
        if self._animated:
            self._thread = threading.Thread(target=self._spin, daemon=True,
                                            name="spinner")
            self._thread.start()
        elif not self.tty_only:
            self._write(f"  {self.label}...\n")
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        took = time.monotonic() - self._started
        if exc_type is not None:
            # Clear the partial line so a traceback starts clean.
            self._write("\r" + " " * (len(self.label) + 24) + "\r")
            return False
        if self._animated:
            self._write(f"\r  {self.label}... done in {took:.1f}s"
                        + " " * 8 + "\n")
        elif not self.tty_only:
            self._write(f"  {self.label}... done in {took:.1f}s\n")
        return False
