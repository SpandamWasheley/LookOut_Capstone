"""The weighted-sum violation-scoring model shared by every detector.

WHY THIS EXISTS
---------------
Every module except the theft pattern engine used to decide an alert through a
chain of hard gates: the object must be detected, AND the dwell must elapse, AND
the posture check must pass. Any one gate failing means nothing is reported, and
the number stored as ``Alert.confidence`` was just the YOLO confidence of the
winning box.

That number answers "how sure is the model that this is a bottle?". It does not
answer "how likely is it that this is a drinking violation?" -- and only the
second question matters to the barangay official acting on the alert.

Here each indicator contributes points in proportion to how strongly it
distinguishes a violation from ordinary behaviour, and the total becomes the
reported violation likelihood:

    raw_score   = SUM( weight_i * cue_i )          cue_i = 1 if fired else 0
    final_score = min(1.0, raw_score * PRODUCT( multiplier_j ))

ADDITIVE cues are pieces of evidence that a violation is occurring.
MULTIPLICATIVE factors are context that makes the same evidence more or less
suspicious -- time of day is the clearest case. A knife-and-freeze pattern at
4PM in Manila is likelier to be a robbery than the identical pattern at 7AM, so
the score is SCALED rather than having points added.

The practical consequence, and the reason for the whole refactor: a missed
object detection no longer SILENCES the system, it only prevents escalation to
the highest level. That is the direct answer to the domain-gap problem -- our
YOLO weights are trained on datasets that do not look like barangay CCTV.

RELATIONSHIP TO theft.py's E28/E29
----------------------------------
theft.py implemented this first, with its own two thresholds and the band names
discard/observe/candidate. Those names are kept as aliases below and the
thresholds are unchanged, so theft's calibration is untouched: everything that
alerted before still alerts. The only addition is that the old top band is now
SPLIT at 0.75 into WARNING and VIOLATION, which is new reporting granularity
rather than a new filter.

No Django model access here -- pure scoring plumbing, same rule as
recognition.py and tracking.py, so the weights stay unit-testable without a
database.
"""

import datetime


# --- decision bands ---------------------------------------------------------
# The three level names and their thresholds are a design decision of this
# project, not a published standard. The band STRUCTURE follows theft.py's E29
# engine; the 0.75 split is set from testing. Say so in the limitations section
# rather than implying the numbers are borrowed from a paper.

VIOLATION = "violation"   # >= 0.75 -- file as a confirmed violation
WARNING = "warning"       # >= 0.55 -- actionable, shown to tanods
WATCH = "watch"           # >= 0.35 -- retained for calibration, not dispatched
NONE = "none"             # below WATCH -- discarded

SCORE_VIOLATION = 0.75
SCORE_WARNING = 0.55
SCORE_WATCH = 0.35

# Levels at or above which an Alert row is actually written. WATCH-band events
# are scored and logged but never dispatched: their purpose is to supply the
# labelled negatives the calibration harness needs (see calibrate_weights).
ALERTING_LEVELS = (WARNING, VIOLATION)

# theft.py's original E29 vocabulary, preserved so nothing downstream breaks.
# candidate == "alerting", which is exactly what it meant before the split.
DISCARD = NONE
OBSERVE = WATCH
CANDIDATE = WARNING

LEVEL_ORDER = {NONE: 0, WATCH: 1, WARNING: 2, VIOLATION: 3}

LEVEL_CHOICES = (
    (NONE, "None"),
    (WATCH, "Watch"),
    (WARNING, "Warning"),
    (VIOLATION, "Violation"),
)


# --- display names ----------------------------------------------------------
# The spec (§2) names the bands Monitoring / Possible / Confirmed in the UI,
# because "watch/warning/violation" reads as three severities of the same claim
# to a barangay tanod, while these three read as three degrees of certainty --
# which is what the score actually measures.
#
# The internal constants keep their old values so theft.py's calibration, the
# stored Alert.level rows and every existing test are untouched. Only the label
# shown to a human changes.
LEVEL_LABELS = {
    # v3 §2. "Confirmed" became "Likely" deliberately: the system proposes and
    # the tanod confirms. Calling a machine's reading "Confirmed" claims the
    # officer's judgement for the model, and it is the officer's Verified /
    # Dismissed decision that is the real confirmation -- and the labelled data
    # calibration needs.
    VIOLATION: "Likely",
    WARNING: "Possible",
    # Scored and stored, shown to nobody. Kept as a band rather than folded into
    # NONE because these rows are the labelled negatives calibrate_weights fits
    # against; discarding them would discard half the training signal.
    WATCH: "Not shown",
    NONE: "Not shown",
}


