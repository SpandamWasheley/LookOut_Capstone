import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402

import videoclock as vc  # noqa: E402


def test_clock_follows_the_video_not_the_wall_clock():
    clock = vc.VideoClock()
    clock.set(12.5)
    t0 = time.time()
    clock.sleep(5)                               # must not wait
    assert time.time() - t0 < 0.5
    assert clock.time() == clock.monotonic() == clock.perf_counter() == 12.5
    assert isinstance(clock.strftime("%Y"), str)  # everything else still comes from the real time module


def test_clocked_capture_moves_the_clock_on_every_read():
    class FakeCap:
        def __init__(self):
            self.i = 0

        def read(self):
            self.i += 1
            return self.i <= 3, "frame"

        def get(self, prop):
            assert prop == cv2.CAP_PROP_POS_MSEC
            return self.i * 100.0

        def isOpened(self):
            return True

    clock = vc.VideoClock()
    cap = vc.ClockedCapture(FakeCap(), clock)
    seen = []
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        seen.append(clock.time())
    assert seen == [0.1, 0.2, 0.3] and cap.isOpened()


def test_no_sleep_time_keeps_real_clock():
    ns = vc.NoSleepTime()
    t0 = time.time()
    ns.sleep(3)
    assert time.time() - t0 < 0.5 and abs(ns.time() - time.time()) < 1
