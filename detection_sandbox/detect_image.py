"""Run vehicle + face detection on a single test image and save an annotated copy.

Examples:
    python detect_image.py --image sample_images/street.jpg
    python detect_image.py --image street.jpg --zones zones.example.json
    python detect_image.py --image portrait.jpg --no-vehicles
    python detect_image.py --image street.jpg --no-faces --rebuild-faces

For a whole folder at once, use detect_batch.py instead.

Green box  = vehicle (OK)      Red box   = vehicle in a no-parking zone
Blue box   = known face        Orange box = unknown face
"""

import argparse
from pathlib import Path

import cv2

import face_recognition_test as fr
import smoking_detection as smoke
import vehicle_detection as vd

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def run_on_image(img, *, zones=None, do_vehicles=True, do_faces=True,
                 db=None, conf=0.35, face_threshold=35.0, verbose=True):
    """Annotates `img` in place and returns a summary dict.

    `db` is a pre-loaded face database (see face_recognition_test.enroll); pass
    it in so batch runs don't re-enroll for every image.
    """
    summary = {"vehicles": 0, "violations": 0, "faces": 0, "known_faces": 0, "smoking": 0}

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

    if do_faces:
        faces = fr.recognize_faces_in_image(img, db or [], threshold=face_threshold)
        summary["faces"] = len(faces)
        summary["known_faces"] = sum(1 for f in faces if f["name"] != "Unknown")
        for f in faces:
            x1, y1, x2, y2 = f["box"]
            known = f["name"] != "Unknown"
            color = (255, 180, 0) if known else (0, 165, 255)  # blue / orange (BGR)
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            cv2.putText(img, f"{f['name']} {f['score']:.0f}", (x1, max(y1 - 8, 0)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        if verbose:
            print(f"  faces: {len(faces)}")
            for f in faces:
                print(f"    - {f['name']} ({f['score']:.0f})")

    return summary


def main():
    ap = argparse.ArgumentParser(description="Vehicle + face detection on an image.")
    ap.add_argument("--image", required=True, help="Path to the test image.")
    ap.add_argument("--zones", help="JSON file of no-parking polygons.")
    ap.add_argument("--no-vehicles", action="store_true", help="Skip vehicle detection.")
    ap.add_argument("--no-faces", action="store_true", help="Skip face recognition.")
    ap.add_argument("--rebuild-faces", action="store_true", help="Re-enroll face_db/.")
    ap.add_argument("--conf", type=float, default=0.35, help="Vehicle confidence min.")
    ap.add_argument("--face-threshold", type=float, default=35.0, help="Face match min.")
    args = ap.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        raise SystemExit(f"Could not read image: {args.image}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"\n=== Analyzing {args.image} ===")

    zones = vd.load_zones(args.zones) if (args.zones and not args.no_vehicles) else None

    db = []
    if not args.no_faces:
        print("Loading face database...")
        db = fr.enroll(rebuild=args.rebuild_faces)
        if not db:
            print("  (face_db/ is empty - drop reference photos there; see README)")

    run_on_image(img, zones=zones, do_vehicles=not args.no_vehicles,
                 do_faces=not args.no_faces, db=db, conf=args.conf,
                 face_threshold=args.face_threshold)

    out_path = OUTPUT_DIR / f"{Path(args.image).stem}_annotated.jpg"
    cv2.imwrite(str(out_path), img)
    print(f"\nAnnotated image saved -> {out_path}\n")


if __name__ == "__main__":
    main()
