"""The adjustable indicator timings / conditions (scoring spec v6), their spec defaults, and
how the detectors read them.

Only TIMINGS and CONDITIONS are adjustable. The points per indicator and the 55 / 75 cutoffs
are fixed by the spec and are deliberately not settings: changing them would change what
"Possible" and "Likely" mean, and the thresholds are what the panel defends.

Each entry is a SystemSettings field -> its spec default. `GROUPS` says which violation
a field belongs to, so "Reset to spec defaults" can be done per violation.
"""
import datetime

from core.vision import momentum

SPEC_DEFAULTS = {
    "object_confirm_seconds": 2.0,          # all: object seen about 2 s before Monitoring
    "drinking_group_duration": 600,         # drinking: stayed 10+ minutes (seconds)
    "drinking_min_group": 2,                # drinking: group of 2+
    "drinking_start": datetime.time(16, 0),  # drinking: evening band 16:00-24:00
    "drinking_end": datetime.time(0, 0),
    "smoking_puff_count": 3,                # smoking: 3+ puffs ...
    "smoking_puff_window_minutes": 5.0,     # ... within 5 minutes
    "holdup_loiter_seconds": 20,            # holdup: someone loitering first
    "holdup_near_person_heights": 1.75,     # holdup: "nearby person" distance, in holder heights
}

GROUPS = {
    "all": ["object_confirm_seconds"],
    "drinking": ["drinking_group_duration", "drinking_min_group", "drinking_start", "drinking_end"],
    "smoking": ["smoking_puff_count", "smoking_puff_window_minutes"],
    "holdup": ["holdup_loiter_seconds", "holdup_near_person_heights"],
}

# (min, max) the API accepts. Wide enough to experiment, narrow enough to stay meaningful.
LIMITS = {
    "object_confirm_seconds": (0.5, 10.0),
    "drinking_group_duration": (60, 7200),
    "drinking_min_group": (2, 20),
    "smoking_puff_count": (2, 10),
    "smoking_puff_window_minutes": (1.0, 30.0),
    "holdup_loiter_seconds": (5, 300),
    "holdup_near_person_heights": (0.5, 4.0),
}


def defaults_for(violation):
    """{field: spec default} for one violation group ('all', 'drinking', 'smoking', 'holdup')."""
    return {f: SPEC_DEFAULTS[f] for f in GROUPS[violation]}


def snapshot(cfg):
    """The active adjustable settings as plain JSON, logged with every alert so a later
    reader knows what timings produced that status."""
    out = {}
    for field in SPEC_DEFAULTS:
        value = getattr(cfg, field, SPEC_DEFAULTS[field])
        out[field] = value.strftime("%H:%M") if isinstance(value, datetime.time) else value
    out["vlm_model"] = getattr(cfg, "vlm_model", "")
    out["vlm_model_holdup"] = getattr(cfg, "vlm_model_holdup", "")
    return out


def momentum_config(seconds):
    """Object confirmation time -> momentum thresholds.

    With the spec defaults (ON 1.5, 2 s) the time to turn ON is roughly proportional to the
    ON threshold at a fixed frame rate and confidence, so ON scales with the requested
    time. OFF stays 0.4 and MAX stays at least twice ON so the cue can hold through dips.
    """
    base = momentum.MomentumConfig()
    seconds = max(float(seconds or 2.0), 0.25)
    on = round(base.on * seconds / 2.0, 3)
    return momentum.MomentumConfig(decay=base.decay, on=on, off=base.off, max=max(base.max, on * 2.0))
