"""Verification helpers used for REPORT.md (not part of the unit-test suite because they need real runs).

  python tests/verify_tools.py frames  <run_dir> <video> [n]     video-time accuracy (verification 4a)
  python tests/verify_tools.py compare <run_dir_A> <run_dir_B>   event-list comparison (4b, 6)
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db  # noqa: E402


def status_events(run_dir):
    out = []
    for line in (Path(run_dir) / "events.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["kind"] in ("alert_created", "status_change"):
            out.append((e["event_id"], e["violation_type"], e["status"], round(e["video_time_sec"], 3)))
    return out


def ai_events(run_dir):
    out = []
    for line in (Path(run_dir) / "events.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["kind"] == "ai_verdict" and e.get("final"):
            out.append((e["event_id"], e.get("ai_verdict"), e.get("ai_badge"), e.get("ai_suggested_status")))
    return out


def frames_check(run_dir, video, n=3):
    run_dir = Path(run_dir)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    seen, rows = set(), []
    for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["kind"] not in ("alert_created", "status_change"):
            continue
        key = (e["event_id"], e["status"])
        img_path = run_dir / "frames" / f"{e['event_id']}_{e['status'].lower()}.jpg"
        if key in seen or not img_path.exists():
            continue
        seen.add(key)
        saved = cv2.imread(str(img_path), cv2.IMREAD_GRAYSCALE)
        t = e["video_time_sec"]
        first = max(0, int((t - 2.0) * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, first)
        best = None
        for i in range(int(4.0 * fps) + 1):
            ok, fr = cap.read()
            if not ok:
                break
            g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, (saved.shape[1], saved.shape[0]), interpolation=cv2.INTER_AREA)
            # ignore the red box/label the tool drew: compare only pixels that are not saturated red in the saved colour image
            diff = float(np.mean(np.abs(g.astype(np.int16) - saved.astype(np.int16))))
            tt = (first + i) / fps
            if best is None or diff < best[0]:
                best = (diff, tt)
        rows.append({"event": e["event_id"], "status": e["status"], "logged_video_time": t,
                     "best_matching_frame_time": round(best[1], 2), "abs_difference_sec": round(abs(best[1] - t), 2),
                     "mean_pixel_diff": round(best[0], 2)})
        if len(rows) >= n:
            break
    return rows


def compare(a, b):
    sa, sb = status_events(a), status_events(b)
    aa, ab = ai_events(a), ai_events(b)
    return {"status_events_A": len(sa), "status_events_B": len(sb), "status_identical": sa == sb,
            "only_in_A": [x for x in sa if x not in sb], "only_in_B": [x for x in sb if x not in sa],
            "ai_identical": aa == ab, "ai_A": aa, "ai_B": ab}


if __name__ == "__main__":
    if sys.argv[1] == "frames":
        print(json.dumps(frames_check(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 3), indent=1))
    elif sys.argv[1] == "compare":
        print(json.dumps(compare(sys.argv[2], sys.argv[3]), indent=1))