def label_of(level):
    """Human-facing name for a level, per spec §2."""
    return LEVEL_LABELS.get(level, str(level or "").title())


def level_of(score):
    """Map a final score to one of the four levels."""
    if score >= SCORE_VIOLATION:
        return VIOLATION
    if score >= SCORE_WARNING:
        return WARNING
    if score >= SCORE_WATCH:
        return WATCH
    return NONE


def alerts_at(level):
    """True when `level` is high enough to write an Alert row."""
    return level in ALERTING_LEVELS


# --- hysteresis -------------------------------------------------------------
# The interpretable robbery-detection work (arXiv 2604.14329) stabilises
# frame-level predictions with a temporal hysteresis filter to cut spurious
# alarms. A score sitting exactly on a threshold otherwise oscillates across it
# frame to frame, and each upward crossing looks like a new incident.
#
# So a level, once ENTERED, is only left when the score falls this far below the
# threshold that admitted it. Entry uses the plain thresholds above.
HYSTERESIS_DROP = 0.05


def level_with_hysteresis(score, previous_level):
    """Level of `score`, resisting a drop out of `previous_level`.

    Rising is immediate -- evidence accumulating should escalate without delay.
    Falling needs the score to clear HYSTERESIS_DROP below the band floor, so a
    score hovering on a boundary reports one stable level instead of flapping.
    """
    fresh = level_of(score)
    if previous_level is None:
        return fresh
    if LEVEL_ORDER.get(fresh, 0) >= LEVEL_ORDER.get(previous_level, 0):
        return fresh
    floor = {VIOLATION: SCORE_VIOLATION, WARNING: SCORE_WARNING,
             WATCH: SCORE_WATCH}.get(previous_level)
    if floor is not None and score >= floor - HYSTERESIS_DROP:
        return previous_level
    return fresh


# --- time-of-day multipliers (holdup) ---------------------------------------
# Robielos & Duran (2020), IEOM -- five years of City of Manila robbery and
# theft records modelled as a discrete Bayesian network, reporting the
# probability of an incident falling in each three-hour block. Dividing each
# block's probability by 12.5% (the uniform expectation across eight blocks)
# gives a multiplier applicable directly to the holdup score.
#
# This REPLACES the flat nocturnal x1.3 applied 22:00-05:00. Both Philippine
# datasets examined (Manila and Butuan City) put the peak in the AFTERNOON and
# early evening, not late at night, so the nocturnal assumption was not
# supported by local data.
#
# HONEST CAVEAT, to be reported rather than hidden: international evidence does
# associate darkness with street robbery, so this table disagrees with the
# wider literature. It is also drawn from a different city, combines robbery
# with theft, and counts only cases that reached conviction.
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
    a holdup score: a clock problem must never suppress evidence.
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
# alcohol-related presentations in the same window. This is a SCORED CUE worth
# DRINKING_WEIGHTS["time_band"], not a gate: drinking outside the band is still
# drinking, it is just less characteristic, so it loses points instead of being
# silently dropped.
DRINKING_HIGH_BAND = (datetime.time(16, 0), datetime.time(0, 0))


def in_time_band(now, start, end):
    """True when `now` falls in [start, end), handling windows crossing midnight."""
    t = now.time() if hasattr(now, "time") else now
    if start == end:
        return True
    if start < end:
        return start <= t < end
    return t >= start or t < end   # wraps midnight


