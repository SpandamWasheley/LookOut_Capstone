"""HISTORICAL: calibrated the pose-based mouth anchor against insightface on the test clips
(insightface has since been removed; this needs the old commit to run).

For every person box on sampled frames it runs BOTH finders on the same box:
  * insightface (recognition.find_mouth)   -> face width + mouth point
  * YOLOv8-pose  (recognition.pose_on_box) -> ear / eye / shoulder widths + nose
and writes one CSV row per person. Then it prints the median ratios
(insightface face width / each pose width), fallback usage, how often each
finder produced a mouth, and the mouth-point error in face-widths.

  KMP_DUPLICATE_LIB_OK=TRUE python detection_sandbox/mouth_calibration.py [--stride 10] [--max-frames N]
"""
import argparse, csv, json, os, sys, time
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lookout_backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lookout_backend.settings")
import django; django.setup()
import cv2, numpy as np
from core.vision import recognition as R

if not hasattr(R, "find_mouth"):
    sys.exit("Historical script: it compared the pose anchor against insightface, which has been removed from LookOut. "
             "The fitted factors it produced live in recognition.py (POSE_*_TO_FACE, POSE_MOUTH_DY).")

BASE = "C:/Users/User/OneDrive/Desktop/Violation testing"
CLIPS = [("Smoking", "Aug14_3 - MorningMediumBldg - Trim.mp4"),
         ("Smoking", "Aug18_18 - TrimCigaretteNightFar1.mp4"),
         ("Smoking", "Aug18_18 - Trim2.mp4"),
         ("Drinking", "Aug18_4 - TrimDrinkingEveningFar3.mp4"),
         ("Drinking", "Aug18_5 - DrinkingKabilangRoad1.mp4")]

ap = argparse.ArgumentParser()
ap.add_argument("--stride", type=int, default=10)
ap.add_argument("--max-frames", type=int, default=0, help="cap frames per clip (0 = all)")
ap.add_argument("--out", default=os.path.join(ROOT, "detection_sandbox", "output", "mouth_calibration.csv"))
a = ap.parse_args()
os.makedirs(os.path.dirname(a.out), exist_ok=True)

FIELDS = ["clip", "frame", "box_h", "if_found", "if_w", "if_mx", "if_my", "pose_found", "nose_x", "nose_y",
          "nose_c", "ear", "eye", "shoulder", "t_if", "t_pose"]
rows = []
for cat, name in CLIPS:
    cap = cv2.VideoCapture(f"{BASE}/{cat}/{name}")
    n = int(cap.get(7)); done = 0
    for fi in range(0, n, a.stride):
        if a.max_frames and done >= a.max_frames: break
        cap.set(1, fi); ok, fr = cap.read()
        if not ok: break
        done += 1
        for b in R.detect_persons(fr, conf=0.5):
            r = dict.fromkeys(FIELDS, "")
            r.update(clip=name[:22], frame=fi, box_h=b[3] - b[1])
            t = time.time(); f = R.find_mouth(fr, b); r["t_if"] = round(time.time() - t, 3)
            r["if_found"] = int(f is not None)
            if f: r["if_mx"], r["if_my"], r["if_w"] = f
            t = time.time(); k = R.pose_on_box(fr, b); r["t_pose"] = round(time.time() - t, 3)
            r["pose_found"] = int(k is not None)
            if k is not None:
                r["nose_x"], r["nose_y"], r["nose_c"] = (float(v) for v in k[R.KP_NOSE])
                m = R.pose_face_metrics(k)
                for key in ("ear", "eye", "shoulder"): r[key] = m[key] if m[key] else ""
            rows.append(r)
    print(f"{name[:40]}: {done} frames, {sum(1 for x in rows if x['clip']==name[:22])} persons", flush=True)

with open(a.out, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)

def fl(x): return float(x) if x != "" else None
P = len(rows)
ifs = [r for r in rows if r["if_found"] == 1]
conf_nose = [r for r in rows if r["pose_found"] == 1 and (fl(r["nose_c"]) or 0) >= R.KP_MIN_CONF]
def has(r, k): return fl(r[k]) is not None
pose_mouth = [r for r in conf_nose if has(r, "ear") or has(r, "eye") or has(r, "shoulder")]
print(f"\npersons: {P}   insightface found a face: {len(ifs)} ({100*len(ifs)/max(P,1):.0f}%)   "
      f"pose found nose+width: {len(pose_mouth)} ({100*len(pose_mouth)/max(P,1):.0f}%)")
both = [r for r in pose_mouth if r["if_found"] == 1]
print(f"both found: {len(both)}   pose only: {len(pose_mouth)-len(both)}   insightface only: {len(ifs)-len(both)}   neither: {P-len(pose_mouth)-len(ifs)+len(both)}")
src = {"ear": 0, "eye": 0, "shoulder": 0}
for r in pose_mouth:
    for k in ("ear", "eye", "shoulder"):
        if has(r, k): src[k] += 1; break
print("width source used (first confident of ear->eye->shoulder), over all pose results:", src)
for k in ("ear", "eye", "shoulder"):
    rr = [fl(r["if_w"]) / fl(r[k]) for r in both if has(r, k)]
    if rr:
        q = np.percentile(rr, [25, 50, 75])
        print(f"  insightface_w / {k:8s}: median {q[1]:.3f}  IQR [{q[0]:.3f}, {q[2]:.3f}]  n={len(rr)}")
# mouth offset: insightface mouth minus pose nose, in insightface face-widths
dy = [(fl(r["if_my"]) - fl(r["nose_y"])) / fl(r["if_w"]) for r in both]
dx = [(fl(r["if_mx"]) - fl(r["nose_x"])) / fl(r["if_w"]) for r in both]
if dy: print(f"  mouth - nose, in face-widths: dy median {np.median(dy):.3f} (IQR {np.percentile(dy,25):.3f}..{np.percentile(dy,75):.3f}); dx median {np.median(dx):.3f}")
print(f"timing per person: insightface {np.mean([fl(r['t_if']) for r in rows]):.3f}s   pose {np.mean([fl(r['t_pose']) for r in rows]):.3f}s")
print("csv:", a.out)
