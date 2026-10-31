"""Drinking's solo Possible rule: a person alone earns Possible only with a held Bottle AND a held sip.

Pure bookkeeping, no Django, no models: the watcher feeds it one sample per processed frame.

  * Bottle held: a Bottle detection of confidence >= BOTTLE_MIN_CONF on the same person, CONTINUOUSLY for
    BOTTLE_MIN_RUN_SECONDS (two samples further apart than RUN_GAP_SECONDS are not one run).
  * Sip: a confident Bottle inside the mouth zone, continuously for SIP_MIN_HOLD_SECONDS.

A vape draw lifts a mis-read "Bottle" to the face for a second or so, so a mouth-zone hit alone is not enough. A Bottle
box shorter than tracking.VAPE_BOTTLE_MAX_FRAC of the person's height is a vape, not a drink: the watcher never feeds it
here, so it counts as neither "bottle held" nor a sip. Both
conditions latch once met: after that the incident's level only falls through Score's own drop-out rule. Group
(inuman) events do not use this gate.
"""
BOTTLE_MIN_CONF = 0.5
BOTTLE_MIN_RUN_SECONDS = 3.0
SIP_MIN_HOLD_SECONDS = 1.0
RUN_GAP_SECONDS = 1.0

CAP_TEXT = "capped: solo Possible needs a Bottle held 3 s and a 1 s sip"


class _Run:
    """The current continuous run of a condition, plus the longest one seen."""

    def __init__(self):
        self.start = None
        self.last = None
        self.best = 0.0

    def current(self, now):
        """Length of the run going on at `now` (0.0 when the last sample is more than RUN_GAP_SECONDS old)."""
        if self.start is None or now - self.last > RUN_GAP_SECONDS:
            return 0.0
        return self.last - self.start

    def feed(self, now, on):
        if not on:
            return
        if self.start is None or now - self.last > RUN_GAP_SECONDS:
            self.start = now
        self.last = now
        self.best = max(self.best, now - self.start)


class SoloSipGate:
    def __init__(self):
        self.bottle = _Run()
        self.sip = _Run()

    def update(self, now, bottle_conf, in_mouth_zone):
        """`bottle_conf` is the best Bottle confidence on this person this frame (None = no Bottle);
        `in_mouth_zone` says a Bottle of at least BOTTLE_MIN_CONF sits in the mouth zone."""
        confident = bottle_conf is not None and bottle_conf >= BOTTLE_MIN_CONF
        self.bottle.feed(now, confident)
        self.sip.feed(now, confident and bool(in_mouth_zone))

    @property
    def bottle_ok(self):
        return self.bottle.best >= BOTTLE_MIN_RUN_SECONDS

    @property
    def sip_ok(self):
        return self.sip.best >= SIP_MIN_HOLD_SECONDS

    @property
    def satisfied(self):
        return self.bottle_ok and self.sip_ok

    def summary(self, now=None):
        """What the testing view and the alert's score breakdown print: the run going on now and the best one so far."""
        cur = lambda run: 0.0 if now is None else run.current(now)
        return {"bottle_now_s": round(cur(self.bottle), 1), "sip_now_s": round(cur(self.sip), 1),
                "bottle_held_s": round(self.bottle.best, 1), "sip_s": round(self.sip.best, 1),
                "bottle_ok": self.bottle_ok, "sip_ok": self.sip_ok,
                "bottle_needed_s": BOTTLE_MIN_RUN_SECONDS, "sip_needed_s": SIP_MIN_HOLD_SECONDS}


def indicators(summary):
    """Testing-view rows: 'bottle held 1.2 s (best 4.8 s)', 'sip 0.0 s (best 2.7 s)' (no points: gates, not weights)."""
    return [{"name": "bottle held %.1f s (best %.1f s)" % (summary.get("bottle_now_s", 0.0), summary["bottle_held_s"]), "points": None},
            {"name": "sip %.1f s (best %.1f s)" % (summary.get("sip_now_s", 0.0), summary["sip_s"]), "points": None}]
