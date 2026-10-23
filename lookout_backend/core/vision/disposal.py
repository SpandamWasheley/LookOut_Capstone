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

Same rule as recognition.py and theft.py: NO Django model access in here, so
stage 1 is unit-testable without a database, a GPU or a camera. The YOLO-World
import is lazy for the same reason -- importing this module costs cv2 and
numpy only.
"""

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

# --- stage 2: tunables -----------------------------------------------------
CROP_PAD = 0.5              # pad the crop by 50% of object size; context helps
MIN_CROP = 96               # px, minimum crop side at full resolution

# What we are looking for, and the things that also sit still and would
# otherwise alert. Order matters only in that the first len(PROMPTS) class ids
# are the garbage ones (see garbage_class_ids).
PROMPTS = ["plastic bag", "garbage bag", "trash bag", "pile of trash", "sack"]
REJECT = ["person", "dog", "cat", "motorcycle", "car", "bicycle", "chair", "box"]

# Zero-shot scores run low compared with a fine-tuned model's -- an open-vocab
# head spreads probability over the whole prompt set, so a correct bag often
# lands near 0.2. A "high confidence" intuition calibrated on the merged model
# would reject nearly every true positive here.
CONFIRM_CONF = 0.15

DEFAULT_WORLD_MODEL = "yolov8s-worldv2.pt"   # auto-downloads on first use

# --- defaults mirrored in SystemSettings ------------------------------------
STATIC_SECONDS = 5.0        # must stay still this long before stage 2 runs
COOLDOWN_SECONDS = 120      # do not re-alert the same spot within this window
COOLDOWN_DIST = 60          # px at PROC_WIDTH that counts as "the same spot"


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
        self.label = ""
        self.score = 0.0

    @property
    def center(self):
        x, y, w, h = self.box
        return x + w / 2.0, y + h / 2.0

    def still_for(self, now):
        return now - self.first


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
                 area=None):
        self.area = area
        self.proc_width = proc_width
        self.warmup_seconds = warmup_seconds
        self.min_area_frac = min_area_frac
        self.max_area_frac = max_area_frac
        self.match_dist = match_dist
        self.candidate_grace = candidate_grace

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
        self._next_id = 1
        self._t0 = None

    def update(self, frame, now=None, ignore_boxes=None):
        """Process one frame. Returns the current candidate list (empty while warming).

        `ignore_boxes` is an optional list of (x, y, w, h) in FULL-RESOLUTION
        frame coordinates — people and vehicles the caller has already
        identified. Blobs sitting inside one never become candidates. Full-res
        rather than PROC_WIDTH because that is what a detector hands back, and
        converting here keeps the one scale factor in one place.
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

        # While warming, BOTH models learn fast -- the point is to converge on
        # the empty scene quickly. The slow/fast split only starts afterwards,
        # which is why a bag already lying there when the detector starts is
        # learned as background and never alerts (a stated limitation).
        long_lr = WARMUP_LR if self.warming else LONG_LR
        short_lr = WARMUP_LR if self.warming else SHORT_LR
        m_long = self._bg_long.apply(blur, learningRate=long_lr)
        m_short = self._bg_short.apply(blur, learningRate=short_lr)
        self.small = small

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

        if ignore_boxes:
            # Same reason, same place: a person who stands still must never get
            # an id, not merely fail the bag check 5 seconds later.
            scaled = [_padded(tuple(v * self.scale for v in box)) for box in ignore_boxes]
            self.ignored = [b for b in blobs
                            if any(_coverage(b, o) >= IGNORE_COVERAGE for o in scaled)]
            blobs = [b for b in blobs if b not in self.ignored]
        else:
            self.ignored = []

        self._match(blobs, now)
        return self.candidates

    def _match(self, blobs, now):
        """Nearest-centre association of blobs to existing candidates.

        Deliberately simple: a dumped bag does not move, so there is no motion
        model worth running. A candidate that goes unseen longer than
        `candidate_grace` is dropped, which is what stops a flickering blob
        from accruing still-time forever.
        """
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
                self.candidates.append(Candidate(self._next_id, blob, now))
                claimed.add(self._next_id)
                self._next_id += 1
        self.candidates = [c for c in self.candidates
                           if now - c.last <= self.candidate_grace]

    def due_for_check(self, now, static_seconds, recheck_seconds=RECHECK_SECONDS):
        """Candidates that have been still long enough and are not already confirmed.

        A rejected candidate is re-offered after `recheck_seconds` rather than
        being written off: stage 2 sees one crop at one moment, and a bag that
        was half-occluded by a passing person when first checked deserves a
        second look.
        """
        out = []
        for cand in self.candidates:
            if cand.confirmed or cand.still_for(now) < static_seconds:
                continue
            if cand.last_check is not None and now - cand.last_check < recheck_seconds:
                continue
            out.append(cand)
        return out


