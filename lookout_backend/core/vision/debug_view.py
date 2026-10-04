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
    def __init__(self, directory, interval=FRAME_INTERVAL):
        self._area = None      # a fixed region to draw; see set_area
        self.dir = directory
        self.interval = interval
        os.makedirs(self.dir, exist_ok=True)
        self._clean = None
        self._last_pub = 0.0
        self._entries = {}
        self._seq = 0
        self._frame_wh = (0, 0)
        self.published = 0

    @classmethod
    def from_env(cls):
        directory = os.environ.get("LOOKOUT_DEBUG_DIR", "").strip()
        return cls(directory) if directory and cv2 is not None else None

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
        the frame in place, so the pixels must be copied BEFORE any drawing)."""
        if frame is not None and time.monotonic() - self._last_pub >= self.interval:
            self._clean = frame.copy()

    def note(self, key, violation, ident, box, status, score_pct, indicators, multipliers, momentum, kind="track"):
        """Record the latest state of one subject. Called on every processed frame; cheap."""
        now = time.time()
        old = self._entries.get(key)
        changed = old["changed"] if old and old["status"] == status else now
        self._entries[key] = {
            "key": "%s:%s" % (key[0], key[1]) if isinstance(key, tuple) else str(key),
            "id": ident, "violation": violation, "kind": kind,
            "box": [int(v) for v in box] if box is not None else None,
            "status": status, "score": score_pct,
            "indicators": indicators, "multipliers": multipliers,
            "momentum": round(float(momentum), 2),
            "seen": now, "changed": changed,
        }

    def commit(self, media_ts=None):
        """End of the frame: publish if a clean frame was stashed."""
        clean, self._clean = self._clean, None
        if clean is None:
            return
        self._last_pub = time.monotonic()
        now = time.time()
        for key in [k for k, e in self._entries.items() if now - e["seen"] > KEEP_SECONDS]:
            del self._entries[key]
        h, w = clean.shape[:2]
        scale = min(1.0, MAX_WIDTH / float(w))
        small = cv2.resize(clean, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else clean
        ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            return
        self._seq += 1
        state = {
            "seq": self._seq, "wall": now, "media_ts": media_ts,
            "frame_w": w, "frame_h": h, "area": self._area,
            "subjects": sorted(self._entries.values(), key=lambda e: (e["violation"], str(e["id"]))),
        }
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


def indicator_list(score):
    """[{"name", "points"}] for the indicators that fired, biggest first."""
    cues = getattr(score, "cues", None) or {}
    items = [{"name": _pretty(n), "points": int(round(float(w) * 100))} for n, w in cues.items()]
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
    try:
        with open(os.path.join(directory, "frame.jpg"), "rb") as fh:
            return state, fh.read()
    except OSError:
        return state, None
