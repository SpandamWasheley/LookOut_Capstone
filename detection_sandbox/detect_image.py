"""Run vehicle (+ smoking) detection on a single test image and save an annotated copy.

Examples:
    python detect_image.py --image sample_images/street.jpg
    python detect_image.py --image street.jpg --zones zones.example.json

For a whole folder at once, use detect_batch.py instead.

Green box  = vehicle (OK)      Red box   = vehicle in a no-parking zone
"""

import argparse
from pathlib import Path

import cv2

import smoking_detection as smoke
import vehicle_detection as vd

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def run_on_image(img, *, zones=None, do_vehicles=True, conf=0.35, verbose=True):
    """Annotates `img` in place and returns a summary dict.
    """
    summary = {"vehicles": 0, "violations": 0, "smoking": 0}

    if do_vehicles:
        vehicles = vd.detect_vehicles(img, conf=conf)
        vd.flag_violations(vehicles, zones)
        vd.annotate(img, vehicles, zones)
        summary["vehicles"] = len(vehicles)
        summary["violations"] = sum(1 for v in vehicles if v.get("violation"))
        if verbose:
            print(f"  vehicles: {len(vehicles)}")
            for v in vehicles:
                flag = f"   <-- {v['violation']}" if v.get("violation") else ""
                print(f"    - {v['label']:11s} {v['conf'] * 100:4.0f}%{flag}")
            if zones:
                print(f"  illegal parking / obstruction: {summary['violations']}")

    # Smoking runs only if a custom models/smoking.pt is installed (COCO can't
    # detect cigarettes); otherwise it's skipped silently.
    if smoke.is_available():
        smokes = smoke.detect_smoking(img, conf=conf)
        summary["smoking"] = len(smokes)
        for s in smokes:
            x1, y1, x2, y2 = s["box"]
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 215, 255), 2)  # amber (BGR)
            cv2.putText(img, f"{s['label']} {s['conf'] * 100:.0f}%", (x1, max(y1 - 8, 0)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 215, 255), 2)
        if verbose:
            print(f"  smoking: {len(smokes)}")
            for s in smokes:
                print(f"    - {s['label']} {s['conf'] * 100:.0f}%")
    elif verbose and do_vehicles:
        print("  smoking: skipped (no models/smoking.pt — see models/README.txt)")

    return summary


def main():
    ap = argparse.ArgumentParser(description="Vehicle detection on an image.")
    ap.add_argument("--image", required=True, help="Path to the test image.")
    ap.add_argument("--zones", help="JSON file of no-parking polygons.")
    ap.add_argument("--no-vehicles", action="store_true", help="Skip vehicle detection.")
    ap.add_argument("--conf", type=float, default=0.35, help="Vehicle confidence min.")
    args = ap.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        raise SystemExit(f"Could not read image: {args.image}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"\n=== Analyzing {args.image} ===")

    zones = vd.load_zones(args.zones) if (args.zones and not args.no_vehicles) else None

    run_on_image(img, zones=zones, do_vehicles=not args.no_vehicles, conf=args.conf)

    out_path = OUTPUT_DIR / f"{Path(args.image).stem}_annotated.jpg"
    cv2.imwrite(str(out_path), img)
    print(f"\nAnnotated image saved -> {out_path}\n")


if __name__ == "__main__":
    main()
