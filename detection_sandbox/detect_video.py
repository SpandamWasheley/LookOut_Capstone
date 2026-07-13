"""Illegal-parking / obstruction detection on a VIDEO file.

Unlike the single-image tools, a video lets us apply the dwell rule: a vehicle
is only flagged once it has stayed (roughly) still for `--dwell` seconds. A car
merely driving through resets its own timer and never triggers — which is the
whole point of using video.

Pipeline per (sampled) frame:
  YOLOv8 detects vehicles  ->  IoU-track them across frames  ->  measure how long
  each has been stationary  ->  (optionally) check it's inside a no-parking zone
  ->  flag VIOLATION when stationary >= dwell.

    python detect_video.py --video clips/street.mp4
    python detect_video.py --video clips/street.mp4 --zones zones.example.json --dwell 60
    python detect_video.py --video clips/street.mp4 --dwell 5 --move 40

Writes an annotated copy + a violations report to output/.

  Green box  = vehicle moving / not yet parked long enough
  Yellow box = stationary, counting toward the dwell threshold
  Red box    = VIOLATION (parked >= dwell, and inside a no-parking zone if given)
"""

import argparse
from pathlib import Path

import cv2

import vehicle_detection as vd

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"


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
    """Bottom-center of the box — where the wheels meet the road."""
    x1, y1, x2, y2 = box
    return (int((x1 + x2) / 2), int(y2))


def detect_video(video_path, zones, dwell, move_thresh, conf, target_fps):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise SystemExit(f"Could not open video: {video_path}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    stride = max(1, round(src_fps / target_fps))  # process ~target_fps frames/sec
    out_fps = src_fps / stride

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"{Path(video_path).stem}_parking.mp4"
    writer = None

    # tracks: id -> dict(box, label, conf, anchor, still_since, last_seen_t,
    #                     violated, violation_reason)
    tracks = {}
    next_id = 0
    violations = []  # (track_id, label, t_start, duration, reason)

    print(f"\n=== {Path(video_path).name} ===")
    print(f"source fps ~{src_fps:.0f}, processing ~{out_fps:.1f} fps, "
          f"dwell {dwell}s, move-tolerance {move_thresh}px, "
          f"zones: {'yes' if zones else 'none (any stationary vehicle flagged)'}\n")

    frame_idx = -1
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_idx += 1
        if frame_idx % stride != 0:
            continue

        t = frame_idx / src_fps  # seconds into the clip (real time, skip-safe)
        vehicles = vd.detect_vehicles(frame, conf=conf)

        matched = set()
        for v in vehicles:
            box, label, score = v["box"], v["label"], v["conf"]
            # associate this detection to an existing track by IoU
            best_id, best_iou = None, 0.3
            for tid, tr in tracks.items():
                if tid in matched:
                    continue
                overlap = _iou(box, tr["box"])
                if overlap > best_iou:
                    best_id, best_iou = tid, overlap

            if best_id is None:
                best_id = next_id
                next_id += 1
                tracks[best_id] = {
                    "anchor": _center(box), "still_since": t,
                    "violated": False, "reason": "",
                }
            matched.add(best_id)
            tr = tracks[best_id]
            tr.update({"box": box, "label": label, "conf": score, "last_seen_t": t})

            # movement check: if the vehicle drifted far from its anchor, it's
            # moving -> reset the stationary timer and re-anchor.
            cx, cy = _center(box)
            ax, ay = tr["anchor"]
            if ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5 > move_thresh:
                tr["anchor"] = (cx, cy)
                tr["still_since"] = t
                if not tr["violated"]:
                    tr["reason"] = ""

            still_for = t - tr["still_since"]

            # zone gate: with zones, only a vehicle whose ground point is inside
            # a no-parking polygon can violate. Without zones, any stationary
            # vehicle counts (useful for a quick test clip).
            in_zone, zone_name = _zone_hit(box, zones)
            eligible = in_zone if zones else True

            if eligible and still_for >= dwell and not tr["violated"]:
                tr["violated"] = True
                reason = f"illegal parking: {zone_name}" if zones else "stationary vehicle"
                tr["reason"] = reason
                violations.append((best_id, label, tr["still_since"], still_for, reason))
                print(f"  [{_ts(tr['still_since'])}] VIOLATION - {label} "
                      f"stationary {still_for:.0f}s ({reason})")

            _draw(frame, tr, still_for, dwell, eligible)

        _draw_zones(frame, zones)

        # drop tracks not seen for a second (occlusion / left the scene)
        for tid in list(tracks.keys()):
            if tid not in matched and t - tracks[tid]["last_seen_t"] > 1.0:
                del tracks[tid]

        if writer is None:
            h, w = frame.shape[:2]
            writer = cv2.VideoWriter(
                str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (w, h)
            )
        writer.write(frame)

    cap.release()
    if writer:
        writer.release()

    print(f"\n{len(violations)} violation(s) detected.")
    for tid, label, t0, dur, reason in violations:
        print(f"  - {label:11s} at {_ts(t0)}  ({reason})")
    print(f"Annotated video saved -> {out_path}\n")
    return violations


def _zone_hit(box, zones):
    if not zones:
        return False, ""
    for z in zones:
        if vd.vehicle_in_zone(box, z["points"]):
            return True, z.get("name", "zone")
    return False, ""


def _draw(frame, tr, still_for, dwell, eligible):
    x1, y1, x2, y2 = tr["box"]
    if tr["violated"]:
        color, tag = (0, 0, 255), f"{tr['label']} VIOLATION"
    elif eligible and still_for > 0.5:
        color = (0, 200, 255)  # yellow-ish: counting toward dwell
        tag = f"{tr['label']} {still_for:.0f}/{dwell}s"
    else:
        color, tag = (0, 200, 0), tr["label"]
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    cv2.putText(frame, tag, (x1, max(y1 - 8, 0)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    gx, gy = _ground_point(tr["box"])
    cv2.circle(frame, (gx, gy), 4, color, -1)  # the point tested against zones


def _draw_zones(frame, zones):
    if not zones:
        return
    import numpy as np
    for z in zones:
        poly = np.array(z["points"], dtype=np.int32)
        cv2.polylines(frame, [poly], True, (0, 0, 255), 2)
        cv2.putText(frame, z.get("name", "no-parking"), tuple(poly[0]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)


def _ts(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def main():
    ap = argparse.ArgumentParser(description="Illegal-parking detection on a video.")
    ap.add_argument("--video", required=True, help="Path to the video file.")
    ap.add_argument("--zones", help="JSON of no-parking polygons. Without it, any "
                                    "stationary vehicle is flagged (test mode).")
    ap.add_argument("--dwell", type=float, default=5.0,
                    help="Seconds a vehicle must stay put to count as parked. "
                         "Default 5 (raise to ~60 for real streets).")
    ap.add_argument("--move", type=float, default=40.0,
                    help="Pixels the vehicle can drift and still count as 'still'. "
                         "Default 40.")
    ap.add_argument("--conf", type=float, default=0.35, help="Vehicle confidence min.")
    ap.add_argument("--fps", type=float, default=5.0,
                    help="How many frames per second to analyze. Default 5.")
    args = ap.parse_args()

    zones = vd.load_zones(args.zones) if args.zones else None
    detect_video(Path(args.video), zones, args.dwell, args.move,
                 args.conf, args.fps)


if __name__ == "__main__":
    main()
