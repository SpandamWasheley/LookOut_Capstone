"""Vision-language verification of a candidate violation -- the second stage.

WHY A SECOND STAGE
------------------
The YOLO weights are trained on datasets that do not look like barangay CCTV, so
their confidence thresholds sit low (30-35 on a 0-100 scale) and the temporal
rules in tracking.py do the real filtering. That recovers precision but cannot
recover *context*: a bottle on a table during a family lunch and a bottle in a
tagay session look identical to an object detector, and a knife on a fish
vendor's board looks identical to one held at somebody.

A VLM answers exactly the questions the geometry cannot. It is called ONCE, at
alert-creation time -- never per frame. By then the pipeline has already tracked
the person, won the vote window, completed the dwell and cleared the cooldown,
so the call rate is a few per hour rather than a few per second, and a 1-3
second round trip costs nothing in the frame loop.

THE QUESTIONS
-------------
Ten questions, three per violation except smoking's four, and they are FIXED --
specified deliberately rather than grown by accident. Each spec asks one main
question, whose answer is the verdict, plus the remaining ones as true/false
sub-observations that each add weight.

Two of the main questions ask for a reading of the scene rather than an object:
"is this a holdup or is it just an unharmful scenario?" and "do you think this
is a public drinking violation?". That phrasing matters. An object alone is
almost never the violation -- what separates a tool from a weapon is whether the
context that would EXPLAIN the object is present, and a question that offers the
innocent reading out loud lets the model take it.

So a 'no' is not merely the absence of a 'yes'. A confident 'no' scales the
whole score DOWN (scoring.VLM_DENIAL_MULTIPLIER) instead of quietly adding
nothing -- otherwise the innocent branch of the question would be unwired, and
asking it would be theatre. It is a multiplier and not a negative weight because
"this looks like an ordinary fish vendor" is not evidence against "a blade was
detected"; it says the whole picture is less suspicious than the sum of its
parts, which is what is actually meant. Floored, never a veto.

FAIL-OPEN, ALWAYS
-----------------
Every failure path -- no API key, no network, a timeout, a malformed reply, the
library not installed, a rate limit -- returns a Verdict with ok=False. Callers
add no cue and publish the alert on the geometry alone. A dead API key must
never silently stop a security system from alerting; that is a worse failure
than any false positive this module exists to remove.

NO DJANGO HERE
--------------
Same rule as recognition.py and tracking.py: configuration arrives as explicit
arguments, so this module stays importable and testable without a database.
The watchers read SystemSettings and pass the values down.
"""

import base64
import json
import os
import threading

try:
    import cv2
except ImportError:                       # pragma: no cover - cv2 is a hard dep
    cv2 = None                            # in practice, but keep the import soft


# --- verdicts ---------------------------------------------------------------

YES = "yes"
NO = "no"
UNCLEAR = "unclear"

# Gemini Flash: cheap enough to run on a barangay budget, fast enough not to
# hold an alert back, and it supports both image input and a schema-constrained
# JSON reply -- the two things this module actually needs.
#
# Held as a SETTING (SystemSettings.vlm_model) rather than hard-coded, because
# model ids turn over far faster than this code will. If a run reports the model
# as not found, change the setting rather than editing here.
DEFAULT_PROVIDER = "ollama"
# v3 §8. Runs LOCALLY through Ollama, which changes the privacy argument
# entirely: frames never leave the device, so there is nothing to blur and no
# third-party transfer to declare under RA 10173.
DEFAULT_MODEL = "qwen3-vl:4b"
DEFAULT_ENDPOINT = "http://localhost:11434"
DEFAULT_TIMEOUT = 60.0        # a local 4B model on CPU is slower than a cloud call
DEFAULT_MAX_TOKENS = 512

YES, NO, UNCLEAR = "yes", "no", "unclear"

# Longest edge of an encoded crop. Qwen3-VL tiles large images into many visual
# tokens, and a 4B model on modest hardware slows sharply as that count grows.
MAX_EDGE = 1024
JPEG_QUALITY = 85

# v3 §8: ONE full scene frame plus three crops, about a second apart. The full
# frame is what makes the scene questions answerable -- "is this a store?", "is
# there a work surface?" -- because that context lives outside the person box
# and a crop has already thrown it away.
FRAME_COUNT = 3
FRAME_SPACING = 1.0
SEND_FULL_SCENE = True