# --- cue weights ------------------------------------------------------------
# Every weight here is a REASONED INITIAL VALUE taken from how central the
# indicator is to the violation as described in the cited literature -- the
# drinking vessel is the identity marker of a tagay session, so it outweighs the
# presence of chairs. None of them is a calibrated result yet.
#
# The second stage, and the far stronger justification, is calibrate_weights:
# fitting a logistic regression to 50-100 labelled clips from the deployment
# cameras produces, by construction, the coefficients that best separate
# violations from non-violations in OUR footage. Those fitted values then
# replace these and the resulting precision/recall is reported.

DRINKING_WEIGHTS = {
    # Spec §4.1. Every row below appears in the spec's table; nothing else does.
    # System indicators total 0.85, so the object alone (0.40) lands on
    # Monitoring and the supporting cues decide how much higher it goes.
    "bottle":             0.40,  # GATE -- Omamalin (2022), the identity marker
    # The spec folds "group stationary" INTO this cue rather than pricing it
    # separately: a group that is not stationary is people passing each other,
    # which the tracker already declines to call a group.
    "gathering":          0.10,  # Omamalin; Setti et al. (2015)
    "gathering_duration": 0.15,  # Omamalin -- 3-5 hour sessions, 600-900s
    # Conditional on `bottle` (see CONDITIONAL_CUES): the spec adds this ON TOP
    # of the object rather than replacing it, so a raised bottle scores 0.55
    # where a held one scores 0.40.
    "at_mouth":           0.15,  # skeleton drink-action (arXiv 2507.00566)
    "time_band":          0.05,  # Omamalin; Thai ED (2021); Trinity

    # --- AI context checker (v3 §5, prompts in vlm.py DRINKING_SPEC) --------
    # Totals 0.40, exactly the cap: the checker can never carry a case alone.
    #
    # It is asked only what YOLO and the pose model cannot answer. v3 removed
    # `beverage_container_visible` (it repeats the bottle gate) and merged
    # glass/cup and food into one `drinking_items` question -- a single glass or
    # snack is too small to identify reliably at CCTV resolution, while a set of
    # items laid out near a group is both easier to see and a better description
    # of an inuman.
    "vlm_verdict":        0.20,  # group_appears_to_be_drinking_together
    "vlm_seating":        0.10,  # table_chairs_or_seating_visible
    "vlm_drinking_items": 0.10,  # drinking_items_visible
}

SMOKING_WEIGHTS = {
    # Spec §4.2. System indicators total 0.95.
    "cigarette":   0.40,   # GATE -- YOLOv8-MNC (2023)
    # Conditional on `cigarette`: an object at the mouth is only evidence of
    # smoking once the object itself has been seen.
    "near_mouth":  0.15,   # keypoints + YOLOv8 (IEEE)
    # The two pose cues total 0.40 -- exactly the pose exception's budget, so
    # behaviour alone reaches Monitoring and stops there (§2b).
    "gesture":     0.20,   # hand-to-mouth (IEEE)
    "puffs":       0.20,   # >=2 cycles (IEEE; PACT2.0)

    # --- AI context checker (v3 §6) ----------------------------------------
    # Totals 0.35, exactly the cap.
    #
    # `vlm_verdict` here is the hand_to_mouth_activity answer reading "smoking".
    # That question replaces the old yes/no "is he smoking": the same
    # hand-to-mouth motion is also drinking, eating and a phone call, and the
    # pose model cannot tell them apart. Any answer other than smoking or
    # unclear also scales the whole score down (SCENE_MULTIPLIER).
    "vlm_verdict":      0.20,  # hand_to_mouth_activity == "smoking"
    # Scores ONLY when YOLO missed the item -- otherwise it counts the same
    # cigarette twice. It is the fallback that lets a puff-only case be shown at
    # all: 0.40 + 0.15 + 0.20 = 0.75.
    "vlm_smoking_item": 0.15,  # smoking_item_visible
}

# The spec rejects a smoke-plume indicator BY NAME: MLLMs fail on small smoke
# (SmokeBench), and gesture + item is sufficient (MDPI Applied Sciences 2020).
# Do not reintroduce one without overturning that citation.

