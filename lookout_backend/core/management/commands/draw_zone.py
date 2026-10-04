"""
Draw the obstruction polygon on one frame from the camera or a video.

  python manage.py draw_zone --source "rtsp://USER:PASS@192.168.1.64:554/Streaming/Channels/101"
  python manage.py draw_zone --source path\\to\\clip.mp4 --at 12

Controls: LEFT click = add point | RIGHT click = undo | C = clear | S = save | Q/Esc = quit
Points are saved normalized (0-1), so they survive resolution changes,
but NOT camera moves - redraw if the tripod/pan-tilt is touched.
"""
import json
from pathlib import Path

import cv2
from django.core.management.base import BaseCommand, CommandError

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "vision" / "zones" / "obstruction_zone.json"
WINDOW = "LookOut - draw obstruction zone"


class Command(BaseCommand):
    help = "Draw the single obstruction polygon and save it as JSON."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="RTSP URL, video path, or webcam index")
        parser.add_argument("--at", type=float, default=0.0, help="Seek to this second (video files)")
        parser.add_argument("--out", default=str(DEFAULT_OUT), help="Where to save the zone JSON")
        parser.add_argument("--max-width", type=int, default=1280, help="Display width (clicks are rescaled)")
        parser.add_argument("--edit", action="store_true", help="Load the existing zone to adjust it")

    def handle(self, *args, **opts):
        source = opts["source"]
        cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
        if opts["at"] > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, opts["at"] * 1000)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            raise CommandError(f"Could not read a frame from {source}")

        h, w = frame.shape[:2]
        scale = min(1.0, opts["max_width"] / w)
        disp = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1.0 else frame.copy()
        dh, dw = disp.shape[:2]

        out = Path(opts["out"])
        points = []
        if opts["edit"] and out.exists():
            points = [tuple(p) for p in json.loads(out.read_text())["points"]]

        def on_mouse(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN:
                points.append((x / dw, y / dh))
            elif event == cv2.EVENT_RBUTTONDOWN and points:
                points.pop()

        cv2.namedWindow(WINDOW)
        cv2.setMouseCallback(WINDOW, on_mouse)

        while True:
            img = disp.copy()
            px = [(int(x * dw), int(y * dh)) for x, y in points]
            for i, p in enumerate(px):
                cv2.circle(img, p, 5, (0, 191, 255), -1)
                if i:
                    cv2.line(img, px[i - 1], p, (0, 191, 255), 2)
            if len(px) >= 3:
                cv2.line(img, px[-1], px[0], (0, 191, 255), 1)
            cv2.putText(img, f"{len(points)} pts | L-click add  R-click undo  C clear  S save  Q quit",
                        (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.imshow(WINDOW, img)

            key = cv2.waitKey(30) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("c"):
                points.clear()
            if key == ord("s"):
                if len(points) < 3:
                    self.stdout.write(self.style.WARNING("Need at least 3 points."))
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps({
                    "points": [[round(x, 4), round(y, 4)] for x, y in points],
                    "frame_size": [w, h],
                    "source": source if not source.startswith("rtsp") else "rtsp",
                }, indent=2))
                self.stdout.write(self.style.SUCCESS(f"Saved {len(points)}-point zone -> {out}"))

        cv2.destroyAllWindows()