# +40% around the subject. A tight person box cuts off the table the bottle
# sits on and the second person in a holdup.
CROP_PAD = 0.40

# v3 §8: NO blurring. Frames never leave the device, and blurring would hide the
# mouth the hand_to_mouth_activity question depends on -- the single most
# important question in the smoking set.
BLUR_FACES = False

# v3 §8's three tiers and what each is worth.
_CONFIDENCE_TIERS = (("high", 1.0), ("medium", 0.5), ("low", 0.0))


# v3 JSON field -> the weight key it scores under (see scoring.py). The two are
# deliberately different strings: the field names are the model's contract and
# have to read as questions, while the weight keys stay short and stable so
# renaming one cannot silently zero the other.
#
# A field absent from this table scores under its own name, which surfaces in
# Score.unknown as the wiring bug it is.
WEIGHT_KEYS = {
    # drinking
    "group_appears_to_be_drinking_together": "verdict",
    "table_chairs_or_seating_visible": "seating",
    "drinking_items_visible": "drinking_items",
    # smoking
    "smoking_item_visible": "smoking_item",
    # holdup -- these map to Layer E codes in scoring.HOLDUP_CUE_CODES rather
    # than to vlm_ keys, so they are absent here on purpose.
}


class Verdict:
    """One VLM answer, plus everything the audit trail needs.

    `ok` is the honesty flag: False means the call did not produce a usable
    answer (disabled, no credentials, network failure, bad reply). Callers must
    check it before treating `verdict` as evidence -- a failed call and a
    genuine "no" are very different things and must never be conflated.
    """

    def __init__(self, verdict=UNCLEAR, confidence=0.0, reason="", cues=None,
                 ok=False, error="", model="", latency=None, kind=""):
        # Which spec produced this, so enum answers can be told from booleans.
        self.kind = kind
        self.verdict = verdict
        self.confidence = float(confidence or 0.0)
        self.reason = reason or ""
        # The sub-question answers -- each one that is true adds weight.
        self.cues = dict(cues or {})
        self.ok = bool(ok)
        self.error = error or ""
        self.model = model or ""
        self.latency = latency

    @property
    def tier(self):
        """The spec's three-way confidence bucket (§5.3).

        A language model's numeric confidence is not calibrated -- 0.82 and 0.79
        do not reliably differ -- but its choice between "high" and "low" is a
        coarser judgement it makes more reliably. The tier is what scales its
        points; the float is kept for the audit trail.
        """
        from core.vision import scoring
        return scoring.confidence_tier(self.confidence)

    @property
    def confirms(self):
        """True only on a successful, affirmative reading."""
        return self.ok and self.verdict == YES

    @property
    def denies(self):
        """True only on a successful, negative reading.

        The model read the scene and said this is not the violation. Callers
        turn this into scoring.VLM_DENIAL_MULTIPLIER -- a demotion, never a
        veto: a floored multiplier keeps a genuine incident inside the WATCH
        band rather than erasing it, because the VLM does not get the last word
        on its own reading.
        """
        return self.ok and self.verdict == NO

    def fired_cues(self, prefix="vlm_"):
        """True answers, mapped onto the weight keys scoring.py prices.

        Enum answers are excluded: they drive multipliers, not points, and
        summing a scene reading with the evidence would double-count it.
        """
        if not self.ok:
            return set()
        spec = SPECS.get(self.kind, _EMPTY_SPEC)
        enums = {c.name for c in spec.cues if isinstance(c, EnumCue)}
        out = set()
        for name, value in self.cues.items():
            if name in enums or value is not True:
                continue
            out.add(prefix + WEIGHT_KEYS.get(name, name))
        # The verdict is an enum answer for smoking, so it never appears in the
        # loop above -- add it here when the spec's nominated cue says yes.
        if self.confirms and spec.verdict_cue in enums:
            out.add(prefix + "verdict")
        return out

    def enum_multipliers(self):
        """{cue name: factor} for every enum answer, per its own table.

        This is how `scene_type = other_activity` cuts a holdup score -- a
        multiplier rather than negative points, because "this is an ordinary
        vendor" is not evidence against "a blade was detected"; it says the
        whole picture is less suspicious than the sum of its parts.
        """
        if not self.ok or not self.kind:
            return {}
        out = {}
        for cue in SPECS.get(self.kind, _EMPTY_SPEC).cues:
            if isinstance(cue, EnumCue):
                factor = cue.multiplier_for(self.cues.get(cue.name))
                if factor != 1.0:
                    out[cue.name] = factor
        return out

    def as_dict(self):
        """Serialisable record -- stored on the Alert for the audit trail."""
        return {
            "ok": self.ok,
            "verdict": self.verdict,
            "confidence": round(self.confidence, 3),
            "tier": self.tier if self.ok else "low",
            "reason": self.reason,
            "cues": dict(self.cues),
            "model": self.model,
            "error": self.error,
            "latency": round(self.latency, 2) if self.latency is not None else None,
        }

    def __repr__(self):
        state = self.verdict if self.ok else f"unavailable:{self.error[:40]}"
        return f"<Verdict {state} {self.confidence:.2f}>"


