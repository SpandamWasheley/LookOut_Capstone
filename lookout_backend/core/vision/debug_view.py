"""Live processing view: what the detector is tracking right now, for the Testing view and the
Panel view on Run Detection and Live Feeds.

The detector writes two small files into a directory (set with LOOKOUT_DEBUG_DIR; nothing is
written when it is unset):

    frame.jpg    the latest CLEAN frame (no boxes, no labels), downscaled, about 2 per second
    state.json   every subject being tracked: ID, violation, status, score, indicators, momentum,
                 box, when it was last seen and when its status last changed

The web page draws the boxes and labels itself on top of the clean frame, in whichever view mode
is selected. Keeping the frame clean means (a) no extra drawing work in the detector, (b) the two
view modes cost the same, and (c) a label can never end up in an evidence image or clip.

Cost: one frame copy and one resize + JPEG encode about every half second, no extra model calls.
"""
import json
import os
import time

try:
    import cv2
except ImportError:                       # pragma: no cover
    cv2 = None

FRAME_INTERVAL = 0.5          # seconds between published frames (~2 per second)
MAX_WIDTH = 960               # published frame width
JPEG_QUALITY = 70
KEEP_SECONDS = 3.0            # a subject not seen for this long leaves the list
BELOW = "Below Monitoring"


def _pretty(name):
    return str(name).replace("_", " ")


class DebugPublisher:
    def __init__(self, directory, interval=FRAME_INTERVAL, companion=False):
        self._area = None      # a fixed region to draw; see set_area
        self.dir = directory
        self.interval = interval
        # companion=True: write subjects to state_companion.json and skip the
        # frame write so the primary detector's frame.jpg is never overwritten.
        self._companion = companion
        os.makedirs(self.dir, exist_ok=True)
        self._clean = None
        self._clean_orig_wh = None   # original (w, h) before the stash downscale
        self._last_pub = 0.0
        self._entries = {}
        self._seq = 0
        self._frame_wh = (0, 0)
        self.published = 0

    @classmethod
    def from_env(cls, companion=False):
        directory = os.environ.get("LOOKOUT_DEBUG_DIR", "").strip()
        return cls(directory, companion=companion) if directory and cv2 is not None else None

    # ---- per frame ---------------------------------------------------------------

    def set_area(self, points):
        """A FIXED region the detector judges against — parking's no-parking
        polygon — in the pixel coordinates of the published frame.

        Published with the state so the page can draw it, rather than baked
        into the image: the frame sent here is deliberately clean (the page
        draws its own labels), which meant the one detector whose whole rule is
        "is the vehicle inside this shape" published no way to see the shape.
        Debugging it came down to inferring the polygon's position from which
        vehicles happened to score, which is as slow as it sounds.

        Set once; it does not change during a run.
        """
        self._area = [[int(x), int(y)] for x, y in points] if points else None

    def stash(self, frame):
        """Take a clean copy of this frame, but only when a publish is due (the detectors draw on
        the frame in place, so the pixels must be copied BEFORE any drawing).

        Downscaled to MAX_WIDTH here rather than in commit() so a 2560×1440 frame
        (10.5 MiB) is never kept in memory as a full-res copy — that caused OOM
        crashes on high-resolution clips when RAM was under pressure.  Original
        dimensions are stored separately so state.json still reports the original
        frame_w / frame_h (which is the coordinate space the box data lives in).
        """
        if frame is None or time.monotonic() - self._last_pub < self.interval:
            return
        h, w = frame.shape[:2]
        self._clean_orig_wh = (w, h)
        if w > MAX_WIDTH:
            scale = MAX_WIDTH / float(w)
            self._clean = cv2.resize(frame, (int(w * scale), int(h * scale)),
                                     interpolation=cv2.INTER_AREA)
        else:
            self._clean = frame.copy()

    def note(self, key, violation, ident, box, status, score_pct, indicators, multipliers, momentum,
             kind="track", detail="", tag=""):
        """Record the latest state of one subject. Called on every processed frame; cheap.

        `tag` is an optional SHORT line for the box on the picture -- the whole
        of what the rule used, in one phrase ("car #9 · 100% in · 24/60s ·
        STOP"). The page falls back to assembling a line out of every field
        when a detector does not set one, which on a busy street produced
        labels wider than the vehicles they pointed at, overlapping each other
        and hiding the footage underneath. The full breakdown is still on the
        panel beside the picture, where it costs no pixels of the frame.

        `detail` is an optional sub-label shown beside the status. One status can
        mean several different things — disposal's Possible covers waiting for an
        owner to leave, waiting for the bag check, and a bag the check scored too
        low to act on — and those call for different responses from whoever is
        watching. Carried as its own field rather than folded into `status` so the
        status stays one of the system's four, and so the page keeps colouring by
        status alone.
        """
        now = time.time()
        old = self._entries.get(key)
        # Keyed on status only: a sub-label changing (Checking -> Weak match) is
        # not a change of status, and restarting the "changed" clock for it would
        # misreport how long a subject has held its status.
        changed = old["changed"] if old and old["status"] == status else now
        self._entries[key] = {
            "key": "%s:%s" % (key[0], key[1]) if isinstance(key, tuple) else str(key),
            "id": ident, "violation": violation, "kind": kind,
            "box": [int(v) for v in box] if box is not None else None,
            "status": status, "detail": detail, "score": score_pct, "tag": tag,
            "indicators": indicators, "multipliers": multipliers,
            "momentum": round(float(momentum), 2),
            "seen": now, "changed": changed,
        }

    def commit(self, media_ts=None):
        """End of the frame: publish if a clean frame was stashed."""
        clean, self._clean = self._clean, None
        orig_wh, self._clean_orig_wh = self._clean_orig_wh, None
        if clean is None:
            return
        self._last_pub = time.monotonic()
        now = time.time()
        for key in [k for k, e in self._entries.items() if now - e["seen"] > KEEP_SECONDS]:
            del self._entries[key]
        # Use the original frame dimensions for frame_w / frame_h: the page
        # maps detector box coordinates (which are in the original frame's pixel
        # space) onto the displayed image using these values, so they must match
        # the coordinate space the boxes live in even though the JPEG we're
        # about to write is already downscaled.
        orig_w, orig_h = orig_wh if orig_wh else (clean.shape[1], clean.shape[0])
        self._seq += 1
        state = {
            "seq": self._seq, "wall": now, "media_ts": media_ts,
            "frame_w": orig_w, "frame_h": orig_h, "area": self._area,
            "subjects": sorted(self._entries.values(), key=lambda e: (e["violation"], str(e["id"]))),
        }
        if self._companion:
            # Subjects-only: write to a separate file so the primary's
            # frame.jpg and state.json are never touched. read_state() merges
            # these subjects into the primary's state before serving the page.
            self._write(os.path.join(self.dir, "state_companion.json"), json.dumps(state).encode("utf8"), binary=True)
            self.published += 1
            return
        # clean is already downscaled to MAX_WIDTH by stash(); no second resize.
        ok, buf = cv2.imencode(".jpg", clean, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            return
        self._write(os.path.join(self.dir, "frame.jpg"), buf.tobytes(), binary=True)
        self._write(os.path.join(self.dir, "state.json"), json.dumps(state).encode("utf8"), binary=True)
        self.published += 1

    @staticmethod
    def _write(path, data, binary=True):
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)       # atomic: a reader never sees half a file
        except OSError:
            pass                        # a reader had it open; the next publish will land


