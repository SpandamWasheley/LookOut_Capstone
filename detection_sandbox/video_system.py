"""LookOut video detection system — web app.

Upload a video in the browser; it analyzes it for:
  1. Illegal parking / obstruction  (a vehicle stationary >= dwell seconds,
     optionally only inside a no-parking zone)
  2. Public drinking  (a bottle / cup / wine glass held by a person)

and shows a report with evidence snapshots, plus the full annotated video to
download.

    python video_system.py
    -> open http://localhost:5001

Detection lives in detection_core.py (shared with the CLI). CPU analysis of a
video takes a while — a short clip (10-30s) is best for a demo.
"""

import shutil
import time
import uuid
from pathlib import Path

from flask import (Flask, jsonify, render_template_string, request,
                   send_from_directory)

import detection_core as core

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "output" / "web"
WORK_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # 300 MB video cap

PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LookOut - Video Detection</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 0; background:#0f172a; color:#e2e8f0; }
  header { padding:20px 24px; background:#1e293b; border-bottom:1px solid #334155; }
  header h1 { margin:0; font-size:20px; }
  header p { margin:4px 0 0; color:#94a3b8; font-size:13px; }
  main { max-width:920px; margin:0 auto; padding:24px; }
  #drop { border:2px dashed #475569; border-radius:12px; padding:38px 20px;
          text-align:center; cursor:pointer; background:#1e293b; }
  #drop.hover { border-color:#38bdf8; background:#172033; }
  #drop strong { color:#38bdf8; }
  .row { display:flex; gap:18px; align-items:center; margin-top:16px; flex-wrap:wrap; }
  .row label { font-size:13px; color:#94a3b8; }
  button { background:#38bdf8; color:#0f172a; border:0; padding:10px 18px;
           border-radius:8px; font-weight:600; cursor:pointer; }
  .bar { height:8px; background:#1e293b; border-radius:99px; overflow:hidden; margin-top:18px; display:none; }
  .bar > div { height:100%; width:0; background:#38bdf8; transition:width .3s; }
  .status { margin-top:10px; color:#94a3b8; font-size:14px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:14px; margin-top:16px; }
  .card { background:#1e293b; border:1px solid #334155; border-radius:12px; overflow:hidden; }
  .card img { width:100%; display:block; }
  .card .body { padding:10px 12px; font-size:13px; }
  .tag { display:inline-block; padding:2px 8px; border-radius:99px; font-size:11px; font-weight:700; }
  .tag.park { background:#7f1d1d; color:#fecaca; }
  .tag.drink { background:#701a75; color:#f5d0fe; }
  h2 { margin:26px 0 6px; font-size:16px; }
  .summary { display:flex; gap:12px; flex-wrap:wrap; margin-top:16px; }
  .stat { background:#1e293b; border:1px solid #334155; border-radius:12px; padding:12px 16px; min-width:130px; }
  .stat b { font-size:26px; display:block; color:#38bdf8; }
  .stat.red b { color:#f87171; } .stat.pink b { color:#e879f9; }
  a.dl { color:#38bdf8; }
  .muted { color:#94a3b8; }
</style>
</head>
<body>
<header>
  <h1>LookOut — Video Detection System</h1>
  <p>Illegal parking / obstruction &amp; public drinking, from an uploaded video.</p>
</header>
<main>
  <div id="drop">
    <p><strong>Click to choose a video</strong> or drag &amp; drop it here</p>
    <p class="muted">MP4 / MOV / AVI — a short clip (10-30s) analyzes fastest</p>
    <input id="file" type="file" accept="video/*" hidden>
  </div>
  <div class="row">
    <label>Dwell (s): <input id="dwell" type="number" value="5" min="1" style="width:60px"></label>
    <label>Sensitivity: <input id="conf" type="number" value="0.35" min="0.1" max="0.8" step="0.05" style="width:60px"></label>
    <span class="muted">dwell = seconds parked before flagged (use ~60 for real streets)</span>
  </div>

  <div class="bar" id="bar"><div id="fill"></div></div>
  <div class="status" id="status"></div>
  <div id="result"></div>
</main>

<script>
const drop=document.getElementById('drop'), file=document.getElementById('file');
const bar=document.getElementById('bar'), fill=document.getElementById('fill');
const statusEl=document.getElementById('status'), result=document.getElementById('result');

drop.onclick=()=>file.click();
file.onchange=()=>{ if(file.files[0]) upload(file.files[0]); };
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hover');}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hover');}));
drop.addEventListener('drop',ev=>{ if(ev.dataTransfer.files[0]) upload(ev.dataTransfer.files[0]); });

function upload(f){
  result.innerHTML='';
  bar.style.display='block'; fill.style.width='10%';
  statusEl.textContent='Uploading & analyzing "'+f.name+'"… (first run loads the model; large videos take a while)';
  const fd=new FormData();
  fd.append('video',f); fd.append('dwell',document.getElementById('dwell').value);
  fd.append('conf',document.getElementById('conf').value);
  fill.style.width='35%';
  fetch('/analyze',{method:'POST',body:fd})
    .then(r=>r.json())
    .then(d=>{ fill.style.width='100%'; setTimeout(()=>bar.style.display='none',400);
      statusEl.textContent=''; if(d.error){result.innerHTML='<p class="muted">'+d.error+'</p>';return;} render(d); })
    .catch(e=>{ bar.style.display='none'; statusEl.textContent='Error: '+e; });
}

function render(d){
  const p=d.parking.length, k=d.drinking.length;
  let h='<div class="summary">'
    +'<div class="stat"><b>'+d.frames_analyzed+'</b>frames analyzed</div>'
    +'<div class="stat red"><b>'+p+'</b>parking / obstruction</div>'
    +'<div class="stat pink"><b>'+k+'</b>public drinking</div>'
    +'</div>';
  h+='<p style="margin-top:14px"><a class="dl" href="'+d.video_url+'" download>⬇ Download annotated video</a> '
    +'<span class="muted">('+d.duration+'s clip)</span></p>';

  if(p){ h+='<h2>🚗 Illegal parking / obstruction</h2><div class="cards">';
    d.parking.forEach(e=>{ h+=card('park', e); }); h+='</div>'; }
  if(k){ h+='<h2>🍾 Public drinking</h2><div class="cards">';
    d.drinking.forEach(e=>{ h+=card('drink', e); }); h+='</div>'; }
  if(!p && !k) h+='<p class="muted" style="margin-top:16px">No violations detected. '
    +'Try a lower dwell or higher sensitivity, or a clip with a parked vehicle / a bottle held by someone.</p>';
  result.innerHTML=h;
}
function card(kind,e){
  const cls=kind==='park'?'park':'drink';
  const label=kind==='park'?'ILLEGAL PARKING':'PUBLIC DRINKING';
  return '<div class="card">'+(e.thumb_url?'<img src="'+e.thumb_url+'">':'')
    +'<div class="body"><span class="tag '+cls+'">'+label+'</span>'
    +'<div style="margin-top:6px">'+e.label+' @ '+e.time_str+'</div>'
    +'<div class="muted" style="margin-top:2px">'+e.reason+'</div></div></div>';
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/analyze", methods=["POST"])
def analyze():
    upload = request.files.get("video")
    if upload is None:
        return jsonify({"error": "No video uploaded."})

    try:
        dwell = float(request.form.get("dwell", 5))
    except ValueError:
        dwell = 5.0
    try:
        conf = float(request.form.get("conf", 0.35))
    except ValueError:
        conf = 0.35

    session = uuid.uuid4().hex[:10]
    sess_dir = WORK_DIR / session
    sess_dir.mkdir(parents=True, exist_ok=True)
    src = sess_dir / ("input" + Path(upload.filename or "clip.mp4").suffix)
    upload.save(str(src))

    try:
        report = core.process_video(src, sess_dir, dwell=dwell, conf=conf)
    except Exception as exc:  # noqa: BLE001 - surface any decode/model error to the UI
        return jsonify({"error": f"Could not analyze video: {exc}"})

    def to_url(path):
        return f"/files/{session}/{Path(path).relative_to(sess_dir).as_posix()}"

    for ev in report["parking"] + report["drinking"]:
        if ev.get("thumb"):
            ev["thumb_url"] = to_url(ev["thumb"])

    return jsonify({
        "video_url": to_url(report["output_video"]),
        "duration": report["duration"],
        "frames_analyzed": report["frames_analyzed"],
        "parking": report["parking"],
        "drinking": report["drinking"],
    })


@app.route("/files/<session>/<path:filename>")
def files(session, filename):
    return send_from_directory(WORK_DIR / session, filename)


if __name__ == "__main__":
    print("LookOut video system running -> http://localhost:5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