def unavailable(error):
    """The fail-open result. Every error path funnels through here."""
    return Verdict(verdict=UNCLEAR, ok=False, error=error)


# --- prompt specifications --------------------------------------------------

class Cue(object):
    """One yes/no observation the VLM reports alongside the main verdict.

    The question travels WITH the name. A bare list of cue names ("cup",
    "chairs") leaves the model guessing what each one means, and two people
    reading the code disagree about it later; a written question is both the
    prompt and the definition.
    """

    def __init__(self, name, question):
        self.name = name
        self.question = question


class EnumCue:
    """A cue answered with one of several words rather than true/false.

    Exists for the spec's `scene_type` (§6.1). Listing benign scenes one by one
    is a losing game -- vendors, construction, butchers, kitchens, repairs,
    children playing -- so everything that is not a confrontation collapses into
    one value that cuts the score.

    `multipliers` maps each value to what it does to the total. A value worth
    1.0 is a real answer that happens to change nothing, which is why "unclear"
    is listed explicitly rather than left to a default: "I cannot tell" is not
    the same as "this is harmless", and if hedging cut the score, real holdups
    would be lost every time the model was unsure.
    """

    def __init__(self, name, question, values, multipliers, default):
        self.name = name
        self.question = question
        self.values = tuple(values)
        self.multipliers = dict(multipliers)
        self.default = default

    def multiplier_for(self, value):
        return self.multipliers.get(value, self.multipliers[self.default])


class PromptSpec:
    """One violation's question set, plus how its answers become a verdict.

    v3 dropped the separate "is this a violation?" field. Each spec now names
    which of its own cues carries the verdict, so `verdict_from` can derive it
    without a redundant question -- for smoking that is the
    hand_to_mouth_activity choice, which the model has to answer anyway.
    """

    def __init__(self, kind, question, cues=(), guidance="", verdict_cue=None,
                 verdict_values=()):
        self.kind = kind
        self.question = question
        self.cues = tuple(cues)
        self.guidance = guidance
        # Which cue's answer IS the verdict, and which of its values count as
        # affirmative. A boolean cue needs no values list.
        self.verdict_cue = verdict_cue
        self.verdict_values = tuple(verdict_values)

    def verdict_from(self, cues):
        """yes / no / unclear, derived from the cue the spec nominated."""
        if not self.verdict_cue:
            return UNCLEAR
        value = cues.get(self.verdict_cue)
        if self.verdict_values:
            if value in self.verdict_values:
                return YES
            return UNCLEAR if value in (None, "unclear") else NO
        return YES if value else NO


# The question sets below are v3 §9 verbatim. The checker is asked ONLY what
# the detector and the pose model cannot answer: the surrounding scene, how the
# people interact, and whether there is an ordinary explanation. It is never
# asked to confirm an object YOLO already found -- v3 removed six such
# questions, because in local testing the 4B model called a bottle held to the
# mouth a cigarette, then a bottle when the same image was asked about
# differently. YOLO answers what is there; the checker answers what is
# happening.

DRINKING_SPEC = PromptSpec(
    kind="drinking",
    question="Answer these questions about the people in the images:",
    cues=(
        Cue("group_appears_to_be_drinking_together",
            "two or more people appear to be drinking together at the same "
            "spot."),
        Cue("table_chairs_or_seating_visible",
            "chairs, stools, benches, or a table used by the group."),
        # Replaces the old glass/cup and food questions. A single glass or
        # snack is too small to identify reliably at CCTV resolution; a set of
        # items laid out near a group is easier to see and still describes the
        # inuman setup.
        Cue("drinking_items_visible",
            "glasses, cups, plates, or snacks set out near the group."),
        EnumCue(
            "scene_type",
            '"drinking_session" if people appear to be drinking together; '
            '"other_activity" for selling, carrying, delivering, or storing '
            'drinks, or having a meal; "unclear" if you cannot tell.',
            values=("drinking_session", "other_activity", "unclear"),
            multipliers={"drinking_session": 1.0,
                         "other_activity": 0.25,
                         "unclear": 1.0},
            default="unclear",
        ),
    ),
    verdict_cue="group_appears_to_be_drinking_together",
)

