"""Shared video-analysis core for the LookOut detection system.

One YOLOv8 pass per (sampled) frame is split into three groups of interest:
  - vehicles  -> illegal parking / obstruction (dwell + optional no-parking zones)
  - drinks    -> public drinking (a bottle / cup / wine glass held by a person)
  - persons   -> used to associate a drink container with someone

`process_video()` runs the whole thing and returns an annotated video path plus a
structured report (with evidence thumbnails), so both the CLI and the web app can
reuse it.

Honest limits (state these in your capstone):
  * COCO's `bottle` covers ANY bottle — water or alcohol look identical to the
    model. This flags a *drink container near a person*, i.e. "possible public
    drinking", not confirmed alcohol.
  * Tricycles / tok-tok are reported as `motorcycle` (no COCO tricycle class).
"""

import time
from pathlib import Path

import cv2
import numpy as np

import vehicle_detection as vd  # reuse the cached YOLO model + zone helpers

VEHICLE_CLASSES = {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
DRINK_CLASSES = {39: "bottle", 40: "wine glass", 41: "cup"}
PERSON_CLASS = 0


# ---- detection -----------------------------------------------------------

def detect_all(frame, conf):
    """Runs YOLO once and returns (vehicles, drinks, persons) lists of dicts."""
    model = vd.load_model()
    results = model(frame, verbose=False)[0]
    vehicles, drinks, persons = [], [], []
    for box in results.boxes:
        cid = int(box.cls[0])
        score = float(box.conf[0])
        if score < conf:
            continue
        xyxy = tuple(int(v) for v in box.xyxy[0].tolist())
        item = {"box": xyxy, "conf": score}
        if cid in VEHICLE_CLASSES:
            vehicles.append({**item, "label": VEHICLE_CLASSES[cid]})
        elif cid in DRINK_CLASSES:
            drinks.append({**item, "label": DRINK_CLASSES[cid]})
        elif cid == PERSON_CLASS:
            persons.append(item)
    return vehicles, drinks, persons


# ---- geometry helpers ----------------------------------------------------

def _iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


def _center(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _ground_point(box):
    x1, y1, x2, y2 = box
    return (int((x1 + x2) / 2), int(y2))


def _near_person(drink_box, persons):
    """True if the drink container overlaps or sits within any person's box."""
    dcx, dcy = _center(drink_box)
    for p in persons:
        if _iou(drink_box, p["box"]) > 0.02:
            return True
        px1, py1, px2, py2 = p["box"]
        # small margin so a bottle just at the hand still counts
        m = 0.15 * (px2 - px1)
        if px1 - m <= dcx <= px2 + m and py1 - m <= dcy <= py2 + m:
            return True
    return False


# ---- lightweight IoU tracker ---------------------------------------------

class Tracker:
    """Associates detections to persistent ids by IoU, frame to frame."""

    def __init__(self, iou_thresh=0.3):
        self.iou_thresh = iou_thresh
        self.tracks = {}
        self._next = 0

    def update(self, detections, t):
        matched = {}
        used = set()
        for det in detections:
            best_id, best = None, self.iou_thresh
            for tid, tr in self.tracks.items():
                if tid in used:
                    continue
                ov = _iou(det["box"], tr["box"])
                if ov > best:
                    best_id, best = tid, ov
            if best_id is None:
                best_id = self._next
                self._next += 1
                self.tracks[best_id] = {"first_t": t, "state": {}}
            used.add(best_id)
            tr = self.tracks[best_id]
            tr.update({"box": det["box"], "det": det, "last_t": t})
            matched[best_id] = tr
        return matched

    def prune(self, t, max_age=1.0):
        for tid in list(self.tracks.keys()):
            if t - self.tracks[tid]["last_t"] > max_age:
                del self.tracks[tid]


# ---- main entry ----------------------------------------------------------

def process_video(input_path, output_dir, *, dwell=5.0, move=40.0, conf=0.35,
                  target_fps=4.0, zones=None, thumbs=True, progress=None):
    """Analyzes a video for illegal parking + public drinking.

    Returns a report dict:
      {output_video, duration, frames_analyzed, parking, drinking, counts}
    where `parking`/`drinking` are lists of event dicts (with a `thumb` image
    path when thumbs=True).
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir = output_dir / "thumbs"
    if thumbs:
        thumb_dir.mkdir(exist_ok=True)

    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {input_path}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    stride = max(1, round(src_fps / target_fps))
    out_fps = src_fps / stride

    out_video = output_dir / f"{input_path.stem}_analyzed.mp4"
    writer = None

    vehicles_tracker = Tracker()
    drinks_tracker = Tracker()
    parking_events, drinking_events = [], []
    counts = {"vehicles_seen": 0, "drinks_seen": 0}

    frame_idx = -1
    analyzed = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_idx += 1
        if frame_idx % stride != 0:
            continue
        analyzed += 1
        t = frame_idx / src_fps

        # downscale wide frames for speed (keeps aspect ratio)
        h0, w0 = frame.shape[:2]
        if w0 > 960:
            s = 960 / w0
            frame = cv2.resize(frame, (960, int(h0 * s)))

        vehicles, drinks, persons = detect_all(frame, conf)
        counts["vehicles_seen"] = max(counts["vehicles_seen"], len(vehicles))
        counts["drinks_seen"] = max(counts["drinks_seen"], len(drinks))

        _draw_zones(frame, zones)

        # ---- parking / obstruction ----
        vtracks = vehicles_tracker.update(vehicles, t)
        for tid, tr in vtracks.items():
            det = tr["det"]
            st = tr["state"]
            st.setdefault("anchor", _center(det["box"]))
            st.setdefault("still_since", t)
            st.setdefault("violated", False)

            cx, cy = _center(det["box"])
            ax, ay = st["anchor"]
            if ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5 > move:
                st["anchor"] = (cx, cy)
                st["still_since"] = t
            still_for = t - st["still_since"]

            in_zone, zone_name = _zone_hit(det["box"], zones)
            eligible = in_zone if zones else True

            if eligible and still_for >= dwell and not st["violated"]:
                st["violated"] = True
                reason = f"illegal parking: {zone_name}" if zones else "stationary vehicle"
                ev = {"kind": "parking", "label": det["label"], "time": t,
                      "time_str": _ts(t), "duration": round(still_for, 1),
                      "reason": reason}
                _annotate_vehicle(frame, det, still_for, dwell, eligible, violated=True)
                if thumbs:
                    ev["thumb"] = _save_thumb(frame, thumb_dir, f"parking_{tid}_{int(t)}")
                parking_events.append(ev)
            else:
                _annotate_vehicle(frame, det, still_for, dwell, eligible,
                                  violated=st["violated"])
        vehicles_tracker.prune(t)

        # ---- public drinking ----
        # attach person context to each drink detection before tracking
        for d in drinks:
            d["with_person"] = _near_person(d["box"], persons)
        dtracks = drinks_tracker.update(drinks, t)
        for tid, tr in dtracks.items():
            det = tr["det"]
            st = tr["state"]
            st.setdefault("person_since", None)
            st.setdefault("flagged", False)

            if det.get("with_person"):
                if st["person_since"] is None:
                    st["person_since"] = t
                held_for = t - st["person_since"]
                # require ~1s of a container staying with a person to avoid
                # flagging someone merely walking past a bottle
                if held_for >= 1.0 and not st["flagged"]:
                    st["flagged"] = True
                    ev = {"kind": "drinking", "label": det["label"], "time": t,
                          "time_str": _ts(t),
                          "reason": f"{det['label']} held by a person (possible public drinking)"}
                    _annotate_drink(frame, det, flagged=True)
                    if thumbs:
                        ev["thumb"] = _save_thumb(frame, thumb_dir, f"drink_{tid}_{int(t)}")
                    drinking_events.append(ev)
                else:
                    _annotate_drink(frame, det, flagged=st["flagged"])
            else:
                st["person_since"] = None
                _annotate_drink(frame, det, flagged=False)
        drinks_tracker.prune(t)

        if writer is None:
            h, w = frame.shape[:2]
            writer = cv2.VideoWriter(str(out_video),
                                     cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (w, h))
        writer.write(frame)

        if progress and total_frames:
            progress(min(1.0, frame_idx / total_frames))

    cap.release()
    if writer:
        writer.release()

    return {
        "output_video": str(out_video),
        "duration": round((total_frames / src_fps) if total_frames else t, 1),
        "frames_analyzed": analyzed,
        "parking": parking_events,
        "drinking": drinking_events,
        "counts": counts,
    }


# ---- drawing / io --------------------------------------------------------

def _zone_hit(box, zones):
    if not zones:
        return False, ""
    for z in zones:
        if vd.vehicle_in_zone(box, z["points"]):
            return True, z.get("name", "zone")
    return False, ""


def _annotate_vehicle(frame, det, still_for, dwell, eligible, violated):
    x1, y1, x2, y2 = det["box"]
    if violated:
        color, tag = (0, 0, 255), f"{det['label']} VIOLATION"
    elif eligible and still_for > 0.5:
        color, tag = (0, 200, 255), f"{det['label']} {still_for:.0f}/{dwell:.0f}s"
    else:
        color, tag = (0, 200, 0), det["label"]
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    cv2.putText(frame, tag, (x1, max(y1 - 8, 0)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)


def _annotate_drink(frame, det, flagged):
    x1, y1, x2, y2 = det["box"]
    color = (255, 0, 255) if flagged else (200, 160, 0)
    tag = f"{det['label']} PUBLIC DRINKING" if flagged else det["label"]
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    cv2.putText(frame, tag, (x1, max(y1 - 8, 0)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)


def _draw_zones(frame, zones):
    if not zones:
        return
    for z in zones:
        poly = np.array(z["points"], dtype=np.int32)
        cv2.polylines(frame, [poly], True, (0, 0, 255), 2)
        cv2.putText(frame, z.get("name", "no-parking"), tuple(poly[0]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)


def _save_thumb(frame, thumb_dir, name):
    path = thumb_dir / f"{name}.jpg"
    cv2.imwrite(str(path), frame)
    return str(path)


def _ts(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"
