"""Interactively draw no-parking / obstruction zones on an image by clicking.

Instead of hand-writing polygon coordinates, open an image and click to place
points. The saved JSON works directly with detect_image.py / detect_batch.py
(--zones), because the coordinates are in that image's pixel space.

    python draw_zones.py --image sample_images/street.jpg
    python draw_zones.py --image street.jpg --out zones.json

Controls (focus the image window):
    left click   add a point to the current zone
    n            finish current zone, start a new one
    z            undo the last point
    s            save all zones to the output JSON and exit
    q / Esc      quit without saving
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
WINDOW = "Draw zones  |  click=add  n=new  z=undo  s=save  q=quit"


def main():
    ap = argparse.ArgumentParser(description="Click to draw no-parking zones.")
    ap.add_argument("--image", required=True, help="Background image to draw on.")
    ap.add_argument("--out", default="zones.json", help="Output JSON path.")
    args = ap.parse_args()

    base = cv2.imread(args.image)
    if base is None:
        raise SystemExit(f"Could not read image: {args.image}")

    zones = []          # completed zones: list of point-lists
    current = []        # points of the zone being drawn

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            current.append([x, y])

    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, on_mouse)

    print(__doc__)
    while True:
        canvas = base.copy()
        # completed zones in red
        for i, pts in enumerate(zones):
            poly = np.array(pts, dtype=np.int32)
            cv2.polylines(canvas, [poly], True, (0, 0, 255), 2)
            cv2.putText(canvas, f"zone {i + 1}", tuple(poly[0]),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        # current zone in green (open polyline + points)
        if current:
            poly = np.array(current, dtype=np.int32)
            cv2.polylines(canvas, [poly], False, (0, 200, 0), 2)
            for px, py in current:
                cv2.circle(canvas, (px, py), 4, (0, 200, 0), -1)

        cv2.imshow(WINDOW, canvas)
        key = cv2.waitKey(20) & 0xFF

        if key in (ord("q"), 27):  # q or Esc
            print("Quit without saving.")
            break
        if key == ord("z") and current:
            current.pop()
        if key == ord("n") and len(current) >= 3:
            zones.append(current.copy())
            current.clear()
            print(f"Zone {len(zones)} added. Start clicking the next one.")
        if key == ord("s"):
            if len(current) >= 3:
                zones.append(current.copy())
                current.clear()
            if not zones:
                print("No zones drawn (a zone needs at least 3 points).")
                continue
            data = [
                {"name": f"Zone {i + 1}", "points": pts}
                for i, pts in enumerate(zones)
            ]
            out_path = Path(args.out)
            if not out_path.is_absolute():
                out_path = BASE_DIR / out_path
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"Saved {len(zones)} zone(s) -> {out_path}")
            break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
