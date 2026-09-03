"""
Perceptual fingerprint calculators.

All functions are pure (PIL.Image in -> plain‑Python‑serialisable values out)
and never raise: on internal failure they return a neutral zero‑value so a
single unreadable image cannot take down a batch job.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Tuple, Optional

import numpy as np
from PIL import Image

from ._imaging_backend import HAS_OPENCV, cv2
from .constants import (
    SIG_POINTS_MIN,
    SIG_POINTS_MAX,
    SIG_POINTS_DEFAULT,
    SCAFFOLD_SIZE,
    SPATIAL_RADIUS,
    GROUND_CONFIDENCE_THRESHOLD,
    CURVATURE_MAX,
)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
HASH_SIZE = 8
PHASH_IMG_SIZE = 32
LAB_RESIZE = 64
WAVEFORM_BINS = 256
WAVEFORM_KEEP = 16


# ----------------------------------------------------------------------
# Colour conversion helpers (used for all points)
# ----------------------------------------------------------------------

def _hex_to_lab_fast(hex_code: str) -> Tuple[float, float, float]:
    """Convert #rrggbb to CIELAB (L*, a*, b*) using sRGB D65 reference."""
    hex_code = hex_code.lstrip('#')
    if len(hex_code) != 6:
        return (0.0, 0.0, 0.0)

    r = int(hex_code[0:2], 16) / 255.0
    g = int(hex_code[2:4], 16) / 255.0
    b = int(hex_code[4:6], 16) / 255.0

    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r_lin, g_lin, b_lin = lin(r), lin(g), lin(b)

    X = r_lin * 0.4124564 + g_lin * 0.3575761 + b_lin * 0.1804375
    Y = r_lin * 0.2126729 + g_lin * 0.7151522 + b_lin * 0.0721750
    Z = r_lin * 0.0193339 + g_lin * 0.1191920 + b_lin * 0.9503041

    def f(t):
        return t ** (1/3) if t > 0.008856 else 7.787 * t + 16/116

    Xn, Yn, Zn = 0.95047, 1.0, 1.08883
    L = 116 * f(Y / Yn) - 16
    a = 500 * (f(X / Xn) - f(Y / Yn))
    b = 200 * (f(Y / Yn) - f(Z / Zn))
    return (L, a, b)