# Holdup VLM questions, additive alongside theft.py's existing E7-E22 weights.
# Numbering continues Layer E's own scheme so the audit trail reads as one
# sequence rather than two, and starts at E30 because the geometry stops at E22.
HOLDUP_VLM_WEIGHTS = {
    # v3 §7. Totals 0.45, exactly the cap.
    # `knife_or_sharp_object_visible` is gone: it repeated the E14 gate, and v3
    # is explicit that the checker is never asked to confirm an object the
    # detector already found.
    "E30": 0.20,   # appears_to_be_a_holdup -- LAVAD; AnyAnomaly
    "E32": 0.15,   # object_pointed_at_a_person -- Ruiz-Santaquiteria (2021)
    "E34": 0.10,   # victim_response_visible -- citation pending (v3 §12)
}

# VLM cue name -> Layer E code, so neither side hard-codes the pairing twice.
HOLDUP_CUE_CODES = {
    "object_pointed_at_a_person": "E32",
    "victim_response_visible": "E34",
}

# The code the denial multiplier is filed under, so a suppressed alert's audit
# trail names the reason rather than showing an unexplained factor.
HOLDUP_DENIAL_CODE = "E33"


# --- the visibility gate (v3 §4, rule 1) ------------------------------------
# "No object, no alert." Behaviour alone -- standing, chatting, waiting -- is
# never shown, however many points it earns. People stand, wait, chat and sell
# things all day, and a feed full of that is a feed nobody reads.
#
# The score is still computed and still stored: the gate governs VISIBILITY, not
# scoring. A gated result keeps its full cue vector, because those are the
# labelled negatives calibrate_weights fits against.
GATE_CUES = {
    "drinking": "bottle",
    "smoking": "cigarette",
    "holdup": "E14",        # weapon presence
}

# The one exception (v3 §4). Repeated puff motion without a detected cigarette
# still calls the checker, because the cigarette is the hardest object in the
# system to detect at CCTV range. On puff motion alone the score is 0.40 and
# NOTHING is shown; it can only become visible if the checker also reads the
# motion as smoking, which is what `vlm_smoking_item` (0.15) plus the verdict
# (0.20) are for: 0.40 + 0.15 + 0.20 = 0.75.
POSE_EXCEPTION = {"smoking": ("gesture", "puffs")}
# What lifts the exception: the checker agreeing. Either its reading of the
# motion (vlm_verdict) or its sighting of the item YOLO missed is enough --
# both are things only the checker can contribute, so neither can fire without
# a real second opinion.
POSE_EXCEPTION_RELEASE = ("vlm_verdict", "vlm_smoking_item")


# --- the AI contribution cap (v3 §4, rule 2) --------------------------------
# The checker may never raise an alert by itself. Its answers are summed,
# scaled by its own confidence, capped, and only then added to the system
# indicators.
#
# Applied BEFORE the total, not after. Capping only the final score would let a
# confident checker push a weak geometric case over the line on its own, which
# is precisely what this exists to prevent.
VLM_CAP = {
    "drinking": 0.40,
    "smoking": 0.35,
    "holdup": 0.45,
}

# --- confidence tiers (v3 §8) -----------------------------------------------
# The checker's own certainty scales everything it contributed. This is the
# guard against a model sounding authoritative about something it invented:
# "low" earns nothing at all.
#
# Words rather than a number because a language model's numeric confidence is
# not calibrated -- 0.82 and 0.79 do not reliably differ -- while its choice
# between "high" and "low" is a coarser judgement it makes more reliably.
CONFIDENCE_TIERS = (("high", 1.0), ("medium", 0.5), ("low", 0.0))
CONFIDENCE_SCALE = dict(CONFIDENCE_TIERS)

# Float -> tier, for a provider that returns a number instead of a word.
CONFIDENCE_HIGH = 0.70
CONFIDENCE_MEDIUM = 0.40


def confidence_tier(confidence):
    """Bucket a confidence into v3's three tiers."""
    if confidence is None:
        return "low"
    if isinstance(confidence, str):
        return confidence.lower() if confidence.lower() in CONFIDENCE_SCALE else "low"
    if confidence >= CONFIDENCE_HIGH:
        return "high"
    if confidence >= CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


