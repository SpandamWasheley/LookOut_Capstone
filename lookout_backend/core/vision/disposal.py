"""Improper disposal — static-object detection plus a zero-shot bag check.

Hybrid, and deliberately NOT a trained class: the merged model knows Bottle,
Cigarette and knife, and garbage has no class in it. Rather than retrain, this
asks two cheap questions in sequence.

    Stage 1 (classical CV)  what arrived and then STOPPED MOVING?
    Stage 2 (zero-shot VLM) is that thing a garbage bag, and not a dog?

Stage 1 is the dual-background trick, and it is the whole reason this works
without training data:

  * the LONG model learns very slowly, so a dumped bag stays "foreground" for
    minutes after it lands;
  * the SHORT model learns fast, so the same still bag is absorbed into its
    background within seconds;
  * static_new_object = LONG foreground AND NOT SHORT foreground.

So a person walking through is foreground in BOTH models and cancels out; a
bag set down and left behind survives in the difference. Motion is what gets
filtered, which is exactly the right primitive for dumping: the ordinance
concerns rubbish LEFT somewhere, and "left" is a motion property, not an
appearance one.

Stage 2 exists because stage 1 cannot tell a sack from a sleeping dog, a
parked traffic cone or a dropped backpack. YOLO-World is prompted with both
the classes we want AND the classes that commonly sit still and would
otherwise false-alarm, and garbage only wins if it outscores every reject
class. That comparison -- not a bare confidence floor -- is what suppresses
the obvious look-alikes.

Stage 2 also does a second job, which the pipeline above makes necessary: it
decides whether a candidate is STILL THERE. Both background models keep
learning, so the slow one eventually absorbs the dumped bag as well and stage
1 loses the blob while the rubbish is plainly still on the pavement -- a
change detector has nothing left to detect once the change is the scene. So a
candidate stage 2 has already called garbage is pinned at its box when stage 1
forgets it, and from then on an appearance check answers what motion no longer
can. See PIN_MAX_SECONDS.

Same rule as recognition.py and theft.py: NO Django model access in here, so
stage 1 is unit-testable without a database, a GPU or a camera. The YOLO-World
import is lazy for the same reason -- importing this module costs cv2 and
numpy only.
"""

import datetime
import hashlib
import os
import time
from pathlib import Path

import cv2
import numpy as np

# --- stage 1: tunables ------------------------------------------------------
# Every value here is a reasoned default calibrated against the two sample
# clips in the repository root, not a field-calibrated result. Same honesty
# caveat as theft.py's constants table.

PROC_WIDTH = 960            # frames downscale to this width for stage 1 (speed)
WARMUP_SECONDS = 5.0        # learn the empty scene before detecting anything
WARMUP_LR = 0.05            # fast learning while warming up
LONG_LR = 0.0005            # slow model: ~minutes to absorb a new object
SHORT_LR = 0.02             # fast model: ~seconds to absorb a still object
FG_THRESHOLD = 200          # MOG2 writes shadows as 127 and real foreground as 255

# A blob must be a plausible size for a bag. Both bounds earn their place: the
# floor drops compression specks and insects, and the ceiling drops the
# frame-wide "blob" that a cloud passing over the sun produces.
MIN_AREA_FRAC = 0.0008      # >= 0.08% of frame
MAX_AREA_FRAC = 0.08        # <= 8% of frame

MATCH_DIST = 40             # px at PROC_WIDTH to call a blob the same candidate
CANDIDATE_GRACE = 2.0       # s a candidate may go unseen before it is dropped
RECHECK_SECONDS = 10.0      # re-ask stage 2 about a rejected candidate after this

# --- owner proximity (the abandoned-object rule) ----------------------------
# Stage 1 answers "did something arrive and stop moving?" -- which is also true
# of a sack a vendor has just set down beside their own stall and is standing
# over. The ordinance is about rubbish LEFT, and "left" means the person who
# put it there walked away from it. So a candidate with somebody standing over
# it is HELD, not alerted, until they go.
#
# Who counts as the owner is decided ONCE, at the moment the object appears:
# whoever was within OWNER_RADIUS of it then. Anyone who wanders past
# afterwards is a passerby and must not extend the hold, or a busy pavement
# would suppress every alert indefinitely.
#
# Person boxes come from the SAME stock-yolov8n pass the mover filter already
# runs twice a second -- no new model, no new weights, no extra forward pass.
OWNER_RADIUS = 80           # px at PROC_WIDTH: "standing over it"
OWNER_AWAY_SECONDS = 3.0    # owner must be gone this long before stage 2 runs
# Operator-chosen, and deliberately short: at 3s this is barely longer than the
# 2s static requirement, so in practice the hold releases almost as soon as it
# engages. The abandoned-object rule is therefore close to disabled by
# configuration -- expect alerts on people standing beside their own goods.
OWNER_MAX_HOLD = 3.0        # ... but never hold longer than this in total
# How far an owner may move between two mover passes and still be recognised as
# the same person. The passes are 0.5s apart and these detections carry no
# track ids, so re-association is nearest-centre within a gate; 120px at
# 960-wide is a brisk walk in half a second. Too small and an owner who takes a
# step is "gone"; too large and a passerby inherits the owner's identity.
OWNER_TRACK_GATE = 120

# --- did anything actually ARRIVE? -----------------------------------------
# The gap this closes is the one stage 1 cannot see on its own.
#
# Stage 1 asks "is there foreground the fast model has forgotten?", which is a
# question about MOTION, not about arrival. On night footage a headlight sweep,
# a reflection off wet tarmac or a shadow swinging across a shutter all answer
# yes for a second or two and then revert, leaving the scene exactly as it was.
# Nothing was dumped; the detector had no way to say so, because by the time it
# looked, the only evidence either way -- what this spot looked like BEFORE --
# was already absorbed into the background models it was comparing against.
#
# So the reference is kept explicitly: a ring of downscaled grey frames, and at
# the moment a candidate is born it keeps the one from ARRIVAL_LOOKBACK seconds
# earlier, cropped to its own patch of ground. From then on the question is not
# "is there foreground here" but "does this spot still look DIFFERENT from how
# it looked before the object turned up". Rubbish answers yes for as long as it
# lies there; a light that moved answers no the moment it has moved on.
#
# Two guards keep that honest:
#
#   * the ring. A patch grown to twice the object's size is compared too, and a
#     change confined to the object must beat the change in the ground around
#     it by ARRIVAL_MIN_MARGIN. A camera's auto-exposure lifting, a cloud, or a
#     vehicle's headlights washing the whole street move both equally -- that is
#     the scene changing, not something landing in it.
#   * consecutive looks, with NO latch. The counter resets on any failing look,
#     so the test is "this spot has looked different for the last few looks",
#     asked again every ARRIVAL_INTERVAL. A transient cannot bank a pass early
#     and coast on it, which a one-shot test taken at birth would let it do.
ARRIVAL_LOOKBACK = 6.0      # s before the blob was born that the reference comes from
ARRIVAL_SAMPLE_INTERVAL = 0.5   # s between kept reference frames
ARRIVAL_REF_SECONDS = 10.0  # how far back the reference ring reaches
ARRIVAL_PAD = 0.5           # ring is the box grown by 50% of its size each way
ARRIVAL_DIFF = 25           # grey levels a pixel must move to count as changed
ARRIVAL_MIN_CHANGE = 0.18   # share of the object's box that must have changed
ARRIVAL_MIN_MARGIN = 0.10   # ... and by this much more than the ground around it
ARRIVAL_INTERVAL = 0.25     # s between looks
ARRIVAL_CONFIRM_LOOKS = 3   # consecutive passing looks before the gate opens

# --- arrival and owner are EVIDENCE, not gates ------------------------------
# Both of these are proxies for "something was dumped here", and both of them
# can be wrong about a real dump:
#
#   * the arrival test measures changed PIXELS, so a dark sack on dark asphalt
#     at night can genuinely move fewer of them than the floor asks for;
#   * the owner rule needs a person detection, and yolov8n at distance, at
#     night, or on somebody with their back turned does not always give one.
#
# They were briefly used as GATES -- a candidate that failed either was
# withheld from the bag check -- and that was a mistake worth recording. A
# wrong answer from a gate is final and silent: a violation in plain view that
# the detector has decided never to look at, indistinguishable on screen from
# the rubbish having been cleared. Reported from footage: a person visibly
# left a sack and nothing was ever flagged, because the carrier was never
# detected.
#
# So they are cues now, scored in scoring.DISPOSAL_WEIGHTS. A missing person
# detection costs 0.35 of 1.00 instead of costing the whole violation, and the
# object route (object + arrival = 0.60) reaches Possible without any person
# evidence at all. Nothing here withholds anything; `cues_for` turns these
# measurements into the cue set and the shared scoring layer decides.

# How far back person detections are remembered when a new candidate latches
# its owner.
#
# `claim_owners` reads the people standing over an object AS IT APPEARS, and
# "as it appears" is later than it sounds: the fast background model has to
# forget the carrier before the object they set down shows up as static
# foreground, so by the time the blob exists the person who left it is
# routinely a second or two gone. Asking only about the current frame therefore
# misses the owner of the great majority of real dumps -- which matters a lot
# more now that having one is a REQUIREMENT, not just a reason to wait.
OWNER_LOOKBACK = 6.0


