# LookOut — Camera & Runtime Setup (Detection Range)

Manual, one-time settings that affect detection range. These are **human tasks** —
apply them in the camera's web UI and the host environment; the software reads
the results but cannot set them for you.

## 1. Camera web UI — http://192.168.1.64 (admin)

Hikvision DS-2CD1047G2-LUF, 2.8 mm, 2560×1440 @ 20 FPS main stream.

| Setting | Value | Why |
|---|---|---|
| **Stream used by LookOut** | **Main / `/Streaming/Channels/101`** (2560×1440) | The sub-stream (`/102`, 640×360) throws away the resolution distant objects need. Use `/101` in `--source`. |
| Bitrate | **8 Mbps, VBR** | Enough detail without saturating the 100 Mbps USB-Ethernet link. |
| Video encoding | **H.265** | Half the bitrate of H.264 for the same quality → sharper frames at 8 Mbps. |
| Sharpness | **Low** | Camera-side sharpening creates edge artifacts that hurt small-object detection. Let the model see clean pixels. |
| Shutter / exposure floor | **~1/250 s** (daytime, if available) | Freezes motion blur on walking subjects, which otherwise smears cigarettes/bottles into nothing. |
| WDR | On, moderate | The 2nd-floor view is backlit by sky/street — WDR keeps faces out of shadow. |

After changing the stream to `/101`, use it everywhere:

```
--source "rtsp://admin:<password>@192.168.1.64:554/Streaming/Channels/101"
```

(URL-encode special characters in the password: `[` → `%5B`, `]` → `%5D`.)

## 2. Host runtime — inference resolution

LookOut downscales each frame to `imgsz` before inference. Higher = distant
subjects stay resolvable, at quadratic GPU cost. Configurable per run:

```
# environment variables (multiples of 32):
LOOKOUT_IMGSZ=960          # near-mode / whole-frame passes (person, smoking, thief)
LOOKOUT_CASCADE_IMGSZ=1280 # long-range tiling/cascade passes
```

Defaults are **GPU-aware**: `960` when a CUDA GPU is present, `640` on a CPU-only
build (where 960 drops near mode well under 10 FPS).

## 3. GPU — the prerequisite the range plan assumes

**The range plan targets an RTX 4050. The current install is CPU-only:**

```
torch 2.12.1+cpu     CUDA available: False     onnxruntime: CPU only
```

Until this is fixed, main-stream + higher imgsz + cascade crops run at seconds
per frame, and any measured "range" reflects a CPU-crippled pipeline. To enable
the GPU (if the laptop has the RTX 4050):

```
pip uninstall torch onnxruntime
pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install onnxruntime-gpu
```

Then confirm:

```
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Expect `True NVIDIA GeForce RTX 4050`. Once true, the imgsz defaults auto-raise
to 960 and the whole range plan becomes runnable at real time.

## 4. Reference — camera capability (DORI, do not exceed in claims)

Per the DS-2CD1047G2-LUF datasheet at this lens/mount:

| DORI level | Range | Meaning for LookOut |
|---|---|---|
| **Detect** | 64 m | "something is there" — person-class presence |
| **Observe** | 25 m | behaviour/posture cues — theft actions, drinking gesture |
| **Recognize** | 12 m | object cues — bottle, gun/knife |
| **Identify** | 6 m | face/identity — curfew face-match, cigarette |

These are physics caps. No software extends identification past ~6 m or the
cigarette object cue much past ~8 m — beyond that, action/posture heuristics
(puff cycle, drinking posture, theft motion) carry, not the object detector.
