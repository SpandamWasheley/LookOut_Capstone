"""The AI checker (scoring spec v6, sections 8 and 9).

A local Qwen3-VL model, served by Ollama, looks at a short run of frames cropped
around the alert's subject and answers a few questions about what is HAPPENING
(the detectors already answered what is THERE). Its answers are display-only:
the AI context card, the AI badge, the suggested status. Nothing here ever
changes the official score or status, and a failed or invalid reply simply
becomes "AI context unavailable".

Pieces, in the order a call uses them:

    FrameRing        rolling buffer of FULL-RESOLUTION frames (JPEG-compressed in
                     memory) plus the tracked box of every subject per frame
    select_frames    8-12 frames bunched around the trigger moment
    crop_for         the per-violation crop, cut from the full-res frame and then
                     resized (so the person keeps their pixels)
    build_messages   shared instructions + the violation's block, {system_note} filled
    OllamaClient     the HTTP call; temperature 0, JSON-schema constrained reply
    check()          all of the above, returning a CheckResult
    check_async()    the same on a worker thread, result handed to a callback
"""
import base64
import json
import threading
import time
from collections import deque

try:
    import cv2
    import numpy as np
except ImportError:                       # pragma: no cover - hard deps in practice
    cv2 = None
    np = None

from core.vision import ai_status

DEFAULT_MODEL = "qwen3-vl:2b-instruct"
DEFAULT_ENDPOINT = "http://localhost:11434"
DEFAULT_TIMEOUT = 90.0
TOKENS_PER_IMAGE = 1200    # measured: ~1,100 per image whatever the crop size (qwen3-vl via Ollama)
PROMPT_TOKENS = 2500       # instructions + definitions + answer, with headroom


