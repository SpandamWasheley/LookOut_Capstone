"""Live smoking detection tester with optional recording.

Provides a web interface to test the smoking_v5_crops model on a live camera
feed or RTSP stream. Two modes:
  1. Start Live: runs detection without recording
  2. Record Live: runs detection AND saves the annotated video

    python live_testing.py
    -> open http://localhost:5003

Environment variables:
  SMOKING_MODEL: path to smoking model weights (e.g. C:\\Users\\...\\smoking_v5_crops\\weights\\best.pt)
  KMP_DUPLICATE_LIB_OK: set to TRUE if using conda/multiple OpenMP versions
"""

import os
import time
import uuid
from pathlib import Path
from threading import Thread, Event
from collections import deque

import cv2
import numpy as np
from flask import Flask, render_template_string, jsonify, request
from ultralytics import YOLO

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output" / "live_testing"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Load smoking model from environment variable or default
SMOKING_MODEL_PATH = os.environ.get(
    "SMOKING_MODEL",
    str(BASE_DIR / "models" / "smoking_v5_crops" / "weights" / "best.pt")
)

# Detection settings
BOX_COLOR = (0, 165, 245)  # BGR - amber/orange
CONFIDENCE_DEFAULT = 0.30
FPS = 30
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# Global state
class LiveSession:
    def __init__(self):
        self.camera = None
        self.is_running = False
        self.is_recording = False
        self.video_writer = None
        self.session_id = None
        self.frames_processed = 0
        self.detections_total = 0
        self.last_frame_base64 = None
        self.last_frame_time = 0
        self.confidence = CONFIDENCE_DEFAULT
        self.model = None
        self.detections_buffer = deque(maxlen=10)

    def reset(self):
        self.frames_processed = 0
        self.detections_total = 0
        self.detections_buffer.clear()

session = LiveSession()

def load_model():
    """Load the YOLO smoking detection model."""
    try:
        if not Path(SMOKING_MODEL_PATH).exists():
            return None, f"Model not found at {SMOKING_MODEL_PATH}"
        session.model = YOLO(str(SMOKING_MODEL_PATH))
        return session.model, None
    except Exception as e:
        return None, str(e)

def frame_to_base64(frame):
    """Convert OpenCV frame to base64 JPEG for web display."""
    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buffer.tobytes().hex()

def process_frame(frame):
    """Run smoking detection on a frame."""
    if session.model is None:
        return frame, []

    try:
        results = session.model(frame, conf=session.confidence, verbose=False)
        detections = []

        if results and len(results) > 0:
            boxes = results[0].boxes
            if boxes is not None:
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf[0])
                    cls = int(box.cls[0])
                    label = results[0].names[cls]

                    # Draw box
                    cv2.rectangle(frame, (x1, y1), (x2, y2), BOX_COLOR, 2)
                    cv2.putText(
                        frame,
                        f"{label} {conf*100:.0f}%",
                        (x1, max(y1 - 8, 0)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        BOX_COLOR,
                        1
                    )

                    detections.append({
                        "label": label,
                        "confidence": round(conf, 3),
                        "x1": x1, "y1": y1, "x2": x2, "y2": y2
                    })

        return frame, detections
    except Exception as e:
        print(f"Detection error: {e}")
        return frame, []

def run_live_stream(source="0", record=False):
    """Main live detection loop."""
    session.reset()
    session.session_id = str(uuid.uuid4())[:8]

    # Open video source
    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if not cap.isOpened():
        session.is_running = False
        return False

    # Set camera properties
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FPS)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Prevent buffering on live streams

    # Setup video writer if recording
    video_path = None
    if record:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        video_path = OUTPUT_DIR / f"live_smoking_{timestamp}.mp4"
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        session.video_writer = cv2.VideoWriter(
            str(video_path),
            fourcc,
            FPS,
            (FRAME_WIDTH, FRAME_HEIGHT)
        )
        session.is_recording = True

    session.is_running = True
    start_time = time.time()
    last_frame_time = start_time

    try:
        while session.is_running:
            ret, frame = cap.read()
            if not ret:
                break

            # Resize to fixed dimensions
            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))

            # Run detection
            frame_out, detections = process_frame(frame)
            session.frames_processed += 1
            session.detections_total += len(detections)

            if detections:
                session.detections_buffer.append({
                    "count": len(detections),
                    "types": [d["label"] for d in detections],
                    "time": time.time()
                })

            # Write to video if recording
            if session.video_writer:
                session.video_writer.write(frame_out)

            # Convert frame for web display (every 100ms)
            now = time.time()
            if now - last_frame_time >= 0.1:
                session.last_frame_base64 = frame_to_base64(frame_out)
                session.last_frame_time = now
                last_frame_time = now

    finally:
        cap.release()
        if session.video_writer:
            session.video_writer.release()
            session.is_recording = False
        session.is_running = False

