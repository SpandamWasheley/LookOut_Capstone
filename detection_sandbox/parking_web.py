"""Sidewalk-obstruction tester — upload YOUR video, draw the sidewalks, get a verdict.

    python parking_web.py
    -> open http://localhost:5003

Two steps in the browser:

  1. Drop in a clip. The first frame comes back and you draw ONE ZONE PER
     SIDEWALK on it — four corners each — and type each one's real width in
     metres (a tape measure, or scale it off a car: a sedan is about 1.8m wide).
     A street with footpaths on both sides gets two zones.

  2. It runs YOLO over the clip, tracks each vehicle, and for anything that has
     been stationary longer than the dwell it measures how much walking room is
     left ON EACH SIDEWALK separately. You get an annotated video and the moments
     each footpath was blocked.

The answer is a number a barangay official can act on — "north footpath: 0.42m
of 1.50m left passable" — instead of a pixel overlap that means nothing on
another camera.

WHY ONE ZONE PER SIDEWALK, not one big polygon. Each footpath needs its own
width (they are rarely equal), its own across-axis (the direction a pedestrian's
shoulders have to fit into points opposite ways on the two sides of a street),
and its own verdict. And each must be exactly FOUR corners: that is not a UI
limit but the maths — four corners of a known rectangle is precisely what solves
for the perspective. A footpath that bends gets two zones, not a five-sided one.

Geometry lives in sidewalk_geometry.py; vehicle detection reuses
vehicle_detection.py, the same cached YOLO the other sandbox tools use.

The camera must be STATIC and looking down at the ground — each homography is
computed once from your clicks. Handheld, drone and dashcam footage cannot work.
"""

import base64
import json
import re
import uuid
from pathlib import Path

import cv2
from flask import (Flask, abort, jsonify, render_template_string, request,
                   send_from_directory)

import sidewalk_geometry as sg
import vehicle_detection as vd

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "output" / "parking_web"
WORK_DIR.mkdir(parents=True, exist_ok=True)

EVENT_COOLDOWN_S = 4.0      # don't log the same ongoing blockage more often
TRACK_MATCH_IOU = 0.3
TRACK_GRACE_S = 2.0

# Detection rate. A 10-minute clip at 30fps is 18,000 frames; decoding,
# annotating and re-encoding every one of them would take far longer than the
# clip itself and produce a huge file for no extra information. Frames between
# these samples are grabbed but never decoded, and the output video is written at
# this rate — so a 10-minute clip stays a 10-minute video, at 4fps.
ANALYSIS_FPS = 4.0

COLOR = {sg.CLEAR: (80, 200, 80), sg.ENCROACHING: (40, 165, 240),
         sg.BLOCKED: (60, 60, 220)}
# Per-zone outline colours (BGR), cycled. Matched in the browser by ZONE_CSS.
ZONE_COLORS = [(250, 200, 90), (150, 240, 140), (200, 150, 250), (120, 220, 250)]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024   # 1 GB — 10 min of 1080p

PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LookOut - Sidewalk Obstruction Tester</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin:0; background:#0f172a; color:#e2e8f0; }
  header { padding:20px 24px; background:#1e293b; border-bottom:1px solid #334155; }
  header h1 { margin:0; font-size:20px; } header h1 span { color:#38bdf8; }
  header p { margin:4px 0 0; color:#94a3b8; font-size:13px; }
  main { max-width:1040px; margin:0 auto; padding:24px; }
  #drop { border:2px dashed #475569; border-radius:12px; padding:40px 20px;
          text-align:center; cursor:pointer; transition:.15s; background:#1e293b; }
  #drop.hover { border-color:#38bdf8; background:#102030; }
  #drop strong { color:#38bdf8; }
  .step { margin-top:22px; padding:16px; background:#1e293b; border:1px solid #334155;
          border-radius:12px; display:none; }
  .step h2 { margin:0 0 10px; font-size:15px; color:#38bdf8; }
  #canvas { max-width:100%; border-radius:10px; border:1px solid #334155;
            cursor:crosshair; display:block; }
  .row { display:flex; gap:16px; align-items:center; margin-top:14px; flex-wrap:wrap; }
  .row label { font-size:13px; color:#94a3b8; }
  input[type=number]{ width:86px; background:#0f172a; color:#e2e8f0; border:1px solid #334155;
                      border-radius:8px; padding:7px 9px; }
  input[type=text]{ width:150px; background:#0f172a; color:#e2e8f0; border:1px solid #334155;
                    border-radius:8px; padding:7px 9px; }
  button { background:#38bdf8; color:#03202e; border:0; border-radius:9px; padding:10px 18px;
           font-weight:700; cursor:pointer; font-size:14px; }
  button:disabled { background:#334155; color:#64748b; cursor:not-allowed; }
  button.ghost { background:#334155; color:#e2e8f0; }
  button.tiny { padding:5px 10px; font-size:12px; }
  table.zones { width:100%; border-collapse:collapse; margin-top:14px; font-size:13px; }
  table.zones th { text-align:left; color:#94a3b8; font-weight:600; padding:6px 8px;
                   border-bottom:1px solid #334155; }
  table.zones td { padding:6px 8px; border-bottom:1px solid #24324a; }
  .swatch { display:inline-block; width:12px; height:12px; border-radius:3px; margin-right:8px;
            vertical-align:-1px; }
  .bar { height:8px; background:#0f172a; border-radius:99px; overflow:hidden; margin-top:16px; display:none; }
  .bar > div { height:100%; width:0; background:#38bdf8; transition:width .4s; }
  .status { margin-top:10px; color:#94a3b8; font-size:14px; }
  #result { margin-top:22px; }
  #result video { max-width:100%; border-radius:12px; border:1px solid #334155; display:block; }
  .summary { display:flex; gap:12px; flex-wrap:wrap; margin:16px 0; }
  .stat { background:#1e293b; border:1px solid #334155; border-radius:12px; padding:12px 16px; min-width:150px; }
  .stat b { font-size:24px; display:block; color:#38bdf8; }
  .stat.bad b { color:#f87171; }
  .stat small { color:#94a3b8; display:block; margin-top:3px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:14px; margin-top:14px; }
  .card { background:#1e293b; border:1px solid #334155; border-radius:12px; overflow:hidden; }
  .card img { width:100%; display:block; }
  .card .body { padding:9px 12px; font-size:13px; }
  .tag { display:inline-block; padding:2px 8px; border-radius:99px; font-size:11px; font-weight:700;
         background:#7f1d1d; color:#fecaca; }
  .muted { color:#94a3b8; font-size:13px; }
  h2.sec { margin:24px 0 6px; font-size:16px; }
  ol { margin:6px 0 0 18px; padding:0; color:#94a3b8; font-size:13px; line-height:1.6; }
  a.dl { color:#38bdf8; }
</style>
</head>
<body>
<header>
  <h1>LookOut &mdash; <span>Sidewalk Obstruction</span> Tester</h1>
  <p>Upload a clip from a fixed, elevated camera. Draw one zone per footpath. Get how much walking room is left, in metres.</p>
</header>
<main>
  <div id="drop">
    <p><strong>Click to choose a video</strong> or drag &amp; drop it here</p>
    <p class="muted">MP4 / MOV / AVI, up to 1&nbsp;GB &mdash; a 10-minute clip is fine but takes several minutes to analyse.
       The camera must be STATIC and looking down; handheld or dashcam footage cannot work.</p>
    <input id="file" type="file" accept="video/*" hidden>
  </div>

  <div class="step" id="step2">
    <h2>Step 2 &mdash; draw one zone per sidewalk (4 corners each)</h2>
    <ol>
      <li><b>1</b> &mdash; near corner, building side (nearest the camera)</li>
      <li><b>2</b> &mdash; far corner, building side</li>
      <li><b>3</b> &mdash; far corner, kerb side</li>
      <li><b>4</b> &mdash; near corner, kerb side</li>
    </ol>
    <p class="muted" style="margin:10px 0 12px">Go around each footpath in order. The
      <b>width</b> is the building&rarr;kerb direction &mdash; that is the number you measure.
      A street with paths on both sides gets <b>two zones</b>; draw the second one the same way.</p>
    <canvas id="canvas"></canvas>
    <table class="zones" id="zoneTable" style="display:none">
      <thead><tr><th>Zone</th><th>Name</th><th>Width (m)</th><th></th></tr></thead>
      <tbody id="zoneBody"></tbody>
    </table>
    <div class="row">
      <button id="undo" class="ghost">Undo point</button>
      <button id="clear" class="ghost">Clear all zones</button>
      <span class="muted" id="hint">0 of 4 corners placed &mdash; 0 zones</span>
    </div>
    <div class="row">
      <label>Min passable (m) <input id="minpass" type="number" value="0.75" step="0.05" min="0.2"></label>
      <label>Parked for (s) <input id="dwell" type="number" value="5" step="1" min="0"></label>
      <label>Confidence <input id="conf" type="number" value="0.35" step="0.05" min="0.1" max="0.9"></label>
      <button id="run" disabled>Analyse clip</button>
    </div>
    <p class="muted" style="margin-top:12px">No tape measure? Scale it off a vehicle in
       frame &mdash; a sedan is ~1.8m wide, a jeepney ~2.1m. &plusmn;10% is fine against a 0.75m threshold.</p>
  </div>

  <div class="bar" id="bar"><div id="fill"></div></div>
  <div class="status" id="status"></div>
  <div id="result"></div>
</main>

<script>
const ZONE_CSS = ['#5ac8fa','#8cf08c','#fa96c8','#fadc78'];
const drop=document.getElementById('drop'), file=document.getElementById('file');
const step2=document.getElementById('step2'), canvas=document.getElementById('canvas');
const ctx=canvas.getContext('2d'), runBtn=document.getElementById('run');
const undoBtn=document.getElementById('undo'), clearBtn=document.getElementById('clear');
const hint=document.getElementById('hint'), zoneTable=document.getElementById('zoneTable');
const zoneBody=document.getElementById('zoneBody');
const bar=document.getElementById('bar'), fill=document.getElementById('fill');
const statusEl=document.getElementById('status'), result=document.getElementById('result');
let session=null, img=new Image(), zones=[], cur=[];

drop.onclick=()=>file.click();
file.onchange=()=>{ if(file.files[0]) upload(file.files[0]); };
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hover');}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hover');}));
drop.addEventListener('drop',ev=>{ if(ev.dataTransfer.files[0]) upload(ev.dataTransfer.files[0]); });

function upload(f){
  result.innerHTML=''; step2.style.display='none'; zones=[]; cur=[];
  bar.style.display='block'; fill.style.width='30%';
  statusEl.textContent='Reading "'+f.name+'"…';
  const fd=new FormData(); fd.append('file',f);
  fetch('/frame',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{
    bar.style.display='none'; statusEl.textContent='';
    if(d.error){ statusEl.textContent=d.error; return; }
    session=d.session;
    img.onload=()=>{ canvas.width=img.width; canvas.height=img.height; redraw();
                     step2.style.display='block'; };
    img.src=d.image;
  }).catch(e=>{ bar.style.display='none'; statusEl.textContent='Error: '+e; });
}

canvas.addEventListener('click',ev=>{
  const r=canvas.getBoundingClientRect();
  // the canvas is CSS-scaled to fit, so map the click back to native pixels
  cur.push([ (ev.clientX-r.left)*(canvas.width/r.width),
             (ev.clientY-r.top)*(canvas.height/r.height) ]);
  if(cur.length===4){                     // a zone is complete the moment it has 4
    zones.push({name:'Sidewalk '+(zones.length+1), width:1.5, pts:cur});
    cur=[];
    renderZones();
  }
  redraw();
});
undoBtn.onclick=()=>{
  if(cur.length) cur.pop();
  else if(zones.length){ cur=zones.pop().pts; cur.pop(); renderZones(); }
  redraw();
};
clearBtn.onclick=()=>{ zones=[]; cur=[]; renderZones(); redraw(); };

function renderZones(){
  zoneBody.innerHTML='';
  zones.forEach((z,i)=>{
    const tr=document.createElement('tr');
    tr.innerHTML='<td><span class="swatch" style="background:'+ZONE_CSS[i%4]+'"></span>'+(i+1)+'</td>'
      +'<td><input type="text" value="'+z.name+'" data-i="'+i+'" data-k="name"></td>'
      +'<td><input type="number" value="'+z.width+'" step="0.1" min="0.3" data-i="'+i+'" data-k="width"></td>'
      +'<td><button class="ghost tiny" data-del="'+i+'">remove</button></td>';
    zoneBody.appendChild(tr);
  });
  zoneBody.querySelectorAll('input').forEach(inp=>{
    inp.oninput=()=>{ const z=zones[+inp.dataset.i];
      z[inp.dataset.k] = inp.dataset.k==='width' ? parseFloat(inp.value)||0 : inp.value; };
  });
  zoneBody.querySelectorAll('button[data-del]').forEach(b=>{
    b.onclick=()=>{ zones.splice(+b.dataset.del,1); renderZones(); redraw(); };
  });
  zoneTable.style.display = zones.length ? 'table' : 'none';
  runBtn.disabled = zones.length===0;
}

function poly(pts,color,close,label){
  if(pts.length<2) { dots(pts,color); return; }
  ctx.beginPath(); ctx.moveTo(pts[0][0],pts[0][1]);
  for(let i=1;i<pts.length;i++) ctx.lineTo(pts[i][0],pts[i][1]);
  if(close){ ctx.closePath(); ctx.fillStyle=color+'2e'; ctx.fill(); }
  ctx.strokeStyle=color; ctx.lineWidth=3; ctx.stroke();
  if(label){ ctx.fillStyle=color; ctx.font='bold 15px system-ui'; ctx.textAlign='left';
    ctx.fillText(label, pts[0][0]+8, pts[0][1]-8); }
  dots(pts,color);
}
function dots(pts,color){
  pts.forEach((p,i)=>{
    ctx.beginPath(); ctx.arc(p[0],p[1],9,0,7); ctx.fillStyle=color; ctx.fill();
    ctx.fillStyle='#03202e'; ctx.font='bold 13px system-ui'; ctx.textAlign='center';
    ctx.fillText(i+1,p[0],p[1]+5);
  });
}
function redraw(){
  ctx.drawImage(img,0,0);
  zones.forEach((z,i)=>poly(z.pts, ZONE_CSS[i%4], true, z.name));
  poly(cur, ZONE_CSS[zones.length%4], false, null);
  hint.textContent = cur.length+' of 4 corners placed — '+zones.length+' zone'
    +(zones.length===1?'':'s')+' drawn';
}

runBtn.onclick=()=>{
  if(zones.some(z=>!(z.width>0))){ statusEl.textContent='Every zone needs a width in metres.'; return; }
  bar.style.display='block'; fill.style.width='20%'; result.innerHTML='';
  statusEl.textContent='Analysing… a long clip takes several minutes '
    +'(the first run also downloads YOLOv8n, ~6MB). Leave this tab open.';
  const fd=new FormData();
  fd.append('session',session); fd.append('zones',JSON.stringify(zones));
  fd.append('minpass',document.getElementById('minpass').value);
  fd.append('dwell',document.getElementById('dwell').value);
  fd.append('conf',document.getElementById('conf').value);
  fill.style.width='55%';
  fetch('/analyse',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{
    fill.style.width='100%'; setTimeout(()=>bar.style.display='none',400);
    statusEl.textContent='';
    if(d.error){ result.innerHTML='<p class="muted">'+d.error+'</p>'; return; }
    render(d);
  }).catch(e=>{ bar.style.display='none'; statusEl.textContent='Error: '+e; });
};

function render(d){
  let h='<div class="summary">';
  d.zones.forEach((z,i)=>{
    const bad = z.state==='BLOCKED';
    h+='<div class="stat'+(bad?' bad':'')+'"><b>'+z.worst_free.toFixed(2)+'m</b>'
      +'<span class="swatch" style="background:'+ZONE_CSS[i%4]+'"></span>'+z.name
      +'<small>of '+z.width.toFixed(2)+'m &middot; '+z.state
      +(z.blocked_s?' &middot; '+z.blocked_s+'s blocked':'')+'</small></div>';
  });
  h+='<div class="stat"><b>'+d.frames_analyzed+'</b>frames analysed<small>of '+d.duration+'s</small></div>';
  h+='</div>';
  h+='<p class="muted">'+d.summary+'</p>';
  h+='<p style="margin-top:10px"><a class="dl" href="'+d.video_url+'" download>&#11015; Download annotated video</a> '
    +'<span class="muted">('+d.duration+'s, written at '+d.out_fps+' fps)</span></p>';
  h+='<div style="margin-top:12px"><video src="'+d.video_url+'" controls></video></div>';
  if(d.events.length){
    h+='<h2 class="sec">Blocked moments</h2><div class="cards">';
    d.events.forEach(e=>{ h+='<div class="card"><img src="'+e.thumb_url+'">'
      +'<div class="body"><span class="tag">BLOCKED</span>'
      +'<div style="margin-top:6px">'+e.zone+' &mdash; '+e.free.toFixed(2)+'m free @ '+e.time_str+'</div>'
      +'<div class="muted" style="margin-top:2px">'+e.label+', parked '+e.parked+'s</div></div></div>'; });
    h+='</div>';
  } else {
    h+='<p class="muted" style="margin-top:14px">No footpath was blocked. If you expected a '
      +'hit: check each zone sits on the sidewalk, lower "Parked for" (a short clip may not '
      +'contain enough dwell), or lower the confidence.</p>';
  }
  result.innerHTML=h;
}
</script>
</body>
</html>
"""


class Track:
    """Minimal IoU tracker with a stillness clock — mirrors watch_parking, so a
    vehicle merely driving past never counts as an obstruction."""

    __slots__ = ("box", "anchor", "still_since", "last_seen", "label")

    def __init__(self, box, label, now):
        self.box, self.label = box, label
        self.anchor = sg.ground_point(box)
        self.still_since = now
        self.last_seen = now

    def update(self, box, now, move_tol):
        self.box, self.last_seen = box, now
        gx, gy = sg.ground_point(box)
        ax, ay = self.anchor
        if ((gx - ax) ** 2 + (gy - ay) ** 2) ** 0.5 > move_tol:
            self.anchor, self.still_since = (gx, gy), now

    def parked_for(self, now):
        return now - self.still_since


def _iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    return inter / ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)


def _session_dir(session):
    if not re.fullmatch(r"[0-9a-f]{10}", session or ""):
        abort(404)
    return WORK_DIR / session


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/frame", methods=["POST"])
def frame():
    """Saves the upload and hands back its first frame for the corner-clicking."""
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "No file uploaded."})

    session = uuid.uuid4().hex[:10]
    sess = WORK_DIR / session
    sess.mkdir(parents=True, exist_ok=True)
    src = sess / ("input" + Path(upload.filename or "clip.mp4").suffix)
    upload.save(str(src))

    cap = cv2.VideoCapture(str(src))
    ok, first = cap.read()
    cap.release()
    if not ok:
        return jsonify({"error": "Could not read that video file."})

    ok, buf = cv2.imencode(".jpg", first)
    return jsonify({
        "session": session,
        "image": "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii"),
    })


def _parse_zones(raw, min_passable):
    """[{name, width, pts}] -> [(name, Sidewalk)], or raises ValueError."""
    zones = json.loads(raw)
    if not zones:
        raise ValueError("Draw at least one sidewalk zone.")
    out = []
    for i, z in enumerate(zones):
        pts = z.get("pts") or []
        if len(pts) != 4:
            raise ValueError(f"Zone {i + 1} needs exactly four corners.")
        width = float(z.get("width") or 0)
        if width <= 0:
            raise ValueError(f"Zone {i + 1} needs a width in metres.")
        name = (z.get("name") or f"Sidewalk {i + 1}").strip()[:40]
        out.append((name, sg.Sidewalk(pts, width, min_passable_m=min_passable)))
    return out


@app.route("/analyse", methods=["POST"])
def analyse():
    sess = _session_dir(request.form.get("session"))
    srcs = list(sess.glob("input.*"))
    if not srcs:
        return jsonify({"error": "Session expired — upload the clip again."})

    def num(name, default):
        try:
            return float(request.form.get(name, default))
        except (TypeError, ValueError):
            return default

    try:
        walks = _parse_zones(request.form.get("zones", "[]"), num("minpass", 0.75))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return jsonify({"error": str(exc) or "Could not read the zones."})

    dwell = max(num("dwell", 5.0), 0.0)
    conf = min(max(num("conf", 0.35), 0.05), 0.95)

    cap = cv2.VideoCapture(str(srcs[0]))
    if not cap.isOpened():
        return jsonify({"error": "Could not open that video file."})
    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480

    # Sample at ANALYSIS_FPS. Frames in between are grabbed (cheap, no decode)
    # purely to keep the frame index — and therefore every timestamp and dwell
    # timer — exact.
    step = max(1, int(round(fps / ANALYSIS_FPS)))
    out_fps = fps / step
    out_path = sess / "annotated.mp4"
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             out_fps, (w, h))

    move_tol = max(w, h) * 0.02      # "still" radius, scaled to the frame
    tracks = []
    frame_idx, analyzed = 0, 0
    events = []
    # per zone: worst free width, where, how many analysed frames were blocked,
    # and when it last raised an event
    worst = [(walk.width_m, 0.0) for _, walk in walks]
    blocked_n = [0] * len(walks)
    last_event = [-999.0] * len(walks)

    try:
        while True:
            if not cap.grab():
                break
            if frame_idx % step:
                frame_idx += 1
                continue
            ok, img = cap.retrieve()
            if not ok:
                break

            t = frame_idx / fps
            analyzed += 1
            dets = vd.detect_vehicles(img, conf=conf)

            claimed = set()
            for d in dets:
                best, best_iou = None, TRACK_MATCH_IOU
                for tr in tracks:
                    if id(tr) in claimed:
                        continue
                    o = _iou(d["box"], tr.box)
                    if o > best_iou:
                        best, best_iou = tr, o
                if best is None:
                    best = Track(d["box"], d["label"], t)
                    tracks.append(best)
                claimed.add(id(best))
                best.update(d["box"], t, move_tol)
                d["parked"] = best.parked_for(t)
            tracks = [tr for tr in tracks if t - tr.last_seen <= TRACK_GRACE_S]

            # Each parked vehicle is tested against every zone. Its ground point
            # can only fall inside one, so a vehicle on the north footpath never
            # affects the south one's verdict.
            zone_spans = [[] for _ in walks]
            zone_of = {}
            for n, d in enumerate(dets):
                if d.get("parked", 0.0) < dwell:
                    continue
                for zi, (_, walk) in enumerate(walks):
                    span = walk.footprint_span(d["box"], d["label"])
                    if span is not None and walk.blocked_m(span) >= walk.encroach_m:
                        zone_spans[zi].append(span)
                        zone_of[n] = (zi, span)
                        break

            states = []
            for zi, (name, walk) in enumerate(walks):
                free_m, at_m = walk.free_width(zone_spans[zi])
                state = walk.verdict(free_m, bool(zone_spans[zi]))
                states.append((name, walk, free_m, at_m, state))
                if free_m < worst[zi][0]:
                    worst[zi] = (free_m, at_m)
                if state == sg.BLOCKED:
                    blocked_n[zi] += 1

            # ---- annotate ----
            for zi, (name, walk) in enumerate(walks):
                walk.draw_zone(img, ZONE_COLORS[zi % len(ZONE_COLORS)])
                x, y = walk.quad[0]
                cv2.putText(img, name, (int(x) + 6, max(int(y) - 8, 14)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                            ZONE_COLORS[zi % len(ZONE_COLORS)], 2)
            for n, d in enumerate(dets):
                x1, y1, x2, y2 = d["box"]
                hit = zone_of.get(n)
                if hit is None:
                    st = sg.CLEAR
                else:
                    zi, span = hit
                    # Colour each vehicle by ITS OWN contribution, not the zone
                    # verdict — otherwise one blocker paints every encroaching
                    # vehicle red and you cannot see which one is the problem.
                    st = walks[zi][1].verdict(
                        walks[zi][1].free_width([span])[0], True)
                cv2.rectangle(img, (x1, y1), (x2, y2), COLOR[st], 2)
                gx, gy = sg.ground_point(d["box"])
                cv2.circle(img, (int(gx), int(gy)), 6, (0, 235, 255), -1)
                cv2.putText(img, f"{d['label']} {d.get('parked', 0):.0f}s",
                            (x1, max(y1 - 8, 12)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR[st], 1)

            for i, (name, walk, free_m, _, state) in enumerate(states):
                line = f"{name}: {free_m:.2f}m free of {walk.width_m:.2f}m - {state}"
                y = h - 16 - (len(states) - 1 - i) * 30
                cv2.rectangle(img, (10, y - 22), (18 + 13 * len(line), y + 8),
                              (24, 24, 28), -1)
                cv2.putText(img, line, (16, y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.62, COLOR[state], 2)
            writer.write(img)

            # ---- events, per zone ----
            for zi, (name, walk, free_m, _, state) in enumerate(states):
                if state != sg.BLOCKED or t - last_event[zi] < EVENT_COOLDOWN_S:
                    continue
                culprit = next((d for n, d in enumerate(dets)
                                if zone_of.get(n, (None,))[0] == zi), None)
                thumb = sess / f"event_{len(events):03d}.jpg"
                cv2.imwrite(str(thumb), img)
                mm, ss = divmod(int(t), 60)
                events.append({
                    "zone": name,
                    "free": free_m,
                    "time_str": f"{mm:01d}:{ss:02d}",
                    "label": culprit["label"] if culprit else "vehicle",
                    "parked": int(culprit.get("parked", 0)) if culprit else 0,
                    "thumb_url": f"/files/{sess.name}/{thumb.name}",
                })
                last_event[zi] = t
            frame_idx += 1
    finally:
        cap.release()
        writer.release()

    duration = round(frame_idx / fps, 1) if fps else 0
    zones_out = []
    for zi, (name, walk) in enumerate(walks):
        free_m, at_m = worst[zi]
        blocked_s = round(blocked_n[zi] * step / fps, 1) if fps else 0
        zones_out.append({
            "name": name,
            "width": walk.width_m,
            "worst_free": free_m,
            "worst_at": at_m,
            "blocked_s": blocked_s,
            "state": walk.verdict(free_m, free_m < walk.width_m - 1e-9),
        })

    hit = [z for z in zones_out if z["state"] == sg.BLOCKED]
    if hit:
        summary = "; ".join(
            f"{z['name']} blocked for {z['blocked_s']}s (narrowest {z['worst_free']:.2f}m "
            f"of {z['width']:.2f}m)" for z in hit
        ) + f". Clip length {duration}s."
    else:
        summary = (f"No footpath fell below the {walks[0][1].min_passable_m:.2f}m "
                   f"minimum over {duration}s.")

    return jsonify({
        "video_url": f"/files/{sess.name}/{out_path.name}",
        "duration": duration,
        "out_fps": round(out_fps, 1),
        "frames_analyzed": analyzed,
        "zones": zones_out,
        "events": events,
        "summary": summary,
    })


@app.route("/files/<session>/<path:filename>")
def files(session, filename):
    # session is a server-generated 10-char hex id; reject anything else so it
    # cannot traverse out of WORK_DIR. filename is sanitized by send_from_directory.
    return send_from_directory(_session_dir(session), filename)


if __name__ == "__main__":
    print("LookOut sidewalk obstruction tester -> http://localhost:5003")
    app.run(host="0.0.0.0", port=5003, debug=False)