def context_for(images):
    """num_ctx that fits `images` frames plus the prompt (rounded up to 1024).
    A fixed 8192 fails with HTTP 400 beyond ~6 images; 16384 for 8 images wastes GPU memory."""
    need = max(int(images), 1) * TOKENS_PER_IMAGE + PROMPT_TOKENS
    return max(4096, -(-need // 1024) * 1024)

MAX_TOKENS = 400           # the reply is a small JSON object; no reasoning budget is needed

FRAMES_MIN, FRAMES_MAX = 8, 12
FRAMES_DEFAULT = 8         # chosen by benchmark: 2B-instruct, 8 frames, ctx 10240, ~9-11 s/call, 100% GPU
FRAMES_BEFORE = 2.5        # seconds of footage before the trigger that may be used
FRAMES_AFTER = 1.0         # seconds after the trigger to wait for (the checker runs async)
RING_SECONDS = 6.0
RING_MIN_GAP = 0.15        # never store two frames closer than this (processed fps is 4-6)
SEND_EDGE = 640            # longest edge of each crop after resizing
MIN_EDGE = 0               # crops smaller than this are upscaled to it (0 = never); set after benchmarking
JPEG_QUALITY_RING = 90
JPEG_QUALITY_SEND = 88

# --- prompts (spec v6 section 9, verbatim) ------------------------------------

SHARED_INSTRUCTIONS = """You are reviewing a short CCTV video from a barangay street camera in the Philippines.
The video is cropped around the person or group of interest. The frames are in time order.
System detections (from the object and pose models): {system_note}
Describe what you see first, then answer.
For true/false fields about objects, answer true only if you can see them.
For likelihood and choice fields, give your best judgment from what is visible.
Pick "unclear" if you cannot tell.
Rate confidence: "high" if the evidence is clearly visible, "medium" if it is
partly visible or you are partly sure, "low" if you are mostly guessing.
Reply only with the JSON below. No other text."""

BLOCKS = {
    "drinking": """{
"observations": "one sentence, under 20 words, only what you see",
"drinking_likelihood": "likely" | "possible" | "unlikely",
"table_chairs_or_seating_visible": true/false,
"drinking_items_visible": true/false,
"scene_type": "drinking_session" | "other_activity" | "unclear",
"confidence": "high" | "medium" | "low"
}
Definitions:
- observations: the people, objects and actions you can see. No conclusions.
- drinking_likelihood: how likely it is that people are drinking at this spot.
"likely" = someone drinks from a bottle, can or cup, or drinks are shared;
"possible" = people are gathered with drinks nearby, but no drinking is seen;
"unlikely" = no sign of drinking.
- table_chairs_or_seating_visible: chairs, stools, benches, or a table used by the people.
- drinking_items_visible: drinks, glasses, cups, plates or snacks set down near the people.
- scene_type: "drinking_session" if people are drinking or gathered with drinks;
"other_activity" ONLY if you can clearly see an ordinary activity, such as a store
selling drinks, someone carrying or delivering drinks, or people eating a meal.
Vehicles or people passing in the background do not count;
"unclear" if you cannot tell.""",
    "smoking": """{
"observations": "one sentence, under 20 words, only what you see",
"smoking_item_visible": true/false,
"hand_to_mouth_activity": "smoking" | "other_activity" | "none" | "unclear",
"confidence": "high" | "medium" | "low"
}
Definitions:
- observations: the person's hands, what they hold, and what they do. If smoke or vapor
is clearly leaving the person's mouth, nose, or a held item, mention it; otherwise do
not mention smoke. No conclusions.
- smoking_item_visible: a small thin object (cigarette) or a small device (vape)
held between the fingers or at the lips.
- hand_to_mouth_activity: what the hand is doing when it is at the mouth.
"smoking" = fingers held together at the lips briefly, then the hand moves away;
may hold a small thin object or exhale smoke;
"other_activity" = an ordinary reason, such as eating, drinking from a bottle or cup,
using a phone, or wiping the face;
"none" = the hand never reaches the mouth;
"unclear" = you cannot tell.""",
    "holdup": """{
"observations": "one sentence, under 20 words, only what you see",
"object_pointed_at_a_person": true/false,
"victim_response_visible": true/false,
"holdup_likelihood": "likely" | "possible" | "unlikely",
"scene_type": "confrontation" | "other_activity" | "unclear",
"confidence": "high" | "medium" | "low"
}
Definitions:
- observations: the people, the object, and how they act toward each other. No conclusions.
- object_pointed_at_a_person: a knife or sharp object is pointed or held toward another person.
- victim_response_visible: a person raising their hands, handing over items, or backing away.
- holdup_likelihood: how likely it is that one person is robbing or threatening another.
"likely" = threatening behaviour is visible; "possible" = tense or unusual, but no clear threat;
"unlikely" = no sign of a threat.
- scene_type: "confrontation" if one person appears to be threatening another;
"other_activity" ONLY if you can clearly see ordinary use of the object, such as vending,
food preparation, work, or play; "unclear" if you cannot tell.""",
}

KINDS = tuple(BLOCKS)

_ENUMS = {
    "drinking_likelihood": ["likely", "possible", "unlikely"],
    "holdup_likelihood": ["likely", "possible", "unlikely"],
    "scene_type": {"drinking": ["drinking_session", "other_activity", "unclear"],
                   "holdup": ["confrontation", "other_activity", "unclear"]},
    "hand_to_mouth_activity": ["smoking", "other_activity", "none", "unclear"],
    "confidence": ["high", "medium", "low"],
}


def kind_for(violation):
    """Alert/violation kind name -> the checker's kind (thief/knife -> holdup)."""
    v = str(violation or "").lower()
    if v in KINDS:
        return v
    if v in ("thief", "knife", "weapon", "holdup", "snatch", "carnapping", "property"):
        return "holdup"
    return None


def response_schema(kind):
    """JSON schema handed to Ollama's `format`, so the reply cannot be prose or
    use a value outside the spec's options. observations is first so the model
    describes before it judges."""
    props = {"observations": {"type": "string"}}
    for field in ai_status.REQUIRED_FIELDS[kind]:
        if field == "observations":
            continue
        if field in ("drinking_likelihood", "holdup_likelihood", "hand_to_mouth_activity", "confidence"):
            props[field] = {"type": "string", "enum": _ENUMS[field]}
        elif field == "scene_type":
            props[field] = {"type": "string", "enum": _ENUMS["scene_type"][kind]}
        else:
            props[field] = {"type": "boolean"}
    return {"type": "object", "properties": props,
            "required": list(ai_status.REQUIRED_FIELDS[kind])}


def system_note(kind, *, puff_only=False, people=None, minutes=None, puffs=None, stationary=False, extra=""):
    """What the detectors found, in the spec's wording (section 9 examples)."""
    if kind == "drinking":
        note = "bottle detected"
        if people and stationary and minutes is not None and minutes >= 1:
            note += f"; {people} people stationary together for {minutes:g} minutes"
        elif people and stationary:
            note += f"; {people} people stationary together"
        elif people:
            note += f"; {people} people close together"
    elif kind == "smoking":
        n = int(puffs or 0)
        times = f"{n} time" + ("" if n == 1 else "s")
        note = (f"hand reached the mouth {times}; no smoking item detected" if puff_only
                else "smoking item detected" + (f"; hand reached the mouth {times}" if n else ""))
    else:
        note = "knife detected" + ("; two people standing still close together" if people else "")
    return note + (f"; {extra}" if extra else "")


def build_messages(kind, note, images_b64):
    prompt = SHARED_INSTRUCTIONS.format(system_note=note) + "\n\n" + BLOCKS[kind]
    return [{"role": "user", "content": prompt, "images": list(images_b64)}]


# --- the rolling full-resolution buffer ---------------------------------------

class FrameRing:
    """Last few seconds of FULL-RESOLUTION frames, JPEG-compressed in memory.

    A 2560x1440 frame is 11 MB raw, so ~25 of them would be a quarter-gigabyte;
    as JPEG (q90) each is ~0.5 MB and costs ~10 ms to encode. Boxes of every
    tracked subject are stored with the frame so the crop can follow the
    subject through time rather than reuse the last box.

    Thread-safe: the detector thread adds, the checker's worker reads.
    """

    def __init__(self, seconds=RING_SECONDS, min_gap=RING_MIN_GAP):
        self.seconds = seconds
        self.min_gap = min_gap
        self._items = deque()          # (ts, jpeg_bytes, {key: box})
        self._lock = threading.Lock()
        self._pending_boxes = {}
        self._clean = None

    def stash(self, frame):
        """Keep a clean copy of THIS frame (before any drawing) until commit(). The
        detectors annotate the frame in place, so the raw pixels must be taken first."""
        self._clean = None if frame is None else frame.copy()

    def commit(self, ts):
        """End of the frame: store the stashed frame with the boxes noted during it.
        Called once per engine per frame; a second call for the same frame only merges
        its engine's boxes into the stored entry."""
        clean, self._clean = self._clean, None
        if clean is not None:
            self.add(clean, ts)
            return
        boxes, self._pending_boxes = self._pending_boxes, {}
        with self._lock:
            if self._items and abs(ts - self._items[-1][0]) <= self.min_gap:
                self._items[-1][2].update(boxes)

    def note_box(self, key, box):
        """Record this frame's box for a subject; stored with the next add()."""
        if box is not None:
            self._pending_boxes[key] = tuple(int(v) for v in box)

    def add(self, frame, ts):
        boxes, self._pending_boxes = self._pending_boxes, {}
        if frame is None or cv2 is None:
            return False
        with self._lock:
            if self._items and ts - self._items[-1][0] < self.min_gap:
                # keep the boxes for the stored frame's neighbours: merge into the last entry
                self._items[-1][2].update(boxes)
                return False
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY_RING])
        if not ok:
            return False
        with self._lock:
            self._items.append((ts, buf.tobytes(), boxes))
            while self._items and ts - self._items[0][0] > self.seconds:
                self._items.popleft()
        return True

    def window(self, t0, before=FRAMES_BEFORE, after=FRAMES_AFTER):
        with self._lock:
            return [it for it in self._items if t0 - before <= it[0] <= t0 + after]

    def __len__(self):
        return len(self._items)


