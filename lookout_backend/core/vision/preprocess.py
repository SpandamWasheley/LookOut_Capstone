"""Image-enhancement preprocessing, applied to a frame BEFORE detection.

The detectors only see what the frame gives them; in poor light or with sensor
noise, detail the model needs is buried before it ever runs. This layer recovers
some of it — brighten, denoise, boost local contrast, optionally sharpen — so the
same detector performs better on bad frames.

Gated on brightness: daytime frames (already well-exposed) BYPASS untouched, so
this costs almost nothing in good light and can't hurt an already-clean image.
Only dim frames pay the enhancement cost.

Everything here is classical OpenCV (no new dependency, no model). Pure CV
plumbing — no Django access — same rule as recognition.py.
"""
import cv2
import numpy as np

# Mean luma (0-255) at or above which a frame is treated as daytime and returned
# untouched. Below it, the low-light enhancement path runs.
LUMA_DAY_THRESHOLD = 90
# Below this mean luma the frame is very dark, so gamma-brighten before the rest.
LUMA_DARK_THRESHOLD = 50
GAMMA_DARK = 1.5            # >1 brightens; applied only to very dark frames

CLAHE_CLIP = 2.0           # contrast-limit for CLAHE (higher = stronger, noisier)
CLAHE_TILE = (8, 8)        # local-contrast tile grid

# Pre-built gamma lookup table (LUT) so brightening is a fast table map, not a
# per-pixel power each frame.
_GAMMA_LUT = np.array(
    [((i / 255.0) ** (1.0 / GAMMA_DARK)) * 255 for i in range(256)],
).astype("uint8")

# Unsharp-style sharpening kernel (optional).
_SHARPEN_KERNEL = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])


def mean_luma(frame):
    """Average brightness of the frame (0-255), the day/night gate signal."""
    return float(np.mean(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))


def preprocess(frame, mode="near", sharpen=False):
    """Enhance a frame before detection, or return it untouched in daylight.

    mode:
      "near"    — bilateral denoise (fast, edge-preserving), for ~15 FPS runs.
      "cascade" — fastNlMeans denoise (stronger, much slower), for ~1 FPS runs.
    sharpen: apply an unsharp kernel after contrast (helps small edges, can
             amplify noise — off by default).

    Low-light pipeline: gamma brighten (if very dark) -> denoise -> CLAHE local
    contrast on the L channel of LAB (so colour isn't shifted) -> optional sharpen.
    """
    luma = mean_luma(frame)
    if luma >= LUMA_DAY_THRESHOLD:
        return frame  # daytime: already well-exposed, leave it alone

    out = frame
    # 1. Brighten very dark frames with a gamma LUT.
    if luma < LUMA_DARK_THRESHOLD:
        out = cv2.LUT(out, _GAMMA_LUT)

    # 2. Denoise — cost scaled to the runtime budget.
    if mode == "cascade":
        out = cv2.fastNlMeansDenoisingColored(out, None, 5, 5, 7, 15)
    else:
        out = cv2.bilateralFilter(out, 5, 50, 50)

    # 3. CLAHE local-contrast on the L (lightness) channel only, so hues stay put.
    lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP, tileGridSize=CLAHE_TILE)
    l = clahe.apply(l)
    out = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)

    # 4. Optional sharpening.
    if sharpen:
        out = cv2.filter2D(out, -1, _SHARPEN_KERNEL)

    return out


def add_cli_flags(parser, ablatable=True):
    """Registers --preprocess/--sharpen on a watch_<x> management command.

    Every detector takes the same two flags with the same meaning, so they are
    declared once here instead of being copy-pasted per command. `ablatable` is
    False for the watchers that have no --ablate flag of their own.
    """
    parser.add_argument(
        "--preprocess",
        action="store_true",
        help="Enhance dim/noisy frames before detection: gamma-brighten, "
             "denoise, and CLAHE local contrast (daytime frames bypass "
             "untouched). Helps in low light; adds a little cost per dark "
             "frame."
             + (" Add 'preprocess' to --ablate to A/B it." if ablatable else ""),
    )
    parser.add_argument(
        "--sharpen",
        action="store_true",
        help="With --preprocess, also apply an unsharp kernel (sharper edges "
             "for small objects, but can amplify noise).",
    )