SMOKING_SPEC = PromptSpec(
    kind="smoking",
    question="Answer these questions about the person in the images:",
    cues=(
        # Asked always, scored ONLY when YOLO missed the item -- otherwise it
        # counts the same cigarette twice. It is the fallback that lets a
        # puff-only case be shown at all.
        Cue("smoking_item_visible",
            "any object a person smokes or inhales from, held in the hand or "
            "at the mouth."),
        # The question that carries this whole detector. The same hand-to-mouth
        # motion is smoking, drinking, eating and a phone call, and the pose
        # model cannot tell them apart -- so this both confirms (smoking, +20)
        # and de-escalates (anything else, x0.25).
        EnumCue(
            "hand_to_mouth_activity",
            "compare the frames. What is the hand doing each time it reaches "
            'the mouth? "smoking" = bringing a smoking item to the mouth; '
            '"drinking" = drinking from a bottle, glass, or cup; "eating" = '
            'eating food or snacks; "phone" = holding a phone to the face; '
            '"unclear" = you cannot tell.',
            values=("smoking", "drinking", "eating", "phone", "unclear"),
            multipliers={"smoking": 1.0, "drinking": 0.25, "eating": 0.25,
                         "phone": 0.25, "unclear": 1.0},
            default="unclear",
        ),
    ),
    verdict_cue="hand_to_mouth_activity",
    verdict_values=("smoking",),
)

HOLDUP_SPEC = PromptSpec(
    kind="holdup",
    question="Answer these questions about the people in the images:",
    cues=(
        Cue("object_pointed_at_a_person",
            "a knife or sharp object is pointed or held toward another "
            "person."),
        Cue("victim_response_visible",
            "a person raising their hands, handing over items, or backing "
            "away."),
        Cue("appears_to_be_a_holdup",
            "one person appears to be robbing or threatening another."),
        EnumCue(
            "scene_type",
            '"confrontation" if one person appears to be threatening another; '
            '"other_activity" for ordinary use of the object, such as '
            'vending, food preparation, work, or play; "unclear" if you '
            'cannot tell.',
            values=("confrontation", "other_activity", "unclear"),
            multipliers={"confrontation": 1.0,
                         "other_activity": 0.25,
                         "unclear": 1.0},
            default="unclear",
        ),
    ),
    verdict_cue="appears_to_be_a_holdup",
)

# A stand-in so a lookup for an unknown kind never needs a None check.
_EMPTY_SPEC = PromptSpec(kind="", question="")

SPECS = {
    "drinking": DRINKING_SPEC,
    "smoking": SMOKING_SPEC,
    "holdup": HOLDUP_SPEC,
    "thief": HOLDUP_SPEC,     # watch_thief's kind names map onto the same spec
    "snatch": HOLDUP_SPEC,
    "carnapping": HOLDUP_SPEC,
    "property": HOLDUP_SPEC,
}


SYSTEM_PROMPT = (
    # v3 §9.1, verbatim. Note what it does NOT say: it never names the object
    # YOLO found, and never lists the violation option first, so nothing in the
    # wording nudges the answer toward the detector's guess.
    "You are reviewing CCTV frames from a barangay street camera in the "
    "Philippines.\n"
    "The first image is the full scene. The next images are crops of the same "
    "person or group, about 1 second apart, in order.\n"
    "Answer only about what is clearly visible. Do not guess.\n"
    "For true/false fields, answer only true or false. Answer true only if it "
    "is clearly visible; otherwise answer false.\n"
    'For choice fields, pick exactly one of the listed options. Pick "unclear" '
    "if you cannot tell.\n"
    'Rate confidence: "high" if the evidence is clearly visible, "medium" if '
    'it is partly visible or you are partly sure, "low" if you are mostly '
    "guessing.\n"
    "Reply only with the JSON below. No other text."
)


