# filename: src/image_bridge_toolkit/extended_matcher.py

"""
Query‑side matcher for extended topology payloads.
"""

import struct
import numpy as np
from typing import List, Dict, Any

from .constants import MAGIC_BYTE, CURVATURE_MAX


def unpack_payload(binary_data: bytes) -> Dict[str, Any]:
    """
    Unpack quantised binary payload to normalised vectors.
    """
    magic, count, ground_conf_u8 = struct.unpack(">BBB", binary_data[:3])
    if magic != MAGIC_BYTE:
        raise ValueError(f"Invalid magic byte: expected {MAGIC_BYTE:#x}, got {magic:#x}")

    aspect_ratio = float(np.frombuffer(binary_data[3:5], dtype=np.float16)[0])
    nodes = []

    offset = 5
    for _ in range(count):
        (
            x_u16,
            y_u16,
            size_u8,
            l_u8,
            a_u8,
            b_u8,
            theta_u8,
            mag_u16,
            elong_u8,
            phi_u8,
            prim_u8,
            dens_u8,
            kappa_u16,
        ) = struct.unpack(">HHBBBBBHHBBBBH", binary_data[offset:offset + 17])

        nodes.append({
            "x": x_u16 / 65535.0,
            "y": y_u16 / 65535.0,
            "size": size_u8 / 255.0,
            "lab": [
                (l_u8 / 255.0) * 100.0,
                (a_u8 / 255.0) * 255.0 - 128.0,
                (b_u8 / 255.0) * 255.0 - 128.0,
            ],
            "grad_theta": (theta_u8 / 255.0) * (2 * np.pi),
            "grad_magnitude": mag_u16 / 10.0,
            "elongation": elong_u8 / 255.0,
            "axis_phi": (phi_u8 / 255.0) * np.pi,
            "primitive": prim_u8,
            "density": dens_u8 / 255.0,
            "curvature": (kappa_u16 / 65535.0) * CURVATURE_MAX,
        })
        offset += 17

    return {
        "aspect_ratio": aspect_ratio,
        "ground_confidence": ground_conf_u8 / 255.0,
        "nodes": nodes,
    }


def compute_topological_similarity(query_bytes: bytes, index_bytes: bytes) -> float:
    """
    Compute structural similarity between two binary payloads.

    Greedy per‑node matching: each query node picks its best match among index nodes.
    This maximises recall at low cost; a stricter correspondence‑preserving
    match should be done in a later stage.

    Returns 0.0–1.0, higher = more similar.
    """
    q_data = unpack_payload(query_bytes)
    i_data = unpack_payload(index_bytes)

    q_nodes = q_data["nodes"]
    i_nodes = i_data["nodes"]

    if not q_nodes or not i_nodes:
        return 0.0

    aspect_ratio = q_data["aspect_ratio"]
    matched_scores = []

    for q in q_nodes:
        best_score = 0.0
        q_pos = np.array([q["x"], q["y"]])

        for idx_node in i_nodes:
            i_pos = np.array([idx_node["x"], idx_node["y"]])

            delta = q_pos - i_pos
            delta[0] *= aspect_ratio
            dist = np.linalg.norm(delta)

            if dist > 0.20:
                continue

            spatial_sim = max(0.0, 1.0 - (dist / 0.20))
            kappa_sim = max(0.0, 1.0 - (abs(q["curvature"] - idx_node["curvature"]) / CURVATURE_MAX))
            phi_sim = np.cos(abs(q["axis_phi"] - idx_node["axis_phi"])) ** 2

            node_score = (spatial_sim * 0.4) + (kappa_sim * 0.4) + (phi_sim * 0.2)
            best_score = max(best_score, node_score)

        matched_scores.append(best_score)

    return float(np.mean(matched_scores)) if matched_scores else 0.0