"""Replay the momentum cue offline on the per-frame input logged by a clip run.

  LOOKOUT_MOUTH_LOG=<file>.jsonl python manage.py watch_merged ...   # logs kind="mom" rows
  python detection_sandbox/momentum_replay.py <file>.jsonl [decay,on,off,max ...]

For every (track, class) slot it reports when the cue turned ON, how long it
stayed ON, how many raw detections fed it and how fast the person moved, so real
held cigarettes (a person standing still) can be told from vehicle / motorcycle
blur (a fast-moving "person" box).
"""
import json, math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lookout_backend"))
from core.vision.momentum import MomentumConfig, Slot, update_momentum


def load(path):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    return [r for r in rows if r.get("kind") == "mom"]


def replay(rows, cfg, cls_filter=None):
    """-> {(track, cls): dict(spans=[(t_on, t_off)], frames, hits, peak)}"""
    slots, out, last_t = {}, {}, {}
    by_t = {}
    for r in rows:
        by_t.setdefault(r["t"], []).append(r)
    for t in sorted(by_t):
        seen = set()
        for r in by_t[t]:
            tid = r["track"]
            seen.add(tid)
            for cls in set(list(r["confs"]) + [k[1] for k in slots if k[0] == tid]):
                if cls_filter and cls not in cls_filter:
                    continue
                conf = r["confs"].get(cls, 0.0)
                key = (tid, cls)
                if key not in slots:
                    if conf <= 0:
                        continue
                    slots[key] = Slot()
                    out[key] = dict(spans=[], frames=0, hits=0, peak=0.0, open=None, speed=[])
                s = slots[key]
                was = s.cue_on
                update_momentum(s, conf, cfg)
                o = out[key]
                o["frames"] += 1
                o["hits"] += conf > 0
                o["peak"] = max(o["peak"], s.momentum)
                if s.cue_on and not was:
                    o["open"] = t
                if was and not s.cue_on and o["open"] is not None:
                    o["spans"].append((o["open"], t)); o["open"] = None
                last_t[key] = t
    for key, o in out.items():
        if o["open"] is not None:
            o["spans"].append((o["open"], last_t[key])); o["open"] = None
    return out


def speed_of(rows):
    """px of box-centre travel per second, per track (median)."""
    pos = {}
    for r in rows:
        if r.get("box"):
            x1, y1, x2, y2 = r["box"]
            pos.setdefault(r["track"], []).append((r["t"], (x1 + x2) / 2, (y1 + y2) / 2, y2 - y1))
    out = {}
    for tid, p in pos.items():
        v = [math.hypot(b[1] - a[1], b[2] - a[2]) / max(b[0] - a[0], 1e-6) / max(a[3], 1) for a, b in zip(p, p[1:]) if b[0] > a[0]]
        out[tid] = sorted(v)[len(v) // 2] if v else 0.0
    return out


if __name__ == "__main__":
    path = sys.argv[1]
    cfgs = [MomentumConfig(*(float(x) for x in a.split(","))) for a in sys.argv[2:]] or [MomentumConfig()]
    rows = load(path)
    spd = speed_of(rows)
    print(f"{len(rows)} slot-frames, {len({r['track'] for r in rows})} tracks")
    for cfg in cfgs:
        res = replay(rows, cfg)
        on = {k: v for k, v in res.items() if v["spans"]}
        print(f"\n== decay {cfg.decay} on {cfg.on} off {cfg.off} max {cfg.max}: {len(on)}/{len(res)} slots turned ON")
        for (tid, cls), o in sorted(on.items(), key=lambda kv: kv[1]["spans"][0][0]):
            dur = sum(b - a for a, b in o["spans"])
            print(f"  track {tid:>4} {cls:<10} ON {len(o['spans'])}x, {dur:6.1f}s total, first at t={o['spans'][0][0]:7.1f}  "
                  f"hits {o['hits']}/{o['frames']}  peak {o['peak']:.1f}  person speed {spd.get(tid, 0):.2f} heights/s")
