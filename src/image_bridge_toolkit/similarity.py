"""Distance / similarity primitives used by the multi-tier matcher."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from ._imaging_backend import HAS_OPENCV, cv2

if not HAS_OPENCV:
    from PIL import Image


def hamming_matrix(query_hash: int, dataset_hashes: np.ndarray) -> np.ndarray:
    """Vectorised popcount of (query XOR every hash in dataset_hashes)."""
    xor = np.bitwise_xor(dataset_hashes, np.uint64(query_hash))
    dist = np.zeros(len(dataset_hashes), dtype=np.int32)
    for b in range(64):
        dist += ((xor >> np.uint64(b)) & np.uint64(1)).astype(np.int32)
    return dist


def _hex_to_rgb(hex_str: str) -> np.ndarray:
    h = hex_str.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def visual_sig_distance(sig1: List[Dict[str, Any]], sig2: List[Dict[str, Any]]) -> float:
    """Spatial+colour layout discrepancy; border points (watermark zones,
    x/y outside 10-90%) are dropped so DeviantArt watermarks/frames don't
    dominate the score."""
    if not sig1 or not sig2:
        return 1.0

    p1 = [pt for pt in sig1 if 10.0 <= pt["x"] <= 90.0 and 10.0 <= pt["y"] <= 90.0] or sig1
    p2 = [pt for pt in sig2 if 10.0 <= pt["x"] <= 90.0 and 10.0 <= pt["y"] <= 90.0] or sig2

    rgb2 = np.array([_hex_to_rgb(pt["hex"]) for pt in p2])
    xy2 = np.array([[pt["x"], pt["y"]] for pt in p2], dtype=np.float32)

    total = 0.0
    for pt1 in p1:
        rgb1 = _hex_to_rgb(pt1["hex"])
        xy1 = np.array([pt1["x"], pt1["y"]], dtype=np.float32)
        c_dist = np.linalg.norm(rgb2 - rgb1, axis=1) / 441.67
        s_dist = np.linalg.norm(xy2 - xy1, axis=1) / 141.42
        combined = 0.6 * c_dist + 0.4 * s_dist
        total += float(np.min(combined))
    return total / len(p1)


def direct_ssim(img_path1: str, img_path2: str) -> float:
    """Optional, expensive pixel-level structural similarity for the final
    verification tier. Never raises - returns 0.0 (no similarity) on any
    read/decode failure so a bad file can't crash a batch run."""
    try:
        if HAS_OPENCV:
            i1 = cv2.imread(img_path1, cv2.IMREAD_GRAYSCALE)
            i2 = cv2.imread(img_path2, cv2.IMREAD_GRAYSCALE)
            if i1 is None or i2 is None:
                return 0.0
            i2 = cv2.resize(i2, (i1.shape[1], i1.shape[0]), interpolation=cv2.INTER_AREA)
            c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
            i1 = i1.astype(np.float64)
            i2 = i2.astype(np.float64)
            mu1 = cv2.GaussianBlur(i1, (11, 11), 1.5)
            mu2 = cv2.GaussianBlur(i2, (11, 11), 1.5)
            mu1_sq, mu2_sq, mu1_mu2 = mu1 ** 2, mu2 ** 2, mu1 * mu2
            sigma1_sq = cv2.GaussianBlur(i1 ** 2, (11, 11), 1.5) - mu1_sq
            sigma2_sq = cv2.GaussianBlur(i2 ** 2, (11, 11), 1.5) - mu2_sq
            sigma12 = cv2.GaussianBlur(i1 * i2, (11, 11), 1.5) - mu1_mu2
            ssim_map = ((2 * mu1_mu2 + c1) * (2 * sigma12 + c2)) / ((mu1_sq + mu2_sq + c1) * (sigma1_sq + sigma2_sq + c2))
            return float(np.mean(ssim_map))
        else:
            with Image.open(img_path1) as im1, Image.open(img_path2) as im2:
                g1 = np.array(im1.convert("L").resize((256, 256)), dtype=np.float64)
                g2 = np.array(im2.convert("L").resize((256, 256)), dtype=np.float64)
                mse = np.mean((g1 - g2) ** 2)
                return max(0.0, 1.0 - (mse / 65535.0))
    except Exception:
        return 0.0
