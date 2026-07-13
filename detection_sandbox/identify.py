"""Interactive vehicle identifier — give it a picture, see what it detects.

Run it, then paste (or drag-and-drop) an image path when prompted. It opens a
window with a labelled box on every vehicle it finds (car / motorcycle / bus /
truck / bicycle) and prints a summary. Type another path to check the next
image, or 'q' to quit.

    python identify.py                       # then paste paths one by one
    python identify.py sample_images/x.jpg   # check one image and exit

Press any key (with the image window focused) to close it and check the next.
Lower --conf to catch fainter/blurrier vehicles.
"""

import argparse
from collections import Counter
from pathlib import Path

import cv2

import vehicle_detection as vd

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"


def identify(image_path, conf):
    """Detects vehicles in one image, prints a summary, and shows/saves the result."""
    path = Path(image_path.strip().strip('"').strip("'"))
    if not path.is_absolute():
        path = BASE_DIR / path

    img = cv2.imread(str(path))
    if img is None:
        print(f"  X  Could not read image: {path}")
        return

    vehicles = vd.detect_vehicles(img, conf=conf)

    if not vehicles:
        print("  No vehicles detected. (Try a lower --conf to catch faint ones.)")
    else:
        counts = Counter(v["label"] for v in vehicles)
        summary = ", ".join(f"{n} {label}{'s' if n > 1 else ''}"
                            for label, n in counts.items())
        print(f"  Found {len(vehicles)} vehicle(s): {summary}")
        for v in vehicles:
            print(f"    - {v['label']:11s} {v['conf'] * 100:4.0f}% confidence")

    # Draw green labelled boxes and show the result.
    vd.annotate(img, vehicles)
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"{path.stem}_identified.jpg"
    cv2.imwrite(str(out_path), img)
    print(f"  Saved annotated copy -> {out_path}")

    # Shrink oversized photos so the whole thing fits on screen.
    h, w = img.shape[:2]
    scale = min(1.0, 1200 / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)))

    cv2.imshow("Vehicle identifier - press any key for next, or close window", img)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description="Interactive vehicle identifier.")
    ap.add_argument("image", nargs="?", help="Image path to check once, then exit.")
    ap.add_argument("--conf", type=float, default=0.35,
                    help="Minimum detection confidence (0-1). Default 0.35.")
    args = ap.parse_args()

    if args.image:
        identify(args.image, args.conf)
        return

    print("Vehicle identifier ready.")
    print("Paste an image path and press Enter (or 'q' to quit).")
    print("Tip: you can drag an image from Explorer onto this window.\n")
    while True:
        try:
            line = input("image> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in {"q", "quit", "exit"}:
            break
        if not line:
            continue
        identify(line, args.conf)
        print()


if __name__ == "__main__":
    main()
