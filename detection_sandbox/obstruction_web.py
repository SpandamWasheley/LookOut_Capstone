"""Browser tester for the obstruction rule, with hand-placed edge lines.

    python obstruction_web.py
    -> open http://localhost:5004

Upload a clip, click the two ends of the LEFT edge line, then the two ends of
the RIGHT one, and analyse. A street has a boundary on each side, and a vehicle
can block either, so each line is judged independently and reported separately.

AUTOMATIC LINE DETECTION IS NOT USED HERE, deliberately. An edge line is defined
by what it does - marking where the carriageway ends - not by how it looks, so a
stripe on a road and one on a basketball court are identical at pixel level.
Measured on this project's own night footage, the real line scored a brightness
contrast of 46.5 while the kerb beside it scored 35: too close to separate once
weather and exposure move each by more than that gap. A detector tuned loose
enough to catch the paint also invents lines on unmarked roads, and that failure
is silent - every vehicle would be measured against a roofline and reported as a
violation. Two clicks per camera, done once at installation, is exact and costs
seconds. The finder still exists in edge_line.py for anyone wanting to revisit
it; it is simply not in this tool's path.

For each vehicle the tool measures the share of its footprint past each line and
for how long, and reports:

    PASSING      over a line but moving, or only briefly    -> not a violation
    WATCHING     over a line and stationary, still counting -> not yet
    OBSTRUCTION  held past the threshold                    -> violation

Analysis is strided: only every Nth frame goes through YOLO, and the clock comes
from the video's own timestamps, so a 9-minute clip is judged at its real pace
without pushing every frame through the detector.
"""

import base64
import re
import uuid
from pathlib import Path

import cv2
from flask import (Flask, abort, jsonify, render_template_string, request,
                   send_from_directory)

import edge_line as el
import obstruction as ob
import vehicle_detection as vd

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "output" / "obstruction_web"
WORK_DIR.mkdir(parents=True, exist_ok=True)

PROC_W = 960                 # everything below is in this canonical width
MAX_ANALYSIS_FRAMES = 900

# BGR, matched to the browser's stroke colours so the annotated clip reads the
# same way the drawing screen did.
SIDE_COLOURS = {"left": (0, 165, 255), "right": (255, 190, 0)}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024


def _session_dir(session):
    if not re.fullmatch(r"[0-9a-f]{10}", session or ""):
        abort(404)
    return WORK_DIR / session


def _resize(frame):
    h, w = frame.shape[:2]
    if w == PROC_W:
        return frame
    return cv2.resize(frame, (PROC_W, int(h * PROC_W / w)))


def _open_writer(base, size, fps=8.0):
    """Opens the annotated-clip writer, preferring a codec browsers can play.

    CODEC NOTE, checked on this machine rather than assumed. H264/avc1 report
    themselves as opened but write a broken 1.4KB file, because the OpenH264
    DLL is missing. mp4v encodes correctly but Chrome and Edge refuse to decode
    it, so the preview player stays blank. WebM/VP8 both encodes and plays, so
    it is tried first; mp4v remains as a download-only fallback.
    """
    for tag, ext in (("VP80", ".webm"), ("mp4v", ".mp4")):
        path = base.with_suffix(ext)
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*tag), fps, size)
        if writer.isOpened():
            return writer, path
        writer.release()
    return None, None


def _jpeg(frame):
    ok, buf = cv2.imencode(".jpg", frame)
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")


_build_line = ob.build_edge   # shared with watch_parking


