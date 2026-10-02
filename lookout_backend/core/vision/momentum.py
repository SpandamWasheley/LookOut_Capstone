"""Object cue accumulation ("momentum") -- docs/specs/LookOut_Object_Cue_Momentum_Spec.md.

A YOLO detection of a small object (bottle, cigarette, knife) flickers from
frame to frame even when the object never left the scene. A hard "detected N
seconds straight" rule resets on every dip. Instead each (track, class) slot
keeps a running score that rises when the object is seen and DECAYS, rather
than resets, when it is not:

    momentum = momentum * DECAY + confidence_this_frame      (capped at MAX)
    cue turns ON  when momentum >= ON
    cue turns OFF when momentum <  OFF        (OFF < ON: hysteresis)

This replaces the old 5 s / 40% vote and the dwell timer as the *object cue*.

Timing note: the update runs once per PROCESSED frame. A video file is processed
frame by frame (10 fps for the test clips) while a live stream is processed at
about 4-6 fps, so DECAY is per processed frame, not per second. The effective
memory is roughly log(0.35) / log(DECAY) frames (about 10 frames at 0.90).

State is in memory only (high-frequency and ephemeral) and a slot is dropped as
soon as its track is lost; momentum never carries across track ids.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class MomentumConfig:
    decay: float = 0.90
    on: float = 1.5
    off: float = 0.4
    max: float = 3.0


def config_from_env(name="LOOKOUT_MOMENTUM", fallback=None):
    """MomentumConfig from "decay,on,off,max" in an environment variable (for tuning
    runs); the spec defaults otherwise."""
    import os
    raw = os.environ.get(name, "").strip()
    base = fallback or MomentumConfig()
    if not raw:
        return base
    try:
        decay, on, off, mx = (float(x) for x in raw.split(","))
        return MomentumConfig(decay, on, off, mx)
    except ValueError:
        return base


DEFAULT_CONFIG = config_from_env()


class Slot:
    __slots__ = ("momentum", "cue_on", "last_conf", "peak")

    def __init__(self):
        self.momentum = 0.0
        self.cue_on = False
        self.last_conf = 0.0
        self.peak = 0.0


def update_momentum(slot, confidence, cfg=DEFAULT_CONFIG):
    """One step of the spec's algorithm. `confidence` is 0.0 when the class was
    not detected this frame. Returns the slot."""
    slot.momentum = min(slot.momentum * cfg.decay + float(confidence), cfg.max)
    slot.last_conf = float(confidence)
    slot.peak = max(slot.peak, slot.momentum)
    if not slot.cue_on and slot.momentum >= cfg.on:
        slot.cue_on = True
    elif slot.cue_on and slot.momentum < cfg.off:
        slot.cue_on = False
    return slot


class MomentumBook:
    """All the slots of one detector, keyed by (track_id, class_name)."""

    def __init__(self, cfg=DEFAULT_CONFIG, per_class=None):
        self.cfg = cfg
        self.per_class = dict(per_class or {})     # class -> MomentumConfig override
        self.slots = {}

    def config_for(self, cls):
        return self.per_class.get(cls, self.cfg)

    def step(self, track_id, confidences):
        """Advance every slot of `track_id` by one processed frame.

        `confidences` is {class: best confidence this frame}; a class that is
        absent decays with confidence 0. A slot is created the first time a
        class has confidence > 0. Returns {class: Slot} for the track's slots.
        """
        out = {}
        for cls, conf in confidences.items():
            if conf > 0 and (track_id, cls) not in self.slots:
                self.slots[(track_id, cls)] = Slot()
        for (tid, cls), slot in self.slots.items():
            if tid == track_id:
                update_momentum(slot, confidences.get(cls, 0.0), self.config_for(cls))
                out[cls] = slot
        return out

    def get(self, track_id, cls):
        return self.slots.get((track_id, cls))

    def on_classes(self, track_id):
        """Classes whose cue is ON for this track right now."""
        return [cls for (tid, cls), s in self.slots.items() if tid == track_id and s.cue_on]

    def drop_missing(self, live_track_ids):
        """Destroy slots whose track is gone (no leak, no carry-over). Returns the count."""
        live = set(live_track_ids)
        dead = [k for k in self.slots if k[0] not in live]
        for k in dead:
            del self.slots[k]
        return len(dead)

    def drop_idle(self):
        """Destroy slots that have fully decayed and are OFF."""
        dead = [k for k, s in self.slots.items() if not s.cue_on and s.momentum < 1e-3]
        for k in dead:
            del self.slots[k]
        return len(dead)

    def snapshot(self, track_id, cls):
        s = self.slots.get((track_id, cls))
        if s is None:
            return None
        cfg = self.config_for(cls)
        return {"class": cls, "momentum": round(s.momentum, 3), "cue_on": s.cue_on,
                "peak": round(s.peak, 3), "decay": cfg.decay, "on": cfg.on,
                "off": cfg.off, "max": cfg.max}

    def __len__(self):
        return len(self.slots)