def select_frames(items, t0, n=FRAMES_MAX, minimum=FRAMES_MIN):
    """Pick up to `n` items nearest the trigger time `t0` (bunched, not evenly
    spread), returned in time order. Fewer than `minimum` frames is still
    returned (a short clip): the caller decides whether that is enough."""
    near = sorted(items, key=lambda it: abs(it[0] - t0))[:n]
    return sorted(near, key=lambda it: it[0])


# --- crops ---------------------------------------------------------------------

CROP_PAD = {"smoking": 0.25, "drinking": 0.45, "holdup": 0.35}
SMOKING_BODY_FRACTION = 0.55      # head, shoulders and hands: the top of the person box


def crop_box(kind, boxes, frame_shape):
    """The crop rectangle for one frame, from the subject's box(es).

    smoking  half body (top part of the person box)
    drinking the whole group (union of member boxes) plus surrounding space
    holdup   both people (union)
    """
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[2] for b in boxes)
    y2 = max(b[3] for b in boxes)
    if kind == "smoking":
        y2 = y1 + (y2 - y1) * SMOKING_BODY_FRACTION
    h, w = frame_shape[:2]
    bw, bh = max(x2 - x1, 1), max(y2 - y1, 1)
    pad = CROP_PAD[kind]
    x1, x2 = max(0, x1 - bw * pad), min(w, x2 + bw * pad)
    y1, y2 = max(0, y1 - bh * pad), min(h, y2 + bh * pad)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return int(x1), int(y1), int(x2), int(y2)