def _hex_to_luma(hex_code: str) -> float:
    """Approximate lightness (luma) from hex."""
    hex_code = hex_code.lstrip('#')
    if len(hex_code) != 6:
        return 0.0
    r = int(hex_code[0:2], 16)
    g = int(hex_code[2:4], 16)
    b = int(hex_code[4:6], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


# ----------------------------------------------------------------------
# Neighbour-finding with adaptive radius
# ----------------------------------------------------------------------

def _get_neighbours(
    centre: Dict,
    scaffold: List[Dict],
    min_neighbours: int = 4,
    max_radius: float = 0.20,
    step: float = 0.01,
) -> Tuple[List[Dict], float]:
    """
    Find neighbours within adaptive radius. Grows radius until at least
    min_neighbours neighbours are found, or max_radius reached.
    """
    x0, y0 = centre["x"], centre["y"]
    radius = SPATIAL_RADIUS
    while radius <= max_radius:
        neighbours = [
            sp for sp in scaffold
            if sp is not centre and math.hypot(sp["x"] - x0, sp["y"] - y0) <= radius
        ]
        if len(neighbours) >= min_neighbours:
            return neighbours, radius
        radius += step
    # fallback: return all scaffold points except self (likely large radius)
    return [sp for sp in scaffold if sp is not centre], max_radius


# ----------------------------------------------------------------------
# Neighbour‑based topology helpers
# ----------------------------------------------------------------------

def _compute_size(selected_idx: int, scaffold: List[Dict], selected_indices: List[int]) -> float:
    """Size = fraction of scaffold points assigned to this selected point (Voronoi)."""
    if not scaffold or not selected_indices:
        return 0.0
    assignments = {}
    for j, sp in enumerate(scaffold):
        best_dist = float('inf')
        best_sel = -1
        for k, sel_idx in enumerate(selected_indices):
            dx = sp["x"] - scaffold[sel_idx]["x"]
            dy = sp["y"] - scaffold[sel_idx]["y"]
            d = math.hypot(dx, dy)
            if d < best_dist:
                best_dist = d
                best_sel = k
        assignments[best_sel] = assignments.get(best_sel, 0) + 1
    total = len(scaffold)
    return assignments.get(selected_idx, 0) / total if total else 0.0


def _compute_gradient(centre: Dict, neighbours: List[Dict]) -> Tuple[float, float]:
    """Return (theta, mag) where theta in [0, 2π)."""
    if not neighbours:
        return 0.0, 0.0
    Gx = 0.0
    Gy = 0.0
    centre_luma = centre.get("luma", 0.0)
    for n in neighbours:
        dx = n["x"] - centre["x"]
        dy = n["y"] - centre["y"]
        dl = n["luma"] - centre_luma
        Gx += dl * dx
        Gy += dl * dy
    mag = math.hypot(Gx, Gy)
    theta = math.atan2(Gy, Gx) % (2 * math.pi)
    return theta, mag


def _compute_elongation_axis(centre: Dict, neighbours: List[Dict]) -> Tuple[float, float]:
    """Return (elongation, axis_phi) where axis_phi in [0, π)."""
    if len(neighbours) < 2:
        return 0.0, 0.0
    cov = np.zeros((2, 2))
    for n in neighbours:
        dx = n["x"] - centre["x"]
        dy = n["y"] - centre["y"]
        cov[0,0] += dx*dx
        cov[0,1] += dx*dy
        cov[1,0] += dx*dy
        cov[1,1] += dy*dy
    cov /= len(neighbours)
    eigvals, eigvecs = np.linalg.eig(cov)
    idx = np.argsort(eigvals)[::-1]
    eigvals = eigvals[idx]
    eigvecs = eigvecs[:, idx]
    if eigvals[0] > 1e-6:
        elongation = max(0.0, min(1.0, 1.0 - abs(eigvals[1] / eigvals[0])))
    else:
        elongation = 0.0
    axis_phi = math.atan2(eigvecs[1, 0], eigvecs[0, 0]) % math.pi
    return elongation, axis_phi


def _compute_primitive_quadrant(
    centre: Dict,
    neighbours: List[Dict],
    elongation: float,
    grad_mag: float
) -> int:
    """
    Primitive using quadrant variance of neighbour positions/colours.
    Returns 0 = plane, 1 = edge.
    """
    if len(neighbours) < 4:
        return 0
    cx, cy = centre["x"], centre["y"]
    # Split into quadrants (relative to centre)
    quadrants = [[], [], [], []]  # NE, NW, SW, SE
    for n in neighbours:
        dx = n["x"] - cx
        dy = n["y"] - cy
        if dx >= 0 and dy >= 0:
            quadrants[0].append(n)
        elif dx < 0 and dy >= 0:
            quadrants[1].append(n)
        elif dx < 0 and dy < 0:
            quadrants[2].append(n)
        else:
            quadrants[3].append(n)

    # Compute variance of luma in each quadrant
    def variance(lst):
        if len(lst) < 2:
            return 0.0
        vals = [n["luma"] for n in lst]
        return np.var(vals)

    variances = [variance(q) for q in quadrants]
    max_v = max(variances)
    min_v = min(variances)
    # If max_v > 2 * min_v, treat as edge (1), else plane (0)
    if max_v > 2 * min_v + 1e-6:
        return 1
    return 0


def _compute_density(neighbours: List[Dict], total_scaffold: int) -> float:
    if total_scaffold <= 1:
        return 0.0
    return len(neighbours) / (total_scaffold - 1)


def _compute_curvature(centre: Dict, neighbours: List[Dict], axis_phi: float) -> float:
    """Curvature κ = |2a| from parabola fit v = a·u² + b·u."""
    if len(neighbours) < 3:
        return 0.0
    u_axis = np.array([math.cos(axis_phi), math.sin(axis_phi)])
    v_axis = np.array([-math.sin(axis_phi), math.cos(axis_phi)])
    u_vals = []
    v_vals = []
    for n in neighbours:
        dx = n["x"] - centre["x"]
        dy = n["y"] - centre["y"]
        u = dx * u_axis[0] + dy * u_axis[1]
        v = dx * v_axis[0] + dy * v_axis[1]
        u_vals.append(u)
        v_vals.append(v)

    if len(u_vals) < 3:
        return 0.0
    u_arr = np.array(u_vals)
    v_arr = np.array(v_vals)
    A = np.vstack([u_arr**2, u_arr]).T
    try:
        coeffs, _, _, _ = np.linalg.lstsq(A, v_arr, rcond=None)
        a = coeffs[0]
        curvature = abs(2.0 * a)
        return min(curvature, CURVATURE_MAX)
    except Exception:
        return 0.0


def _compute_structural_score(scaffold_point: Dict) -> float:
    """Score a scaffold point for structural importance."""
    size = scaffold_point.get("size", 0.0)
    elong = scaffold_point.get("elongation", 0.0)
    curv = scaffold_point.get("curvature", 0.0)
    return (size * 0.3) + ((elong * 1.5 + curv * 2.0) * 0.7)


# ----------------------------------------------------------------------
# Ground plane detection
# ----------------------------------------------------------------------

def _detect_ground(scaffold: List[Dict]) -> Tuple[float, bool]:
    """Return (confidence, detected) for ground plane."""
    # Consider points in bottom region (y >= 0.65)
    bottom = [sp for sp in scaffold if sp["y"] >= 0.65]
    if not bottom:
        return 0.0, False

    # Need at least some spread in x
    xs = [sp["x"] for sp in bottom]
    span = max(xs) - min(xs) if len(xs) > 1 else 0.0
    if span < 0.55:
        return 0.0, False

    # Check horizontal orientation (using gradient or elongation)
    # For simplicity, we use elongation and axis_phi from precomputed data
    # This function should be called after topology is computed on scaffold.
    # We'll assume scaffold points have elongation, axis_phi computed.
    horizontal_count = 0
    for sp in bottom:
        axis = sp.get("axis_phi", 0.0)
        if axis < 0.40 or abs(axis - math.pi) < 0.40:
            horizontal_count += 1
    if horizontal_count < len(bottom) * 0.5:
        return 0.0, False

    # Sigmoidal confidence based on span
    confidence = 1.0 / (1.0 + math.exp(-15.0 * (span - 0.55)))
    return confidence, confidence > GROUND_CONFIDENCE_THRESHOLD


# ----------------------------------------------------------------------
# Main visual signature computation
# ----------------------------------------------------------------------

def compute_visual_signature(image: Image.Image, num_points: int = SIG_POINTS_MAX) -> Dict[str, Any]:
    """
    Compute visual signature points with neighbour‑based topology.

    Returns a dict with:
      - "visual_sig": list of selected point dicts (each with all fields)
      - "ground_confidence": float
      - "ground_detected": bool
    """
    try:
        # 1. Build scaffold: 16x16 grid (256 points) with colours and luma
        size = SCAFFOLD_SIZE
        small = image.resize((size, size), resample=Image.Resampling.LANCZOS)
        pixels = np.array(small.convert("RGB")).reshape(-1, 3) / 255.0
        coords = np.mgrid[0:1:size*1j, 0:1:size*1j].reshape(2, -1).T  # (size*size, 2)

        scaffold = []
        for i, (x, y) in enumerate(coords):
            r, g, b = (pixels[i] * 255).astype(int)
            hex_str = f"#{r:02x}{g:02x}{b:02x}"
            lab = _hex_to_lab_fast(hex_str)
            luma = _hex_to_luma(hex_str)
            scaffold.append({
                "x": float(x),
                "y": float(y),
                "hex": hex_str,
                "lab": lab,
                "luma": luma,
            })

        # 2. Pre‑compute topology for all scaffold points (needed for scoring)
        # We'll compute neighbours for each scaffold point (adaptive radius)
        # and store elongation, curvature, etc. in scaffold.
        total_scaffold = len(scaffold)
        for sp in scaffold:
            neighbours, _ = _get_neighbours(sp, scaffold, min_neighbours=4)
            grad_theta, grad_mag = _compute_gradient(sp, neighbours)
            elong, axis = _compute_elongation_axis(sp, neighbours)
            curv = _compute_curvature(sp, neighbours, axis)
            sp["grad_theta"] = grad_theta
            sp["grad_mag"] = grad_mag
            sp["elongation"] = elong
            sp["axis_phi"] = axis
            sp["curvature"] = curv
            sp["density"] = _compute_density(neighbours, total_scaffold)
            sp["primitive"] = _compute_primitive_quadrant(sp, neighbours, elong, grad_mag)

        # 3. Greedy selection (colour diversity + spatial spread)
        # First point: max colour variance
        selected_indices = []
        # Compute colour variance (std of RGB)
        stds = np.std(pixels, axis=1)
        first = int(np.argmax(stds))
        selected_indices.append(first)

        # Greedily select up to half of target budget
        target_budget = max(SIG_POINTS_MIN, min(SIG_POINTS_MAX, int(SIG_POINTS_DEFAULT * 1.2)))
        greedy_count = max(4, target_budget // 2)  # at least 4
        for _ in range(greedy_count - 1):
            if len(selected_indices) >= target_budget:
                break
            # For each candidate, min distance to selected in colour and space
            c_dists = np.min([np.linalg.norm(pixels - pixels[s], axis=1) for s in selected_indices], axis=0)
            s_dists = np.min([np.linalg.norm(coords - coords[s], axis=1) for s in selected_indices], axis=0)
            # Weighted product
            scores = c_dists * s_dists
            # Avoid selecting already selected
            for s in selected_indices:
                scores[s] = -1
            best = int(np.argmax(scores))
            if scores[best] <= 0:
                break
            selected_indices.append(best)

        # 4. Structural scoring for remaining points (after greedy)
        # Score each unselected scaffold point using structural score
        remaining = [i for i in range(total_scaffold) if i not in selected_indices]
        scored = []
        for i in remaining:
            sp = scaffold[i]
            score = _compute_structural_score(sp)
            scored.append((score, i))
        scored.sort(key=lambda x: x[0], reverse=True)

        # 5. NMS for additional structural points
        # Select up to (target_budget - len(selected_indices)) additional points
        additional_needed = max(0, target_budget - len(selected_indices))
        added = 0
        nms_radius = 0.04
        for score, idx in scored:
            if added >= additional_needed:
                break
            # Check distance to already selected (including greedy)
            too_close = False
            x0, y0 = scaffold[idx]["x"], scaffold[idx]["y"]
            for sel in selected_indices:
                x1, y1 = scaffold[sel]["x"], scaffold[sel]["y"]
                if math.hypot(x0 - x1, y0 - y1) < nms_radius:
                    too_close = True
                    break
            if not too_close:
                selected_indices.append(idx)
                added += 1

        # 6. If still less than min, add best remaining regardless of NMS
        while len(selected_indices) < SIG_POINTS_MIN and scored:
            _, idx = scored.pop(0)
            if idx not in selected_indices:
                selected_indices.append(idx)

        # 7. Build final visual_sig list
        sigs = []
        for i, idx in enumerate(selected_indices):
            sp = scaffold[idx]
            # Compute size using Voronoi assignment over scaffold
            size = _compute_size(i, scaffold, selected_indices)

            # Get all fields
            lab = sp["lab"]
            node = {
                "x": round(sp["x"] * 100, 1),
                "y": round(sp["y"] * 100, 1),
                "size": round(size * 100, 1),
                "hex": sp["hex"],
                "lab": [round(lab[0], 2), round(lab[1], 2), round(lab[2], 2)],
                "grad_theta": round(sp["grad_theta"], 4),
                "grad_mag": round(sp["grad_mag"], 2),
                "elongation": round(sp["elongation"], 4),
                "axis_phi": round(sp["axis_phi"], 4),
                "primitive": sp["primitive"],
                "density": round(sp["density"], 4),
                "curvature": round(sp["curvature"], 4),
            }
            sigs.append(node)

        # 8. Ground detection
        ground_conf, ground_detected = _detect_ground(scaffold)

        return {
            "visual_sig": sigs,
            "ground_confidence": round(ground_conf, 4),
            "ground_detected": ground_detected,
        }

    except Exception:
        return {
            "visual_sig": [],
            "ground_confidence": 0.0,
            "ground_detected": False,
        }


# ----------------------------------------------------------------------
# Existing calculator functions (unchanged except they now ignore extra keys)
# ----------------------------------------------------------------------

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
    luma = _dct_phash_pil(image)
    color = luma
    try:
        hsv = image.convert("HSV")
        hue_channel = Image.fromarray(np.asarray(hsv)[:, :, 0])
        color = _dct_phash_pil(hue_channel)
    except Exception:
        pass
    return {"phash_luma": luma, "phash_color": color}


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