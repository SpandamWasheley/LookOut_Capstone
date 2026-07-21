"""Run vehicle + face detection over EVERY image in a folder at once.

Examples:
    python detect_batch.py                                  # processes sample_images/
    python detect_batch.py --folder my_photos
    python detect_batch.py --folder sample_images --zones zones.example.json
    python detect_batch.py --no-faces

The face database is enrolled once and reused across all images. Annotated
copies and a summary CSV are written to output/.
"""

import argparse
import csv
from pathlib import Path

import cv2

import face_recognition_test as fr
import vehicle_detection as vd
from detect_image import run_on_image

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def main():
    ap = argparse.ArgumentParser(description="Batch vehicle + face detection.")
    ap.add_argument("--folder", default="sample_images", help="Folder of images.")
    ap.add_argument("--zones", help="JSON file of no-parking polygons.")
    ap.add_argument("--no-vehicles", action="store_true", help="Skip vehicle detection.")
    ap.add_argument("--no-faces", action="store_true", help="Skip face recognition.")
    ap.add_argument("--rebuild-faces", action="store_true", help="Re-enroll face_db/.")
    ap.add_argument("--conf", type=float, default=0.35, help="Vehicle confidence min.")
    ap.add_argument("--face-threshold", type=float, default=35.0, help="Face match min.")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_absolute():
        folder = BASE_DIR / folder
    if not folder.is_dir():
        raise SystemExit(f"Not a folder: {folder}")

    images = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        raise SystemExit(f"No images found in {folder}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    zones = vd.load_zones(args.zones) if (args.zones and not args.no_vehicles) else None

    db = []
    if not args.no_faces:
        print("Loading face database...")
        db = fr.enroll(rebuild=args.rebuild_faces)
        if not db:
            print("  (face_db/ is empty - drop reference photos there; see README)")

    print(f"\nProcessing {len(images)} image(s) from {folder}\n")
    rows = []
    for path in images:
        img = cv2.imread(str(path))
        if img is None:
            print(f"=== {path.name}: could not read, skipped ===")
            continue
        print(f"=== {path.name} ===")
        summary = run_on_image(
            img, zones=zones, do_vehicles=not args.no_vehicles,
            do_faces=not args.no_faces, db=db, conf=args.conf,
            face_threshold=args.face_threshold,
        )
        out_path = OUTPUT_DIR / f"{path.stem}_annotated.jpg"
        cv2.imwrite(str(out_path), img)
        rows.append({"image": path.name, **summary})
        print()

    csv_path = OUTPUT_DIR / "batch_summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["image", "vehicles", "violations", "faces", "known_faces", "smoking"]
        )
        writer.writeheader()
        writer.writerows(rows)

    totals = {k: sum(r.get(k, 0) for r in rows)
              for k in ("vehicles", "violations", "faces", "known_faces", "smoking")}
    print(f"Done. {len(rows)} image(s) processed.")
    print(f"  totals -> vehicles: {totals['vehicles']}, violations: {totals['violations']}, "
          f"faces: {totals['faces']} ({totals['known_faces']} known), smoking: {totals['smoking']}")
    print(f"  annotated images + summary CSV -> {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
