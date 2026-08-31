# LookOut Live Smoking Detection Testing

A web-based interface for real-time testing of the smoking_v5_crops model with optional video recording.

## Quick Start

### Option 1: PowerShell (Recommended)
```powershell
cd C:\Users\User\OneDrive\Desktop\LookOut_v2\detection_sandbox
.\start_live_testing.ps1
```

Then open your browser to: **http://localhost:5003**

### Option 2: Manual Python
```bash
cd C:\Users\User\OneDrive\Desktop\LookOut_v2\detection_sandbox
set SMOKING_MODEL=C:\Users\User\OneDrive\Desktop\CAPSTONE\runs\detect\smoking_v5_crops\weights\best.pt
set KMP_DUPLICATE_LIB_OK=TRUE
python live_testing.py
```

## Features

### Two Detection Modes

**1. Start Live**
- Runs smoking detection in real-time
- Streams annotated video to your browser
- Shows live statistics (frames, detections)
- **NO video recording** — fast, uses minimal storage

**2. Record Live**
- Runs smoking detection in real-time
- **Records the annotated video** to disk
- Saves as MP4 to `detection_sandbox/output/live_testing/`
- Can be stopped and downloaded later
- Useful for post-analysis and reports

### Additional Features

- **Adjustable Confidence Threshold**: Slide to tune detection sensitivity (0.1 - 0.9)
- **Live Statistics**: See frames processed and total detections
- **Recent Detections**: Buffer showing last 10 detection events
- **Video Source Options**:
  - Webcam: Use `0` (default)
  - RTSP Camera: Paste URL (e.g., `rtsp://camera-ip:554/stream`)
  - Video File: Full path to MP4/AVI/MOV file

## Browser Interface

```
┌─────────────────────────────────────┬──────────────────┐
│                                     │                  │
│  Live Video Stream with Boxes       │  Statistics      │
│  (Annotated detections in real-time)│  • Frames: 1234  │
│                                     │  • Detections: 5 │
│                                     │                  │
│                                     │  Settings        │
│  [Start Live] [Record Live] [Stop]  │  • Confidence    │
│                                     │                  │
│  Source: [0 or rtsp://...]          │  Recent Detects  │
└─────────────────────────────────────┴──────────────────┘
```

## Workflow Example

### Testing on Webcam (No Recording)
1. Open http://localhost:5003
2. Leave "Source" as `0`
3. Click **Start Live**
4. Watch the video stream and adjustments
5. Click **Stop** when done

### Recording Test Footage
1. Open http://localhost:5003
2. Enter camera source (e.g., `rtsp://192.168.1.100:554/stream`)
3. Click **Start Live** (to preview first)
4. Once ready, click **Record Live**
5. Model runs and records annotated video
6. Click **Stop** when finished
7. Video saved to `detection_sandbox/output/live_testing/live_smoking_YYYYMMDD_HHMMSS.mp4`

### Tuning Sensitivity
- **Lower confidence** (0.10 - 0.20): Catch more detections (more false positives)
- **Default** (0.30): Balanced detection
- **Higher confidence** (0.50+): Only strong detections (fewer false positives)

Adjust the slider in real-time while running.

## Model Information

- **Model**: smoking_v5_crops
- **Location**: `C:\Users\User\OneDrive\Desktop\CAPSTONE\runs\detect\smoking_v5_crops\weights\best.pt`
- **Classes Detected**: cigarette, vape, smoke, smoking
- **Precision**: 90.9%
- **Recall**: 91.1%
- **mAP50-95**: 0.533

## Output Files

All recordings saved to:
```
C:\Users\User\OneDrive\Desktop\LookOut_v2\detection_sandbox\output\live_testing\
```

Format: `live_smoking_YYYYMMDD_HHMMSS.mp4`

## Troubleshooting

### Model Not Found
```
Error: Model load failed: Model not found at C:\...
```
**Fix**: Update the model path in `start_live_testing.ps1` or set `SMOKING_MODEL` env var

### OpenMP Error
```
OMP: Error #15: Initializing libiomp5md.dll, but found libiomp5md.dll already initialized
```
**Fix**: Script already sets `KMP_DUPLICATE_LIB_OK=TRUE`. If error persists, ensure only one OpenMP library is loaded.

### Camera Won't Connect
- Verify camera index: try `0`, `1`, `2` for different webcams
- For RTSP: check URL format and network connectivity
- Try the camera with `ffmpeg` or `vlc` to confirm it works

### Video Won't Save
- Check disk space in `output/live_testing/`
- Verify write permissions on `detection_sandbox/` folder
- Check Flask console for errors

## Performance Notes

- **CPU Usage**: ~80-100% (4 CPU cores on modern processor)
- **Frame Rate**: 1-5 FPS typical (depends on model size and CPU)
- **Memory**: ~2-3 GB typical
- **Disk I/O** (with recording): +50 MB/min (~1GB per 20 minutes)

## API Endpoints (Advanced)

If you want to integrate with other tools:

```
GET  http://localhost:5003/api/model-status       → Check if model loaded
POST http://localhost:5003/api/start               → Start detection
POST http://localhost:5003/api/start-recording     → Add recording
POST http://localhost:5003/api/stop                → Stop detection
GET  http://localhost:5003/api/status              → Get live stats
POST http://localhost:5003/api/set-confidence      → Update confidence
```

## Key Improvements Over CLI

| Feature | CLI (`watch_all`) | Web UI (`live_testing.py`) |
|---------|-------------------|----------------------------|
| Live preview | ❌ (--debug only) | ✅ Browser stream |
| Recording | ✅ (flags) | ✅ One-click button |
| Confidence tuning | ❌ (restart needed) | ✅ Live slider |
| Statistics | ✅ (at end) | ✅ Live updates |
| User-friendly | ❌ (terminal) | ✅ Web interface |

