"""
Perceptual fingerprint calculators. Algorithms are kept numerically
equivalent to the original gridview.py reference implementation so that
existing caches / downstream consumers stay compatible.

Every function is pure (path/PIL.Image in -> plain-python-serialisable
values out) and never raises: on internal failure it returns a neutral
zero-value so a single unreadable image can't take down a batch job.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import numpy as np
from PIL import Image

from ._imaging_backend import HAS_OPENCV, cv2

HASH_SIZE = 8
PHASH_IMG_SIZE = 32
SIG_POINTS = 16
LAB_RESIZE = 64
WAVEFORM_BINS = 256
WAVEFORM_KEEP = 16


def _dct_phash_pil(image: Image.Image, hash_size: int = HASH_SIZE) -> str:
    try:
        img = image.convert("L").resize((PHASH_IMG_SIZE, PHASH_IMG_SIZE), Image.Resampling.LANCZOS)
        pixels = np.asarray(img, dtype=np.float64)
        dct_abs = np.abs(np.fft.fft2(pixels))
        dct_slice = dct_abs[1:hash_size + 1, 1:hash_size + 1].flatten()
        median_val = np.median(dct_slice)
        bits = (dct_slice > median_val).astype(np.uint8)
        hex_str = "".join(f"{int(''.join(map(str, bits[i:i + 4])), 2):x}" for i in range(0, 64, 4))
        return hex_str.zfill(16)
    except Exception:
        return "0" * 16


def compute_hashes(image: Image.Image) -> Dict[str, str]:
    """phash_luma / phash_color from an already-open PIL image."""
    luma = _dct_phash_pil(image)
    color = luma
    try:
        hsv = image.convert("HSV")
        hue_channel = Image.fromarray(np.asarray(hsv)[:, :, 0])
        color = _dct_phash_pil(hue_channel)
    except Exception:
        pass
    return {"phash_luma": luma, "phash_color": color}


def compute_visual_signature(image: Image.Image, num_points: int = SIG_POINTS) -> Dict[str, Any]:
    try:
        small = image.resize((16, 16), resample=Image.Resampling.LANCZOS)
        pixels = np.array(small.convert("RGB")).reshape(-1, 3) / 255.0
        coords = np.mgrid[0:1:16j, 0:1:16j].reshape(2, -1).T

        selected = [int(np.argmax(np.std(pixels, axis=1)))]
        for _ in range(num_points - 1):
            c_dists = np.min([np.linalg.norm(pixels - pixels[s], axis=1) for s in selected], axis=0)
            s_dists = np.min([np.linalg.norm(coords - coords[s], axis=1) for s in selected], axis=0)
            selected.append(int(np.argmax(c_dists * s_dists)))

        weights: Dict[int, int] = defaultdict(int)
        for p in pixels:
            dists = [np.linalg.norm(p - pixels[s]) for s in selected]
            weights[int(np.argmin(dists))] += 1

        sig = []
        for i, idx in enumerate(selected):
            r, g, b = (pixels[idx] * 255).astype(int)
            sig.append({
                "hex": f"#{r:02x}{g:02x}{b:02x}",
                "x": round(float(coords[idx][1]) * 100, 1),
                "y": round(float(coords[idx][0]) * 100, 1),
                "size": round((weights[i] / 256) * 100, 1),
            })
        return {"visual_sig": sig}
    except Exception:
        return {"visual_sig": []}


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    a = 0.055
    return np.where(c <= 0.04045, c / 12.92, ((c + a) / (1 + a)) ** 2.4)


def _rgb_to_lab(arr: np.ndarray) -> np.ndarray:
    lin = _srgb_to_linear(arr)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = (lin.reshape(-1, 3) @ m.T).reshape(lin.shape)
    xn, yn, zn = 0.95047, 1.0, 1.08883
    xyz[..., 0] /= xn
    xyz[..., 1] /= yn
    xyz[..., 2] /= zn
    t = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * t[..., 1] - 16
    a = 500 * (t[..., 0] - t[..., 1])
    b = 200 * (t[..., 1] - t[..., 2])
    return np.stack([L, a, b], axis=-1)


def compute_lab_metrics(image: Image.Image) -> Dict[str, float]:
    try:
        img = image.convert("RGB").resize((LAB_RESIZE, LAB_RESIZE), Image.Resampling.LANCZOS)
        arr = np.array(img, dtype=np.float64) / 255.0
        lab = _rgb_to_lab(arr)
        L, a, b = tuple(lab.mean(axis=(0, 1)))
        return {
            "lab_value": round(float(L), 2),
            "lab_hue": round(float(np.arctan2(b, a)), 2),
            "lab_chroma": round(float(math.sqrt(a ** 2 + b ** 2)), 2),
            "lab_warmth": round(float(b), 2),
        }
    except Exception:
        return {"lab_value": 0.0, "lab_hue": 0.0, "lab_chroma": 0.0, "lab_warmth": 0.0}


def _dct_1d_numpy(hist_norm: np.ndarray) -> np.ndarray:
    n = len(hist_norm)
    out = np.zeros(n, dtype=np.float64)
    k = np.arange(n)
    factor = np.pi / (2.0 * n)
    # vectorised type-II DCT (equivalent to the reference's nested-loop version)
    n_idx = np.arange(n).reshape(-1, 1)
    cos_table = np.cos((2 * n_idx + 1) * k.reshape(1, -1) * factor)
    s = hist_norm @ cos_table
    c = np.full(n, math.sqrt(2.0 / n))
    c[0] = math.sqrt(1.0 / n)
    return c * s


def compute_hist_waveform(image: Image.Image) -> Dict[str, List[float]]:
    try:
        gray = np.array(image.convert("L"), dtype=np.uint8)
        hist, _ = np.histogram(gray, bins=WAVEFORM_BINS, range=(0, WAVEFORM_BINS))
        total = hist.sum()
        hist_norm = (hist.astype(np.float32) / total) if total > 0 else np.zeros(WAVEFORM_BINS, dtype=np.float32)

        if HAS_OPENCV:
            dct_hist = cv2.dct(hist_norm.reshape(-1, 1)).flatten()
        else:
            dct_hist = _dct_1d_numpy(hist_norm.astype(np.float64))

        return {"hist_waveform": [round(float(v), 6) for v in dct_hist[:WAVEFORM_KEEP]]}
    except Exception:
        return {"hist_waveform": [0.0] * WAVEFORM_KEEP}
