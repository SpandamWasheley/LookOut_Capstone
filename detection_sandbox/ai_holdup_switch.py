"""Holdup accuracy / model-switch test for the AI checker.

  python detection_sandbox/ai_holdup_switch.py --stage frames      # 2B: 8 vs 12 frames on the holdup case
  python detection_sandbox/ai_holdup_switch.py --stage switch      # 2B smoking -> 4B holdup -> 2B smoking, timed
Run `stage switch` while watch_merged is running to measure with detection on.
"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_checker_bench as B
from core.vision import ai_checker as A

ap = argparse.ArgumentParser()
ap.add_argument("--stage", required=True)
ap.add_argument("--repeat", type=int, default=2)
a = ap.parse_args()
B.args = argparse.Namespace(frames=12, min_edge=0)
cases = {c[0]: c for c in B.CASES}


def prep(name, frames):
    _, kind, clip, t0, region, kw = cases[name]
    ring = B.build_ring(clip, t0, region)
    items = A.select_frames(ring.window(t0), t0, n=frames)
    keys = sorted({k for _, _, b in items for k in b})
    return kind, A.prepare_images(kind, items, keys), A.system_note(kind, **kw)


def call(model, kind, imgs, note):
    c = A.OllamaClient(model=model, timeout=300)
    r = A.check(c, kind, note, imgs)
    ans = r.reply and {k: r.reply[k] for k in ("holdup_likelihood", "scene_type", "object_pointed_at_a_person", "confidence") if k in r.reply}
    print(f"  {model:24s} {len(imgs):2d} frames {r.seconds and round(r.seconds,1)}s  {'valid' if r.ok else 'INVALID '+r.error[:60]} {json.dumps(ans) if ans else ''}", flush=True)
    return r


if a.stage == "frames":
    for n in (8, 12):
        kind, imgs, note = prep("holdup_knife", n)
        print(f"holdup, {n} frames, ctx {A.context_for(len(imgs))}")
        for _ in range(a.repeat):
            call("qwen3-vl:2b-instruct", kind, imgs, note)
    kind, imgs, note = prep("holdup_knife", 8)
    for _ in range(a.repeat):
        call("qwen3-vl:4b-instruct", kind, imgs, note)
else:
    sk, si, sn = prep("smoking_curb", 8)
    hk, hi, hn = prep("holdup_knife", 8)
    print("sequence: 2B smoking -> 4B holdup (switch) -> 4B holdup (warm) -> 2B smoking (switch back)")
    for model, k, i, n in (("qwen3-vl:2b-instruct", sk, si, sn), ("qwen3-vl:4b-instruct", hk, hi, hn),
                           ("qwen3-vl:4b-instruct", hk, hi, hn), ("qwen3-vl:2b-instruct", sk, si, sn),
                           ("qwen3-vl:4b-instruct", hk, hi, hn)):
        call(model, k, i, n)
