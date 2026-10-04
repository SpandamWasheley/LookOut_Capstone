"""The weighted-sum violation-scoring model shared by every detector (spec v6).

The official score is SYSTEM INDICATORS ONLY: the object detector, the pose
model, the tracker and the clock. The AI checker adds no points, applies no
multiplier and never changes a status; it is shown beside the official status
(see ai_status.py).

    score  = min(1.0, SUM(weight_i * cue_i) * PRODUCT(multiplier_j))
    status = Monitoring  the object cue is ON and the score is under 55
             Possible    55 and over   (notified, low priority)
             Likely      75 and over   (full alert)

Points are kept on the 0-1 scale: 40 points = 0.40, Possible = 0.55,
Likely = 0.75, cap = 1.00. The scale is a bounded evidence score, not a
probability.

Rules (spec sections 3-4):
  * No object, no event. The one exception is the puff-only smoking path
    (3+ puffs, no item), which is capped at Possible and tagged.
  * Holdup needs a second person NEAR the knife holder to go above
    Monitoring; a knife alone, or with nobody near it, is Monitoring only.
  * Hysteresis: Possible / Likely are left only when the score falls 5 points
    below the threshold that admitted them. Rising is immediate.

No Django model access here -- pure scoring plumbing, so it is unit-testable
without a database.
"""

import datetime


# --- statuses ----------------------------------------------------------------
# Stored level names. `warning` and `violation` keep their historical stored
# values (existing rows and the officer app use them); the labels are what a
# person reads: Possible and Likely.
VIOLATION = "violation"     # Likely    >= 0.75
WARNING = "warning"         # Possible  >= 0.55
MONITORING = "monitoring"   # object cue ON, score under 0.55 (quiet watchlist)
NONE = "none"               # not shown (behaviour points are still logged)

SCORE_VIOLATION = 0.75
SCORE_WARNING = 0.55

# Hysteresis: a status is left only when the score is this far below the
# threshold that admitted it.
HYSTERESIS_DROP = 0.05

LEVEL_ORDER = {NONE: 0, MONITORING: 1, WARNING: 2, VIOLATION: 3}

LEVEL_CHOICES = (
    (NONE, "None"),
    (MONITORING, "Monitoring"),
    (WARNING, "Possible"),
    (VIOLATION, "Likely"),
)

LEVEL_LABELS = {
    VIOLATION: "Likely",
    WARNING: "Possible",
    MONITORING: "Monitoring",
    NONE: "Not shown",
}

# Which statuses are written to the database, and which of those notify.
STORED_LEVELS = (MONITORING, WARNING, VIOLATION)
NOTIFY_LEVELS = (WARNING, VIOLATION)


def label_of(level):
    """Human-facing name for a status, per spec section 2."""
    return LEVEL_LABELS.get(level, str(level or "").title())


def _r(x):
    """Round away float noise (0.2 + 0.2 + 0.15) before comparing to a threshold."""
    return round(x, 6)


def level_of(score, object_on=True):
    """Status for a score. With the object cue ON the floor is Monitoring; with it
    OFF nothing is shown (the puff-only path is handled in Score)."""
    if not object_on:
        return NONE
    score = _r(score)
    if score >= SCORE_VIOLATION:
        return VIOLATION
    if score >= SCORE_WARNING:
        return WARNING
    return MONITORING


def stored_at(level):
    """True when `level` is written to the database (Monitoring and above)."""
    return level in STORED_LEVELS


def alerts_at(level):
    """True when `level` notifies the tanod (Possible and Likely only)."""
    return level in NOTIFY_LEVELS


def level_with_hysteresis(score, previous_level, object_on=True):
    """Status of `score`, resisting a drop out of Possible / Likely.

    Rising is immediate. Falling out of Possible or Likely needs the score to
    clear HYSTERESIS_DROP below that status's entry threshold, so a score
    hovering on a boundary reports one stable status instead of flapping.
    Monitoring has no floor: it ends when the object cue goes OFF.
    """
    fresh = level_of(score, object_on)
    if previous_level is None or not object_on:
        return fresh
    if LEVEL_ORDER.get(fresh, 0) >= LEVEL_ORDER.get(previous_level, 0):
        return fresh
    floor = {VIOLATION: SCORE_VIOLATION, WARNING: SCORE_WARNING}.get(previous_level)
    if floor is not None and _r(score) >= _r(floor - HYSTERESIS_DROP):
        return previous_level
    return fresh


# --- time-of-day multipliers (holdup) ---------------------------------------
# Robielos & Duran (2020), IEOM -- five years of City of Manila robbery and
# theft records modelled as a discrete Bayesian network, reporting the
# probability of an incident falling in each three-hour block. Dividing each
# block's probability by 12.5% (the uniform expectation across eight blocks)
# gives a multiplier applicable directly to the holdup score.
#
# HONEST CAVEAT, to be reported rather than hidden: international evidence does
# associate darkness with street robbery, so this table disagrees with the
# wider literature. It is also drawn from a different city, combines robbery
# with theft, and counts only cases that reached conviction. A bad hour can
# never create an alert by itself.
MANILA_HOUR_BLOCKS = (
    # (start_hour, end_hour_exclusive, incident_probability, multiplier)
    (0,  3,  0.095, 0.76),
    (3,  6,  0.100, 0.80),
    (6,  9,  0.070, 0.56),
    (9,  12, 0.156, 1.25),
    (12, 15, 0.163, 1.31),
    (15, 18, 0.170, 1.36),   # peak
    (18, 21, 0.136, 1.09),
    (21, 24, 0.109, 0.87),
)