def crop_for(kind, jpeg_bytes, boxes, edge=SEND_EDGE, min_edge=None):
    """Decode the full-res frame, cut the crop, THEN resize. Returns a BGR array
    (or None). Cropping before resizing keeps the person's pixels."""
    if cv2 is None:
        return None
    frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    rect = crop_box(kind, boxes, frame.shape)
    if rect is None:
        return None
    x1, y1, x2, y2 = rect
    img = frame[y1:y2, x1:x2]
    h, w = img.shape[:2]
    floor = MIN_EDGE if min_edge is None else min_edge
    if max(h, w) > edge:
        s = edge / float(max(h, w))
        img = cv2.resize(img, (max(int(w * s), 1), max(int(h * s), 1)), interpolation=cv2.INTER_AREA)
    elif floor and max(h, w) < floor:
        s = floor / float(max(h, w))
        img = cv2.resize(img, (max(int(w * s), 1), max(int(h * s), 1)), interpolation=cv2.INTER_CUBIC)
    return img


def encode(img):
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY_SEND])
    return buf.tobytes() if ok else None


# --- the call --------------------------------------------------------------------

class CheckResult:
    """One AI check: `reply` is the validated JSON (or None -> unavailable)."""

    def __init__(self, kind, reply=None, error="", model="", seconds=None, frames=0,
                 note="", raw="", frame_files=None):
        self.kind = kind
        self.reply = reply
        self.error = error
        self.model = model
        self.seconds = seconds
        self.frames = frames
        self.note = note
        self.raw = raw
        self.frame_files = list(frame_files or [])

    @property
    def ok(self):
        return self.reply is not None

    def as_dict(self):
        return {"kind": self.kind, "ok": self.ok, "reply": self.reply, "error": self.error,
                "model": self.model, "seconds": None if self.seconds is None else round(self.seconds, 2),
                "frames": self.frames, "system_note": self.note, "frame_files": self.frame_files}


class OllamaClient:
    def __init__(self, model=DEFAULT_MODEL, endpoint=DEFAULT_ENDPOINT, timeout=DEFAULT_TIMEOUT,
                 num_ctx=None, max_tokens=MAX_TOKENS):
        self.model = model or DEFAULT_MODEL
        self.endpoint = (endpoint or DEFAULT_ENDPOINT).rstrip("/")
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self.num_ctx = num_ctx
        self.max_tokens = max_tokens

    def _post(self, path, payload):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"{self.endpoint}{path}", data=json.dumps(payload).encode("utf8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf8")[:900]
            except Exception:                                  # noqa: BLE001
                pass
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from None

    def available(self):
        """(ok, message): Ollama reachable and the model pulled."""
        import urllib.request
        try:
            with urllib.request.urlopen(f"{self.endpoint}/api/tags", timeout=5) as resp:
                names = {m.get("name", "") for m in json.loads(resp.read().decode("utf8")).get("models", [])}
        except Exception as exc:
            return False, f"Ollama is not reachable at {self.endpoint} ({exc}). Start it with: ollama serve"
        if self.model not in names:
            return False, f"model {self.model!r} is not pulled. Run: ollama pull {self.model}"
        return True, "ok"

    def chat(self, kind, note, images_jpeg):
        started = time.monotonic()
        images = [base64.standard_b64encode(b).decode("ascii") for b in images_jpeg]
        reply = self._post("/api/chat", {
            "model": self.model,
            "messages": build_messages(kind, note, images),
            "format": response_schema(kind),
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_predict": self.max_tokens,
                        "num_ctx": self.num_ctx or context_for(len(images))},
        })
        text = (reply.get("message") or {}).get("content", "")
        return text, time.monotonic() - started, reply


