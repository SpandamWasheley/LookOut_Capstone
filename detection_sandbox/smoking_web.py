"""Standalone smoking-detection tester — upload a picture OR a video.

A tiny local Flask app dedicated to trying the custom smoking model
(models/smoking.pt). Drag in a photo and it boxes every cigarette / smoke /
vape / smoking it finds; drag in a short clip and it annotates the whole video
plus lists each moment smoking was seen.

    python smoking_web.py
    -> open http://localhost:5002

Uses smoking_detection.py (same model the Django backend + video_system use),
so results match everywhere.
"""

import base64
import re
import time
import uuid
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from flask import (Flask, abort, jsonify, render_template_string, request,
                   send_from_directory)

import smoking_detection as smoke

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "output" / "smoking_web"
WORK_DIR.mkdir(parents=True, exist_ok=True)

# amber/orange boxes, matching the "public smoking" tag colour elsewhere
BOX_COLOR = (0, 165, 245)  # BGR
EVENT_COOLDOWN_S = 1.5     # don't log the same ongoing smoking more than this often

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # 300 MB cap (videos)

PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LookOut - Smoking Detection Tester</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 0; background:#0f172a; color:#e2e8f0; }
  header { padding:20px 24px; background:#1e293b; border-bottom:1px solid #334155; }
  header h1 { margin:0; font-size:20px; } header h1 span { color:#fbbf24; }
  header p { margin:4px 0 0; color:#94a3b8; font-size:13px; }
  main { max-width:920px; margin:0 auto; padding:24px; }
  #drop { border:2px dashed #475569; border-radius:12px; padding:40px 20px;
          text-align:center; cursor:pointer; transition:.15s; background:#1e293b; }
  #drop.hover { border-color:#fbbf24; background:#1c1a10; }
  #drop strong { color:#fbbf24; }
  .row { display:flex; gap:18px; align-items:center; margin-top:16px; flex-wrap:wrap; }
  .row label { font-size:13px; color:#94a3b8; }
  input[type=range]{ vertical-align:middle; }
  .bar { height:8px; background:#1e293b; border-radius:99px; overflow:hidden; margin-top:18px; display:none; }
  .bar > div { height:100%; width:0; background:#fbbf24; transition:width .3s; }
  .status { margin-top:10px; color:#94a3b8; font-size:14px; }
  #result { margin-top:22px; }
  #result img, #result video { max-width:100%; border-radius:12px; border:1px solid #334155; display:block; }
  .summary { display:flex; gap:12px; flex-wrap:wrap; margin:16px 0; }
  .stat { background:#1e293b; border:1px solid #334155; border-radius:12px; padding:12px 16px; min-width:120px; }
  .stat b { font-size:26px; display:block; color:#fbbf24; }
  .chips { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
  .chip { background:#1e293b; border:1px solid #334155; border-radius:999px; padding:6px 14px; font-size:14px; }
  .chip b { color:#fbbf24; }
  .cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:14px; margin-top:14px; }
  .card { background:#1e293b; border:1px solid #334155; border-radius:12px; overflow:hidden; }
  .card img { width:100%; display:block; border:0; border-radius:0; }
  .card .body { padding:9px 12px; font-size:13px; }
  .tag { display:inline-block; padding:2px 8px; border-radius:99px; font-size:11px; font-weight:700;
         background:#78350f; color:#fde68a; }
  a.dl { color:#fbbf24; } h2 { margin:24px 0 6px; font-size:16px; }
  .muted { color:#94a3b8; }
</style>
</head>
<body>
<header>
  <h1>LookOut — <span>Smoking</span> Detection Tester</h1>
  <p>Upload a picture or a short video. It boxes every cigarette / smoke / vape / smoking it detects.</p>
</header>
<main>
  <div id="drop">
    <p><strong>Click to choose a picture or video</strong> or drag &amp; drop it here</p>
    <p class="muted">JPG / PNG image, or MP4 / MOV / AVI video (a 10-30s clip analyzes fastest)</p>
    <input id="file" type="file" accept="image/*,video/*" hidden>
  </div>
  <div class="row">
    <label>Sensitivity (confidence): <span id="confVal">0.30</span></label>
    <input id="conf" type="range" min="0.1" max="0.8" step="0.05" value="0.30">
    <span class="muted">lower = catch more (cigarettes are tiny)</span>
  </div>

  <div class="bar" id="bar"><div id="fill"></div></div>
  <div class="status" id="status"></div>
  <div id="result"></div>
</main>

<script>
const drop=document.getElementById('drop'), file=document.getElementById('file');
const conf=document.getElementById('conf'), confVal=document.getElementById('confVal');
const bar=document.getElementById('bar'), fill=document.getElementById('fill');
const statusEl=document.getElementById('status'), result=document.getElementById('result');

conf.oninput=()=>confVal.textContent=conf.value;
drop.onclick=()=>file.click();
file.onchange=()=>{ if(file.files[0]) handle(file.files[0]); };
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hover');}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hover');}));
drop.addEventListener('drop',ev=>{ if(ev.dataTransfer.files[0]) handle(ev.dataTransfer.files[0]); });

function handle(f){
  const isVideo = f.type.startsWith('video');
  result.innerHTML=''; bar.style.display='block'; fill.style.width='15%';
  statusEl.textContent=(isVideo?'Analyzing video ':'Detecting in image ')+'"'+f.name+'"… '
    +'(first run loads the model, give it a moment)';
  const fd=new FormData(); fd.append('file',f); fd.append('conf',conf.value);
  fill.style.width='40%';
  fetch(isVideo?'/detect-video':'/detect-image',{method:'POST',body:fd})
    .then(r=>r.json())
    .then(d=>{ fill.style.width='100%'; setTimeout(()=>bar.style.display='none',400); statusEl.textContent='';
      if(d.error){result.innerHTML='<p class="muted">'+d.error+'</p>';return;}
      isVideo?renderVideo(d):renderImage(d); })
    .catch(e=>{ bar.style.display='none'; statusEl.textContent='Error: '+e; });
}

function renderImage(d){
  let h='';
  if(d.count===0){ h+='<div class="summary"><div class="stat"><b>0</b>detections</div></div>'
    +'<p class="muted">Nothing detected — try lowering the sensitivity.</p>'; }
  else{
    h+='<div class="summary"><div class="stat"><b>'+d.count+'</b>detection'+(d.count>1?'s':'')+'</div></div>';
    h+='<div class="chips">';
    for(const [label,n] of Object.entries(d.counts)) h+='<span class="chip"><b>'+n+'</b> '+label+'</span>';
    h+='</div><ul class="muted">';
    for(const x of d.detections) h+='<li>'+x.label+' — '+Math.round(x.conf*100)+'%</li>';
    h+='</ul>';
  }
  h+='<div style="margin-top:14px"><img src="'+d.image+'"></div>';
  result.innerHTML=h;
}

function renderVideo(d){
  const s=d.events.length;
  let h='<div class="summary">'
    +'<div class="stat"><b>'+d.frames_analyzed+'</b>frames analyzed</div>'
    +'<div class="stat"><b>'+s+'</b>smoking moment'+(s!==1?'s':'')+'</div>'
    +'</div>';
  h+='<p style="margin-top:6px"><a class="dl" href="'+d.video_url+'" download>⬇ Download annotated video</a> '
    +'<span class="muted">('+d.duration+'s clip)</span></p>';
  h+='<div style="margin-top:12px"><video src="'+d.video_url+'" controls></video></div>';
  if(s){ h+='<h2>🚬 Smoking moments</h2><div class="cards">';
    d.events.forEach(e=>{ h+='<div class="card">'+(e.thumb_url?'<img src="'+e.thumb_url+'">':'')
      +'<div class="body"><span class="tag">SMOKING</span>'
      +'<div style="margin-top:6px">'+e.label+' @ '+e.time_str+'</div>'
      +'<div class="muted" style="margin-top:2px">'+Math.round(e.conf*100)+'% confidence</div></div></div>'; });
    h+='</div>'; }
  else h+='<p class="muted" style="margin-top:14px">No smoking detected — try a lower sensitivity '
    +'or a clip where a cigarette is clearly visible.</p>';
  result.innerHTML=h;
}
</script>
</body>
</html>
"""


def _annotate(frame, dets):
    for d in dets:
        x1, y1, x2, y2 = d["box"]
        cv2.rectangle(frame, (x1, y1), (x2, y2), BOX_COLOR, 2)
        cv2.putText(frame, f"{d['label']} {d['conf'] * 100:.0f}%", (x1, max(y1 - 8, 0)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, BOX_COLOR, 1)


@app.route("/")
def index():
    if not smoke.is_available():
        return ("<h2 style='font-family:system-ui'>Smoking model not installed</h2>"
                "<p style='font-family:system-ui'>Expected weights at "
                "<code>detection_sandbox/models/smoking.pt</code>. Train one with "
                "<code>train_smoking.py</code> first.</p>")
    return render_template_string(PAGE)


@app.route("/detect-image", methods=["POST"])
def detect_image():
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "No file uploaded."})
    try:
        conf = float(request.form.get("conf", 0.30))
    except ValueError:
        conf = 0.30

    data = np.frombuffer(upload.read(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"error": "Could not read that image file."})

    dets = smoke.detect_smoking(img, conf=conf)
    _annotate(img, dets)
    ok, buf = cv2.imencode(".jpg", img)
    b64 = base64.b64encode(buf).decode("ascii") if ok else ""

    counts = Counter(d["label"] for d in dets)
    return jsonify({
        "count": len(dets),
        "counts": dict(counts),
        "detections": [{"label": d["label"], "conf": d["conf"]} for d in dets],
        "image": "data:image/jpeg;base64," + b64,
    })


@app.route("/detect-video", methods=["POST"])
def detect_video():
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "No file uploaded."})
    try:
        conf = float(request.form.get("conf", 0.30))
    except ValueError:
        conf = 0.30

    session = uuid.uuid4().hex[:10]
    sess_dir = WORK_DIR / session
    sess_dir.mkdir(parents=True, exist_ok=True)
    src = sess_dir / ("input" + Path(upload.filename or "clip.mp4").suffix)
    upload.save(str(src))

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return jsonify({"error": "Could not open that video file."})

    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
    out_path = sess_dir / "annotated.mp4"
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    # Run detection ~4x/second (reuse boxes on skipped frames so the output video
    # stays smooth) — full-frame detection every frame is too slow on CPU.
    stride = max(1, int(round(fps / 4)))
    frame_idx = 0
    analyzed = 0
    last_dets = []
    events = []
    last_event_ts = -999.0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = frame_idx / fps
            if frame_idx % stride == 0:
                last_dets = smoke.detect_smoking(frame, conf=conf)
                analyzed += 1
                if last_dets and (t - last_event_ts) >= EVENT_COOLDOWN_S:
                    best = max(last_dets, key=lambda d: d["conf"])
                    x1, y1, x2, y2 = best["box"]
                    annotated = frame.copy()
                    _annotate(annotated, last_dets)
                    thumb = sess_dir / f"event_{len(events):03d}.jpg"
                    cv2.imwrite(str(thumb), annotated)
                    mm, ss = divmod(int(t), 60)
                    events.append({
                        "label": best["label"], "conf": best["conf"],
                        "time_str": f"{mm:01d}:{ss:02d}",
                        "thumb_url": f"/files/{session}/{thumb.name}",
                    })
                    last_event_ts = t
            _annotate(frame, last_dets)
            writer.write(frame)
            frame_idx += 1
    finally:
        cap.release()
        writer.release()

    duration = round(frame_idx / fps, 1) if fps else 0
    return jsonify({
        "video_url": f"/files/{session}/{out_path.name}",
        "duration": duration,
        "frames_analyzed": analyzed,
        "events": events,
    })


@app.route("/files/<session>/<path:filename>")
def files(session, filename):
    # session is a server-generated 10-char hex id (uuid4().hex[:10]); reject
    # anything else so it can't be used to traverse out of WORK_DIR. filename is
    # additionally sanitized by send_from_directory itself.
    if not re.fullmatch(r"[0-9a-f]{10}", session):
        abort(404)
    return send_from_directory(WORK_DIR / session, filename)


if __name__ == "__main__":
    print("LookOut smoking detection tester -> http://localhost:5002")
    app.run(host="0.0.0.0", port=5002, debug=False)