def _schema_for(spec):
    """Response schema constraining the reply.

    Written in the OpenAPI/JSON-Schema subset both Gemini's `response_schema`
    and ordinary JSON-schema validators accept, so the model cannot reply with
    prose and there is no parse-and-retry loop.
    """
    properties = {
        "verdict": {"type": "string", "enum": [YES, NO, UNCLEAR]},
        # Spec 6.1 asks for a WORD, not a number. A language model's numeric
        # confidence is not calibrated -- 0.82 and 0.79 do not reliably differ
        # -- while its choice between "high" and "low" is a coarser judgement it
        # makes more reliably. Constraining the schema to three values also
        # stops it inventing 0.87 to sound precise.
        "confidence": {"type": "string",
                       "enum": [t for t, _ in _CONFIDENCE_TIERS]},
        "reason": {"type": "string"},
    }
    required = ["verdict", "confidence", "reason"]
    for cue in spec.cues:
        if isinstance(cue, EnumCue):
            properties[cue.name] = {"type": "string", "enum": list(cue.values)}
        else:
            properties[cue.name] = {"type": "boolean"}
        required.append(cue.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        # Gemini honours a declared key order when generating, which keeps the
        # verdict first and the sub-answers after it.
        "propertyOrdering": required,
    }


def _read_cue(cue, data):
    """One answer out of the reply, typed by what the cue asked for."""
    if isinstance(cue, EnumCue):
        value = str(data.get(cue.name, cue.default)).lower()
        # An unrecognised value falls back to the default rather than raising:
        # for scene_type that default is "unclear", which changes nothing --
        # the safe reading of an answer we could not parse.
        return value if value in cue.values else cue.default
    return bool(data.get(cue.name))


def _build_prompt(spec, context="", frames=1):
    parts = []
    if frames > 1:
        # Stated explicitly, or the model treats them as unrelated images and
        # answers about the last one only.
        parts.append(
            f"These are {frames} consecutive frames from a fixed CCTV camera, "
            f"about {FRAME_SPACING:.0f} second apart, in order. Judge them "
            f"together as one short event -- what changes between them is "
            f"evidence. Faces are blurred for privacy; this is expected and is "
            f"not a reason to answer 'unclear'.")
    parts.append(f"Question: {spec.question}")
    if spec.guidance:
        parts.append(spec.guidance)
    if spec.cues:
        listed = "\n".join(f"- {c.name}: {c.question}" for c in spec.cues)
        parts.append(
            "Also answer each of these true or false, independently of your "
            f"verdict above:\n{listed}")
    if context:
        # What the geometry already established. Given as background so the
        # model knows what the detector saw, explicitly marked as not evidence
        # so it doesn't simply agree with the first stage.
        parts.append(
            f"Detector context (background only -- do not treat as evidence "
            f"for your answer): {context}"
        )
    return "\n\n".join(parts)


# --- image encoding ---------------------------------------------------------

def blur_faces(frame_bgr, detector=None):
    """Return a copy with every detected face blurred (RA 10173, spec §6).

    Uses OpenCV's Haar cascade rather than the insightface detector already in
    recognition.py: this runs on the alert path where latency is cheap, it needs
    no model download, and a false POSITIVE here is harmless -- blurring a patch
    that was not a face costs nothing, while a miss would send an identifiable
    face to a third party.

    Fails OPEN by returning the frame unblurred if the cascade is unavailable,
    consistent with everything else in this module -- but callers that care
    about the privacy guarantee should check `blur_faces_available()` at startup
    rather than discovering it per alert.
    """
    if cv2 is None or frame_bgr is None:
        return frame_bgr
    try:
        cascade = detector or _face_cascade()
        if cascade is None:
            return frame_bgr
        out = frame_bgr.copy()
        grey = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=4,
                                         minSize=(20, 20))
        for (x, y, w, h) in faces:
            roi = out[y:y + h, x:x + w]
            if roi.size:
                out[y:y + h, x:x + w] = cv2.GaussianBlur(
                    roi, (BLUR_KERNEL, BLUR_KERNEL), 0)
        return out
    except Exception:
        # Never let a privacy nicety crash a detector.
        return frame_bgr


_CASCADE = None
_CASCADE_TRIED = False


def _face_cascade():
    global _CASCADE, _CASCADE_TRIED
    if _CASCADE_TRIED:
        return _CASCADE
    _CASCADE_TRIED = True
    try:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(path)
        _CASCADE = None if cascade.empty() else cascade
    except Exception:
        _CASCADE = None
    return _CASCADE


