"""Compare Qwen3-VL variants on the AI checker, using the real FrameRing / crop code.

  KMP_DUPLICATE_LIB_OK=TRUE python detection_sandbox/ai_checker_bench.py [--models a,b] [--repeat 2] [--out DIR]

Three cases from the test clips (subject = person(s) inside a region of the
frame, found with the person detector at 5 fps): a smoker at the curb (Trim2), the
"gathering" Monitoring row on the same clip (a passing motorbike rider counted as
a person), and the knife in the Holdup clip. For each model it reports JSON valid
rate, the answers, seconds per call and GPU memory while the call runs. The
frames sent for every case are saved under --out for inspection.
"""
import argparse, json, os, subprocess, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lookout_backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lookout_backend.settings")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
import django
django.setup()
import cv2
from core.vision import ai_checker as A, recognition

V = "C:/Users/User/OneDrive/Desktop/Violation testing/"
CASES = [
    # name, kind, clip, trigger second, region (x1,y1,x2,y2 as frame fractions), system_note kwargs
    ("smoking_curb", "smoking", V + "Smoking/Aug18_18 - Trim2.mp4", 2.5, (0.28, 0.25, 0.40, 0.52), dict(puffs=0)),
    ("gathering_passing_bike", "drinking", V + "Smoking/Aug18_18 - Trim2.mp4", 21.0, (0.32, 0.20, 0.47, 0.60), dict(people=3, minutes=0.03)),
    ("holdup_knife", "holdup", V + "Holdup/Aug24_16 - TrimHoldupBldg.mp4", 20.0, (0.24, 0.30, 0.36, 0.56), dict(people=2)),
]


def build_ring(path, t0, region):
    global args
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 10
    w, h = cap.get(3), cap.get(4)
    ring = A.FrameRing()
    key = ("subject", 1)
    start = max(0.0, t0 - A.FRAMES_BEFORE - 0.5)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(start * fps))
    step = max(1, int(round(fps / 5)))              # ~5 processed fps, like live
    n = int(start * fps)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = n / fps
        if t > t0 + A.FRAMES_AFTER + 0.2:
            break
        if (n % step) == 0:
            persons = recognition.detect_persons(frame, conf=0.4)
            inside = []
            for x1, y1, x2, y2, _ in persons:
                cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h
                if region[0] <= cx <= region[2] and region[1] <= cy <= region[3]:
                    inside.append((x1, y1, x2, y2))
            for i, b in enumerate(inside):
                ring.note_box(("subject", i), b)
            ring.add(frame, t)
        n += 1
    cap.release()
    return ring


class GpuWatcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.peak, self.stop = 0, False

    def run(self):
        while not self.stop:
            try:
                out = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], text=True)
                self.peak = max(self.peak, int(out.strip().splitlines()[0]))
            except Exception:
                pass
            time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="qwen3-vl:4b-instruct,qwen3-vl:2b-instruct")
    ap.add_argument("--ctx", type=int, default=None)
    ap.add_argument("--min-edge", type=int, default=0)
    ap.add_argument("--frames", type=int, default=A.FRAMES_MAX)
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "ai_bench"))
    global args
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    rings = {}
    for name, kind, clip, t0, region, kw in CASES:
        ring = build_ring(clip, t0, region)
        items = A.select_frames(ring.window(t0), t0, n=args.frames)
        keys = sorted({k for _, _, b in items for k in b})
        images = A.prepare_images(kind, items, keys, min_edge=args.min_edge)
        for i, data in enumerate(images):
            with open(os.path.join(args.out, f"{name}_{i:02d}.jpg"), "wb") as fh:
                fh.write(data)
        rings[name] = (kind, images, A.system_note(kind, **kw))
        print(f"{name}: {len(ring)} frames in ring, {len(images)} sent, crop sizes "
              f"{[cv2.imdecode(__import__('numpy').frombuffer(images[0], 'uint8'), 1).shape[:2]] if images else None}")

    summary = []
    for model in args.models.split(","):
        client = A.OllamaClient(model=model, num_ctx=args.ctx)
        ok, msg = client.available()
        if not ok:
            print(f"\n== {model}: SKIPPED — {msg}")
            continue
        print(f"\n== {model}")
        # warm-up (model load is not part of per-call time)
        k0, im0, n0 = rings["smoking_curb"]
        A.check(client, k0, n0, im0[:2])
        valid = calls = 0
        times = []
        gw = GpuWatcher(); gw.start()
        for name, (kind, images, note) in rings.items():
            for r in range(args.repeat):
                res = A.check(client, kind, note, images)
                calls += 1
                valid += res.ok
                times.append(res.seconds or 0)
                print(f"  {name} #{r+1}: {'valid' if res.ok else 'INVALID: ' + res.error[:80]} "
                      f"{res.seconds and round(res.seconds, 1)}s -> {json.dumps(res.reply) if res.ok else res.raw[:200]}")
        gw.stop = True
        mean = sum(times) / max(len(times), 1)
        summary.append((f"{model} ctx{args.ctx} up{args.min_edge} fr{args.frames}", valid, calls, mean, gw.peak))
    print("\nmodel | JSON valid | mean s/call | peak GPU MiB (whole system, detection NOT running)")
    for m, v, c, mean, peak in summary:
        print(f"{m} | {v}/{c} | {mean:.1f} | {peak}")


if __name__ == "__main__":
    main()