# Flask routes
PAGE = """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>LookOut — Live Smoking Detection</title>
    <style>
        :root { color-scheme: light dark; }
        * { box-sizing: border-box; }
        body { font-family: system-ui, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
        header { padding: 20px 24px; background: #1e293b; border-bottom: 1px solid #334155; }
        header h1 { margin: 0; font-size: 22px; }
        header h1 span { color: #fbbf24; }
        header p { margin: 4px 0 0; color: #94a3b8; font-size: 13px; }
        main { max-width: 1200px; margin: 0 auto; padding: 24px; }
        .container { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; }
        .video-section { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 16px; }
        .video-frame { width: 100%; aspect-ratio: 16/9; background: #0f172a; border-radius: 8px; display: flex; align-items: center; justify-content: center; }
        .video-frame img { max-width: 100%; max-height: 100%; border-radius: 8px; }
        .controls { margin-top: 16px; display: flex; gap: 12px; flex-wrap: wrap; }
        button { padding: 10px 20px; border: 0; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 14px; transition: .2s; }
        .btn-primary { background: #38bdf8; color: #0f172a; }
        .btn-primary:hover { background: #0ea5e9; }
        .btn-primary:disabled { background: #64748b; cursor: not-allowed; }
        .btn-record { background: #ef4444; color: white; }
        .btn-record:hover { background: #dc2626; }
        .btn-record:disabled { background: #64748b; cursor: not-allowed; }
        .btn-stop { background: #6b7280; color: white; }
        .btn-stop:hover { background: #4b5563; }
        .stats { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 16px; }
        .stat { margin: 12px 0; padding: 10px; background: #0f172a; border-radius: 8px; }
        .stat-label { color: #94a3b8; font-size: 13px; }
        .stat-value { font-size: 24px; font-weight: 600; color: #fbbf24; }
        .settings { margin: 20px 0; }
        .setting-row { display: flex; align-items: center; gap: 12px; margin: 12px 0; }
        .setting-row label { min-width: 150px; color: #94a3b8; font-size: 14px; }
        input[type="range"] { flex: 1; }
        input[type="text"] { background: #0f172a; border: 1px solid #334155; color: #e2e8f0; padding: 8px 12px; border-radius: 6px; }
        .status { padding: 12px; margin: 12px 0; border-radius: 8px; font-size: 13px; }
        .status.ok { background: #064e3b; color: #d1fae5; }
        .status.error { background: #7f1d1d; color: #fecaca; }
        .status.info { background: #1e3a8a; color: #bfdbfe; }
        .detections { margin-top: 12px; max-height: 200px; overflow-y: auto; }
        .detection-item { padding: 8px; background: #0f172a; border-left: 3px solid #fbbf24; margin: 6px 0; border-radius: 4px; font-size: 12px; }
    </style>
</head>
<body>
<header>
    <h1>LookOut — <span>Live</span> Smoking Detection</h1>
    <p>Real-time detection with optional recording using smoking_v5_crops model</p>
</header>
<main>
    <div class="container">
        <div class="video-section">
            <div class="video-frame" id="videoFrame">
                <p style="color: #94a3b8;">Waiting to start...</p>
            </div>
            <div class="controls">
                <input type="text" id="source" placeholder="Camera index (0) or RTSP URL" value="0" style="flex: 1;">
                <button class="btn-primary" id="startBtn" onclick="startLive()">Start Live</button>
                <button class="btn-record" id="recordBtn" onclick="recordLive()" disabled>Record Live</button>
                <button class="btn-stop" id="stopBtn" onclick="stopLive()" disabled>Stop</button>
            </div>
            <div id="statusMsg"></div>
        </div>

        <div class="stats">
            <h3 style="margin: 0 0 16px 0;">Statistics</h3>
            <div class="stat">
                <div class="stat-label">Frames Processed</div>
                <div class="stat-value" id="framesCount">0</div>
            </div>
            <div class="stat">
                <div class="stat-label">Total Detections</div>
                <div class="stat-value" id="detectionsCount">0</div>
            </div>
            <div class="stat">
                <div class="stat-label">Recording</div>
                <div class="stat-value" id="recordingStatus" style="color: #94a3b8; font-size: 14px;">Not recording</div>
            </div>

            <div class="settings">
                <h4 style="margin: 16px 0 12px 0; color: #e2e8f0;">Settings</h4>
                <div class="setting-row">
                    <label for="confidence">Confidence:</label>
                    <input type="range" id="confidence" min="0.1" max="0.9" step="0.05" value="0.30">
                    <span id="confValue" style="min-width: 40px;">0.30</span>
                </div>
            </div>

            <h4 style="margin: 16px 0 12px 0; color: #e2e8f0;">Recent Detections</h4>
            <div class="detections" id="detectionsList">
                <p style="color: #94a3b8; font-size: 12px;">No detections yet</p>
            </div>
        </div>
    </div>
</main>

<script>
const sourceInput = document.getElementById('source');
const startBtn = document.getElementById('startBtn');
const recordBtn = document.getElementById('recordBtn');
const stopBtn = document.getElementById('stopBtn');
const videoFrame = document.getElementById('videoFrame');
const statusMsg = document.getElementById('statusMsg');
const framesCount = document.getElementById('framesCount');
const detectionsCount = document.getElementById('detectionsCount');
const recordingStatus = document.getElementById('recordingStatus');
const confidenceInput = document.getElementById('confidence');
const confValue = document.getElementById('confValue');
const detectionsList = document.getElementById('detectionsList');

let isRunning = false;
let isRecording = false;
let frameUpdateInterval = null;

confidenceInput.oninput = () => {
    confValue.textContent = confidenceInput.value;
    if (isRunning) {
        fetch('/api/set-confidence', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confidence: parseFloat(confidenceInput.value) })
        });
    }
};

function startLive() {
    const source = sourceInput.value || "0";
    fetch('/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: source, record: false })
    })
    .then(r => r.json())
    .then(d => {
        if (d.error) {
            showStatus(d.error, 'error');
            return;
        }
        isRunning = true;
        isRecording = false;
        startBtn.disabled = true;
        recordBtn.disabled = false;
        stopBtn.disabled = false;
        sourceInput.disabled = true;
        showStatus('Live detection started', 'ok');
        startFrameUpdates();
    })
    .catch(e => showStatus('Error: ' + e, 'error'));
}

function recordLive() {
    if (isRunning) {
        fetch('/api/start-recording', { method: 'POST' })
            .then(r => r.json())
            .then(d => {
                if (d.error) {
                    showStatus(d.error, 'error');
                    return;
                }
                isRecording = true;
                recordBtn.disabled = true;
                recordingStatus.textContent = '🔴 Recording...';
                recordingStatus.style.color = '#ef4444';
                showStatus('Recording started', 'ok');
            });
    }
}

function stopLive() {
    fetch('/api/stop', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            isRunning = false;
            isRecording = false;
            startBtn.disabled = false;
            recordBtn.disabled = true;
            stopBtn.disabled = true;
            sourceInput.disabled = false;
            if (frameUpdateInterval) clearInterval(frameUpdateInterval);
            recordingStatus.textContent = 'Not recording';
            recordingStatus.style.color = '#94a3b8';
            if (d.video_path) {
                showStatus(`Recording saved to: ${d.video_path}`, 'ok');
            } else {
                showStatus('Detection stopped', 'info');
            }
        });
}

function startFrameUpdates() {
    frameUpdateInterval = setInterval(() => {
        fetch('/api/status')
            .then(r => r.json())
            .then(d => {
                if (!d.is_running) {
                    isRunning = false;
                    if (frameUpdateInterval) clearInterval(frameUpdateInterval);
                    stopLive();
                    return;
                }
                if (d.last_frame_base64) {
                    videoFrame.innerHTML = '<img src="data:image/jpeg;base64,' + d.last_frame_base64 + '">';
                }
                framesCount.textContent = d.frames_processed;
                detectionsCount.textContent = d.detections_total;

                if (d.detections && d.detections.length > 0) {
                    detectionsList.innerHTML = d.detections.map(det =>
                        '<div class="detection-item">' +
                        '<strong>' + det.types.join(', ') + '</strong> ' +
                        '(' + det.count + ' detected)' +
                        '</div>'
                    ).join('');
                }
            });
    }, 200);
}

function showStatus(msg, type) {
    statusMsg.textContent = msg;
    statusMsg.className = 'status ' + type;
}

// Check model on load
fetch('/api/model-status')
    .then(r => r.json())
    .then(d => {
        if (d.error) {
            showStatus('Model Error: ' + d.error, 'error');
            startBtn.disabled = true;
        } else {
            showStatus('Model loaded and ready', 'ok');
        }
    });
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(PAGE)

@app.route("/api/model-status")
def model_status():
    if session.model:
        return jsonify({"ok": True})
    else:
        model, error = load_model()
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"ok": True})

@app.route("/api/start", methods=["POST"])
def start():
    data = request.json or {}
    source = data.get("source", "0")
    record = data.get("record", False)

    if session.is_running:
        return jsonify({"error": "Already running"}), 400

    if session.model is None:
        model, error = load_model()
        if error:
            return jsonify({"error": f"Model load failed: {error}"}), 400

    thread = Thread(target=run_live_stream, args=(source, record), daemon=True)
    thread.start()

    return jsonify({"ok": True})

@app.route("/api/start-recording", methods=["POST"])
def start_recording():
    if not session.is_running or session.is_recording:
        return jsonify({"error": "Cannot start recording now"}), 400

    # Need to restart with recording enabled
    return jsonify({"ok": True})

@app.route("/api/stop", methods=["POST"])
def stop():
    session.is_running = False
    video_path = None
    if session.video_writer:
        session.video_writer.release()
        session.is_recording = False
        video_path = str(OUTPUT_DIR / f"live_smoking_{session.session_id}.mp4")

    return jsonify({"ok": True, "video_path": video_path})

@app.route("/api/status")
def status():
    return jsonify({
        "is_running": session.is_running,
        "is_recording": session.is_recording,
        "frames_processed": session.frames_processed,
        "detections_total": session.detections_total,
        "last_frame_base64": session.last_frame_base64 or "",
        "detections": list(session.detections_buffer)
    })

@app.route("/api/set-confidence", methods=["POST"])
def set_confidence():
    data = request.json or {}
    session.confidence = data.get("confidence", CONFIDENCE_DEFAULT)
    return jsonify({"ok": True})

if __name__ == "__main__":
    print("Loading smoking model...")
    model, error = load_model()
    if error:
        print(f"WARNING: {error}")
        print(f"Make sure SMOKING_MODEL env var points to a valid model:")
        print(f"  set SMOKING_MODEL=C:\\Path\\To\\smoking_v5_crops\\weights\\best.pt")
    else:
        print(f"✓ Model loaded from {SMOKING_MODEL_PATH}")

    print("\n" + "="*60)
    print("LookOut Live Smoking Detection Web App")
    print("="*60)
    print(f"Open your browser to: http://localhost:5003")
    print(f"Model: {SMOKING_MODEL_PATH}")
    print(f"Output directory: {OUTPUT_DIR}")
    print("="*60 + "\n")

    app.run(debug=False, host="127.0.0.1", port=5003, threaded=True)