def blur_faces_available():
    """Whether the privacy guarantee can actually be honoured on this machine."""
    return cv2 is not None and _face_cascade() is not None


class FrameBuffer:
    """The last few frames for one tracked subject, for the multi-frame send.

    Held per detector rather than per track: the VLM is called once per
    incident, so what is needed is "the recent past of the scene", and one
    ring buffer of whole frames costs far less memory than one crop buffer per
    track in a busy frame.
    """

    def __init__(self, count=FRAME_COUNT, spacing=FRAME_SPACING):
        self.count = count
        self.spacing = spacing
        self._frames = []      # [(timestamp, frame)]

    def add(self, frame_bgr, now):
        """Keep a frame if it is at least `spacing` newer than the last kept."""
        if frame_bgr is None:
            return
        if self._frames and now - self._frames[-1][0] < self.spacing:
            return
        self._frames.append((now, frame_bgr.copy()))
        # One more than needed, so the newest frame at call time is never the
        # only one available.
        del self._frames[:-(self.count + 1)]

    def recent(self, count=None):
        """Oldest-to-newest frames, at most `count`."""
        want = count or self.count
        return [f for _, f in self._frames[-want:]]

    def __len__(self):
        return len(self._frames)


def encode_crop(frame_bgr, box=None, pad=CROP_PAD, as_bytes=False, blur=None):
    """BGR frame (optionally cropped to `box`) -> JPEG, or None on any failure.

    `pad` widens the box before cropping: a tight person box cuts off exactly
    the context the VLM needs -- the table the bottle sits on, the board the
    knife is being used on, the second person in a holdup.

    Returns raw bytes when `as_bytes`, else base64 text.
    """
    if cv2 is None or frame_bgr is None:
        return None
    try:
        img = frame_bgr
        # Blurred HERE rather than at the call sites: a privacy rule that every
        # caller must remember to apply is one that a future caller will forget,
        # and the failure is silent and unrecoverable.
        if BLUR_FACES if blur is None else blur:
            img = blur_faces(img)
        if box is not None:
            h, w = img.shape[:2]
            x1, y1, x2, y2 = (int(v) for v in box)
            bw, bh = max(x2 - x1, 1), max(y2 - y1, 1)
            px, py = int(bw * pad), int(bh * pad)
            x1 = max(0, x1 - px)
            y1 = max(0, y1 - py)
            x2 = min(w, x2 + px)
            y2 = min(h, y2 + py)
            if x2 <= x1 or y2 <= y1:
                return None
            img = img[y1:y2, x1:x2]

        h, w = img.shape[:2]
        if max(h, w) > MAX_EDGE:
            scale = MAX_EDGE / float(max(h, w))
            img = cv2.resize(img, (max(int(w * scale), 1), max(int(h * scale), 1)),
                             interpolation=cv2.INTER_AREA)

        ok, buf = cv2.imencode(".jpg", img,
                               [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            return None
        raw = buf.tobytes()
        return raw if as_bytes else base64.standard_b64encode(raw).decode("ascii")
    except Exception:
        # An encoding failure is a fail-open case like any other.
        return None


# --- the client -------------------------------------------------------------

class OllamaVerifier:
    """Qwen3-VL 4B served locally by Ollama (v3 §8).

    Local rather than a cloud API, and that is the point rather than a
    cost-saving: CCTV frames of identifiable residents never leave the
    barangay's own machine, so the privacy limitation the cloud version had to
    declare simply does not arise.

    What it costs is capability. A 4B model misreads small objects at CCTV
    resolution -- in local testing it called a bottle held to the mouth a
    cigarette, then a bottle when the same image was asked about differently.
    That is exactly why v3 stopped asking it to identify objects at all: YOLO
    answers what is there, the checker answers what is happening.

    Talks to Ollama's /api/chat over plain HTTP with the standard library, so
    there is no SDK to install and no version to track.
    """

    name = "ollama"

    def __init__(self, api_key=None, model=DEFAULT_MODEL,
                 max_tokens=DEFAULT_MAX_TOKENS, timeout=DEFAULT_TIMEOUT,
                 endpoint=DEFAULT_ENDPOINT):
        # api_key is accepted and ignored: a local server needs none. Kept in
        # the signature so build_verifier can treat every provider alike.
        self.api_key = api_key or ""
        self.model = model or DEFAULT_MODEL
        self.max_tokens = int(max_tokens or DEFAULT_MAX_TOKENS)
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self.endpoint = (endpoint or DEFAULT_ENDPOINT).rstrip("/")

    def _post(self, path, payload):
        import urllib.request

        data = json.dumps(payload).encode("utf8")
        req = urllib.request.Request(
            f"{self.endpoint}{path}", data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf8"))

    def _ensure_client(self):
        """Fail fast at startup if Ollama is not reachable or lacks the model.

        Checked once per run rather than per alert, so an operator sees "the
        model is not pulled" in the first line of output instead of discovering
        it hours later in a stats counter.
        """
        import urllib.request

        try:
            with urllib.request.urlopen(f"{self.endpoint}/api/tags",
                                        timeout=5) as resp:
                tags = json.loads(resp.read().decode("utf8"))
        except Exception as exc:
            raise RuntimeError(
                f"Ollama is not reachable at {self.endpoint} ({exc}). "
                f"Start it with: ollama serve")

        names = {m.get("name", "") for m in tags.get("models", [])}
        # Ollama reports "qwen3-vl:4b"; a bare "qwen3-vl" should still match.
        if names and not any(n == self.model or n.startswith(self.model + ":")
                             or self.model.startswith(n.split(":")[0])
                             for n in names):
            raise RuntimeError(
                f"model {self.model!r} is not pulled. Run: ollama pull {self.model}")
        return True

    def verify(self, image_b64, spec, context=""):
        """`image_b64` is one base64 JPEG, or a list of them (v3 §8).

        A list is the full scene frame followed by crops about a second apart,
        which is what lets the model answer about ACTION -- what the hand does
        each time it reaches the mouth -- rather than only about what is
        present in one still.
        """
        import time as _time

        started = _time.monotonic()
        payloads = image_b64 if isinstance(image_b64, (list, tuple)) else [image_b64]
        images = [p for p in payloads if p]
        if not images:
            return unavailable("no image payload")

        try:
            self._ensure_client()
        except Exception as exc:
            return unavailable(str(exc))

        try:
            reply = self._post("/api/chat", {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",
                     "content": _build_prompt(spec, context, frames=len(images)),
                     # Ollama takes base64 strings directly, no data: prefix.
                     "images": list(images)},
                ],
                # The setting that makes the reply parseable without a retry
                # loop. Ollama constrains generation to valid JSON.
                "format": "json",
                "stream": False,
                "options": {
                    # A classification, not a creative task: the same crops must
                    # not get different answers on two runs.
                    "temperature": 0.0,
                    "num_predict": self.max_tokens,
                },
            })
        except Exception as exc:
            # Covers a stopped server, a timeout, a pulled model and a refused
            # connection alike. All the same thing to the caller: no answer,
            # publish on the system indicators alone.
            return unavailable(f"{type(exc).__name__}: {exc}")

        latency = _time.monotonic() - started
        text = (reply.get("message") or {}).get("content", "")
        if not text:
            return Verdict(ok=False, error="empty response", model=self.model,
                           latency=latency)
        return _parse_reply(text, spec, self.model, latency)


def _parse_reply(text, spec, model, latency):
    """Provider-agnostic: JSON text -> Verdict, or a fail-open Verdict.

    Every malformed shape funnels through here rather than through each
    provider, so a second backend inherits the same tolerance for free.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return Verdict(ok=False, error="unparseable response",
                       model=model, latency=latency)
    if not isinstance(data, dict):
        return Verdict(ok=False, error="unexpected response shape",
                       model=model, latency=latency)

    # v3 §8: the confidence WORD maps to the factor it is worth. A provider
    # that sends a number instead is read as a float and bucketed the same way.
    raw = data.get("confidence", "low")
    if isinstance(raw, str):
        confidence = dict(_CONFIDENCE_TIERS).get(raw.strip().lower(), 0.0)
    else:
        try:
            confidence = float(raw)
        except (TypeError, ValueError):
            confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    cues = {c.name: _read_cue(c, data) for c in spec.cues}

    # v3 §8: "a scene reading given with low confidence is treated as
    # 'unclear'." A model that is mostly guessing must not be able to cut a
    # score by 75% -- the de-escalation is the single most consequential thing
    # it can say, so it has to be the most confidently said.
    if confidence <= 0.0:
        for cue in spec.cues:
            if isinstance(cue, EnumCue):
                cues[cue.name] = cue.default

    # The verdict is derived from the spec's own verdict cue rather than a
    # separate field: v3 removed the standalone "is this a violation" question
    # for smoking, folding it into hand_to_mouth_activity.
    verdict = spec.verdict_from(cues)
    return Verdict(
        verdict=verdict,
        confidence=confidence,
        reason=str(data.get("reason", ""))[:500],
        cues=cues,
        kind=spec.kind,
        ok=True,
        model=model,
        latency=latency,
    )


class DisabledVerifier:
    """What callers get when the VLM is unusable. Costs nothing, says nothing,
    and keeps every call site free of `if enabled:` branches.

    `reason` explains WHY it is inert, so a watcher can print something more
    useful than "disabled" at startup. "You switched it off" and "you switched
    it on but there is no API key" look identical at the call site and are very
    different things to the operator reading the log.
    """

    name = "disabled"

    def __init__(self, reason="switched off in Settings"):
        self.reason = reason

    def verify(self, image_b64, spec, context=""):
        return unavailable("disabled")


# Provider registry. A second backend is one class with a .verify() method plus
# one entry here -- the detectors never learn which provider answered them.
PROVIDERS = {
    "ollama": OllamaVerifier,
    "disabled": DisabledVerifier,
}


def build_verifier(enabled=True, provider=DEFAULT_PROVIDER, api_key=None,
                   model=DEFAULT_MODEL, max_tokens=DEFAULT_MAX_TOKENS,
                   timeout=DEFAULT_TIMEOUT, endpoint=DEFAULT_ENDPOINT):
    """Construct a verifier from SystemSettings values.

    `enabled` defaults to True because being ON is safe: every unusable
    configuration below returns an inert DisabledVerifier, so the detectors run
    identically to having no checker at all.

    Returns DisabledVerifier -- never raises -- for: switched off, an unknown
    provider, a stopped Ollama server, or a model that has not been pulled. A
    misconfiguration must degrade to "no checker", not to an exception inside a
    watcher's frame loop.

    Note there is no credential check any more. The v3 model runs locally, so
    "no API key" has stopped being a failure mode -- what replaces it is "is
    Ollama running and is the model pulled", which _ensure_client answers.
    """
    if not enabled:
        return DisabledVerifier("switched off in Settings")

    cls = PROVIDERS.get((provider or "").lower())
    if cls is None or cls is DisabledVerifier:
        return DisabledVerifier(f"unknown provider {provider!r}")

    kwargs = dict(api_key=api_key, model=model, max_tokens=max_tokens,
                  timeout=timeout)
    if cls is OllamaVerifier:
        kwargs["endpoint"] = endpoint
    verifier = cls(**kwargs)

    try:
        # Fail fast HERE, at startup, rather than once per alert for the life
        # of the run.
        verifier._ensure_client()
    except Exception as exc:
        return DisabledVerifier(str(exc))

    return verifier


def describe(verifier, model=""):
    """One line for a watcher to print at startup.

    Says WHY when it is off, because "disabled" alone sends an operator who just
    set a key hunting through Settings for a switch that was never the problem.
    """
    if isinstance(verifier, DisabledVerifier):
        return f"VLM verification: OFF — {verifier.reason}"
    return f"VLM verification: ON ({model or DEFAULT_MODEL})"


def verify_frame(verifier, frame_bgr, kind, box=None, context="", frames=None):
    """Convenience wrapper: crop, encode and verify in one call.

    This is what the watchers use. `kind` selects the prompt spec; an unknown
    kind is a wiring bug and fails open like everything else.
    """
    spec = SPECS.get(kind)
    if spec is None:
        return unavailable(f"no prompt spec for {kind!r}")
    # v3 §8: one FULL SCENE frame first, then the crops. The scene questions --
    # is this a store? is there a work surface? is the group seated? -- are
    # answerable only from context that lives outside the person box, which a
    # crop has by definition thrown away. Sending the whole frame first is what
    # makes them answerable at all.
    #
    # `frames` is the recent past from a FrameBuffer; frame_bgr is the current
    # one and is always sent last, so the newest evidence is what the model sees
    # most recently.
    sequence = list(frames or [])
    if frame_bgr is not None:
        sequence.append(frame_bgr)
    encoded = []
    if SEND_FULL_SCENE and sequence:
        scene = encode_crop(sequence[-1], box=None)
        if scene:
            encoded.append(scene)
    encoded += [c for c in (encode_crop(f, box) for f in sequence) if c]
    if not encoded:
        return unavailable("could not encode crop")
    return verifier.verify(encoded, spec, context=context)
