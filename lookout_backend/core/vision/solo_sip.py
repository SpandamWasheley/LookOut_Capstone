"""Drinking's solo Possible rule: a person alone earns Possible only with a held Bottle AND a held sip.

Pure bookkeeping, no Django, no models: the watcher feeds it one sample per processed frame.

  * Bottle held: a Bottle detection of confidence >= BOTTLE_MIN_CONF on the same person, CONTINUOUSLY for
    BOTTLE_MIN_RUN_SECONDS (two samples further apart than RUN_GAP_SECONDS are not one run).
  * Sip: a confident Bottle inside the mouth zone, continuously for SIP_MIN_HOLD_SECONDS, OR (SIP_COUNT_ENABLED)
    SIPS_NEEDED separate holds of at least SIP_MIN_EACH_SECONDS inside SIP_WINDOW_SECONDS (a person who sips in short
    bursts; one accidental hold still cannot qualify).

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

SOLO_GATE_HANDOVER = True          # False = every new track ID starts its gate from zero (the old behaviour)
SIP_COUNT_ENABLED = True           # False = the sip must be ONE continuous SIP_MIN_HOLD_SECONDS hold (the old rule)
SIP_MIN_EACH_SECONDS = 0.5         # a shorter hold is not counted as a sip
SIPS_NEEDED = 2                    # ...but this many separate sips (a gap over RUN_GAP_SECONDS between them) also qualify
SIP_WINDOW_SECONDS = 30.0          # ...when they all fall inside this window

HANDOVER_MAX_GAP_S = 4.0           # the old ID must have been last seen at most this long ago
HANDOVER_MIN_GAP_S = 0.25          # ...and at least this long ago, so a track still being tracked is never robbed
                                   # a new track matches when its centre lies inside the old track's last box

CAP_TEXT = "capped: solo Possible needs a Bottle held 3 s and a 1 s sip or 2 separate sips"


class _Run:
    """The current continuous run of a condition, plus the longest one seen."""

    def __init__(self):
        self.start = None
        self.last = None
        self.best = 0.0
        self.done = []                  # (end, length) of every finished run

    def current(self, now):
        """Length of the run going on at `now` (0.0 when the last sample is more than RUN_GAP_SECONDS old)."""
        if self.start is None or now - self.last > RUN_GAP_SECONDS:
            return 0.0
        return self.last - self.start

    def feed(self, now, on):
        if not on:
            return
        if self.start is None or now - self.last > RUN_GAP_SECONDS:
            if self.start is not None:
                self.done.append((self.last, self.last - self.start))
                del self.done[:-50]
            self.start = now
        self.last = now
        self.best = max(self.best, now - self.start)

    def count_since(self, now, window, min_len):
        """Separate runs of at least `min_len` seconds that ended within the last `window` seconds."""
        runs = list(self.done)
        if self.start is not None:
            runs.append((self.last, self.last - self.start))
        return sum(1 for end, length in runs if length >= min_len and now - end <= window)


class SoloSipGate:
    def __init__(self):
        self.bottle = _Run()
        self.sip = _Run()
        self.sips_latched = False

    def update(self, now, bottle_conf, in_mouth_zone):
        """`bottle_conf` is the best Bottle confidence on this person this frame (None = no Bottle);
        `in_mouth_zone` says a Bottle of at least BOTTLE_MIN_CONF sits in the mouth zone."""
        confident = bottle_conf is not None and bottle_conf >= BOTTLE_MIN_CONF
        self.bottle.feed(now, confident)
        self.sip.feed(now, confident and bool(in_mouth_zone))
        if SIP_COUNT_ENABLED and not self.sips_latched and self.sip_count(now) >= SIPS_NEEDED:
            self.sips_latched = True

    def sip_count(self, now):
        """Separate sips of at least SIP_MIN_EACH_SECONDS inside the last SIP_WINDOW_SECONDS."""
        return self.sip.count_since(now, SIP_WINDOW_SECONDS, SIP_MIN_EACH_SECONDS)

    @property
    def bottle_ok(self):
        return self.bottle.best >= BOTTLE_MIN_RUN_SECONDS

    @property
    def sip_ok(self):
        return self.sip.best >= SIP_MIN_HOLD_SECONDS or (SIP_COUNT_ENABLED and self.sips_latched)

    @property
    def satisfied(self):
        return self.bottle_ok and self.sip_ok

    def summary(self, now=None):
        """What the testing view and the alert's score breakdown print: the run going on now and the best one so far."""
        cur = lambda run: 0.0 if now is None else run.current(now)
        return {"bottle_now_s": round(cur(self.bottle), 1), "sip_now_s": round(cur(self.sip), 1),
                "bottle_held_s": round(self.bottle.best, 1), "sip_s": round(self.sip.best, 1),
                "sip_count": 0 if now is None else self.sip_count(now), "sips_needed": SIPS_NEEDED,
                "bottle_ok": self.bottle_ok, "sip_ok": self.sip_ok,
                "bottle_needed_s": BOTTLE_MIN_RUN_SECONDS, "sip_needed_s": SIP_MIN_HOLD_SECONDS}


class GateHandover:
    """Lets a person who is given a new track ID keep the gate their old ID had built up.

    A new track adopts a gate only when the old track has vanished (not seen for HANDOVER_MIN_GAP_S, so a live
    track is never robbed), was last seen no longer than HANDOVER_MAX_GAP_S ago, and the new box's centre lies
    inside the old track's last box. Each old gate is adopted at most once. With stable IDs `adopt` never finds anything."""

    def __init__(self):
        self._seen = {}                 # track id -> [box, last_seen, gate]

    def note(self, track_id, box, now, gate):
        self._seen[track_id] = [tuple(box), now, gate]
        for tid in [t for t, e in self._seen.items() if now - e[1] > HANDOVER_MAX_GAP_S]:
            del self._seen[tid]

    def adopt(self, track_id, box, now):
        """The gate to carry over for a track that has none yet, or None."""
        if not SOLO_GATE_HANDOVER:
            return None
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        best, best_d = None, None
        for tid, (old_box, seen, _gate) in self._seen.items():
            age = now - seen
            if tid == track_id or age < HANDOVER_MIN_GAP_S or age > HANDOVER_MAX_GAP_S:
                continue
            if not (old_box[0] <= cx <= old_box[2] and old_box[1] <= cy <= old_box[3]):
                continue
            d = abs(cx - (old_box[0] + old_box[2]) / 2) + abs(cy - (old_box[1] + old_box[3]) / 2)
            if best is None or d < best_d:
                best, best_d = tid, d
        if best is None:
            return None
        return self._seen.pop(best)[2]


def indicators(summary):
    """Testing-view rows: 'bottle held 1.2 s (best 4.8 s)', 'sip 0.0 s (best 2.7 s)' (no points: gates, not weights),
    plus 'sips 1/2' while separate short sips are being counted."""
    rows = [{"name": "bottle held %.1f s (best %.1f s)" % (summary.get("bottle_now_s", 0.0), summary["bottle_held_s"]), "points": None},
            {"name": "sip %.1f s (best %.1f s)" % (summary.get("sip_now_s", 0.0), summary["sip_s"]), "points": None}]
    if summary.get("sip_count") and not summary.get("sip_ok"):
        rows.append({"name": "sips %d/%d" % (summary["sip_count"], summary.get("sips_needed", SIPS_NEEDED)), "points": None})
    return rows