def crop_full_res(frame, box, scale, *, pad_frac=CROP_PAD, min_crop=MIN_CROP):
    """Map a PROC_WIDTH box back to full resolution and crop it with padding.

    Padded because an open-vocabulary head classifies a crop by what the whole
    crop looks like; a tight box around a white bag is just a white rectangle,
    while the same bag against visible pavement reads as a bag.
    """
    height, width = frame.shape[:2]
    x, y, w, h = [v / scale for v in box]
    pad = pad_frac * max(w, h)
    side_w = max(w + 2 * pad, min_crop)
    side_h = max(h + 2 * pad, min_crop)
    cx, cy = x + w / 2.0, y + h / 2.0
    x1, y1 = int(max(0, cx - side_w / 2)), int(max(0, cy - side_h / 2))
    x2, y2 = int(min(width, cx + side_w / 2)), int(min(height, cy + side_h / 2))
    return frame[y1:y2, x1:x2]


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


def confirm(model, crop, garbage_ids=None, conf=CONFIRM_CONF, imgsz=640):
    """Stage 2. Garbage wins only if it beats every reject class on this crop.

    Returns (is_garbage, (garbage_label, score), (reject_label, score)).

    The comparison against the best reject class is the real filter. A bare
    confidence floor would fire on a dog at 0.4 just as readily as a sack at
    0.4; requiring the garbage score to lead means the model has to prefer
    "sack" over "dog" for this crop, which is a much stronger claim.
    """
    if crop is None or getattr(crop, "size", 0) == 0:
        return False, ("", 0.0), ("", 0.0)
    if garbage_ids is None:
        garbage_ids = garbage_class_ids()

    result = model.predict(crop, conf=conf, imgsz=imgsz, verbose=False)[0]
    best_g, best_r = ("", 0.0), ("", 0.0)
    for cls, score in zip(result.boxes.cls.tolist(), result.boxes.conf.tolist()):
        name = result.names[int(cls)]
        if int(cls) in garbage_ids:
            if score > best_g[1]:
                best_g = (name, float(score))
        elif score > best_r[1]:
            best_r = (name, float(score))
    # `>=` on the tie so a crop the model scores identically for both still
    # counts: stage 1 already established something was dumped and left.
    is_garbage = best_g[1] > 0 and best_g[1] >= best_r[1]
    return is_garbage, best_g, best_r


class SpatialCooldown:
    """Place-keyed alert cooldown, the same idea as the shared layer's D4.

    Keyed on POSITION, not candidate id, because stage 1 drops a candidate
    after `candidate_grace` and the replacement starts with a clean timer --
    so an id-keyed cooldown would let one bag re-alert every few seconds. This
    is the same failure D4 was added to close for the shared layer.
    """

    def __init__(self, seconds=COOLDOWN_SECONDS, distance=COOLDOWN_DIST):
        self.seconds = seconds
        self.distance = distance
        self._log = []

    def blocks(self, center, now):
        self._log = [(c, t) for c, t in self._log if now - t < self.seconds]
        for (ax, ay), _t in self._log:
            if float(np.hypot(center[0] - ax, center[1] - ay)) < self.distance:
                return True
        return False

    def remember(self, center, now):
        self._log.append((tuple(center), now))