def _region(box, shape, pad=ARRIVAL_PAD):
    """`box` grown by `pad` of its own size, clamped to the frame, as ints.

    Returns None for a region too small to measure anything in.
    """
    gx, gy, gw, gh = _padded(box, pad)
    x0 = max(0, int(round(gx)))
    y0 = max(0, int(round(gy)))
    x1 = min(int(shape[1]), int(round(gx + gw)))
    y1 = min(int(shape[0]), int(round(gy + gh)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def arrival_change(before, now_patch, inner, diff=ARRIVAL_DIFF):
    """How much of `inner` changed since `before`, and how much around it did.

    `before` and `now_patch` are the SAME region of the frame in greyscale, one
    taken before the object arrived and one taken now; `inner` is the object's
    own box as (x, y, w, h) WITHIN that region. Returns
    (inner_fraction, ring_fraction), each 0-1.

    Deliberately a count of changed PIXELS rather than a mean difference: a
    small bright object and a large dim shift can produce the same average, and
    it is coverage -- how much of this patch of ground is no longer the ground
    -- that distinguishes them.
    """
    if before is None or now_patch is None or before.shape != now_patch.shape:
        return 0.0, 0.0
    changed = cv2.absdiff(before, now_patch) > diff
    x, y, w, h = inner
    inner_mask = changed[y:y + h, x:x + w]
    if inner_mask.size == 0:
        return 0.0, 0.0
    inner_frac = float(inner_mask.mean())
    ring_size = changed.size - inner_mask.size
    if ring_size <= 0:
        # The object fills its own padded region -- there is no ground left to
        # compare against, so the lighting guard abstains rather than inventing
        # a number. The coverage test still applies.
        return inner_frac, 0.0
    ring_frac = float((int(changed.sum()) - int(inner_mask.sum())) / ring_size)
    return inner_frac, ring_frac


# --- suspect crop ("likely dropped by") -------------------------------------
# A bag on the pavement identifies nobody. The window either side of the
# moment it APPEARED is the only part of the footage where whoever carried it
# is still in shot, so that is the window the crop comes from -- not the alert
# moment, which can be a minute later with the street empty.
SUSPECT_PRE_SECONDS = 3.0   # look back this far before the object appeared
SUSPECT_POST_SECONDS = 1.0  # ... and this far after
SUSPECT_CONFIDENCE = 0.25   # person-detection floor, lower than the mover pass
SUSPECT_SAMPLE_INTERVAL = 0.25   # s between buffered frames (~4/s)
SUSPECT_MAX_FRAMES = 24     # hard ceiling on a candidate's buffered window
SUSPECT_JPEG_QUALITY = 80
# Among the people in that window, "closest to the object" decides first, but
# only to within this factor -- between two roughly equally close candidates
# the bigger, sharper crop is the one somebody can actually be identified
# from, and that is the whole point of saving it.
SUSPECT_NEAR_FACTOR = 1.5

# --- stage 2: tunables -----------------------------------------------------
CROP_PAD = 0.5              # pad the crop by 50% of object size; context helps
MIN_CROP = 96               # px, minimum crop side at full resolution

# What we are looking for, and the things that also sit still and would
# otherwise alert. Order matters only in that the first len(PROMPTS) class ids
# are the garbage ones (see garbage_class_ids).
#
# "box" moved OUT of REJECT and "cardboard box" into PROMPTS: a dumped
# cardboard box is the single most common thing on this barangay's footage
# after bagged waste, and while "box" sat on the reject side every one of them
# was thrown out by the very comparison that is supposed to protect against
# look-alikes. What actually needed rejecting was the CONTAINER a box gets
# confused with -- a bin or a bucket that lives there and was never dumped --
# so those are named explicitly instead.
PROMPTS = ["plastic bag", "garbage bag", "trash bag", "pile of trash", "sack",
           "cardboard box", "rice sack", "styrofoam", "pile of plastic bottles"]
REJECT = ["person", "dog", "cat", "motorcycle", "car", "bicycle", "chair",
          "trash can", "garbage bin", "bucket", "potted plant"]

# --- the trained detector outranks the prompt ------------------------------
# Four of the eleven REJECT prompts name things the stock yolov8n already
# detects properly, and that pass is ALREADY running every 0.5s for
# `ignore_boxes`. So for those classes there are two opinions available: a
# zero-shot head reading a word off a crop, and a model trained on 200k labelled
# instances of exactly that object.
#
# Reported from footage: rubbish against a fence came back "person 86%" with the
# nearest person several metres away and no person box anywhere near it. Because
# `classify` only asks whether garbage LED, one confident hallucinated reject
# ended the candidate permanently -- Below Monitoring, every recheck, for the
# whole clip, with the object cue unable to fire and therefore every other piece
# of evidence irrelevant.
#
# So a reject class the trained detector positively says is NOT here does not
# get a vote. This is deliberately asymmetric: absence is claimed only from a
# pass that actually ran (`persons=None` means "no pass", which claims nothing),
# and it never ADDS garbage confidence -- it only declines to accept a veto that
# a better-qualified model contradicts. `dog` and `cat` are not on the list
# because detect_scene does not return them, so nothing here can speak to them.
TRUSTED_REJECTS = {"person": "person", "car": "vehicle",
                   "motorcycle": "vehicle", "bicycle": "vehicle"}


# --- the dumper is not a look-alike ----------------------------------------
# `person` sat in REJECT from the beginning, and on this camera that was the
# single biggest reason real violations never alerted. Measured on the
# barangay's own footage:
#
#     #1 rejected: person 92% (best garbage: garbage bag 13%)
#
# The model found the bag. It also found the person standing over it -- who, on
# a candidate whose owner was latched when it appeared, is the person who PUT
# IT THERE. Their presence is half the offence; it is the thing `person` and
# `left` are worth 0.35 between them for. Letting it also veto the object cue
# meant the clearest possible dumping scene -- somebody beside fresh rubbish --
# scored worse than rubbish nobody was seen to leave.
#
# The reject list is there to answer "that is a dog, not garbage". It was never
# meant to answer "there is a human in this picture", and on a crop padded for
# context there is very often a human in the picture.
#
# So `person` is discounted when this candidate HAS an owner. Narrow on purpose:
#   * only `person` -- a dog, a bin, a chair or a bucket still rejects outright;
#   * only with `had_owner`, which is latched once, at the moment the object
#     appeared, from people within disposal_owner_radius -- a passer-by later
#     cannot grant it;
#   * the owner CAP still holds the status at Monitoring while they stand
#     there, so this cannot alert on somebody holding their own bag; and
#   * a standing person cannot be the candidate in the first place -- the
#     ignore rule drops any blob half inside a person box before it gets an id.
def dumper_rejects(cand):
    """Reject classes this candidate's own owner makes meaningless.

    Returns a frozenset to union with absent_rejects(). Empty when nobody was
    seen beside the object as it appeared, which is the case this must not
    change: rubbish with no owner is judged exactly as before.
    """
    return frozenset({"person"}) if cand.had_owner else frozenset()


# --- a fixture cannot have arrived -----------------------------------------
# "trash can", "garbage bin" and "bucket" were put in REJECT to stop the
# detector firing on a CONTAINER that lives on the pavement and was never
# dumped -- the thing a cardboard box is most often confused with.
#
# They were guarding against something that cannot happen. A permanent fixture
# is learned into the background during warmup and never becomes a candidate at
# all; everything that reaches stage 2 was watched arriving. So on a candidate
# whose arrival has been independently MEASURED -- the spot demonstrably holds
# something it did not hold before, by more than the ground around it changed
# -- "that is a bin that lives here" is a claim our own evidence contradicts.
#
# Measured on the barangay's footage, this was the blocker left after the
# dumper fix:
#
#     #12 rejected: trash can 39% (best garbage: cardboard box 21%)
#
# 21% is comfortably over the floor. A dumped box was being thrown out for
# resembling the container it had been dumped beside.
#
# Gated on arrival_proven, not on mere candidacy, deliberately: the pixel test
# is positive evidence, while "stage 1 raised it" is an inference we have
# already seen go wrong on lighting transients. Where arrival cannot be proven
# the old caution stands.
#
# "potted plant" is the same argument and is deliberately NOT included: it
# never blocked a real detection here, and the narrower change is the one that
# can be defended from the footage.
FIXTURE_REJECTS = frozenset({"trash can", "garbage bin", "bucket"})


def fixture_rejects(cand):
    """Container classes this candidate's proven arrival makes impossible.

    Empty when arrival could not be measured, so nothing changes on footage
    where the reference frame was unusable.
    """
    return FIXTURE_REJECTS if cand.arrival_proven else frozenset()


def absent_rejects(obj, persons=None, vehicles=None, pad=None):
    """Reject class names the trained detector says are not at this object.

    `obj` and the box lists are (x, y, w, h) in FULL-RESOLUTION pixels. A list
    of None means that pass was unavailable, and nothing is claimed for the
    classes it covers.

    `pad` defaults to IGNORE_PAD, resolved at call time because that constant
    belongs with the ignore rule further down and this needs to sit above the
    Candidate class that uses it.
    """
    grown = _padded(obj, IGNORE_PAD if pad is None else pad)
    absent = set()
    for kind, boxes in (("person", persons), ("vehicle", vehicles)):
        if boxes is None:
            continue
        if not any(_coverage(box, grown) > 0 for box in boxes):
            absent |= {name for name, k in TRUSTED_REJECTS.items() if k == kind}
    return frozenset(absent)


# Zero-shot scores run low compared with a fine-tuned model's -- an open-vocab
# head spreads probability over the whole prompt set, so a correct bag often
# lands near 0.2. A "high confidence" intuition calibrated on the merged model
# would reject nearly every true positive here.
CONFIRM_CONF = 0.15

# Floor handed to the MODEL, as opposed to the decision floor above.
#
# These must differ, or the Possible/"Weak match" state cannot exist: passing
# the decision floor to predict() makes it ultralytics' NMS threshold, which
# DISCARDS every box under it. A bag the model scored 0.11 would simply not be
# in the results, and "weak garbage" would be indistinguishable from "nothing
# there". So the model is asked for everything down to this, and the decision
# floor is applied afterwards in classify().
#
# This cannot change any previously-confirmed result. A confirmation needs
# garbage >= CONFIRM_CONF and garbage >= reject; for a newly visible reject
# class to overturn one it would have to score above the garbage and below the
# old floor at the same time, which is a contradiction. What it does add is
# visibility of weak detections, which is the whole point.
WEAK_PREDICT_CONF = 0.05

# The three things stage 2 can conclude. Named rather than inferred at each
# call site, so the live view, the alert path and the tests cannot drift.
CONFIRMED = "confirmed"     # garbage leads AND clears the floor -> Likely
WEAK = "weak"               # garbage leads but is under the floor -> Possible
REJECTED = "rejected"       # a reject class wins -> Below Monitoring


def classify(best_g, best_r, conf=CONFIRM_CONF):
    """Which of CONFIRMED / WEAK / REJECTED this crop's scores mean.

    The split between WEAK and REJECTED is the one that matters. A reject class
    winning is an ANSWER -- the model says that is a dog -- and writing the
    candidate off is correct. Garbage leading but short of the floor is a
    MAYBE: the model does think it is a bag and is merely unsure, which is
    exactly the case that deserves another look as the scene changes rather
    than being buried in Below Monitoring.
    """
    if not best_g[0] or best_g[1] < best_r[1]:
        return REJECTED
    return CONFIRMED if best_g[1] >= conf else WEAK

# --- combining the synonymous prompts --------------------------------------
# PROMPTS is nine near-synonyms for one physical thing: a plastic bag IS a
# garbage bag IS a trash bag. An open-vocabulary head scores every one of them
# independently, so a real bag commonly comes back as three or four weak hits
# on the SAME box -- 0.10 "plastic bag", 0.09 "garbage bag", 0.08 "sack" --
# and taking the single best reports 0.10. The detector was penalising a bag
# for matching the prompt list too well, and that is what put correct distant
# objects under the floor.
#
# So garbage hits on one box are combined with noisy-OR: 1 - prod(1 - s). The
# three above become 0.25, which is what the evidence actually supports.
#
# This is asymmetric -- REJECT keeps using the plain maximum -- and the
# asymmetry is the argument, not an oversight. The garbage prompts are
# different DESCRIPTIONS of a single hypothesis, so independent hits on one
# box corroborate each other. The reject classes are different OBJECTS; "dog"
# and "motorcycle" firing on the same box is the model being confused, not two
# witnesses agreeing, and combining them would manufacture a rejection out of
# uncertainty.
#
# Two guards keep this from being a free score multiplier:
#   * only hits on the SAME object combine (GROUP_IOU), so a bag at one end of
#     the crop cannot borrow evidence from a box at the other;
#   * one prompt cannot corroborate ITSELF -- duplicate classes in a group are
#     deduplicated to their best, so corroboration must come from a DIFFERENT
#     description.
GROUP_IOU = 0.5


def _iou(a, b):
    """Intersection over union of two (x1, y1, x2, y2) boxes."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return (inter / union) if union > 0 else 0.0


def combine_garbage(dets, iou=GROUP_IOU):
    """Merge synonymous garbage hits on one object into a single score.

    `dets` is [(name, score, (x1, y1, x2, y2))]. Returns the best
    (label, score) after combination, or ("", 0.0) for nothing.

    The label is the highest-scoring member of the winning group, not an
    invented one: whatever the model liked best is still the most accurate
    single word for what it saw, and it is what the alert card and the
    description read.

    Greedy, seeded highest-score-first. A proper clustering would be more
    principled, but these are at most a handful of boxes on one small crop
    after NMS has already merged the obvious duplicates.
    """
    ranked = sorted(dets, key=lambda d: -d[1])
    taken = [False] * len(ranked)
    best = ("", 0.0)
    for i, (name, score, box) in enumerate(ranked):
        if taken[i]:
            continue
        taken[i] = True
        # Keyed by CLASS so a prompt cannot corroborate itself; ranked
        # descending, so the first time a class is seen is its best score.
        members = {name: score}
        for j in range(i + 1, len(ranked)):
            other_name, other_score, other_box = ranked[j]
            if taken[j] or _iou(box, other_box) < iou:
                continue
            taken[j] = True
            members.setdefault(other_name, other_score)
        if len(members) == 1:
            # Short-circuited rather than run through 1 - (1 - s), which is
            # not bit-exact in floating point. A lone hit must come back as
            # EXACTLY the score the model gave it, or "a clear match is
            # unaffected by this rule" is only approximately true -- and a
            # claim about not having moved the calibration should not need an
            # epsilon.
            combined = score
        else:
            combined = 1.0
            for member_score in members.values():
                combined *= (1.0 - member_score)
            combined = 1.0 - combined
        if combined > best[1]:
            best = (name, combined)
    return best


DEFAULT_WORLD_MODEL = "yolov8s-worldv2.pt"   # auto-downloads on first use

# --- defaults mirrored in SystemSettings ------------------------------------
# Seconds an object must sit STILL before stage 2 is spent on it.
#
# Was 2, which is barely longer than the fast background model takes to forget
# anything at all -- so a headlight wash, a reflection off wet tarmac or a
# shadow swinging across a shutter cleared the bar before it had even faded,
# and the bag check was being asked about transients. On the barangay's night
# footage that was most of what stage 1 raised.
#
# 12 is long enough that nothing which is merely PASSING survives it, and far
# short of what dumped rubbish sits there for. The cost is twelve seconds of
# delay on a real dump, which is nothing against a violation nobody is going
# to go and clear for hours.
STATIC_SECONDS = 12.0
COOLDOWN_SECONDS = 120      # do not re-alert the same spot within this window
# px at PROC_WIDTH that counts as "the same spot".
#
# Was 60, which is about two bag-widths at this scale and roughly 160px on a
# 2560-wide source -- so a second sack dropped BESIDE the first landed inside
# the first one's place, adopted its alert, and never became a violation of
# its own. Two separate dumps were reported as one.
#
# 30 is about one bag width. It still comfortably absorbs the case this radius
# exists for -- stage 1 forgetting an object and re-finding it a few pixels off
# -- because that re-detection lands within a few px of the original, not a
# whole bag away. Lowering it further starts filing one bag twice when its box
# shifts; this is the floor that keeps both behaviours.
COOLDOWN_DIST = 30


def garbage_class_ids():
    """Class ids that count as garbage, given set_classes(PROMPTS + REJECT)."""
    return set(range(len(PROMPTS)))


class Candidate:
    """A static blob being watched to see whether it is dumped rubbish.

    `box` is (x, y, w, h) in PROC_WIDTH coordinates, not full-resolution ones;
    crop_full_res maps it back when stage 2 needs real pixels.
    """

    def __init__(self, cid, box, t):
        self.id = cid
        self.box = box
        self.first = t
        self.last = t
        self.last_check = None
        self.confirmed = False
        self.rejected = False
        # Garbage BEAT every reject class but fell short of the confidence
        # floor. Distinct from `rejected`, and the distinction is the point:
        # a reject class winning means the model says this is a dog, while a
        # weak lead means it says bag and is merely unsure. The first is an
        # answer, the second is a maybe -- so a weak match keeps its place in
        # the recheck queue and shows as Possible rather than being written
        # off as Below Monitoring.
        self.weak = False
        self.label = ""
        self.score = 0.0
        # --- owner proximity ------------------------------------------------
        # `owner_anchors` are the CURRENT believed positions of the people who
        # were standing over this object when it appeared, carried forward by
        # nearest-centre association (see track_owners). `had_owner` is latched
        # at creation and never changes: if nobody was detected there, this
        # candidate has no owner to wait for and behaves exactly as it did
        # before this rule existed.
        self.had_owner = False
        self.owner_anchors = []
        self.last_owner_near_at = None
        # Was person detection AVAILABLE when this candidate was born? The
        # owner requirement can only be applied to a candidate whose owner
        # could have been seen; with the mover pass switched off or broken,
        # `had_owner` is False for everything and a gate on it would be a gate
        # on nothing, blinding the detector instead of sharpening it.
        self.owner_known = False
        # --- arrival test -----------------------------------------------------
        # `ref_patch` is this patch of ground as it looked before the object
        # turned up, `ref_region` where it was cropped from and `ref_inner` the
        # object's own box within it. None means no reference was available
        # (the detector had only just started), which the test treats as
        # unverifiable rather than as a failure. See ARRIVAL_LOOKBACK.
        self.ref_region = None
        self.ref_patch = None
        self.ref_inner = None
        self.arrival_inner = 0.0
        self.arrival_ring = 0.0
        self.arrival_passes = 0
        self.arrival_checked_at = None
        # How many times stage 2 has looked at this object and NOT said "that
        # is a dog". Two or more means it was still there when the detector
        # came back for another look, which is the `persisted` cue.
        self.confirm_count = 0
        # --- suspect crop ----------------------------------------------------
        # The fast-model foreground blob that overlapped this object as it
        # arrived — whoever or whatever carried it. Fallback for when person
        # detection finds nobody. PROC_WIDTH coordinates, like `box`.
        self.carrier_box = None
        # When this object was first hidden behind a person or vehicle, or None
        # if it is currently visible. An occluded candidate keeps its id and
        # its still-time: the object has not moved, something is in front of it.
        self.occluded_since = None
        # (timestamp, jpeg bytes) over the window around `first`; harvested by
        # the detector, read once if this candidate ever alerts.
        self.suspect_frames = []
        # --- pinned (stage 1 has forgotten it; stage 2 is holding it) -------
        # When the grace lapsed with nothing matching, or None while stage 1 is
        # still raising a blob for this object. A pinned candidate keeps its id,
        # its box and its still-time; what changes is WHO decides it still
        # exists. See PIN_MAX_SECONDS.
        self.pinned_since = None
        self.pin_misses = 0
        self.last_presence_check = None
        # --- the Alert row this candidate is reporting on --------------------
        # Written by the command, never read here -- an opaque primary key and
        # the status last stored against it. One dumped object, one alert,
        # whose status rises from Possible to Likely as the bag check firms up;
        # holding the pk here is what stops a weak lead that later confirms
        # from becoming a second row for the same bag.
        #
        # The row OUTLIVES the candidate, so this is not where it is anchored:
        # when stage 1 forgets an object and raises a fresh candidate for it,
        # the new candidate inherits the pk from SpatialCooldown, which keys it
        # on the PLACE. (Still no Django access in this module: these are an
        # int and a string that mean nothing to it.)
        self.alert_pk = None
        self.alert_level = ""
        # When this candidate's row first reached Monitoring, or None if it is
        # not there now. Reset on every move OFF Monitoring so the clock counts
        # the CURRENT spell, not the total across a status that rose and fell.
        self.monitoring_since = None
        # --- waste-collection window hold -----------------------------------
        # `window_held`: the row is parked at Monitoring ONLY because the bag
        # was set out inside the barangay's collection hours. `hold_until` is
        # the detector-clock instant this candidate may not be dropped before:
        # the window's close plus two presence checks, so a bag set out at
        # 05:00 is still being tracked when 07:00 arrives. The pin's usual
        # 30 minute cap would otherwise forget it two hours too early.
        self.window_held = False
        self.hold_until = None
        self.window_misses = 0
        self.last_window_check = None

    @property
    def center(self):
        x, y, w, h = self.box
        return x + w / 2.0, y + h / 2.0

    def still_for(self, now):
        return now - self.first

    def note_level(self, level, now):
        """Record a status the row has just been written at, for the dwell cue.

        Called by the command after the row is updated -- so the dwell cue
        reads it on the NEXT evaluation, one cycle later. That lag is harmless
        (rechecks are seconds apart) and it is what keeps the cue a pure
        function of recorded state rather than of the decision in progress.
        """
        # NOT while the collection-window hold is what is keeping it at
        # Monitoring. Rubbish set out legally between 04:00 and 07:00 is parked
        # there on purpose, and letting the dwell clock run through that would
        # convert a lawful hold into evidence -- every held bag would come out
        # of the window one level higher than it went in. The clock restarts
        # from zero when the hold ends, so the bag is then judged on its own
        # merits exactly as an unheld one is.
        if level == "monitoring" and not self.window_held:
            if self.monitoring_since is None:
                self.monitoring_since = now
        else:
            self.monitoring_since = None

    @property
    def pinned(self):
        """Is this candidate being held on appearance rather than on motion?"""
        return self.pinned_since is not None

    @property
    def engaged(self):
        """Has stage 2 called this garbage, confidently or weakly?

        The pin test. `rejected` is excluded on purpose: a reject verdict is an
        ANSWER -- the model says that is a dog -- and holding it against the
        background models forgetting it would be spending passes to keep
        re-confirming a no.
        """
        return self.confirmed or self.weak

    def hold_active(self, now):
        """Is a collection-window hold keeping this candidate alive at `now`?"""
        return self.hold_until is not None and now <= self.hold_until

    def pinned_for(self, now):
        """Seconds this candidate has been held with no stage-1 support."""
        return 0.0 if self.pinned_since is None else max(0.0, now - self.pinned_since)

    # ---- owner proximity ---------------------------------------------------

    def claim_owners(self, person_centres, now, radius=OWNER_RADIUS):
        """Latch whoever is standing over this object AS IT APPEARS.

        Called once, at creation. Everything after this only ever follows the
        people named here — that is what makes a later passerby a passerby.
        """
        cx, cy = self.center
        near = [(px, py) for px, py in person_centres
                if float(np.hypot(px - cx, py - cy)) <= radius]
        self.had_owner = bool(near)
        self.owner_anchors = near
        self.last_owner_near_at = now if near else None

    def track_owners(self, person_centres, now, radius=OWNER_RADIUS,
                     gate=OWNER_TRACK_GATE):
        """Carry the owners forward one mover pass, and note if any is still near.

        These detections have no track ids, so each anchor re-associates to the
        nearest person within `gate`; an anchor with nobody in reach has left
        the frame and is dropped. An owner who merely stepped back is still
        followed — they just stop refreshing `last_owner_near_at` once they are
        further than `radius` from the object, which is what starts the
        walked-away clock.
        """
        if not self.had_owner:
            return
        moved = []
        for ax, ay in self.owner_anchors:
            best, best_d = None, float(gate)
            for px, py in person_centres:
                dist = float(np.hypot(px - ax, py - ay))
                if dist <= best_d:
                    best, best_d = (px, py), dist
            if best is not None:
                moved.append(best)
        self.owner_anchors = moved
        cx, cy = self.center
        if any(float(np.hypot(px - cx, py - cy)) <= radius for px, py in moved):
            self.last_owner_near_at = now

    def held_by_owner(self, now, away_seconds=OWNER_AWAY_SECONDS,
                      max_hold=OWNER_MAX_HOLD):
        """Is somebody still standing over this, so it is not yet abandoned?

        Three ways to answer no, in order: nobody ever owned it (so there is
        nothing to wait for), the hold has run its full course, or the owner
        has been away long enough. The max hold exists because a vendor who
        never leaves their pavement would otherwise suppress the alert for the
        whole shift — at that point the rubbish is still dumped rubbish.
        """
        if not self.had_owner or self.last_owner_near_at is None:
            return False
        if self.still_for(now) >= max_hold:
            return False
        return (now - self.last_owner_near_at) < away_seconds

    def owner_away_for(self, now):
        """Seconds since an owner was last near, or None if there was no owner."""
        if not self.had_owner or self.last_owner_near_at is None:
            return None
        return max(0.0, now - self.last_owner_near_at)

    # ---- arrival test ------------------------------------------------------

    def arrival_verdict(self, min_change=ARRIVAL_MIN_CHANGE,
                        min_margin=ARRIVAL_MIN_MARGIN):
        """Does this spot look different from before, more than its surroundings?

        One look. `arrival_proven` is what the cue reads; this is the
        measurement that feeds it, split out so the thresholds can be tested
        on their own.
        """
        return (self.arrival_inner >= min_change
                and self.arrival_inner - self.arrival_ring >= min_margin)

    @property
    def arrival_proven(self):
        """Has this spot been SHOWN to hold something that was not here before?

        False when there was no reference frame to compare against, which is a
        different thing from a failed comparison -- the question could not be
        asked. As a gate that distinction had to read as "do not block"; as a
        CUE it reads as "no points", which is the honest handling: an
        unanswerable question is not evidence. The object cue and the owner
        cues can still carry such a candidate to Possible on their own.
        """
        return (self.ref_patch is not None
                and self.arrival_passes >= ARRIVAL_CONFIRM_LOOKS)


# How long a row sits on the Monitoring watchlist before the dwell cue fires.
#
# Asked for off the footage: "when the monitoring reached 7-10 seconds it will
# make as possible". 8 is the middle of that range. It is one constant rather
# than a Settings column because it is a scoring weight's partner, not an
# operator knob -- moving it changes what Possible MEANS, which is a decision
# for the cue table and not for a shift.
#
# Measured from the moment the row reached Monitoring, not from when the object
# arrived, so it answers "this has been sitting unattended and nobody has dealt
# with it" rather than restating the still-timer that already gated the check.
MONITORING_DWELL_SECONDS = 8.0


def behaviour_only(cand, confirmed, cues):
    """Is this the weak-object, full-behaviour case?

    True when the bag check LED every look-alike but fell short of the floor,
    and everything else is there: something arrived at this spot, a person was
    beside it, and they left. Admitted and capped at Possible by the caller.

    `cand.weak` and not `rejected` is the precise condition -- a REJECTED
    candidate is one the model gave a positive answer about ("that is a dog"),
    and no amount of behaviour should talk us past that.
    """
    from core.vision import scoring          # local: keeps this module importable alone
    return (not confirmed and cand.weak and not cand.rejected
            and scoring.DISPOSAL_BEHAVIOUR_CUES <= cues)


def cues_for(cand, confirmed, now, away_seconds=OWNER_AWAY_SECONDS,
             dwell_seconds=MONITORING_DWELL_SECONDS):
    """Which disposal cues this candidate fires, as a set of names.

    Pure: a candidate, a verdict and a clock in, cue names out. No weights, no
    thresholds beyond the ones already on the candidate, and no Django -- the
    arithmetic lives in scoring.DISPOSAL_WEIGHTS and the decision in
    scoring.Score, exactly as it does for the other four detectors.

    `confirmed` is stage 2's CONFIRMED verdict (garbage led every reject class
    AND cleared the floor), which is the gate cue. A weak lead fires nothing
    at all here -- it leaves the candidate with no gate, so `Score` reports
    NONE and nothing is written. That is the floor still doing its job.
    """
    cues = set()
    if confirmed:
        cues.add("object")
    if cand.arrival_proven:
        cues.add("arrival")
    if cand.had_owner:
        cues.add("person")
        away = cand.owner_away_for(now)
        # The ordinance's actual test. `away_seconds` is the same setting the
        # old owner HOLD used, so "long enough to count as walked away" means
        # one thing in both places.
        if away is not None and away >= away_seconds:
            cues.add("left")
    if cand.confirm_count >= 2:
        cues.add("persisted")
    # Time on the watchlist. CONDITIONAL on `object`, so this escalates a row
    # that already means something and cannot conjure one out of waiting.
    if (cand.monitoring_since is not None
            and now - cand.monitoring_since >= dwell_seconds):
        cues.add("dwell")
    return cues


# --- ignoring people and vehicles ------------------------------------------
# Stage 1 asks "did something arrive and stop moving?", and a PERSON WHO STOPS
# answers yes. Measured on real footage: of 37 candidates in one 90s window, 19
# were people and 4 were cars -- 62% of everything stage 2 had to throw out.
# The dual-background trick filters MOTION, and someone standing still is not
# moving, so no amount of tuning the background models fixes this.
#
# So the caller may hand `update()` the boxes of anything it already knows is a
# person or a vehicle, and a blob sitting inside one never becomes a candidate.
# Those boxes come from the stock yolov8n the rest of the system already runs
# (recognition.detect_scene), which knows person/car/motorcycle/bus/truck and
# bicycle -- no new model, no new weights.
IGNORE_PAD = 0.08           # grow the box 8%: subtraction blobs overrun edges
IGNORE_COVERAGE = 0.5       # blob counts as "part of" a box at this overlap

# How long a candidate may stay HIDDEN behind a person or vehicle and still be
# the same candidate.
#
# Without this the two rules above combine into a hole that defeats the whole
# detector on a busy road: a passing motorcycle covers the bag, the ignore rule
# deletes its blob for those frames, nothing matches the candidate, and
# `candidate_grace` drops it two seconds later. The bag reappears as a NEW id
# with a fresh timer, so on a road with traffic every few seconds it can sit
# there for an hour and never accumulate the five still seconds it needs.
# Measured on the sample footage: 1262 blob-frames suppressed as people or
# vehicles in 300 seconds -- about four a second.
#
# The fix is to stop treating HIDDEN as GONE. An occluded candidate keeps its
# id and its still-time, because the object has not moved -- something is
# standing in front of it. Capped, because an object hidden behind a PARKED
# vehicle is genuinely unobservable and a hold with no limit would let the
# detector claim still-time it cannot see; and because a bag picked up while
# out of sight must eventually be forgotten. Stage 2 is the backstop either
# way: it runs on the live frame, so a bag that has actually gone cannot
# confirm.
OCCLUSION_HOLD_SECONDS = 20.0

# --- holding a candidate stage 1 has FORGOTTEN -----------------------------
# The other half of the hole OCCLUSION_HOLD_SECONDS closes, and the more
# fundamental one: a candidate can lose its blob with nothing in front of it
# and nothing having moved, because the SLOW model eventually learns the object
# too. LONG_LR only makes that slow, not impossible -- at ~0.0005 a frame the
# bag crosses into the long model's background in a minute or two, `static`
# goes empty, no blob matches, and `candidate_grace` drops the candidate two
# seconds later. The rubbish is still lying there; the detector has simply
# stopped being able to see it, because the bag IS the scene now.
#
# Stage 1 cannot be fixed to avoid this -- it is a change detector, and after
# long enough there is no change. What CAN be fixed is the assumption that
# stage 1 is the only thing allowed to keep a candidate alive. Stage 2 is an
# APPEARANCE check on the live frame, so it still sees a bag the background
# models have forgotten entirely.
#
# So a candidate stage 2 has already engaged with -- confirmed, or weak and
# still in the recheck queue -- is PINNED at its last known box when its grace
# lapses, instead of being dropped, and from then on its presence is decided by
# re-asking stage 2 rather than by whether stage 1 still raises a blob.
#
# Only engaged candidates, deliberately: pinning every transient blob stage 1
# forgot would spend a model pass indefinitely on noise the detector never had
# a reason to believe in. Something stage 2 called a bag has earned the pass.
PIN_MAX_SECONDS = 1800.0    # 30 min cap: an unobservable claim must expire
PRESENCE_RECHECK_SECONDS = 15.0     # how often a pinned candidate is re-asked
# Consecutive "not garbage" answers before a pin is released. More than one,
# because a single crop can be spoiled by somebody walking through it at the
# wrong instant, and the cost of being wrong is asymmetric: a premature release
# restarts the whole still-timer, while one extra 15s hold costs one pass.
PIN_MISS_LIMIT = 2


# --- waste-collection window ------------------------------------------------
# Barangay Tetuan only lets garbage be set out for collection between 04:00 and
# 07:00, Monday to Saturday; there is no collection on Sunday. A bag set out
# inside that window is allowed, so it is parked at Monitoring instead of
# alerting, and re-checked once the window closes. This class is only the
# calendar arithmetic -- WHICH clock supplies `when` (wall clock live, --clock
# for a file) is the command's business, as it is for the holdup time block.
DEFAULT_COLLECTION_DAYS = (0, 1, 2, 3, 4, 5)        # Monday = 0 ... Saturday = 5


def parse_days(text):
    """"0,1,2" -> (0, 1, 2). Monday = 0, Sunday = 6. Raises ValueError if malformed."""
    if isinstance(text, (tuple, list, set, frozenset)):
        raw = list(text)
    else:
        raw = [p for p in str(text or "").replace(" ", "").split(",") if p != ""]
    try:
        days = sorted({int(p) for p in raw})
    except ValueError:
        raise ValueError(f"Days must be numbers 0-6 separated by commas, got {text!r}.")
    if any(d < 0 or d > 6 for d in days):
        raise ValueError(f"Days must be 0 (Monday) to 6 (Sunday), got {text!r}.")
    return tuple(days)


def _clock12(t):
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


class CollectionWindow:
    """The hours (and weekdays) rubbish may be put out for collection.

    A window that does not span midnight: `start` < `end`. Anything else is
    refused rather than guessed at -- the settings serializer enforces the
    same, so a bad row can only arrive by editing the database by hand.
    """

    def __init__(self, start, end, days=DEFAULT_COLLECTION_DAYS):
        if not start < end:
            raise ValueError("The collection window must end after it starts.")
        self.start = start
        self.end = end
        self.days = frozenset(parse_days(days))

    @classmethod
    def from_settings(cls, cfg):
        """The window in `cfg`, or None when the rule is switched off."""
        if not getattr(cfg, "disposal_collection_enabled", False):
            return None
        return cls(cfg.disposal_collection_start, cfg.disposal_collection_end,
                   cfg.disposal_collection_days)

    def contains(self, when):
        """Is `when` (a datetime, local time) inside the window on a collection day?"""
        return when.weekday() in self.days and self.start <= when.time() < self.end

    def seconds_until_close(self, when):
        """Seconds from `when` to today's closing time (0 once it has passed)."""
        close = datetime.datetime.combine(when.date(), self.end)
        return max(0.0, (close - when.replace(tzinfo=None)).total_seconds())

    def label(self):
        """"4:00-7:00 AM" -- the form a reviewer reads on the alert."""
        a, b = _clock12(self.start), _clock12(self.end)
        if a[-2:] == b[-2:]:
            a = a[:-3]
        return f"{a}–{b}"


def _coverage(blob, box):
    """Share of `blob`'s area lying inside `box`. Both are (x, y, w, h)."""
    bx, by, bw, bh = blob
    ox, oy, ow, oh = box
    ix = max(0.0, min(bx + bw, ox + ow) - max(bx, ox))
    iy = max(0.0, min(by + bh, oy + oh) - max(by, oy))
    area = float(bw) * float(bh)
    return (ix * iy / area) if area > 0 else 0.0


def _padded(box, pad=IGNORE_PAD):
    x, y, w, h = box
    return (x - w * pad, y - h * pad, w * (1 + 2 * pad), h * (1 + 2 * pad))


class Area:
    """An OPTIONAL polygon limiting where disposal is detected.

    Same stored shape as parking's no-parking area (Camera.edges, a "zone"
    spec), but a much simpler rule: parking asks what SHARE of a vehicle's
    footprint lies inside, because half a truck over the line is the thing it
    judges. A dumped sack has no such question — it is either resting in the
    watched spot or it is not — so this is one point-in-polygon test and no
    hysteresis.

    The anchor is the blob's BOTTOM-CENTRE, where the object meets the ground,
    for the same reason parking uses a ground point: the top of a tall bag
    leans into the frame well behind where it actually sits, and judging it by
    its centre would place a bag at the far kerb inside an area drawn on the
    near one.

    Points are normalised 0-1, so one drawn area survives every resolution this
    pipeline uses — stage 1 at PROC_WIDTH, evidence at full res, the live view
    at 960.
    """

    def __init__(self, points_norm):
        if len(points_norm) < 3:
            raise ValueError("A disposal area needs at least 3 points.")
        self.points_norm = [(float(x), float(y)) for x, y in points_norm]
        self._poly = None
        self._shape = None

    def _polygon(self, frame_shape):
        height, width = frame_shape[:2]
        if self._shape != (height, width):
            self._poly = np.array([[x * width, y * height] for x, y in self.points_norm],
                                  dtype=np.float32)
            self._shape = (height, width)
        return self._poly

    def contains_box(self, box, frame_shape):
        """Is this blob's ground point inside the area?"""
        x, y, w, h = box
        point = (float(x + w / 2.0), float(y + h))
        return cv2.pointPolygonTest(self._polygon(frame_shape), point, False) >= 0


class StaticObjectDetector:
    """Stage 1. Feeds frames in, gets static-object candidates out.

    Holds the two background models and the candidate list, so the management
    command stays glue and this stays testable. Call `update(frame, now)` once
    per processed frame; it returns the live candidate list and sets
    `self.warming` while the background is still being learned.
    """

    def __init__(self, *, proc_width=PROC_WIDTH, warmup_seconds=WARMUP_SECONDS,
                 min_area_frac=MIN_AREA_FRAC, max_area_frac=MAX_AREA_FRAC,
                 match_dist=MATCH_DIST, candidate_grace=CANDIDATE_GRACE,
                 area=None, owner_radius=OWNER_RADIUS, suspect_window=True,
                 occlusion_hold=OCCLUSION_HOLD_SECONDS,
                 pin_max_seconds=PIN_MAX_SECONDS, pin_miss_limit=PIN_MISS_LIMIT,
                 arrival_lookback=ARRIVAL_LOOKBACK, arrival_pad=ARRIVAL_PAD,
                 arrival_interval=ARRIVAL_INTERVAL,
                 arrival_ref_seconds=ARRIVAL_REF_SECONDS,
                 owner_lookback=OWNER_LOOKBACK):
        self.area = area
        self.proc_width = proc_width
        self.warmup_seconds = warmup_seconds
        self.min_area_frac = min_area_frac
        self.max_area_frac = max_area_frac
        self.match_dist = match_dist
        self.candidate_grace = candidate_grace
        self.owner_radius = owner_radius
        self.occlusion_hold = occlusion_hold
        self.pin_max_seconds = pin_max_seconds
        self.pin_miss_limit = pin_miss_limit
        self.arrival_lookback = arrival_lookback
        self.arrival_pad = arrival_pad
        self.arrival_interval = arrival_interval
        self.arrival_ref_seconds = arrival_ref_seconds
        self.owner_lookback = owner_lookback
        # Off for the stage-1 unit tests, which have no use for JPEG encoding
        # a synthetic rectangle four times a second.
        self.lookback = SuspectLookback() if suspect_window else None

        # Same history/varThreshold for both; the learning RATE is what makes
        # one slow and one fast, so keeping everything else identical means the
        # difference between the masks is motion and nothing else.
        self._bg_long = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=25, detectShadows=True)
        self._bg_short = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=25, detectShadows=True)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

        self.candidates = []
        self.ignored = []       # blobs suppressed as a person/vehicle, for the live view
        self.warming = True
        self.scale = 1.0
        self.mask = None
        self.fg_short = None    # fast-model foreground, for the carrier fallback
        self._next_id = 1
        self._t0 = None
        # [(t, grey frame)] and [(t, [centre, ...])], both trimmed every frame.
        # Greyscale and already downscaled to PROC_WIDTH, so a 960x540 frame is
        # ~0.5 MB and the ring holds ARRIVAL_REF_SECONDS / ARRIVAL_SAMPLE_INTERVAL
        # of them -- about 10 MB, which is why the window is only a little
        # longer than the lookback it has to cover rather than generous.
        self._refs = []
        self._person_seen = []

    def update(self, frame, now=None, ignore_boxes=None, person_boxes=None):
        """Process one frame. Returns the current candidate list (empty while warming).

        `ignore_boxes` is an optional list of (x, y, w, h) in FULL-RESOLUTION
        frame coordinates — people and vehicles the caller has already
        identified. Blobs sitting inside one never become candidates. Full-res
        rather than PROC_WIDTH because that is what a detector hands back, and
        converting here keeps the one scale factor in one place.

        `person_boxes` is the PERSON subset of those same detections, in the
        same coordinates and from the same pass — passed separately because the
        owner rule has to distinguish a person from a parked car, while the
        ignore rule does not care which it is. Omitting it disables the owner
        hold entirely, which is the documented no-person behaviour.
        """
        if now is None:
            now = time.time()
        if self._t0 is None:
            self._t0 = now
        self.warming = (now - self._t0) < self.warmup_seconds

        height, width = frame.shape[:2]
        self.scale = self.proc_width / width if width > self.proc_width else 1.0
        if self.scale < 1.0:
            small = cv2.resize(frame, (int(width * self.scale), int(height * self.scale)))
        else:
            small = frame.copy()
        blur = cv2.GaussianBlur(small, (5, 5), 0)
        grey = cv2.cvtColor(blur, cv2.COLOR_BGR2GRAY)

        # Kept BEFORE the warming return, for the same reason the suspect
        # lookback is: an object dumped in the first second after warmup still
        # needs a picture of the ground it landed on, and the only frames that
        # old are warmup frames. They are also the best references this
        # detector ever gets -- warmup is by definition the empty scene.
        if not self._refs or now - self._refs[-1][0] >= ARRIVAL_SAMPLE_INTERVAL:
            self._refs.append((now, grey))
            cutoff = now - self.arrival_ref_seconds
            self._refs = [ref for ref in self._refs if ref[0] >= cutoff]

        # While warming, BOTH models learn fast -- the point is to converge on
        # the empty scene quickly. The slow/fast split only starts afterwards,
        # which is why a bag already lying there when the detector starts is
        # learned as background and never alerts (a stated limitation).
        long_lr = WARMUP_LR if self.warming else LONG_LR
        short_lr = WARMUP_LR if self.warming else SHORT_LR
        m_long = self._bg_long.apply(blur, learningRate=long_lr)
        m_short = self._bg_short.apply(blur, learningRate=short_lr)
        self.small = small

        # Buffered BEFORE the warming return: a bag dumped in the first second
        # after warmup still needs the frames from just before it landed, and
        # those are warmup frames.
        if self.lookback is not None:
            self.lookback.add(frame, now)

        if self.warming:
            self.mask = None
            return []

        fg_long = np.where(m_long > FG_THRESHOLD, 255, 0).astype(np.uint8)
        fg_short = np.where(m_short > FG_THRESHOLD, 255, 0).astype(np.uint8)
        # The trick, in one line: present in the slow model, already forgotten
        # by the fast one -> it arrived, and it has stopped moving.
        static = cv2.bitwise_and(fg_long, cv2.bitwise_not(fg_short))
        static = cv2.morphologyEx(static, cv2.MORPH_OPEN, self._kernel)
        static = cv2.morphologyEx(static, cv2.MORPH_CLOSE, self._kernel, iterations=2)
        self.mask = static
        # Kept for the carrier fallback: whatever was still MOVING as the
        # object landed is, by elimination, what brought it there.
        self.fg_short = fg_short

        contours, _ = cv2.findContours(static, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        img_area = small.shape[0] * small.shape[1]
        blobs = [cv2.boundingRect(c) for c in contours
                 if self.min_area_frac * img_area <= cv2.contourArea(c)
                 <= self.max_area_frac * img_area]
        if self.area is not None:
            # Filtered HERE, before _match, so an out-of-area blob never
            # becomes a candidate at all. Dropping them later would still mint
            # ids, accrue still-time and churn the live view — the flood this
            # is meant to stop happens at creation, not at confirmation.
            blobs = [b for b in blobs if self.area.contains_box(b, small.shape)]

        scaled = []
        if ignore_boxes:
            # Same reason, same place: a person who stands still must never get
            # an id, not merely fail the bag check 5 seconds later.
            scaled = [_padded(tuple(v * self.scale for v in box)) for box in ignore_boxes]
            self.ignored = [b for b in blobs
                            if any(_coverage(b, o) >= IGNORE_COVERAGE for o in scaled)]
            blobs = [b for b in blobs if b not in self.ignored]
        else:
            self.ignored = []

        # The same boxes go to _match, so it can tell a candidate that is
        # HIDDEN behind one from a candidate that has actually been taken away.
        born = self._match(blobs, now, scaled)
        self._owners_and_suspects(born, now, frame, person_boxes)
        self._look_for_arrival(grey, now)
        return self.candidates

    # ---- owner proximity + suspect window ----------------------------------

    def _owners_and_suspects(self, born, now, frame, person_boxes):
        """Latch owners on new candidates, carry them forward on the rest.

        Both halves run off the ONE person list the caller already had, so this
        whole rule costs no inference of its own.
        """
        centres = [((x + w / 2.0) * self.scale, (y + h / 2.0) * self.scale)
                   for x, y, w, h in (person_boxes or [])]
        if person_boxes is not None:
            self._person_seen.append((now, centres))
        cutoff = now - self.owner_lookback
        self._person_seen = [seen for seen in self._person_seen if seen[0] >= cutoff]
        # Everyone seen in the last few seconds, not just this frame -- the
        # carrier is usually already walking away by the time their load shows
        # up as static foreground. See OWNER_LOOKBACK.
        recent = [centre for _t, seen in self._person_seen for centre in seen]
        for cand in born:
            cand.owner_known = person_boxes is not None
            cand.claim_owners(recent, now, self.owner_radius)
            cand.ref_region, cand.ref_patch, cand.ref_inner =                 self._reference_patch(cand.box, now)
            cand.carrier_box = self._carrier_box(cand.box)
            if self.lookback is not None:
                # The pre-window is already gone by the time anything is known
                # about this candidate, so it is copied across now; the post
                # window accrues below while the candidate is young.
                cand.suspect_frames = list(
                    self.lookback.window(cand.first - SUSPECT_PRE_SECONDS, cand.first))
        for cand in self.candidates:
            if cand not in born:
                cand.track_owners(centres, now, self.owner_radius)
            if (self.lookback is not None
                    and len(cand.suspect_frames) < SUSPECT_MAX_FRAMES
                    and now - cand.first <= SUSPECT_POST_SECONDS):
                latest = self.lookback.window(cand.first, now)
                have = {t for t, _j in cand.suspect_frames}
                cand.suspect_frames += [(t, j) for t, j in latest if t not in have]

    # ---- arrival test ------------------------------------------------------

    def _reference_patch(self, box, now):
        """How this spot looked BEFORE the object turned up.

        Returns (region, patch, inner) in PROC_WIDTH coordinates, or three
        Nones when there is no usable reference.
        """
        if not self._refs:
            return None, None, None
        target = now - self.arrival_lookback
        chosen = None
        for t, grey in self._refs:
            if t > target:
                break
            chosen = grey          # the latest frame at or before the target
        if chosen is None:
            # Nothing that old yet: the run has only just started. The oldest
            # frame there IS still a frame from before this object arrived,
            # which is the only property the test needs -- it is merely a
            # closer one. Using it is what keeps the seconds right after
            # warmup from being a blind spot.
            chosen = self._refs[0][1]
        region = _region(box, chosen.shape, self.arrival_pad)
        if region is None:
            return None, None, None
        rx, ry, rw, rh = region
        x, y, w, h = box
        ix = max(0, min(int(round(x)) - rx, rw - 1))
        iy = max(0, min(int(round(y)) - ry, rh - 1))
        inner = (ix, iy, max(1, min(int(round(w)), rw - ix)),
                 max(1, min(int(round(h)), rh - iy)))
        return region, chosen[ry:ry + rh, rx:rx + rw].copy(), inner

    def _look_for_arrival(self, grey, now):
        """Re-ask every unproven candidate whether its spot still looks changed.

        Throttled to `arrival_interval`, and the pass counter is CONSECUTIVE --
        a failing look sends it back to zero. That is the difference between
        "something arrived here" and "something flickered here once": a
        transient cannot bank passes early and coast on them to the check,
        which is exactly what a single test taken at birth would let it do.
        """
        for cand in self.candidates:
            if cand.ref_patch is None:
                continue
            # Hidden is not gone, here as everywhere else in this module. The
            # patch of a person standing over the object says nothing about
            # whether the object arrived, and letting that answer reset the
            # streak would cost a bag on a busy road its progress every time
            # traffic went past -- the same hole OCCLUSION_HOLD_SECONDS closes
            # for the still-timer.
            if cand.occluded_since is not None:
                continue
            if (cand.arrival_checked_at is not None
                    and now - cand.arrival_checked_at < self.arrival_interval):
                continue
            cand.arrival_checked_at = now
            rx, ry, rw, rh = cand.ref_region
            cand.arrival_inner, cand.arrival_ring = arrival_change(
                cand.ref_patch, grey[ry:ry + rh, rx:rx + rw], cand.ref_inner)
            if cand.arrival_verdict():
                cand.arrival_passes += 1
            else:
                cand.arrival_passes = 0

    def _carrier_box(self, cand_box):
        """Largest fast-foreground blob overlapping this object as it appeared.

        Fallback for when person detection finds nobody — a carrier who is
        turned away, cropped by the frame edge or too small for the person
        model is still a moving shape that was right there, and showing the
        tanod that shape beats showing them nothing. PROC_WIDTH coordinates.
        """
        if self.fg_short is None:
            return None
        contours, _ = cv2.findContours(self.fg_short, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        best, best_area = None, 0.0
        for contour in contours:
            box = cv2.boundingRect(contour)
            if _coverage(cand_box, _padded(box)) <= 0:
                continue
            area = float(box[2]) * box[3]
            if area > best_area:
                best, best_area = box, area
        return best

    def _match(self, blobs, now, occluders=None):
        """Nearest-centre association of blobs to existing candidates.

        Deliberately simple: a dumped bag does not move, so there is no motion
        model worth running. A candidate that goes unseen longer than
        `candidate_grace` is dropped, which is what stops a flickering blob
        from accruing still-time forever.

        Returns the candidates CREATED on this frame, so the caller can latch
        their owners at the one moment that matters.
        """
        born = []
        claimed = set()
        for blob in blobs:
            cx, cy = blob[0] + blob[2] / 2.0, blob[1] + blob[3] / 2.0
            best, best_d = None, self.match_dist
            for cand in self.candidates:
                if cand.id in claimed:
                    continue
                dist = float(np.hypot(cx - cand.center[0], cy - cand.center[1]))
                if dist < best_d:
                    best, best_d = cand, dist
            if best is not None:
                best.box, best.last = blob, now
                claimed.add(best.id)
            else:
                fresh = Candidate(self._next_id, blob, now)
                self.candidates.append(fresh)
                born.append(fresh)
                claimed.add(self._next_id)
                self._next_id += 1

        # Hidden is not gone. A candidate nothing matched this frame, whose
        # place is covered by a person or vehicle box, is being stood in front
        # of -- so its `last` is refreshed (keeping it inside the grace window)
        # while `first` is left alone, which is what preserves the still-time
        # the whole rule is built on. Without this a passing motorcycle costs
        # the bag its id and restarts its timer; see OCCLUSION_HOLD_SECONDS.
        for cand in self.candidates:
            if cand.id in claimed:
                cand.occluded_since = None
                # Stage 1 can see it again, so stage 2 no longer has to hold
                # it. Identity and `confirmed` are kept rather than reset: the
                # overwhelmingly common reason a blob returns is that this same
                # object flickered back (a partial re-absorption, a shadow
                # shifting), and treating that as a NEW object would restart
                # the still-timer and re-alert. A genuinely different object
                # dumped on the same spot inherits the old candidate, which the
                # SpatialCooldown was already the guard for.
                cand.pinned_since = None
                cand.pin_misses = 0
                continue
            if occluders and any(_coverage(cand.box, o) >= IGNORE_COVERAGE
                                 for o in occluders):
                if cand.occluded_since is None:
                    cand.occluded_since = now
                if now - cand.occluded_since <= self.occlusion_hold:
                    cand.last = now
            else:
                cand.occluded_since = None

        # Forgotten is not gone either -- see PIN_MAX_SECONDS. A candidate
        # past its grace is dropped UNLESS stage 2 has already called it
        # garbage, in which case it is pinned and its presence becomes stage
        # 2's question. The cap is what stops a pin from being an unfalsifiable
        # claim: by then the object has either been re-confirmed many times
        # over or the pin is wrong, and a wrong pin must expire on its own.
        kept = []
        for cand in self.candidates:
            if now - cand.last <= self.candidate_grace:
                kept.append(cand)
                continue
            if cand.pinned_since is None and cand.engaged:
                cand.pinned_since = now
            # A window-held candidate outlives the usual cap: it has to still
            # be here when the collection window closes. See hold_until.
            if cand.pinned and (cand.pinned_for(now) <= self.pin_max_seconds
                                or cand.hold_active(now)):
                kept.append(cand)
        self.candidates = kept
        return born

    def due_for_check(self, now, static_seconds, recheck_seconds=RECHECK_SECONDS,
                      owner_away_seconds=OWNER_AWAY_SECONDS,
                      owner_max_hold=OWNER_MAX_HOLD):
        """Candidates that have been still long enough and are not already confirmed.

        A rejected candidate is re-offered after `recheck_seconds` rather than
        being written off: stage 2 sees one crop at one moment, and a bag that
        was half-occluded by a passing person when first checked deserves a
        second look.

        A candidate whose owner is still standing over it is withheld — not
        rejected. It costs nothing to wait (no model pass is spent on it) and
        it is the difference between "a sack is on the pavement" and "a sack
        was LEFT on the pavement", which is the thing the ordinance names.
        """
        out = []
        for cand in self.candidates:
            if cand.confirmed or cand.still_for(now) < static_seconds:
                continue
            # A pinned candidate is stage 2's to re-ask, via due_for_presence.
            # Offering it here as well would spend two passes on one object and
            # let the two answers disagree about the same crop.
            if cand.pinned:
                continue
            # Never spend a model pass on a crop of the thing standing in
            # front of the object. It would come back "motorcycle", mark the
            # candidate rejected and start a recheck cooldown -- a wrong
            # answer about a question nobody asked. It keeps its still-time
            # and is checked the moment it is visible again.
            if cand.occluded_since is not None:
                continue
            if cand.held_by_owner(now, owner_away_seconds, owner_max_hold):
                continue
            if cand.last_check is not None and now - cand.last_check < recheck_seconds:
                continue
            out.append(cand)
        return out

    # ---- presence of a pinned candidate ------------------------------------

    def due_for_presence(self, now, interval=PRESENCE_RECHECK_SECONDS):
        """Pinned candidates due to be re-asked "are you still there?".

        Separate from `due_for_check` because it is a different question about
        a different kind of candidate: that one asks whether a static blob is
        garbage, this one asks whether garbage already identified is still on
        the pavement now that stage 1 can no longer see it.

        A newly pinned candidate is due immediately -- the pin is a claim about
        the present, and the first thing to do with it is test it.

        Occluded candidates are skipped for the same reason `due_for_check`
        skips them: the crop would be of whatever is standing in front of the
        object, so the answer would be about the wrong thing. It costs a miss
        the candidate did not earn, and two of those release the pin.
        """
        out = []
        for cand in self.candidates:
            if not cand.pinned or cand.occluded_since is not None:
                continue
            if (cand.last_presence_check is not None
                    and now - cand.last_presence_check < interval):
                continue
            out.append(cand)
        return out

    def due_for_window_recheck(self, now, interval=PRESENCE_RECHECK_SECONDS):
        """Window-held candidates that stage 1 can still see, due their look.

        The window closing is a clock event, not something the picture shows:
        a CONFIRMED candidate is never offered to stage 2 again and, while the
        blob is still visible, is not pinned either, so nothing would ask it
        the question that raises its row. Pinned candidates are left out --
        due_for_presence already re-asks them, and a second pass on the same
        crop would only let the two answers disagree.
        """
        out = []
        for cand in self.candidates:
            if not cand.window_held or cand.pinned or cand.occluded_since is not None:
                continue
            if (cand.last_window_check is not None
                    and now - cand.last_window_check < interval):
                continue
            out.append(cand)
        return out

    def record_window_recheck(self, cand, present, now):
        """One post-close answer. Returns True while the hold should continue.

        A candidate that is NOT garbage on `pin_miss_limit` consecutive looks
        is let go and its row stays at Monitoring: the bag was collected (or
        was never one), and raising a row for rubbish that is no longer there
        would be an alert nobody can act on.
        """
        cand.last_window_check = now
        if present:
            cand.window_misses = 0
            return True
        cand.window_misses += 1
        if cand.window_misses >= self.pin_miss_limit:
            cand.window_held = False
            cand.hold_until = None
            return False
        return True

    def record_presence(self, cand, present, now):
        """Record one presence answer for a pinned candidate.

        Returns True if it is still being tracked, False if the pin was
        released and the candidate dropped. `present` is "stage 2 did not call
        this a look-alike" rather than "stage 2 confirmed it": a bag the model
        is merely unsure about is still a bag lying there, and demanding a
        confident answer every 15 seconds would release pins on exactly the
        marginal objects the WEAK state exists to keep.
        """
        cand.last_presence_check = now
        if present:
            cand.pin_misses = 0
            return True
        cand.pin_misses += 1
        if cand.pin_misses >= self.pin_miss_limit:
            # Removed IN PLACE rather than by rebinding the list. update()
            # hands the caller this very list, and the caller goes on to draw
            # and publish from it after calling this -- a rebind would leave it
            # holding the old list and showing a released object for one more
            # frame. Identity comparison because Candidate defines no __eq__.
            self.candidates.remove(cand)
            return False
        return True

    def held(self, now, static_seconds, owner_away_seconds=OWNER_AWAY_SECONDS,
             owner_max_hold=OWNER_MAX_HOLD):
        """Candidates old enough to check but waiting on their owner to leave.

        Only for the live view: an operator watching a bag sit there with no
        alert needs to see WHY, or a working hold is indistinguishable from a
        detector that has stopped.
        """
        return [c for c in self.candidates
                if not c.confirmed and c.still_for(now) >= static_seconds
                and c.held_by_owner(now, owner_away_seconds, owner_max_hold)]


# --- keeping bystanders out of the crop -------------------------------------
# The padding below is what makes a distant bag legible, and it is also how a
# bystander gets classified instead of the rubbish.
#
# Reported from footage twice: rubbish against a fence with two people standing
# a metre away came back "person 88%" and sat at Below Monitoring for the whole
# clip. The reject side of `confirm` is a plain maximum, so one confident person
# detection beats any combination of garbage prompts, and the object cue can
# never fire. On this barangay's camera that is not an edge case -- rubbish is
# routinely set down at somebody's feet, and those are the very scenes where a
# dump is most likely to have just happened.
#
# So the padding is TRADEABLE and the object box is not. `shrink_away` pulls the
# padded rectangle's edges back until the bystander is outside it, never past
# the object's own edges. The boxes come from the person/vehicle pass the
# detector already runs for `ignore_boxes`, so this costs no inference.
#
# A bystander who OVERLAPS the object cannot be excluded without cropping the
# object out, so those are left alone -- the model is then genuinely looking at
# a person holding something, and "person" is the honest answer. That is also
# why this cannot resurrect a standing person as a candidate: `ignore_boxes`
# already stopped a blob inside a person box from ever being one.
def shrink_away(rect, obj, avoid):
    """`rect` pulled in to exclude each box in `avoid`, never inside `obj`.

    All three are (x1, y1, x2, y2) in full-resolution pixels. A box in `avoid`
    that overlaps `obj` is skipped; one that only eats into the padding is
    excluded by moving the one edge that is between it and the object.
    """
    x1, y1, x2, y2 = rect
    ox1, oy1, ox2, oy2 = obj
    for ax1, ay1, ax2, ay2 in avoid:
        # No overlap with the padded rect at all -- nothing to do.
        if ax2 <= x1 or ax1 >= x2 or ay2 <= y1 or ay1 >= y2:
            continue
        # Overlaps the object itself: excluding it would crop the object.
        if ax2 > ox1 and ax1 < ox2 and ay2 > oy1 and ay1 < oy2:
            continue
        # Entirely on one side of the object -> pull that edge back to meet it.
        if ax1 >= ox2:
            x2 = min(x2, max(ox2, ax1))
        elif ax2 <= ox1:
            x1 = max(x1, min(ox1, ax2))
        if ay1 >= oy2:
            y2 = min(y2, max(oy2, ay1))
        elif ay2 <= oy1:
            y1 = max(y1, min(oy1, ay2))
    return x1, y1, x2, y2


def crop_rect_full_res(shape, box, scale, *, pad_frac=CROP_PAD,
                       min_crop=MIN_CROP, avoid=None):
    """The (x1, y1, x2, y2) crop_full_res would cut, in full-resolution pixels.

    Split out from the crop itself so the command can LOG what it asked for
    without re-deriving the arithmetic -- a diagnostic that recomputes the
    geometry can only ever confirm its own copy of it.
    """
    height, width = shape[:2]
    x, y, w, h = [v / scale for v in box]
    pad = pad_frac * max(w, h)
    side_w = max(w + 2 * pad, min_crop)
    side_h = max(h + 2 * pad, min_crop)
    cx, cy = x + w / 2.0, y + h / 2.0
    x1, y1 = max(0.0, cx - side_w / 2), max(0.0, cy - side_h / 2)
    x2, y2 = min(float(width), cx + side_w / 2), min(float(height), cy + side_h / 2)
    if avoid:
        x1, y1, x2, y2 = shrink_away(
            (x1, y1, x2, y2), (x, y, x + w, y + h),
            [(bx, by, bx + bw, by + bh) for bx, by, bw, bh in avoid])
    return int(x1), int(y1), int(x2), int(y2)


def crop_full_res(frame, box, scale, *, pad_frac=CROP_PAD, min_crop=MIN_CROP,
                  avoid=None):
    """Map a PROC_WIDTH box back to full resolution and crop it with padding.

    Padded because an open-vocabulary head classifies a crop by what the whole
    crop looks like; a tight box around a white bag is just a white rectangle,
    while the same bag against visible pavement reads as a bag.

    `avoid` is an optional list of (x, y, w, h) person/vehicle boxes in FULL
    RESOLUTION -- the same ones the caller hands `update()` as `ignore_boxes`.
    The padding is pulled back off them where it can be; see shrink_away.
    """
    x1, y1, x2, y2 = crop_rect_full_res(
        frame.shape, box, scale, pad_frac=pad_frac, min_crop=min_crop,
        avoid=avoid)
    return frame[y1:y2, x1:x2]


# --- suspect crop -----------------------------------------------------------


class SuspectLookback:
    """Short rolling buffer of full-resolution frames, JPEG-encoded.

    Only has to reach back SUSPECT_PRE_SECONDS, because a candidate copies the
    window it cares about onto itself as it is created and then collects the
    post-window frame by frame. Keeping the GLOBAL buffer short and the
    per-candidate windows bounded is what stops a 60-second owner hold from
    needing a minute of 4MP frames in RAM: the alert may fire long after the
    drop, but the frames worth looking at were all within a second or two of
    it.

    JPEG rather than arrays for the same reason RawFrameRecorder does it: a raw
    2560x1440 frame is ~11MB and an array buffer measured in seconds runs to
    gigabytes. Encoding is throttled to SUSPECT_SAMPLE_INTERVAL — a person
    walking does not change usefully in a quarter of a second, and four frames
    a second is plenty to find one clean shot of them.
    """

    def __init__(self, seconds=SUSPECT_PRE_SECONDS,
                 interval=SUSPECT_SAMPLE_INTERVAL, quality=SUSPECT_JPEG_QUALITY):
        self.seconds = seconds
        self.interval = interval
        self.quality = quality
        self._buf = []          # [(ts, jpeg bytes)], oldest first
        self._last_add = None

    def add(self, frame, now):
        """Offer a frame. Stored only if one is due; old frames fall off."""
        if frame is None:
            return
        if self._last_add is not None and now - self._last_add < self.interval:
            return
        ok, buf = cv2.imencode(".jpg", frame,
                               [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
        if not ok:
            return
        self._last_add = now
        self._buf.append((now, buf.tobytes()))
        cutoff = now - self.seconds
        while self._buf and self._buf[0][0] < cutoff:
            self._buf.pop(0)

    def window(self, t0, t1):
        """[(ts, jpeg)] inside [t0, t1]. Encoded — decode_frame() reads one."""
        return [(t, j) for t, j in self._buf if t0 <= t <= t1]


def decode_frame(jpeg):
    """A buffered JPEG back to an image, or None if it will not decode."""
    try:
        return cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:                                           # noqa: BLE001
        return None


def sharpness(crop):
    """Variance of the Laplacian — the usual cheap focus/motion-blur measure.

    A person mid-stride in a motion-blurred frame and the same person standing
    still score very differently here, and only the second is any use for
    recognising somebody later.
    """
    if crop is None or getattr(crop, "size", 0) == 0:
        return 0.0
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def pick_suspect(samples, object_centre, *, near_factor=SUSPECT_NEAR_FACTOR):
    """Best person crop from the window around the drop.

    `samples` is [(frame, [(x, y, w, h), ...]), ...] and `object_centre` the
    dumped object's (x, y) — both in FULL-RESOLUTION coordinates. Returns
    (crop, box) or (None, None).

    Closest wins, but only to within `near_factor`: somebody three metres from
    the bag is far likelier to have dropped it than somebody at the far kerb,
    while between two people about equally close the larger, sharper crop is
    the one a tanod can actually identify a face from. Ranking purely by
    distance picks blurred half-frame crops; ranking purely by quality picks
    whoever happened to walk nearest the camera.
    """
    found = []
    for frame, boxes in samples:
        if frame is None:
            continue
        for box in boxes or []:
            x, y, w, h = [int(v) for v in box]
            if w <= 0 or h <= 0:
                continue
            crop = frame[max(0, y):y + h, max(0, x):x + w]
            if getattr(crop, "size", 0) == 0:
                continue
            dist = float(np.hypot(x + w / 2.0 - object_centre[0],
                                  y + h / 2.0 - object_centre[1]))
            found.append((dist, float(w) * h * sharpness(crop), crop, (x, y, w, h)))
    if not found:
        return None, None
    nearest = min(d for d, _q, _c, _b in found)
    cohort = [f for f in found if f[0] <= max(nearest, 1.0) * near_factor]
    best = max(cohort, key=lambda f: f[1])
    return best[2], best[3]


# --- prompt cache -----------------------------------------------------------
# set_classes() exists only to turn the 13 strings above into text embeddings,
# and to do that ultralytics loads CLIP ViT-B/32 -- 354 MB, ~8s measured --
# on EVERY launch, to recompute an identical result. The prompts are module
# constants, so their embeddings are too: bake them into the weights once and
# later launches skip CLIP entirely (7.9s -> 0.12s, measured).
#
# The filename carries a fingerprint of the prompt lists, so editing PROMPTS or
# REJECT invalidates the cache by itself. Without that, a prompt edit would
# silently have no effect -- the trap that makes a hand-baked weights file a bad
# idea, and the reason this is keyed rather than a fixed name.
VISION_DIR = Path(__file__).resolve().parent
PROMPT_CACHE_DIR = Path(os.environ.get("LOOKOUT_WORLD_CACHE", VISION_DIR / "world_cache"))


def prompt_fingerprint(weights=DEFAULT_WORLD_MODEL):
    """Short hash of the base weights name plus the exact prompt lists.

    repr of the nested lists rather than a joined string, so a prompt that
    happened to contain the separator could not collide with a different pair
    of lists.
    """
    payload = repr([str(weights), list(PROMPTS), list(REJECT)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def prompt_cache_path(weights=DEFAULT_WORLD_MODEL, cache_dir=None):
    base = Path(cache_dir) if cache_dir is not None else PROMPT_CACHE_DIR
    return base / f"disposal-world-{prompt_fingerprint(weights)}.pt"


def _bake_prompt_cache(model, path, log=None):
    """Save `model` with its prompt embeddings baked in, minus CLIP.

    Returns True if the cache was written.

    `clip_model` is dropped first: it is a 354 MB text encoder that only
    set_classes ever needs, and the cached path never calls set_classes.
    Stripping it takes the file from 313 MB to 24 MB (and frees the same from
    RAM) with predictions verified bit-identical.

    Written to a temp name and renamed, because a process killed mid-save --
    and core/monitor.py does kill these -- would otherwise leave a truncated
    .pt for the next launch to load as a corrupt model.
    """
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        model.model.clip_model = None
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        model.save(str(tmp))
        os.replace(tmp, path)
        return True
    except Exception as exc:                                    # noqa: BLE001
        # A cache is an optimisation. It must never be able to stop detection.
        if log:
            log(f"could not write the prompt cache ({exc}); continuing uncached")
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
        return False


def load_world_model(weights=DEFAULT_WORLD_MODEL, *, cache_dir=None, log=None):
    """Load YOLO-World prompted with PROMPTS + REJECT, via the prompt cache.

    Imported lazily so this module costs only cv2/numpy at import time, which
    is what keeps the stage-1 tests runnable without torch.
    """
    from ultralytics import YOLOWorld

    cached = prompt_cache_path(weights, cache_dir)
    if cached.exists():
        try:
            model = YOLOWorld(str(cached))
            if log:
                log(f"prompts read from {cached.name} — CLIP not needed")
            return model
        except Exception as exc:                                # noqa: BLE001
            # Self-healing rather than fatal: a truncated or stale-format cache
            # is recoverable by rebuilding, and refusing to start over a
            # throwaway file would be the wrong trade.
            if log:
                log(f"prompt cache {cached.name} unreadable ({exc}); rebuilding")
            try:
                cached.unlink()
            except OSError:
                pass

    model = YOLOWorld(weights)
    model.set_classes(PROMPTS + REJECT)
    if _bake_prompt_cache(model, cached, log=log):
        if log:
            log(f"prompt cache written to {cached.name}")
        # Re-read through the cache so a first run returns the same thing every
        # later run will, instead of being a one-off code path nothing tests.
        try:
            return YOLOWorld(str(cached))
        except Exception:                                       # noqa: BLE001
            pass        # the in-memory model is already correct; use it
    return model


def confirm(model, crop, garbage_ids=None, conf=CONFIRM_CONF, imgsz=640,
            absent=frozenset()):
    """Stage 2. Garbage must clear the floor AND beat every reject class.

    Returns (is_garbage, (garbage_label, score), (reject_label, score)).

    The garbage score is the COMBINED one (see combine_garbage), because the
    prompt list describes a single object nine ways and a real bag matches
    several of them weakly. The reject score is a plain maximum.

    Two conditions, and they fail different things. The comparison against the
    best reject class is what suppresses look-alikes: a bare floor would fire
    on a dog at 0.4 just as readily as a sack at 0.4, while requiring garbage
    to LEAD means the model has to prefer "sack" over "dog" on this crop.

    `absent` is reject class names a better-qualified model says are not in
    this crop (see absent_rejects). They are dropped from the comparison
    entirely.

    `conf` then has to be applied here as well as handed to predict(). Passing
    it to the model only sets ultralytics' own NMS floor, which drops boxes
    below it — so on a crop where EVERY class is weak, the surviving garbage
    box could still win the comparison on a score the operator had explicitly
    ruled too low to act on. Raising the dashboard's confidence had no effect
    on exactly those marginal detections, which is the opposite of what the
    control promises.
    """
    if crop is None or getattr(crop, "size", 0) == 0:
        return False, ("", 0.0), ("", 0.0)
    if garbage_ids is None:
        garbage_ids = garbage_class_ids()

    # Detect low, decide high — see WEAK_PREDICT_CONF.
    result = model.predict(crop, conf=min(conf, WEAK_PREDICT_CONF),
                           imgsz=imgsz, verbose=False)[0]
    garbage_dets, best_r = [], ("", 0.0)
    boxes = result.boxes.xyxy.tolist()
    for cls, score, box in zip(result.boxes.cls.tolist(),
                               result.boxes.conf.tolist(), boxes):
        name = result.names[int(cls)]
        if int(cls) in garbage_ids:
            garbage_dets.append((name, float(score), tuple(box)))
        elif name in absent:
            # The trained detector says this class is not here; see
            # TRUSTED_REJECTS. Skipped rather than scored down, because a
            # reduced score would still be compared against the garbage and
            # the point is that this opinion does not count.
            continue
        elif score > best_r[1]:
            best_r = (name, float(score))
    # Synonyms on one object corroborate; see combine_garbage. With a single
    # garbage hit this is exactly the old maximum, so a clear match is
    # unaffected -- what changes is the distant bag that matched four prompts
    # weakly and was reported at the strength of one of them.
    best_g = combine_garbage(garbage_dets)
    # `>=` on the tie so a crop the model scores identically for both still
    # counts: stage 1 already established something was dumped and left.
    return classify(best_g, best_r, conf) == CONFIRMED, best_g, best_r


class SpatialCooldown:
    """Which PLACES already have an alert, and which alert each one has.

    Keyed on POSITION, not candidate id, because stage 1 drops a candidate
    after `candidate_grace` and the replacement starts with a clean timer --
    so an id-keyed cooldown would let one bag re-alert every few seconds. This
    is the same failure D4 was added to close for the shared layer.

    One place, one violation. A candidate raised for a bag that already has a
    row does not open a second one: it ADOPTS the row this place holds
    (`alert_at`), and the status moves on that row instead. A bag dumped
    further than `distance` away is a different place and so a different
    violation -- rubbish on the left and rubbish on the right are two things
    to go and clear, and reporting them as one would under-count the street.

    `distance` is therefore doing the real work, and it is in the detector's
    PROC_WIDTH pixels: it is the radius inside which two sightings are "the
    same bag seen twice" rather than "two bags".

    An entry's clock is refreshed every time its place is reported again
    (`remember` updates in place rather than appending), so `seconds` is an
    IDLE window, not a cap: an object that stays put keeps its one row for as
    long as it is visible, and the place is only released once nothing has
    been seen there for `seconds`. Fresh rubbish dumped on a spot that was
    cleared long ago is then a new violation, which is the behaviour the
    operator's cooldown setting is for.
    """

    def __init__(self, seconds=COOLDOWN_SECONDS, distance=COOLDOWN_DIST):
        self.seconds = seconds
        self.distance = distance
        self._places = []

    def _at(self, center, now):
        """The live entry covering this place, or None. Purges stale ones."""
        self._places = [p for p in self._places if now - p["at"] < self.seconds]
        for place in self._places:
            ax, ay = place["center"]
            if float(np.hypot(center[0] - ax, center[1] - ay)) < self.distance:
                return place
        return None

    def blocks(self, center, now):
        """Has this place already been reported?"""
        return self._at(center, now) is not None

    def alert_at(self, center, now):
        """(alert_pk, level) already filed for this place, or None.

        None also covers "reported, but with no row behind it" -- a dry run --
        so a caller that cannot adopt falls through to the blocked path rather
        than opening a duplicate.
        """
        place = self._at(center, now)
        if place is None or place["alert_pk"] is None:
            return None
        return place["alert_pk"], place["level"]

    def remember(self, center, now, alert_pk=None, level=""):
        """Open this place, or hold an already-open one open.

        The ANCHOR is not moved on a refresh. A candidate's box jitters a few
        pixels a frame, and following it would let a place drift across the
        frame over a long dwell until it stopped matching the object that
        opened it.
        """
        place = self._at(center, now)
        if place is None:
            self._places.append({"center": tuple(center), "at": now,
                                 "alert_pk": alert_pk, "level": level})
            return
        place["at"] = now
        if alert_pk is not None:
            place["alert_pk"], place["level"] = alert_pk, level
