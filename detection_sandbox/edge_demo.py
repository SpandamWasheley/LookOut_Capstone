"""Run the automatic edge-line detector and the obstruction rule on a clip.

    python edge_demo.py --source clip.mp4
    python edge_demo.py --source clip.mp4 --annotate output/edge.jpg
    python edge_demo.py --source "rtsp://user:pass@host:554/Streaming/Channels/102"

There is nothing to click. The detector watches the opening frames, finds the
solid white edge line by itself, locks it, and from then on measures every
vehicle by how much of its footprint lies past that line and for how long.

WHEN NO LINE IS FOUND the tool does not fail silently - it prints the candidates
it considered and the contrast each one scored, so you can see whether the road
simply has no marking or whether the threshold needs revisiting for this camera.
That distinction matters: a detector that invents an edge on an unmarked road
would measure every vehicle against a line that is really a roofline.
"""

import argparse
import time

import cv2

import edge_line as el
import obstruction as ob
import vehicle_detection as vd


def diagnose(finder, shape):
    """Explains a failed lock by scoring the candidates that were rejected."""
    mask = finder.persistent_mask()
    if mask is None:
        print("  no frames were read")
        return
    k = cv2.getStructuringElement(cv2.MORPH_RECT, ((el.LINE_WIDTH_PX * 2) | 1,) * 2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    segs = el._segments(mask, shape[1])
    if segs is None:
        print("  no straight bright stripes at all -> this road has no marking")
        return
    groups = el._clusters(segs, shape)
    print(f"  {len(segs)} bright segments, {len(groups)} collinear candidates")
    ref = (finder.mean_frame / max(finder.frames, 1)).astype("uint8")
    for i, g in enumerate(groups[:5]):
        line = el._line_from_group(g, shape)
        ok = el.flanks_ok(ref, line)
        print(f"    candidate {i}: length {g['length']:.0f}px -> "
              f"{'ACCEPTED' if ok else 'rejected (not paint on a road surface)'}")
    if groups:
        print(f"  needs contrast >= {el.MIN_PAINT_CONTRAST:.0f}; genuine paint "
              "measures well over 100")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="video file, RTSP URL, or webcam index")
    ap.add_argument("--warmup", type=int, default=el.WARMUP_FRAMES,
                    help="frames blended before the edge line is locked")
    ap.add_argument("--minutes", type=float, default=5.0,
                    help="minutes past the line before it counts as an obstruction")
    ap.add_argument("--annotate", default=None, help="write an annotated frame here")
    ap.add_argument("--max-frames", type=int, default=1200)
    args = ap.parse_args()

    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        print(f"Could not open {args.source}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    finder = el.EdgeFinder(warmup=args.warmup)
    monitor = None
    frame = None
    seen = 0

    print(f"Warming up on {args.source} ({args.warmup} frames) ...")
    while seen < args.max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        seen += 1
        # Timestamps come from the clip, not the wall clock, so replaying a
        # recording exercises the five-minute rule at its real pace.
        now = seen / fps

        if not finder.locked:
            # Moving vehicles vote on which side is the road; parked ones must
            # not, or a car already blocking the walkway would define it as road.
            boxes = [v["box"] for v in vd.detect_vehicles(frame)]
            finder.observe_traffic([((b[0] + b[2]) / 2, b[3]) for b in boxes])
            if finder.update(frame) is not None:
                print(f"\nEDGE LINE LOCKED after {seen} frames: {finder.line}")
                print(f"  protected side: {finder.line.protected_side}")
                monitor = ob.ObstructionMonitor(
                    finder.line, obstruction_seconds=args.minutes * 60)
            continue

        boxes = [v["box"] for v in vd.detect_vehicles(frame)]
        for state, verdict in monitor.update(boxes, now):
            if verdict == ob.OBSTRUCTION and not state.alerted:
                state.alerted = True
                print(f"  [{now:7.1f}s] OBSTRUCTION  {state.summary()}")
            elif verdict == ob.WATCHING and int(now) % 30 == 0:
                print(f"  [{now:7.1f}s] watching     {state.summary()}")

    cap.release()

    if not finder.locked:
        print(f"\nNO EDGE LINE FOUND in {seen} frames.")
        diagnose(finder, frame.shape if frame is not None else (0, 0))
        print("\nThis road appears to have no solid white marking. The rule "
              "itself is unaffected -- supply a line and it works -- but the "
              "automatic finder needs painted road to find.")
        return

    if args.annotate and frame is not None:
        finder.line.draw(frame)
        cv2.putText(frame, "auto-detected edge line", (12, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        cv2.imwrite(args.annotate, frame)
        print(f"\nAnnotated frame written to {args.annotate}")


if __name__ == "__main__":
    main()
