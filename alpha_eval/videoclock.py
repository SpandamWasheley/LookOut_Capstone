"""Video clock: replaces the wall clock inside LookOut's detection modules, in the worker process only.

LookOut already drives its rule layer with the frame's video position (CAP_PROP_POS_MSEC). This module
makes the *remaining* wall-clock reads (settings refresh cadence, fallbacks) read the same video time, and
turns time.sleep into a no-op, so the result cannot depend on how fast the computer processes frames.
"""
import time as _real_time


class VideoClock:
    def __init__(self):
        self.now = 0.0

    def set(self, seconds):
        self.now = float(seconds)

    # time-module surface used by LookOut's detection modules
    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def perf_counter(self):
        return self.now

    def sleep(self, seconds):          # never wait on the wall clock
        return None

    def __getattr__(self, name):       # strftime, localtime, ... still work
        return getattr(_real_time, name)


class NoSleepTime:
    """Real clock with sleep disabled; used for ai_checker (its monotonic() only measures call latency)."""
    def sleep(self, seconds):
        return None

    def __getattr__(self, name):
        return getattr(_real_time, name)


CLOCK = VideoClock()

VIDEO_CLOCK_MODULES = [
    "core.management.commands.watch_merged",
    "core.management.commands.watch_merged_all",
    "core.management.commands.watch_smoking",
    "core.management.commands.watch_drinking",
    "core.management.commands.watch_thief",
    "core.management.commands.watch_parking",
]
NO_SLEEP_MODULES = ["core.vision.ai_checker"]


def install():
    """Patch the module-level `time` reference of each module. Returns the list of what was patched."""
    import importlib
    patched = []
    for name in VIDEO_CLOCK_MODULES:
        mod = importlib.import_module(name)
        if hasattr(mod, "time"):
            mod.time = CLOCK
            patched.append(f"{name}.time -> VideoClock")
    for name in NO_SLEEP_MODULES:
        mod = importlib.import_module(name)
        if hasattr(mod, "time"):
            mod.time = NoSleepTime()
            patched.append(f"{name}.time -> NoSleepTime")
    return patched


class ClockedCapture:
    """cv2.VideoCapture wrapper: every successful read() moves CLOCK to that frame's video position."""

    def __init__(self, cap, clock=CLOCK):
        self._cap, self._clock = cap, clock
        self.last_pos_sec = 0.0

    def read(self):
        import cv2
        ok, frame = self._cap.read()
        if ok:
            self.last_pos_sec = self._cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            self._clock.set(self.last_pos_sec)
        return ok, frame

    def __getattr__(self, name):
        return getattr(self._cap, name)