def confidence_scale(confidence):
    """The factor the checker's own points are multiplied by (v3 §8)."""
    return CONFIDENCE_SCALE[confidence_tier(confidence)]


# --- de-escalation: the scene reading (v3 §4, §8) ---------------------------
# The checker's one way to CUT a score. If it reads the scene as ordinary
# activity -- a vendor with a knife, a store selling bottles, a person eating --
# the whole total is multiplied by this.
#
# v3 applies it to ALL THREE violations, where v2 had it on holdup only. For
# drinking and smoking it is the answer to `scene_type` / `hand_to_mouth_activity`
# respectively; the mechanism is identical.
#
# A multiplier rather than negative points, because "this looks like an ordinary
# fish vendor" is not evidence against "a blade was detected" -- the two are not
# claims about the same thing. It says the whole picture is less suspicious than
# the sum of its parts, which is what is actually meant.
#
# 0.25 is chosen so that an ordinary-activity reading drops even a MAXIMUM case
# below the 55 floor and out of sight:
#     drinking 125 -> 31    smoking 130 -> 32.5    holdup 120 x1.36 -> 41
# It is not zero, so the event and its score stay in the log and can be audited
# if the checker was wrong.
SCENE_MULTIPLIER = 0.25

# The Layer E code the holdup scene reading is filed under, so a suppressed
# alert's audit trail names the reason rather than showing a bare factor.
HOLDUP_DENIAL_CODE = "E33"


def _is_vlm(cue_name):
    """True for a cue the VLM produced, by naming convention.

    Drinking and smoking prefix theirs `vlm_`; holdup uses Layer E codes, and
    E30-E33 is the block the spec reserves for the VLM (§4.3). The distinction
    matters because the two halves are summed separately -- the VLM's total is
    capped before it joins the system indicators (§5.4).
    """
    if cue_name.startswith("vlm_"):
        return True
    return cue_name in HOLDUP_VLM_WEIGHTS or cue_name == HOLDUP_DENIAL_CODE


# --- conditional cues (spec §4.1, §4.2) -------------------------------------
# A weighted linear sum assumes the indicators are independent, and ours are
# not: an object AT THE MOUTH presupposes the object.
#
# The spec's rule is CONDITIONAL, not suppressive -- "at-mouth posture scores
# only if the bottle cue is ON", and likewise "item near mouth scores only if
# the object cue is ON". So the specific cue ADDS to the general one rather than
# replacing it: a raised bottle scores 0.40 + 0.15, not max(0.40, 0.15).
#
# This differs from the earlier redundant-pair model, which kept only the
# heavier of the two. Under the spec both count, but the dependent one cannot
# fire alone -- which is the same protection against double-counting a single
# observation, reached from the other direction: an at-mouth posture with no
# object detected is a hand near a face, and a hand near a face is also eating,
# phoning, drinking and scratching.
CONDITIONAL_CUES = {
    "drinking": {"at_mouth": "bottle"},
    "smoking": {"near_mouth": "cigarette"},
    "holdup": {},
}


def _apply_conditionals(cues, kind):
    """Drop dependent cues whose required partner did not fire.

    Returns (kept, dropped).
    """
    fired = set(cues)
    requires = CONDITIONAL_CUES.get(kind, {})
    dropped = {c for c, needs in requires.items() if c in fired and needs not in fired}
    return fired - dropped, dropped