PAGE = """
<!doctype html><meta charset="utf-8"><title>LookOut - obstruction tester</title>
<style>
 body{font:15px system-ui,sans-serif;margin:0;background:#11151c;color:#e6edf3}
 header{padding:14px 20px;background:#161b22;border-bottom:1px solid #30363d}
 h1{margin:0;font-size:17px} .sub{color:#8b949e;font-size:13px;margin-top:3px}
 main{padding:18px 20px;max-width:1180px}
 .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-bottom:14px}
 canvas{max-width:100%;border-radius:6px;cursor:crosshair;display:block}
 button{background:#238636;color:#fff;border:0;padding:9px 16px;border-radius:6px;font-size:14px;cursor:pointer}
 button:disabled{background:#30363d;color:#8b949e;cursor:default}
 button.alt{background:#30363d}
 label{color:#8b949e;margin-right:14px}
 input[type=number]{width:70px;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:5px;padding:5px}
 .note{padding:9px 12px;border-radius:6px;margin:10px 0;font-size:14px;background:#1c2331;border:1px solid #30363d}
 table{border-collapse:collapse;width:100%;font-size:14px} td,th{border-bottom:1px solid #30363d;padding:7px;text-align:left}
 .v-obstruction{color:#ff7b72;font-weight:600} .v-watching{color:#e3b341} .v-passing{color:#8b949e}
 .pill{display:inline-block;padding:2px 9px;border-radius:11px;font-size:12px;margin-right:8px}
 .p-left{background:#42280a;color:#ffa657} .p-right{background:#0a2f3a;color:#56d4ff}
 video{max-width:100%;border-radius:6px}
</style>
<header><h1>Road-edge obstruction tester</h1>
<div class="sub">Mark the left and right edge lines, then measure how much of each vehicle sits past them, and for how long.</div></header>
<main>
<div class="card">
  <input type="file" id="file" accept="video/*">
  <button id="up">Upload clip</button>
  <span id="status" style="margin-left:10px;color:#8b949e"></span>
</div>

<div class="card" id="step2" style="display:none">
  <div class="note" id="note"></div>
  <canvas id="cv"></canvas>
  <div style="margin-top:12px">
    <button class="alt" id="next">Next edge</button>
    <button class="alt" id="undo">Undo point</button>
    <button class="alt" id="reset">Clear both</button>
    <button class="alt" id="flipL">Flip left side</button>
    <button class="alt" id="flipR">Flip right side</button>
    <button class="alt" id="save">Save edges for watch_parking</button>
    <label style="margin-left:18px">Past the line <input type="number" id="pct" value="50" min="10" max="90">%</label>
    <label>for <input type="number" id="mins" value="5" min="0.1" step="0.1"> minutes</label>
    <button id="run">Analyse clip</button>
  </div>
  <div class="sub" style="margin-top:8px">Click along each edge - two points for a straight edge, more where the
  footpath bends. The shaded band is the protected footpath; vehicles are judged by the share of their footprint
  sitting on it. Either edge alone is fine if the street only has one.</div>
</div>

<div class="card" id="step3" style="display:none">
  <div id="summary"></div>
  <div style="margin:10px 0"><a id="dl" download style="display:none;background:#238636;color:#fff;
     padding:9px 16px;border-radius:6px;text-decoration:none;font-size:14px">Download annotated clip</a>
     <span id="where" class="sub" style="margin-left:12px"></span></div>
  <table id="events"></table>
  <video id="out" controls style="margin-top:12px"></video>
</div>
</main>
<script>
let session=null, img=new Image(), W=0, H=0;
let paths={left:[], right:[]}, sides={left:1,right:1}, active='left';
const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
const $=id=>document.getElementById(id);
const COL={left:'#ffa657', right:'#56d4ff'};
const BAND=46;   // how far the shaded band extends, in canvas px

// Default the protected side to the one facing the NEARER frame edge: the road
// runs up the middle and footpaths sit on the outside.
function defaultSide(p){
  const a=p[0], b=p[p.length-1];
  const mx=(a.x+b.x)/2, dx=b.x-a.x, dy=b.y-a.y, L=Math.hypot(dx,dy)||1;
  const nx=-dy/L;
  return (mx < W/2) ? (nx<0?1:-1) : (nx>0?1:-1);
}
// Offset a path along its local normal - used to shade the protected side of a
// path that may bend, without any polygon clipping.
function offsetPath(p, side, dist){
  return p.map((q,i)=>{
    const a=p[Math.max(i-1,0)], b=p[Math.min(i+1,p.length-1)];
    const dx=b.x-a.x, dy=b.y-a.y, L=Math.hypot(dx,dy)||1;
    return {x:q.x - dy/L*dist*side, y:q.y + dx/L*dist*side};
  });
}
function drawPath(p, colour, side, done){
  if(p.length>=2){
    const off=offsetPath(p, side, BAND);
    ctx.fillStyle=colour+'33'; ctx.beginPath();
    ctx.moveTo(p[0].x,p[0].y);
    p.forEach(q=>ctx.lineTo(q.x,q.y));
    for(let i=off.length-1;i>=0;i--) ctx.lineTo(off[i].x,off[i].y);
    ctx.closePath(); ctx.fill();
    ctx.strokeStyle=colour; ctx.lineWidth=3; ctx.beginPath();
    ctx.moveTo(p[0].x,p[0].y); p.forEach(q=>ctx.lineTo(q.x,q.y)); ctx.stroke();
  }
  ctx.fillStyle=colour;
  p.forEach(q=>{ctx.beginPath();ctx.arc(q.x,q.y,5,0,7);ctx.fill();});
}
function draw(){
  ctx.drawImage(img,0,0,cv.width,cv.height);
  for(const k of ['left','right']) drawPath(paths[k], COL[k], sides[k]);
  const n=paths[active].length;
  const other = active==='left'?'right':'left';
  $('note').innerHTML =
    'Drawing the <b style="color:'+COL[active]+'">'+active.toUpperCase()+'</b> edge - '+
    n+' point'+(n===1?'':'s')+'. Click along the edge; add extra points where it bends. '+
    (n>=2 ? 'Then <b>Next edge</b>, or analyse now.' : 'At least 2 points needed.');
  $('run').disabled = paths.left.length<2 && paths.right.length<2;
  $('next').textContent = 'Next edge ('+other+')';
}
cv.onclick=e=>{
  const r=cv.getBoundingClientRect();
  paths[active].push({x:(e.clientX-r.left)*cv.width/r.width,
                      y:(e.clientY-r.top)*cv.height/r.height});
  if(paths[active].length>=2) sides[active]=defaultSide(paths[active]);
  draw();
};
$('next').onclick=()=>{active = active==='left'?'right':'left'; draw();};
$('undo').onclick=()=>{paths[active].pop();
  if(paths[active].length>=2) sides[active]=defaultSide(paths[active]); draw();};
$('reset').onclick=()=>{paths={left:[],right:[]};active='left';draw();};
$('flipL').onclick=()=>{sides.left=-sides.left;draw();};
$('flipR').onclick=()=>{sides.right=-sides.right;draw();};
// Export exactly the JSON `manage.py watch_parking --edges` expects, so an edge
// drawn once here is the same edge the live detector uses.
$('save').onclick=()=>{
  const spec={};
  for(const k of ['left','right']) if(paths[k].length>=2)
    spec[k]={points:paths[k].map(q=>[Math.round(q.x),Math.round(q.y)]), side:sides[k]};
  if(!Object.keys(spec).length){$('note').textContent='Draw an edge first.';return;}
  const blob=new Blob([JSON.stringify(spec,null,2)],{type:'application/json'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob); a.download='edges.json'; a.click();
};

$('up').onclick=async()=>{
  const f=$('file').files[0]; if(!f){$('status').textContent='Choose a video first.';return;}
  $('status').textContent='Uploading...'; $('up').disabled=true;
  const fd=new FormData(); fd.append('file',f);
  const r=await (await fetch('/frame',{method:'POST',body:fd})).json();
  $('up').disabled=false;
  if(r.error){$('status').textContent=r.error;return;}
  session=r.session; W=r.w; H=r.h;
  paths={left:[],right:[]}; active='left'; $('status').textContent='';
  img.onload=()=>{cv.width=W;cv.height=H;draw();}; img.src=r.image;
  $('step2').style.display='block';
};

$('run').onclick=async()=>{
  $('run').disabled=true; $('run').textContent='Analysing...';
  const spec={};
  for(const k of ['left','right']) if(paths[k].length>=2)
    spec[k]={points:paths[k].map(q=>[q.x,q.y]), side:sides[k]};
  const r=await (await fetch('/analyse',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({session,lines:spec,pct:+$('pct').value,minutes:+$('mins').value})})).json();
  $('run').disabled=false; $('run').textContent='Analyse clip';
  if(r.error){$('note').textContent=r.error;return;}
  $('step3').style.display='block';
  $('summary').innerHTML='<b>'+r.summary+'</b>';
  let h='<tr><th>time</th><th>edge</th><th>vehicle</th><th>past line</th><th>held</th>'+
        '<th>pedestrians forced onto road</th><th>verdict</th></tr>';
  r.events.forEach(e=>{h+=`<tr><td>${e.t}</td><td><span class="pill p-${e.side}">${e.side}</span></td>
    <td>${e.label} ${e.id}</td><td>${e.pct}%</td><td>${e.held}</td>
    <td>${e.detours?('<b>'+e.detours+'</b>'):'-'}</td>
    <td class="v-${e.verdict}">${e.verdict.toUpperCase()}</td></tr>`;});
  $('events').innerHTML=h;
  if(r.video){
    $('out').src=r.video;
    const a=$('dl'); a.href=r.video; a.setAttribute('download', r.filename||'annotated.webm');
    a.style.display='inline-block'; $('where').textContent='saved to '+(r.path||'');
  }
};
</script>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/frame", methods=["POST"])
def frame():
    """Saves the upload and returns its first frame for the line drawing.

    No detection runs here, so the clip comes back immediately however long it
    is - the previous version spent a YOLO warm-up pass before showing anything.
    """
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
    first = _resize(first)
    return jsonify({"session": session, "image": _jpeg(first),
                    "w": first.shape[1], "h": first.shape[0]})


@app.route("/analyse", methods=["POST"])
def analyse():
    body = request.get_json(silent=True) or {}
    sess = _session_dir(body.get("session"))
    srcs = list(sess.glob("input.*"))
    if not srcs:
        return jsonify({"error": "Session expired - upload the clip again."})

    specs = {k: v for k, v in (body.get("lines") or {}).items()
             if len(v.get("points") or []) >= 2}
    if not specs:
        return jsonify({"error": "No lines were drawn."})

    enter = max(min(float(body.get("pct", 50)), 90), 10) / 100.0
    ob.ENTER_FRACTION = enter
    ob.EXIT_FRACTION = max(enter - 0.10, 0.05)
    seconds = max(float(body.get("minutes", 5)), 0.1) * 60

    # One monitor per line. Both see the same box list in the same order, so
    # results[i] refers to vehicles[i] in each and the two verdicts for one
    # vehicle can be reported side by side.
    monitors = {}
    for name, spec in specs.items():
        line = _build_line(spec)
        monitors[name] = (line, ob.ObstructionMonitor(line,
                                                      obstruction_seconds=seconds))

    cap = cv2.VideoCapture(str(srcs[0]))
    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    stride = max(total // MAX_ANALYSIS_FRAMES, 1)

    writer, out_path, i = None, None, 0
    # Confirmed violations, appended once each: the monitor raises fresh_alert
    # on the single frame an obstruction is first confirmed AND not suppressed
    # by the spatial cooldown, so one parked vehicle yields one row even if its
    # track was lost and reacquired under a new id partway through.
    alerts = []
    worst = {}          # (side, vehicle id) -> (rank, held, verdict, frac, t)
    rank_of = {ob.CLEAR: 0, ob.PASSING: 1, ob.WATCHING: 2, ob.OBSTRUCTION: 3}

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % stride:
            i += 1
            continue
        now = i / fps                     # clock from the video, not the wall
        small = _resize(frame)
        if writer is None:
            writer, out_path = _open_writer(sess / "annotated",
                                            (small.shape[1], small.shape[0]))
        # Brighten dark frames before detection. A vehicle missed at night
        # breaks the dwell timer far more often than a mis-measured one does,
        # and daytime frames pass through untouched.
        small = vd.enhance(small)
        found = vd.detect_vehicles(small, masks=True)
        boxes = [v["box"] for v in found]
        masks = [v["mask"] for v in found]
        labels = [v["label"] for v in found]
        people = vd.detect_persons(small)

        per_box = {}
        for name, (line, monitor) in monitors.items():
            line.draw(small, SIDE_COLOURS.get(name, (0, 165, 255)), 2)
            for idx, (state, verdict) in enumerate(
                    monitor.update(boxes, now, masks=masks, labels=labels,
                                   frame_shape=small.shape, pedestrians=people)):
                key = (name, state.id)
                r = rank_of[verdict]
                if state.fresh_alert:
                    alerts.append({"t": f"{int(now)//60:d}:{int(now)%60:02d}",
                                   "side": name, "id": f"#{state.id}",
                                   "label": labels[idx] if idx < len(labels) else "vehicle",
                                   "pct": f"{state.fraction*100:.0f}",
                                   "held": f"{state.held:.0f}s",
                                   "detours": state.detours,
                                   "verdict": ob.OBSTRUCTION})
                prev = worst.get(key)
                if prev is None or r > prev[0] or (r == prev[0] and state.held > prev[1]):
                    worst[key] = (r, state.held, verdict, state.fraction, now)
                # Keep the more serious of the two sides for the on-screen label.
                if idx not in per_box or r > per_box[idx][0]:
                    per_box[idx] = (r, name, state, verdict)

        for idx, (r, name, state, verdict) in per_box.items():
            colour = {ob.OBSTRUCTION: (0, 0, 220),
                      ob.WATCHING: (0, 190, 230)}.get(verdict, (150, 150, 150))
            x1, y1, x2, y2 = state.box
            cv2.rectangle(small, (x1, y1), (x2, y2), colour, 2)
            if r > 0:
                cv2.putText(small, f"{name} {state.fraction*100:.0f}% {state.held:.0f}s",
                            (x1, max(y1 - 7, 12)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, colour, 2)
        for (px1, py1, px2, py2) in people:
            cv2.rectangle(small, (px1, py1), (px2, py2), (90, 200, 90), 1)
        cv2.putText(small, f"{now:6.1f}s", (10, 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (255, 255, 255), 2)
        if writer is not None:
            writer.write(small)
        i += 1

    cap.release()
    if writer is not None:
        writer.release()

    # Confirmed violations first, then the near-misses that never reached one.
    events = list(alerts)
    for (name, vid), (r, held, verdict, frac, when) in sorted(
            worst.items(), key=lambda kv: (-kv[1][0], kv[0])):
        if r == 0 or verdict == ob.OBSTRUCTION:
            continue        # obstructions are already in `alerts`, deduped
        events.append({"t": f"{int(when)//60:d}:{int(when)%60:02d}", "side": name,
                       "id": f"#{vid}", "label": "vehicle",
                       "pct": f"{frac*100:.0f}", "held": f"{held:.0f}s",
                       "detours": 0, "verdict": verdict})
    n_obs = len(alerts)
    summary = (f"{i} frames analysed across {len(monitors)} line(s), "
               f"{n_obs} obstruction(s) at {enter*100:.0f}% held for "
               f"{seconds/60:.1f} min.")
    video = f"/files/{body['session']}/{out_path.name}" if out_path else None
    return jsonify({"summary": summary, "events": events, "video": video,
                    "filename": out_path.name if out_path else None,
                    "path": str(out_path) if out_path else None})


@app.route("/files/<session>/<path:filename>")
def files(session, filename):
    return send_from_directory(_session_dir(session), filename)


if __name__ == "__main__":
    print("LookOut obstruction tester -> http://localhost:5004")
    app.run(host="0.0.0.0", port=5004, debug=False)
