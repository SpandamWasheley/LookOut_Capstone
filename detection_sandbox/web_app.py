"""Vehicle detection web app — upload a picture in the browser, see what it is.

A tiny local Flask server. Pick (or drag-and-drop) an image, hit Detect, and it
shows the picture with a labelled box on every vehicle it finds (car /
motorcycle / bus / truck / bicycle) plus a summary of counts and confidence.

    python web_app.py
    -> open http://localhost:5000 in your browser

Shares vehicle_detection.py with the other sandbox tools, so detections match.
"""

import base64
from collections import Counter

import cv2
import numpy as np
from flask import Flask, jsonify, render_template_string, request

import vehicle_detection as vd

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # 30 MB per upload

PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LookOut - Vehicle Detection</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
  header { padding: 20px 24px; background: #1e293b; border-bottom: 1px solid #334155; }
  header h1 { margin: 0; font-size: 20px; }
  header p { margin: 4px 0 0; color: #94a3b8; font-size: 13px; }
  main { max-width: 900px; margin: 0 auto; padding: 24px; }
  #drop { border: 2px dashed #475569; border-radius: 12px; padding: 40px 20px;
          text-align: center; cursor: pointer; transition: .15s; background: #1e293b; }
  #drop.hover { border-color: #38bdf8; background: #172033; }
  #drop strong { color: #38bdf8; }
  .row { display: flex; gap: 16px; align-items: center; margin-top: 16px; flex-wrap: wrap; }
  label.conf { font-size: 13px; color: #94a3b8; }
  input[type=range] { vertical-align: middle; }
  button { background: #38bdf8; color: #0f172a; border: 0; padding: 10px 18px;
           border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 14px; }
  button:disabled { opacity: .5; cursor: default; }
  #result { margin-top: 24px; }
  #result img { max-width: 100%; border-radius: 12px; border: 1px solid #334155; }
  .summary { margin: 16px 0; font-size: 18px; }
  .chips { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
  .chip { background: #1e293b; border: 1px solid #334155; border-radius: 999px;
          padding: 6px 14px; font-size: 14px; }
  .chip b { color: #38bdf8; }
  .muted { color: #94a3b8; }
  .spinner { display: none; margin-top: 20px; color: #94a3b8; }
</style>
</head>
<body>
<header>
  <h1>LookOut — Vehicle Detection</h1>
  <p>Upload a street photo. It identifies each vehicle: car, motorcycle, bus, truck, or bicycle.</p>
</header>
<main>
  <div id="drop">
    <p><strong>Click to choose an image</strong> or drag &amp; drop it here</p>
    <p class="muted">JPG / PNG — a photo with vehicles works best</p>
    <input id="file" type="file" accept="image/*" hidden>
  </div>

  <div class="row">
    <label class="conf">Sensitivity (confidence): <span id="confVal">0.35</span></label>
    <input id="conf" type="range" min="0.1" max="0.8" step="0.05" value="0.35">
    <span class="muted">lower = catch more (incl. faint/blurry)</span>
  </div>

  <div class="spinner" id="spinner">Detecting… (first run loads the model, give it a moment)</div>
  <div id="result"></div>
</main>

<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const conf = document.getElementById('conf');
const confVal = document.getElementById('confVal');
const spinner = document.getElementById('spinner');
const result = document.getElementById('result');

conf.oninput = () => confVal.textContent = conf.value;
drop.onclick = () => fileInput.click();
fileInput.onchange = () => { if (fileInput.files[0]) detect(fileInput.files[0]); };

['dragover', 'dragenter'].forEach(e =>
  drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add('hover'); }));
['dragleave', 'drop'].forEach(e =>
  drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove('hover'); }));
drop.addEventListener('drop', ev => {
  const f = ev.dataTransfer.files[0];
  if (f) detect(f);
});

async function detect(file) {
  result.innerHTML = '';
  spinner.style.display = 'block';
  const fd = new FormData();
  fd.append('image', file);
  fd.append('conf', conf.value);
  try {
    const res = await fetch('/detect', { method: 'POST', body: fd });
    const data = await res.json();
    spinner.style.display = 'none';
    if (data.error) { result.innerHTML = '<p class="muted">' + data.error + '</p>'; return; }
    render(data);
  } catch (err) {
    spinner.style.display = 'none';
    result.innerHTML = '<p class="muted">Error: ' + err + '</p>';
  }
}

function render(data) {
  let html = '';
  if (data.count === 0) {
    html += '<div class="summary">No vehicles detected. Try lowering the sensitivity.</div>';
  } else {
    html += '<div class="summary"><b>' + data.count + '</b> vehicle' +
            (data.count > 1 ? 's' : '') + ' detected</div>';
    html += '<div class="chips">';
    for (const [label, n] of Object.entries(data.counts))
      html += '<span class="chip"><b>' + n + '</b> ' + label + (n > 1 ? 's' : '') + '</span>';
    html += '</div>';
    html += '<ul class="muted">';
    for (const d of data.detections)
      html += '<li>' + d.label + ' — ' + Math.round(d.conf * 100) + '% confidence</li>';
    html += '</ul>';
  }
  html += '<div id="result-img"><img src="' + data.image + '"></div>';
  result.innerHTML = html;
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/detect", methods=["POST"])
def detect():
    upload = request.files.get("image")
    if upload is None:
        return jsonify({"error": "No image uploaded."})

    try:
        conf = float(request.form.get("conf", 0.35))
    except ValueError:
        conf = 0.35

    data = np.frombuffer(upload.read(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"error": "Could not read that image file."})

    vehicles = vd.detect_vehicles(img, conf=conf)
    vd.annotate(img, vehicles)

    ok, buf = cv2.imencode(".jpg", img)
    b64 = base64.b64encode(buf).decode("ascii") if ok else ""

    counts = Counter(v["label"] for v in vehicles)
    return jsonify({
        "count": len(vehicles),
        "counts": dict(counts),
        "detections": [{"label": v["label"], "conf": v["conf"]} for v in vehicles],
        "image": "data:image/jpeg;base64," + b64,
    })


if __name__ == "__main__":
    print("Vehicle detection web app running -> http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