def status_for(score):
    """Display status for a Score / Evidence: Below Monitoring, Monitoring, Possible, Likely."""
    from core.vision import scoring
    level = getattr(score, "level", None) if score is not None else None
    if not level or level == scoring.NONE:
        return BELOW
    return scoring.label_of(level)


# Holdup indicators read as their Layer E code plus a plain name.
HOLDUP_LABELS = {
    "E14": "E14 Knife near a hand",
    "E12": "E12 Pair frozen close together",
    "E10": "E10 Loitering first",
    "E11E12": "E11→E12 Approach then freeze",
    "E20": "E20 Time of day",
}


def indicator_list(score):
    """[{"name", "points"}] for the indicators that fired, biggest first."""
    cues = getattr(score, "cues", None) or {}
    items = [{"name": HOLDUP_LABELS.get(n) or _pretty(n), "points": int(round(float(w) * 100))} for n, w in cues.items()]
    return sorted(items, key=lambda i: -i["points"])


def read_state(directory, since=None):
    """For the web API: (state dict or None, jpeg bytes or None). `since` skips the frame when
    the client already has that sequence number."""
    try:
        with open(os.path.join(directory, "state.json"), "rb") as fh:
            state = json.loads(fh.read().decode("utf8"))
    except (OSError, ValueError):
        return None, None
    if since is not None and state.get("seq") == since:
        return state, None
    # Merge subjects written by a companion (e.g. watch_disposal running beside
    # watch_merged). The companion writes state_companion.json with its zone and
    # candidate boxes so they appear in the same live view without overwriting
    # the primary's frame. The area is taken from the companion when the primary
    # has none (Cyan / merged4 has no zone of its own).
    try:
        with open(os.path.join(directory, "state_companion.json"), "rb") as fh:
            companion = json.loads(fh.read().decode("utf8"))
        state["subjects"] = (state.get("subjects") or []) + companion.get("subjects", [])
        if not state.get("area") and companion.get("area"):
            state["area"] = companion["area"]
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(directory, "frame.jpg"), "rb") as fh:
            return state, fh.read()
    except OSError:
        return state, None