UNIFORM_BLOCK_PROBABILITY = 0.125


def manila_time_multiplier(when=None):
    """Holdup time-of-day multiplier for `when` (a datetime or time).

    Returns 1.0 for anything unparseable, so a clock problem can never suppress
    evidence.
    """
    hour = getattr(when, "hour", None)
    if hour is None:
        return 1.0
    for start, end, _prob, multiplier in MANILA_HOUR_BLOCKS:
        if start <= hour < end:
            return multiplier
    return 1.0


def manila_block_label(when=None):
    """Human-readable block name, for the alert description and the audit log."""
    hour = getattr(when, "hour", None)
    if hour is None:
        return ""
    for start, end, _prob, multiplier in MANILA_HOUR_BLOCKS:
        if start <= hour < end:
            return f"{start:02d}:00-{end:02d}:00 x{multiplier:.2f}"
    return ""


# --- drinking time band -----------------------------------------------------
# Omamalin (2022, PSSJ) describes tagay sessions as an afternoon-into-evening
# activity; Thai and Western Australian emergency-department series place
# alcohol-related presentations in the same window. A SCORED CUE (5 points),
# not a gate: drinking outside the band is still drinking.
DRINKING_HIGH_BAND = (datetime.time(16, 0), datetime.time(0, 0))


def in_time_band(now, start, end):
    """True when `now` falls in [start, end), handling windows crossing midnight."""
    t = now.time() if hasattr(now, "time") else now
    if start == end:
        return True
    if start < end:
        return start <= t < end
    return t >= start or t < end   # wraps midnight


# --- cue weights (spec sections 5-7) ----------------------------------------
# System indicators only. Each total matches the spec table exactly.

DRINKING_WEIGHTS = {            # total 0.85
    "bottle":             0.40,  # GATE -- Omamalin (2022)
    "gathering":          0.10,  # group of 2+ (stationary) -- Omamalin; Setti (2015)
    "gathering_duration": 0.15,  # stayed 10+ minutes -- Omamalin
    "at_mouth":           0.15,  # bottle at the mouth (needs the bottle) -- arXiv 2507.00566
    "time_band":          0.05,  # evening 4PM-midnight -- Thai ED (2021)
}

SMOKING_WEIGHTS = {             # total 1.10, capped at 1.00
    "cigarette":     0.40,   # GATE -- YOLOv8-MNC (2023)
    "gesture":       0.20,   # hand to mouth, 1 puff (pose)
    "puffs":         0.20,   # did it again, 2+ puffs (pose)
    "puff_pattern":  0.15,   # 3+ puffs within 5 minutes (pose)
    "near_mouth":    0.15,   # smoking item at the mouth (needs the item)
}

HOLDUP_WEIGHTS = {              # total 0.75 (theft.py E-codes)
    "E14": 0.45,   # knife / weapon present -- GATE
    "E12": 0.20,   # two people frozen close together
    "E10": 0.10,   # someone loitering first
}

# The spec rejects a smoke-plume indicator by name: vision-language models fail
# on small smoke (SmokeBench). Do not reintroduce one.

# --- the object gate ----------------------------------------------------------
GATE_CUES = {
    "drinking": "bottle",
    "smoking": "cigarette",
    "holdup": "E14",        # weapon present
}

# Dependent cues score only when their partner fired: an object AT THE MOUTH
# presupposes the object (a hand near a face is also eating, phoning, scratching).
CONDITIONAL_CUES = {
    "drinking": {"at_mouth": "bottle"},
    "smoking": {"near_mouth": "cigarette"},
    "holdup": {},
}

# The smoking cue that opens the capped puff-only path.
PUFF_ONLY_CUE = "puff_pattern"
PUFF_ONLY_TAG = "No smoking item detected — based on hand movement only"


def _apply_conditionals(cues, kind):
    """Drop dependent cues whose required partner did not fire. Returns (kept, dropped)."""
    fired = set(cues)
    requires = CONDITIONAL_CUES.get(kind, {})
    dropped = {c for c, needs in requires.items() if c in fired and needs not in fired}
    return fired - dropped, dropped


