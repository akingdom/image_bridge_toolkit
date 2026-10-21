"""
Perceptual fingerprint calculators.

All functions are pure (PIL.Image in -> plain-Python-serialisable values out)
and never raise: on internal failure they return a neutral zero-value so a
single unreadable image cannot take down a batch job.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
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
    VIDEO_SAMPLE_PERCENTAGES,
    VIDEO_SAMPLE_FIXED_TIMES,
    VIDEO_MAX_SAMPLES,
)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
HASH_SIZE = 8
PHASH_IMG_SIZE = 32
LAB_RESIZE = 64
WAVEFORM_BINS = 256
WAVEFORM_KEEP = 16

# Histogram-anchored selection.
#
# The 16x16 scaffold is quantised into a fixed LAB grid. A fixed grid (as
# opposed to k-means) means bin boundaries are identical for every image,
# so a colour at (65, 12, -8) lands in the same bin in both versions of a
# lightly-edited image. k-means centres drift between images, which would
# reintroduce the instability this design removes.
#
# HISTOGRAM_BINS = 6 gives 216 bins. Measured overlap mass on a lightly-
# edited pair was 0.996 at 4, 6, and 8 bins per channel -- the coarsest
# resolution is sufficient. 6 is chosen for headroom: finer bins preserve
# more colour detail if the corpus demands it, without the sparsity that
# would come from 8+ on a 256-cell scaffold.
HISTOGRAM_BINS = 6
HISTOGRAM_MIN_CELLS = 3
HISTOGRAM_MIN_FRACTION = 0.02   # bin must contain >= 2% of scaffold cells
HISTOGRAM_NMS_RADIUS = 0.04     # same as the pre-histogram NMS radius


# ----------------------------------------------------------------------
# Colour conversion helpers
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
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    Xn, Yn, Zn = 0.95047, 1.0, 1.08883
    L = 116 * f(Y / Yn) - 16
    a = 500 * (f(X / Xn) - f(Y / Yn))
    b = 200 * (f(Y / Yn) - f(Z / Zn))
    return (L, a, b)


def _hex_to_luma(hex_code: str) -> float:
    hex_code = hex_code.lstrip('#')
    if len(hex_code) != 6:
        return 0.0
    r = int(hex_code[0:2], 16)
    g = int(hex_code[2:4], 16)
    b = int(hex_code[4:6], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


# ----------------------------------------------------------------------
# Neighbour finding
# ----------------------------------------------------------------------

def _get_neighbours(
    centre: Dict,
    scaffold: List[Dict],
    min_neighbours: int = 4,
    max_radius: float = 0.20,
    step: float = 0.01,
) -> Tuple[List[Dict], float]:
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
    return [sp for sp in scaffold if sp is not centre], max_radius


# ----------------------------------------------------------------------
# Topology helpers (content-driven, unchanged from previous fix)
# ----------------------------------------------------------------------

def _compute_size(selected_idx: int, scaffold: List[Dict], selected_indices: List[int]) -> float:
    """Voronoi spatial extent of this point relative to the other points."""
    if not scaffold or not selected_indices:
        return 0.0
    assignments: Dict[int, int] = {}
    for sp in scaffold:
        best_dist = float('inf')
        best_sel = -1
        for k, sel_idx in enumerate(selected_indices):
            d = math.hypot(
                sp["x"] - scaffold[sel_idx]["x"],
                sp["y"] - scaffold[sel_idx]["y"],
            )
            if d < best_dist:
                best_dist = d
                best_sel = k
        assignments[best_sel] = assignments.get(best_sel, 0) + 1
    total = len(scaffold)
    return assignments.get(selected_idx, 0) / total if total else 0.0


def _compute_gradient(centre: Dict, neighbours: List[Dict]) -> Tuple[float, float]:
    if not neighbours:
        return 0.0, 0.0
    Gx = Gy = 0.0
    centre_luma = centre.get("luma", 0.0)
    for n in neighbours:
        dx = n["x"] - centre["x"]
        dy = n["y"] - centre["y"]
        dl = n["luma"] - centre_luma
        Gx += dl * dx
        Gy += dl * dy
    return math.atan2(Gy, Gx) % (2 * math.pi), math.hypot(Gx, Gy)


def _compute_elongation_axis(centre: Dict, neighbours: List[Dict]) -> Tuple[float, float]:
    """Content-weighted elongation. Uniform neighbourhoods give zero."""
    if len(neighbours) < 2:
        return 0.0, 0.0
    centre_luma = centre.get("luma", 0.0)
    cov = np.zeros((2, 2))
    total_w = 0.0
    for n in neighbours:
        dx = n["x"] - centre["x"]
        dy = n["y"] - centre["y"]
        dl = n["luma"] - centre_luma
        w = dl * dl
        cov[0, 0] += w * dx * dx
        cov[0, 1] += w * dx * dy
        cov[1, 0] += w * dx * dy
        cov[1, 1] += w * dy * dy
        total_w += w
    if total_w < 1e-6:
        return 0.0, 0.0
    cov /= total_w
    eigvals, eigvecs = np.linalg.eig(cov)
    idx = np.argsort(eigvals)[::-1]
    eigvals = eigvals[idx]
    eigvecs = eigvecs[:, idx]
    if eigvals[0] > 1e-9:
        elongation = max(0.0, min(1.0, 1.0 - abs(eigvals[1] / eigvals[0])))
    else:
        elongation = 0.0
    axis_phi = math.atan2(eigvecs[1, 0], eigvecs[0, 0]) % math.pi
    return elongation, axis_phi


def _compute_primitive_quadrant(
    centre: Dict, neighbours: List[Dict], elongation: float, grad_mag: float
) -> int:
    if len(neighbours) < 4:
        return 0
    cx, cy = centre["x"], centre["y"]
    quadrants: List[List[Dict]] = [[], [], [], []]
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

    def variance(lst):
        if len(lst) < 2:
            return 0.0
        vals = [n["luma"] for n in lst]
        return float(np.var(vals))

    variances = [variance(q) for q in quadrants]
    max_v, min_v = max(variances), min(variances)
    return 1 if max_v > 2 * min_v + 1e-6 else 0


def _compute_density(neighbours: List[Dict], total_scaffold: int) -> float:
    if total_scaffold <= 1:
        return 0.0
    return len(neighbours) / (total_scaffold - 1)


def _compute_curvature(centre: Dict, neighbours: List[Dict], axis_phi: float) -> float:
    """Curvature with angular-coverage rejection for truncated neighbourhoods.

    Three behaviours:
    1. Neighbours weighted by squared luma difference.
    2. Neighbourhoods not surrounding the centre are rejected (angular
       coverage test). A border point sees a half-disc; a parabola fitted
       to one side is extrapolation, not observation.
    3. Fits where every neighbour sits on the same side of the u-axis are
       rejected. This was the flaw in the earlier one-sided test: for a
       straight edge the principal axis aligns with the edge and the
       projection spans both signs even when the neighbourhood is a
       half-disc.
    """
    if len(neighbours) < 3:
        return 0.0

    # Angular coverage: largest gap between consecutive neighbour bearings.
    # A full-disc neighbourhood has max_gap ~= 0.8 rad; a half-disc ~= pi.
    cx, cy = centre["x"], centre["y"]
    bearings = sorted(
        (math.atan2(n["y"] - cy, n["x"] - cx) + 2 * math.pi) % (2 * math.pi)
        for n in neighbours
    )
    max_gap = 0.0
    for i in range(len(bearings)):
        gap = (bearings[(i + 1) % len(bearings)] - bearings[i]) % (2 * math.pi)
        if gap > max_gap:
            max_gap = gap
    if max_gap > math.pi * 0.7:
        return 0.0

    centre_luma = centre.get("luma", 0.0)
    u_axis = np.array([math.cos(axis_phi), math.sin(axis_phi)])
    v_axis = np.array([-math.sin(axis_phi), math.cos(axis_phi)])
    u_vals, v_vals, w_vals = [], [], []
    for n in neighbours:
        dx = n["x"] - centre["x"]
        dy = n["y"] - centre["y"]
        dl = n["luma"] - centre_luma
        u_vals.append(dx * u_axis[0] + dy * u_axis[1])
        v_vals.append(dx * v_axis[0] + dy * v_axis[1])
        w_vals.append(dl * dl)

    u_arr = np.array(u_vals)
    v_arr = np.array(v_vals)
    w_arr = np.array(w_vals)
    if w_arr.sum() < 1e-6:
        return 0.0
    # Reject one-sided fits as a secondary guard.
    if not (np.any(u_arr > 0.005) and np.any(u_arr < -0.005)):
        return 0.0
    sqrt_w = np.sqrt(w_arr)
    A = np.vstack([sqrt_w * u_arr ** 2, sqrt_w * u_arr]).T
    b_vec = sqrt_w * v_arr
    try:
        coeffs, _, _, _ = np.linalg.lstsq(A, b_vec, rcond=None)
        return min(abs(2.0 * coeffs[0]), CURVATURE_MAX)
    except Exception:
        return 0.0


def _compute_structural_score(scaffold_point: Dict) -> float:
    """Structural score. Content-driven at scoring time (size = 0)."""
    size = scaffold_point.get("size", 0.0)
    elong = scaffold_point.get("elongation", 0.0)
    curv = scaffold_point.get("curvature", 0.0)
    return (size * 0.3) + ((elong * 1.5 + curv * 2.0) * 0.7)


# ----------------------------------------------------------------------
# Ground plane detection
# ----------------------------------------------------------------------

def _detect_ground(scaffold: List[Dict]) -> Tuple[float, bool]:
    bottom = [sp for sp in scaffold if sp["y"] >= 0.65]
    if not bottom:
        return 0.0, False
    xs = [sp["x"] for sp in bottom]
    span = max(xs) - min(xs) if len(xs) > 1 else 0.0
    if span < 0.55:
        return 0.0, False
    horizontal_count = sum(
        1 for sp in bottom
        if sp.get("axis_phi", 0.0) < 0.40
        or abs(sp.get("axis_phi", 0.0) - math.pi) < 0.40
    )
    if horizontal_count < len(bottom) * 0.5:
        return 0.0, False
    confidence = 1.0 / (1.0 + math.exp(-15.0 * (span - 0.55)))
    return confidence, confidence > GROUND_CONFIDENCE_THRESHOLD


# ----------------------------------------------------------------------
# Histogram-anchored selection
# ----------------------------------------------------------------------

def _bin_lab(lab: Tuple[float, float, float], bins: int) -> Tuple[int, int, int]:
    """Quantise an LAB triplet to a fixed-grid bin index. Fixed boundaries
    are essential: they guarantee that two versions of the same colour map
    to the same bin regardless of what other colours are present."""
    L, a, b = lab
    L = max(0.0, min(100.0, L))
    a = max(-128.0, min(127.0, a))
    b = max(-128.0, min(127.0, b))
    # 0.999999 ensures the upper bound maps to bins-1, not bins.
    li = min(bins - 1, int(L / 100.0 * bins * 0.999999))
    ai = min(bins - 1, int((a + 128.0) / 255.0 * bins * 0.999999))
    bi = min(bins - 1, int((b + 128.0) / 255.0 * bins * 0.999999))
    return (li, ai, bi)


def _histogram_select(
    scaffold: List[Dict[str, Any]],
    structural_raw: np.ndarray,
    target_budget: int,
    sig_min: int,
) -> List[int]:
    """Select scaffold indices via LAB histogram anchoring.

    Steps:
      1. Quantise all cells into a fixed LAB grid.
      2. Identify significant bins (>= max(HISTOGRAM_MIN_CELLS, fraction) cells).
      3. Allocate the budget across bins: 1 per bin minimum, remaining
         distributed proportionally to bin size via a fair-share rule.
      4. Within each bin, pick cells by structural score, with NMS for
         spacing so a large region is represented at multiple locations
         rather than clustering on its single most-structural point.

    Deterministic given structural_raw and scaffold, which is the property
    that makes the whole signature stable under minor edits.
    """
    n_cells = len(scaffold)
    bin_of = [_bin_lab(sp["lab"], HISTOGRAM_BINS) for sp in scaffold]
    counts = Counter(bin_of)

    min_cells = max(HISTOGRAM_MIN_CELLS, int(HISTOGRAM_MIN_FRACTION * n_cells))
    significant = {b: c for b, c in counts.items() if c >= min_cells}

    if not significant:
        # Degenerate: no bin is significant. Fall back to top-by-structure.
        order = sorted(range(n_cells), key=lambda i: -structural_raw[i])
        return order[:target_budget]

    # Order bins by count desc; if there are more significant bins than
    # the budget, keep the largest ones.
    sig_sorted = sorted(significant.keys(), key=lambda b: -significant[b])
    if len(sig_sorted) > target_budget:
        sig_sorted = sig_sorted[:target_budget]

    # Start with one pick per bin, then distribute the rest by fair-share:
    # each additional pick goes to the bin currently most under-served
    # relative to its share of significant cells.
    allocation: Dict[Tuple[int, int, int], int] = {b: 1 for b in sig_sorted}
    remaining = target_budget - len(sig_sorted)
    if remaining > 0:
        sig_total = sum(significant[b] for b in sig_sorted)
        for _ in range(remaining):
            best_b = max(
                sig_sorted,
                key=lambda b: significant[b] / (allocation[b] * (allocation[b] + 1)),
            )
            allocation[best_b] += 1

    # Enforce a minimum total (in case the image has very few colours).
    while sum(allocation.values()) < sig_min:
        for b in sig_sorted:
            allocation[b] += 1
            if sum(allocation.values()) >= sig_min:
                break

    # Group cells by bin for the bins we're covering.
    bin_members: Dict[Tuple[int, int, int], List[int]] = defaultdict(list)
    for i, b in enumerate(bin_of):
        if b in allocation:
            bin_members[b].append(i)

    # Per-bin pick, NMS within bin AND across bins so distinct regions
    # don't overlap spatially.
    selected: List[int] = []
    for b in sig_sorted:
        n_picks = allocation[b]
        members = bin_members.get(b, [])
        if not members:
            continue
        members_sorted = sorted(members, key=lambda i: (-float(structural_raw[i]), i))
        picked_here: List[int] = []

        # Strict pass: enforce NMS.
        for i in members_sorted:
            if len(picked_here) >= n_picks:
                break
            if any(
                math.hypot(
                    scaffold[i]["x"] - scaffold[j]["x"],
                    scaffold[i]["y"] - scaffold[j]["y"],
                ) < HISTOGRAM_NMS_RADIUS
                for j in selected + picked_here
            ):
                continue
            picked_here.append(i)

        # Relaxed pass: if NMS rejected too many, take remaining by score.
        if len(picked_here) < n_picks:
            for i in members_sorted:
                if len(picked_here) >= n_picks:
                    break
                if i not in picked_here:
                    picked_here.append(i)

        selected.extend(picked_here)

    # Final fill: if we're still under target_budget (bins were small),
    # top up with the highest-structural cells not already selected.
    if len(selected) < target_budget:
        selected_set = set(selected)
        remaining_ix = sorted(
            (i for i in range(n_cells) if i not in selected_set),
            key=lambda i: -float(structural_raw[i]),
        )
        for i in remaining_ix:
            if len(selected) >= target_budget:
                break
            selected.append(i)

    return selected


# ----------------------------------------------------------------------
# Main visual signature computation
# ----------------------------------------------------------------------

def compute_visual_signature(image: Image.Image, num_points: int = SIG_POINTS_MAX) -> Dict[str, Any]:
    """
    Compute visual signature points.

    Selection is histogram-anchored:
      * Quantise the 16x16 scaffold into a fixed LAB grid.
      * Find significant colour bins.
      * Allocate the point budget across bins proportionally to bin size.
      * Pick each bin's representative(s) by structural score, with NMS for
        spacing within the region.

    This makes selection stable under minor editing (the histogram barely
    moves; the selection barely moves) and guarantees colour coverage
    (every significant colour gets a representative).

    Returns {"visual_sig": [...], "ground_confidence": float,
             "ground_detected": bool}.
    """
    try:
        # 1. Build scaffold.
        size = SCAFFOLD_SIZE
        small = image.resize((size, size), resample=Image.Resampling.LANCZOS)
        pixels = np.array(small.convert("RGB")).reshape(-1, 3) / 255.0
        _grid_rows, _grid_cols = np.mgrid[0:1:size * 1j, 0:1:size * 1j]
        coords = np.column_stack([_grid_cols.flatten(), _grid_rows.flatten()])

        scaffold: List[Dict[str, Any]] = []
        for i, (x, y) in enumerate(coords):
            r, g, b = (pixels[i] * 255).astype(int)
            hex_str = f"#{r:02x}{g:02x}{b:02x}"
            lab = _hex_to_lab_fast(hex_str)
            luma = _hex_to_luma(hex_str)
            scaffold.append({
                "x": float(x), "y": float(y),
                "hex": hex_str, "lab": lab, "luma": luma,
            })

        # 2. Pre-compute topology for every scaffold cell.
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

        structural_raw = np.array(
            [_compute_structural_score(sp) for sp in scaffold], dtype=np.float64
        )

        # 3. Histogram-anchored selection.
        target_budget = max(
            SIG_POINTS_MIN,
            min(SIG_POINTS_MAX, int(SIG_POINTS_DEFAULT * 1.2)),
        )
        selected_indices = _histogram_select(
            scaffold, structural_raw, target_budget, SIG_POINTS_MIN
        )

        # 4. Build output.
        sigs = []
        for i, idx in enumerate(selected_indices):
            sp = scaffold[idx]
            size_v = _compute_size(i, scaffold, selected_indices)
            lab = sp["lab"]
            node = {
                "x": round(sp["x"] * 100, 1),
                "y": round(sp["y"] * 100, 1),
                "size": round(size_v * 100, 1),
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

        # 5. Ground detection.
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
# Hash / LAB / waveform / exact / exif / video (unchanged)
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
    xyz[..., 0] /= xn; xyz[..., 1] /= yn; xyz[..., 2] /= zn
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


def compute_exact_hashes(path: str, chunk_size: int = 1024 * 1024) -> Dict[str, Any]:
    md5 = hashlib.md5(); sha256 = hashlib.sha256(); size = 0
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                md5.update(chunk); sha256.update(chunk); size += len(chunk)
        return {"md5": md5.hexdigest(), "sha256": sha256.hexdigest(), "size": size}
    except Exception:
        return {"md5": "", "sha256": "", "size": 0}


def _gps_to_decimal(dms, ref) -> float:
    try:
        d, m, s = (float(v) for v in dms)
    except (TypeError, ValueError):
        return 0.0
    dd = d + (m / 60.0) + (s / 3600.0)
    if isinstance(ref, str) and ref.upper() in ("S", "W"):
        dd = -dd
    return round(dd, 6)


def compute_exif(path: str) -> Dict[str, Any]:
    empty = {"exif_datetime": "", "exif_make": "", "exif_model": "",
             "exif_gps_lat": 0.0, "exif_gps_lon": 0.0}
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return empty
            out = dict(empty)
            dt = exif.get(36867) or exif.get(306)
            if dt: out["exif_datetime"] = str(dt)
            make = exif.get(271)
            if make: out["exif_make"] = str(make)
            model = exif.get(272)
            if model: out["exif_model"] = str(model)
            try:
                gps_ifd = exif.get_ifd(34853)
            except Exception:
                gps_ifd = None
            if gps_ifd:
                out["exif_gps_lat"] = _gps_to_decimal(gps_ifd.get(2, (0, 0, 0)), gps_ifd.get(1, "N"))
                out["exif_gps_lon"] = _gps_to_decimal(gps_ifd.get(4, (0, 0, 0)), gps_ifd.get(3, "E"))
            return out
    except Exception:
        return empty


_VIDEO_EMPTY: Dict[str, Any] = {
    "video_duration": 0.0, "video_fps": 0.0, "video_frame_count": 0,
    "video_codec": "", "video_width": 0, "video_height": 0, "video_sig": [],
}


def _sample_times(duration: float, fps: float) -> List[float]:
    if duration <= 0 or fps <= 0:
        return [0.0]
    if duration <= 60.0:
        raw = [duration * p for p in VIDEO_SAMPLE_PERCENTAGES]
    else:
        fixed = [t for t in VIDEO_SAMPLE_FIXED_TIMES if 0.0 < t < duration]
        pct = [duration * 0.5, duration * 0.9]
        raw = sorted(set(fixed + pct))
    seen = set(); out: List[float] = []
    for t in raw:
        t = max(0.0, min(duration, t))
        key = round(t, 2)
        if key in seen:
            continue
        seen.add(key); out.append(t)
        if len(out) >= VIDEO_MAX_SAMPLES:
            break
    return out


def compute_video_signature(path: str) -> Dict[str, Any]:
    if not HAS_OPENCV:
        return dict(_VIDEO_EMPTY)
    try:
        cap = cv2.VideoCapture(path)
    except Exception:
        return dict(_VIDEO_EMPTY)
    if not cap.isOpened():
        try: cap.release()
        except Exception: pass
        return dict(_VIDEO_EMPTY)
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = (frame_count / fps) if fps > 0 else 0.0
        try:
            fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
            codec = "".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00").strip()
        except Exception:
            codec = ""
        frames_sig: List[Dict[str, Any]] = []
        for t in _sample_times(duration, fps):
            try:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb)
                entry: Dict[str, Any] = {"t": round(float(t), 2)}
                entry.update(compute_hashes(img))
                entry.update(compute_lab_metrics(img))
                entry["hist_waveform"] = compute_hist_waveform(img).get("hist_waveform", [0.0] * WAVEFORM_KEEP)
                frames_sig.append(entry)
            except Exception:
                continue
        return {
            "video_duration": round(duration, 3), "video_fps": round(fps, 3),
            "video_frame_count": frame_count, "video_codec": codec,
            "video_width": width, "video_height": height, "video_sig": frames_sig,
        }
    except Exception:
        return dict(_VIDEO_EMPTY)
    finally:
        try: cap.release()
        except Exception: pass