def parse_reply(kind, text):
    """JSON text -> validated reply dict, or None."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return ai_status.validate_reply(kind, data)


def check(client, kind, note, images_jpeg, frame_files=None):
    """Send the prepared frames and return a CheckResult. Never raises."""
    if kind not in KINDS:
        return CheckResult(kind, error=f"no prompt for {kind!r}")
    if not images_jpeg:
        return CheckResult(kind, error="no frames", note=note, model=client.model)
    try:
        text, seconds, _ = client.chat(kind, note, images_jpeg)
    except Exception as exc:                                   # noqa: BLE001
        return CheckResult(kind, error=f"{type(exc).__name__}: {exc}", note=note, model=client.model,
                           frames=len(images_jpeg), frame_files=frame_files)
    reply = parse_reply(kind, text)
    return CheckResult(kind, reply=reply, error="" if reply else "reply was not valid JSON for this question set",
                       model=client.model, seconds=seconds, frames=len(images_jpeg), note=note,
                       raw=text, frame_files=frame_files)


def prepare_images(kind, items, keys, min_edge=None, edge=SEND_EDGE):
    """items (from FrameRing) -> list of JPEG bytes cropped around the subject.

    `keys` lists the subject keys whose boxes are unioned: one person for
    smoking, the group's members for drinking, both people for a holdup. A frame
    where the subject was not seen reuses the last known box."""
    last = None
    out = []
    for ts, jpg, boxes in items:
        use = [boxes[k] for k in keys if k in boxes]
        if not use and last is not None:
            use = last
        if not use:
            continue
        last = use
        img = crop_for(kind, jpg, use, edge=edge, min_edge=min_edge)
        data = encode(img) if img is not None else None
        if data:
            out.append(data)
    return out


def check_async(client, ring, kind, keys, t0, note, on_done, save_dir=None, label="ai",
                frames=FRAMES_DEFAULT, edge=SEND_EDGE, wait=False, url_for=None):
    """Wait FRAMES_AFTER for post-trigger frames, then crop, send and call
    on_done(result) on a worker thread (or block until done when `wait`).

    The frames actually sent are written to `save_dir` (if given); `url_for(path)`
    turns a saved path into what is stored with the alert."""
    def _run():
        try:
            time.sleep(FRAMES_AFTER)
            items = select_frames(ring.window(t0), t0, n=frames)
            images = prepare_images(kind, items, keys, edge=edge)
            files = []
            if save_dir and images:
                import os
                os.makedirs(save_dir, exist_ok=True)
                for i, data in enumerate(images):
                    path = os.path.join(save_dir, f"{label}_{i:02d}.jpg")
                    with open(path, "wb") as fh:
                        fh.write(data)
                    files.append(url_for(path) if url_for else path)
            result = check(client, kind, note, images, frame_files=files)
        except Exception as exc:                               # noqa: BLE001
            result = CheckResult(kind, error=f"{type(exc).__name__}: {exc}")
        try:
            on_done(result)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("AI checker callback failed")

    if wait:                    # synchronous mode: the caller waits (Settings: async off)
        _run()
        return None
    # Not a daemon: when a clip ends (or Ctrl+C), the process waits the few seconds a running
    # check needs, so its answer is still stored on the alert.
    thread = threading.Thread(target=_run, daemon=False, name=f"ai-{kind}")
    thread.start()
    return thread