class Score:
    """One scored observation: its cue vector, multipliers, total and status.

    The FULL cue vector is retained even when nothing is shown: behaviour points
    are logged and are the labelled negatives calibration fits against.

    kind           "drinking" | "smoking" | "holdup"
    cues           the cues that fired this moment
    multipliers    {name: factor}; holdup's time block is {"E20": x}
    previous_level the incident's status last frame (enables hysteresis)
    people_near    holdup only: a second person is near the knife holder
    object_on      override the gate (the watchers pass the momentum cue);
                   default is "the gate cue is among `cues`"
    """

    def __init__(self, kind, weights, cues, multipliers=None, *, previous_level=None,
                 people_near=True, object_on=None, detail=""):
        self.kind = kind
        self.detail = detail
        self.multipliers = dict(multipliers or {})
        self.fired = set(cues)

        kept, suppressed = _apply_conditionals(self.fired, kind)
        self.suppressed = suppressed
        self.cues = {name: weights[name] for name in kept if name in weights}
        # A cue that fired but has no weight is a wiring bug, not a scoring decision.
        self.unknown = {name for name in kept if name not in weights}

        self.system_score = sum(self.cues.values())
        self.raw_score = self.system_score
        total = self.system_score
        for factor in self.multipliers.values():
            total *= factor
        self.score = min(1.0, max(0.0, _r(total)))

        gate_cue = GATE_CUES.get(kind)
        self.gate_cue = gate_cue
        self.object_on = (gate_cue in self.fired) if object_on is None else bool(object_on)
        self.gate_open = self.object_on
        self.people_near = bool(people_near)

        # Puff-only: no item, but a repeated pose pattern (3+ puffs). Capped at
        # Possible and always tagged; fewer puffs are logged and never shown.
        self.puff_only = (kind == "smoking" and not self.object_on
                          and PUFF_ONLY_CUE in self.fired)
        self.tag = PUFF_ONLY_TAG if self.puff_only else ""

        if self.object_on:
            level = level_with_hysteresis(self.score, previous_level, True)
        elif self.puff_only:
            level = level_with_hysteresis(self.score, previous_level, True)
            if _r(self.score) < SCORE_WARNING:
                level = NONE
        else:
            level = NONE

        # Holdup hard requirement (spec 7): a second person NEAR the knife holder.
        # Without one the event stays Monitoring, whatever else added up.
        self.holdup_capped = False
        if kind == "holdup" and not self.people_near and LEVEL_ORDER[level] > LEVEL_ORDER[MONITORING]:
            level = MONITORING
            self.holdup_capped = True
        # Puff-only can never exceed Possible, even with hysteresis carry-over.
        if self.puff_only and LEVEL_ORDER[level] > LEVEL_ORDER[WARNING]:
            level = WARNING
        self.level = level

    @property
    def stored(self):
        """True when this status is written to the database (Monitoring and up)."""
        return stored_at(self.level)

    @property
    def alerting(self):
        """True when this notifies the tanod (Possible and Likely)."""
        return alerts_at(self.level)

    @property
    def visible(self):
        """Shown anywhere (the watchlist counts): Monitoring and up."""
        return self.stored

    @property
    def percent(self):
        return round(self.score * 100, 1)

    # What the tanod reads instead of the number (spec section 2).
    CUE_LABELS = {
        # drinking
        "bottle": "Bottle seen",
        "gathering": "Group of 2 or more",
        "gathering_duration": "Stayed 10+ minutes",
        "at_mouth": "Bottle raised to the mouth",
        "time_band": "Evening (4PM - midnight)",
        # smoking
        "cigarette": "Smoking item seen",
        "gesture": "Hand going to the mouth",
        "puffs": "Did it again (2+ puffs)",
        "puff_pattern": "Repeated puffs (3+ within 5 minutes)",
        "near_mouth": "Smoking item at the mouth",
        # holdup (Layer E codes)
        "E14": "Weapon seen",
        "E12": "Two people frozen close together",
        "E10": "Someone loitering first",
    }
    MULTIPLIER_LABELS = {"E20": "Time of day"}

    def checklist(self):
        """Plain-language evidence lines for the Status card."""
        found = [self.CUE_LABELS.get(n, n) for n in sorted(self.cues)]
        notes = [self.MULTIPLIER_LABELS.get(n, n) for n, f in sorted(self.multipliers.items())
                 if f != 1.0]
        return {"found": found, "adjusted_by": notes, "tag": self.tag}

    def as_dict(self):
        """Serialisable cue vector -- stored on the Alert for audit and calibration."""
        return {
            "kind": self.kind,
            "cues": dict(self.cues),
            "multipliers": dict(self.multipliers),
            "suppressed": sorted(self.suppressed),
            "raw_score": round(self.raw_score, 4),
            "score": round(self.score, 4),
            "object_on": self.object_on,
            "puff_only": self.puff_only,
            "people_near": self.people_near,
            "holdup_capped": self.holdup_capped,
            "level": self.level,
            "label": label_of(self.level),
            "tag": self.tag,
            "checklist": self.checklist(),
        }

    def summary(self):
        cues = ", ".join(f"{n}={w:.2f}" for n, w in sorted(self.cues.items()))
        mults = "".join(f" x{f:.2f}" for f in self.multipliers.values())
        head = f"{self.detail} | " if self.detail else ""
        tag = f" [{self.tag}]" if self.tag else ""
        return f"{head}{cues}{mults} -> {self.score:.2f} ({self.level}){tag}"

    def __repr__(self):
        return f"<Score {self.kind} {self.score:.2f} {self.level}>"
