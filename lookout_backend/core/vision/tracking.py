"""Per-person tracking for the violation watchers (smoking, thief).

Greedy IoU association: each frame's person boxes (from recognition.
detect_persons) are matched to existing tracks by highest IoU; unmatched boxes
start new tracks; tracks unseen for TRACK_MAX_GAP seconds are dropped. This is
deliberately simple — no Kalman filter or appearance embedding — because the
watchers only need identity to hold *between adjacent frames* so that dwell
timers and vote windows follow a person instead of the whole scene. Person
boxes move little frame-to-frame at watch-camera framerates, so greedy IoU is
enough.

Each Track carries its own temporal-confirmation state (vote window, dwell
start, alert cooldown), so two people in frame are timed and alerted
independently. Detections that no person box claims (person detector missed,
or the object isn't on a person) fall back to the tracker's `scene`
pseudo-track so nothing is silently dropped.

No Django model access here — same rule as recognition.py, pure CV plumbing.
"""

from collections import deque

from .recognition import _iou

TRACK_MATCH_IOU = 0.3   # min IoU for a person box to continue an existing track
TRACK_MAX_GAP = 2.0     # seconds a track survives without being re-detected
ASSIGN_MARGIN = 0.15    # person-box expansion (fraction) when claiming detections


class Track:
    """One tracked person (or the scene pseudo-track, id 0) and its
    temporal-confirmation state."""

    def __init__(self, track_id, box, now, vote_window):
        self.id = track_id
        self.box = box                    # (x1,y1,x2,y2), None for the scene track
        self.last_seen = now              # last frame the person box was matched
        self.votes = deque(maxlen=vote_window)  # rolling 1/0 detection hits
        self.dets = []                    # last non-empty detections, for evidence
        self.threat_since = None          # dwell timer start
        self.last_threat_seen = 0.0       # for the grace-period reset
        self.last_alerted_at = 0.0        # per-person alert cooldown


class PersonTracker:
    def __init__(self, vote_window, iou_match=TRACK_MATCH_IOU, max_gap=TRACK_MAX_GAP):
        self._next_id = 1
        self.tracks = []
        self.vote_window = vote_window
        self.iou_match = iou_match
        self.max_gap = max_gap
        self.scene = Track(0, None, 0.0, vote_window)

    def update(self, person_boxes, now):
        """Matches person boxes to tracks (greedy, best IoU first), starts
        tracks for unmatched boxes, expires stale tracks. Returns live tracks."""
        pairs = []
        for t in self.tracks:
            for i, pb in enumerate(person_boxes):
                iou = _iou(t.box, pb)
                if iou >= self.iou_match:
                    pairs.append((iou, t, i))
        pairs.sort(key=lambda p: p[0], reverse=True)

        matched_tracks, matched_boxes = set(), set()
        for _, t, i in pairs:
            if id(t) in matched_tracks or i in matched_boxes:
                continue
            t.box = tuple(int(v) for v in person_boxes[i][:4])
            t.last_seen = now
            matched_tracks.add(id(t))
            matched_boxes.add(i)

        for i, pb in enumerate(person_boxes):
            if i not in matched_boxes:
                self.tracks.append(Track(
                    self._next_id, tuple(int(v) for v in pb[:4]), now, self.vote_window,
                ))
                self._next_id += 1

        self.tracks = [t for t in self.tracks if now - t.last_seen <= self.max_gap]
        return self.tracks

    def assign(self, detections):
        """Splits (x1,y1,x2,y2,conf,label) detections among tracks by box-center
        containment in the (slightly expanded) person box. A detection landing in
        overlapping people goes to the smallest containing box — the person it
        is most tightly on. Returns ({track: [dets]}, leftovers)."""
        per_track = {t: [] for t in self.tracks}
        leftovers = []
        for det in detections:
            x1, y1, x2, y2 = det[:4]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            best, best_area = None, None
            for t in self.tracks:
                bx1, by1, bx2, by2 = t.box
                mx = (bx2 - bx1) * ASSIGN_MARGIN
                my = (by2 - by1) * ASSIGN_MARGIN
                if bx1 - mx <= cx <= bx2 + mx and by1 - my <= cy <= by2 + my:
                    area = (bx2 - bx1) * (by2 - by1)
                    if best is None or area < best_area:
                        best, best_area = t, area
            if best is None:
                leftovers.append(det)
            else:
                per_track[best].append(det)
        return per_track, leftovers
