"""Summarises one run_clip_set.sh output dir; with two labels, prints a before/after diff.

  python detection_sandbox/summarize_run.py baseline [pose]
"""
import json, os, re, statistics as st, sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

def load(label):
    d = os.path.join(OUT, label); res = {}
    times = dict(re.findall(r"(\S+) frames=(\d+) wall_s=(\d+)", "")) if False else {}
    for line in open(os.path.join(d, "times.txt")):
        m = re.match(r"(\S+) frames=(\d+) wall_s=(\d+)", line)
        if m: times[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    for name, (frames, wall) in times.items():
        rows = []
        p = os.path.join(d, name + ".jsonl")
        if os.path.exists(p): rows = [json.loads(l) for l in open(p)]
        mouth = [r for r in rows if r["kind"] in ("smoking", "drinking")]
        alerts = [r for r in rows if r["kind"] == "alert"]
        ratios = [r["ratio"] for r in mouth if r.get("ratio") is not None]
        res[name] = dict(
            fps=round(frames / max(wall, 1), 2), wall_s=wall,
            alerts=[(a["engine"], a["label"], round(a["score"], 2), a.get("level", ""), a.get("cues", [])) for a in alerts],
            mouth_queries=len(mouth), mouth_none=sum(1 for r in mouth if r.get("anchor") is None),
            ratio_median=round(st.median(ratios), 2) if ratios else None,
            ratio_p25_p75=(round(st.quantiles(ratios, n=4)[0], 2), round(st.quantiles(ratios, n=4)[2], 2)) if len(ratios) >= 4 else None,
            ratio_min=round(min(ratios), 2) if ratios else None)
    return res

labels = sys.argv[1:]
data = {l: load(l) for l in labels}
for l in labels:
    print(f"\n== {l}")
    for n, r in data[l].items():
        print(f"{n[:44]:44s} fps {r['fps']:5.2f}  mouth q {r['mouth_queries']:4d} (no anchor {r['mouth_none']:4d})  ratio med {r['ratio_median']} IQR {r['ratio_p25_p75']} min {r['ratio_min']}")
        for a in r["alerts"]: print("     ALERT", a)
if len(labels) == 2:
    a, b = data[labels[0]], data[labels[1]]
    print(f"\n== {labels[0]} -> {labels[1]}")
    for n in a:
        if n not in b: continue
        sa = {(x[0], x[1]) for x in a[n]["alerts"]}; sb = {(x[0], x[1]) for x in b[n]["alerts"]}
        print(f"{n[:44]:44s} alerts {len(a[n]['alerts'])} -> {len(b[n]['alerts'])}  new {sorted(sb - sa)}  lost {sorted(sa - sb)}  "
              f"fps {a[n]['fps']} -> {b[n]['fps']}  no-anchor {a[n]['mouth_none']}/{a[n]['mouth_queries']} -> {b[n]['mouth_none']}/{b[n]['mouth_queries']}")