class Score:
    """One scored observation: its cue vector, multipliers, total and level.

    The FULL cue vector is retained even for NONE-level results. That is not
    diagnostics -- it is the mechanism by which the weights get calibrated later
    against real footage instead of against the reasoned defaults above, so
    throwing away low scores would throw away every labelled negative.
    """

    def __init__(self, kind, weights, cues, multipliers=None, abstained=(),
                 redundant_pairs=None, detail="", vlm_confidence=None):
        # `redundant_pairs` is accepted and ignored: the spec replaced pair
        # suppression with CONDITIONAL_CUES. Kept in the signature so existing
        # call sites and tests do not break on an unexpected keyword.
        self.kind = kind
        # None means "no VLM answer in this score" -- the cues cannot include
        # any vlm_* entries in that case, so the factor is inert either way.
        self.vlm_confidence = vlm_confidence
        self.vlm_confidence_scale = (
            1.0 if vlm_confidence is None else confidence_scale(vlm_confidence))
        self.detail = detail
        self.abstained = set(abstained)
        self.multipliers = dict(multipliers or {})

        # The cues that actually FIRED, before redundancy suppression. The gate
        # below must consult these, not the kept set: suppression decides what
        # may SCORE, and a suppressed cue is still an observation that was made.
        # Reading the kept set instead closes the gate whenever the object cue
        # loses its pair -- "cigarette at the mouth" would gate as though no
        # cigarette had been seen at all.
        self.fired = set(cues)

        kept, suppressed = _apply_conditionals(cues, kind)
        self.suppressed = suppressed
        self.cues = {name: weights[name] for name in kept if name in weights}
        # Cues that fired but have no weight defined are a wiring bug, not a
        # scoring decision -- surface them rather than silently ignoring them.
        self.unknown = {name for name in kept if name not in weights}

        # --- the capping arithmetic (spec §7b) ------------------------------
        #   1. sum the system indicators
        #   2. sum the VLM cues, then CAP them
        #   3. add 1 + 2
        #   4. apply multipliers
        #   5. cap the result at 1.0
        system = {n: w for n, w in self.cues.items() if not _is_vlm(n)}
        vlm_cues = {n: w for n, w in self.cues.items() if _is_vlm(n)}

        self.system_score = sum(system.values())
        self.vlm_raw = sum(vlm_cues.values())
        # Confidence scales everything the VLM contributed, then the cap binds.
        self.vlm_score = min(self.vlm_raw * self.vlm_confidence_scale,
                             VLM_CAP.get(kind, 1.0))
        self.vlm_capped = self.vlm_raw * self.vlm_confidence_scale > self.vlm_score

        # Kept UNCAPPED as well. Many real violations land at exactly 1.00 after
        # capping, which hides how strong each case actually was -- and the raw
        # number is what calibration and the results chapter need.
        self.raw_score = self.system_score + self.vlm_raw
        total = self.system_score + self.vlm_score
        for factor in self.multipliers.values():
            total *= factor
        # Capped at 1.0: the score is reported as a likelihood, and evidence
        # beyond certainty is still certainty.
        self.score = min(1.0, max(0.0, total))
        self.level = level_of(self.score)

        # --- the visibility gate (spec §2b) ---------------------------------
        # Computed last, because it constrains what may be SHOWN rather than
        # what is scored. `visible` is the flag a watcher checks before writing
        # an Alert row.
        gate_cue = GATE_CUES.get(kind)
        self.gate_open = gate_cue is None or gate_cue in self.fired
        self.gate_cue = gate_cue
        self.pose_exception = False
        if not self.gate_open:
            # The smoking pose exception: puff motion alone may surface, but
            # never above Monitoring.
            allowed = POSE_EXCEPTION.get(kind, ())
            if allowed and any(c in self.fired for c in allowed):
                self.pose_exception = True
                # v3 §4: "On puff motion alone the score is 40 and nothing is
                # shown. It can only be shown if the AI checker also reads the
                # motion as smoking."
                #
                # So the checker's agreement, not a fixed ceiling, is what
                # lifts a behaviour-only case into view. Pose alone tops out at
                # 0.40 anyway -- below the 0.55 floor -- so the arithmetic and
                # the rule agree without a clamp. What the clamp WOULD have
                # done is block the one case v3 exists to allow: puffs (0.40)
                # plus the item the checker spotted (0.15) plus its reading of
                # the motion (0.20) = 0.75, which is exactly v3's worked
                # example.
                if not any(c in self.fired for c in POSE_EXCEPTION_RELEASE):
                    self.level = NONE
            else:
                # Scored, retained for calibration, shown to nobody.
                self.level = NONE

    @property
    def alerting(self):
        """True when this may be written to the database and shown.

        The gate is folded in here rather than left to each caller: a rule that
        every watcher has to remember to apply is a rule that one of them will
        eventually forget.
        """
        return alerts_at(self.level)

    @property
    def visible(self):
        """Explicit alias for readability at the call sites."""
        return self.alerting

    @property
    def percent(self):
        """Score on the 0-100 scale Alert.confidence and the dashboard use."""
        return round(self.score * 100, 1)

    # v3 §2. What the tanod reads instead of the score.
    #
    # "A score of 68 versus 73 means nothing to a tanod and reads like a
    # percentage, which it is not." So the card lists the evidence that
    # actually fired, in plain words, and the number stays in the database for
    # calibration and audit.
    #
    # Phrased as observations, not cue names: "Bottle seen" rather than
    # "bottle=0.40". A barangay official should not have to learn the schema to
    # read an alert.
    CUE_LABELS = {
        # drinking
        "bottle": "Bottle seen",
        "gathering": "Group of 2 or more",
        "gathering_duration": "Stayed 10+ minutes",
        "at_mouth": "Bottle raised to the mouth",
        "time_band": "Evening (4PM - midnight)",
        "vlm_verdict": "AI checker: drinking together",
        "vlm_seating": "AI checker: seating or a table",
        "vlm_drinking_items": "AI checker: glasses, cups or snacks laid out",
        # smoking
        "cigarette": "Smoking item seen",
        "near_mouth": "Smoking item at the mouth",
        "gesture": "Hand going to the mouth",
        "puffs": "Repeated puffs",
        "vlm_smoking_item": "AI checker: smoking item seen",
        # holdup (Layer E codes)
        "E14": "Weapon seen",
        "E12": "Two people frozen close together",
        "E10": "Someone loitering first",
        "E30": "AI checker: this is a holdup",
        "E32": "AI checker: object pointed at a person",
        "E34": "AI checker: victim reacting",
    }

    # The multipliers worth showing. A de-escalation is the most important
    # thing on the card when it fires, because it explains why an alert the
    # indicators would have raised is not being raised.
    MULTIPLIER_LABELS = {
        "scene_type": "AI checker: ordinary activity",
        "hand_to_mouth_activity": "AI checker: not smoking",
        "E33": "AI checker: ordinary activity",
        "E20": "Time of day",
    }

    def checklist(self):
        """Plain-language evidence lines for the alert card (v3 §2)."""
        found = [self.CUE_LABELS.get(n, n) for n in sorted(self.cues)]
        cut = [self.MULTIPLIER_LABELS.get(n, n)
               for n, f in sorted(self.multipliers.items()) if f < 1.0]
        return {"found": found, "reduced_by": cut}

    def as_dict(self):
        """Serialisable cue vector -- stored on the Alert for calibration."""
        return {
            "kind": self.kind,
            "cues": dict(self.cues),
            "multipliers": dict(self.multipliers),
            "suppressed": sorted(self.suppressed),
            "abstained": sorted(self.abstained),
            "raw_score": round(self.raw_score, 4),
            "system_score": round(self.system_score, 4),
            "vlm_raw": round(self.vlm_raw, 4),
            "vlm_score": round(self.vlm_score, 4),
            "vlm_capped": self.vlm_capped,
            "vlm_confidence_scale": self.vlm_confidence_scale,
            "gate_open": self.gate_open,
            "pose_exception": self.pose_exception,
            "score": round(self.score, 4),
            "level": self.level,
            "label": label_of(self.level),
            # Carried on the stored vector so the dashboard and the officer app
            # cannot drift apart on wording, and so an old alert still renders
            # with the labels that were current when it was filed.
            "checklist": self.checklist(),
        }

    def summary(self):
        cues = ", ".join(f"{n}={w:.2f}" for n, w in sorted(self.cues.items()))
        mults = "".join(f" x{f:.2f}" for f in self.multipliers.values())
        extra = ""
        if self.suppressed:
            extra += f" [redundant: {', '.join(sorted(self.suppressed))}]"
        if self.abstained:
            extra += f" [abstained: {', '.join(sorted(self.abstained))}]"
        head = f"{self.detail} | " if self.detail else ""
        return f"{head}{cues}{mults} -> {self.score:.2f} ({self.level}){extra}"

    def __repr__(self):
        return f"<Score {self.kind} {self.score:.2f} {self.level}>"
