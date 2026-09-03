# filename: src/image_bridge_toolkit/extended_topology.py

"""
Extended topology engine for curvature-aware image signatures.

Adds local curvature (κ) to the existing node signature, along with
aspect-ratio corrected NMS and conditional ground anchoring.
"""

import struct
import numpy as np
from typing import List, Dict, Any, Tuple

from .constants import (
    MAGIC_BYTE,
    MAX_NODES,
    MIN_NODES,
    NMS_RADIUS,
    SPATIAL_RADIUS,
    CURVATURE_MAX,
    GROUND_CONFIDENCE_THRESHOLD
)


class ExtendedTopologyEngine:
    """
    Produces a compact binary payload with up to 20 nodes, each containing
    position, colour, and 7‑parameter topological signature (including κ).
    """

    def __init__(
        self,
        min_nodes: int = MIN_NODES,
        max_nodes: int = MAX_NODES,
        nms_radius: float = NMS_RADIUS,
        spatial_radius: float = SPATIAL_RADIUS,
    ):
        self.min_nodes = min_nodes
        self.max_nodes = max_nodes
        self.nms_radius = nms_radius
        self.spatial_radius = spatial_radius

    def compute_curvature(
        self,
        pos: np.ndarray,
        all_positions: np.ndarray,
        axis_phi: float,
        grad_mag: float,
        aspect_ratio: float,
    ) -> float:
        """
        Compute curvature κ from local neighbourhood.

        Fits v = b·u + a·u² in local (u, v) coordinates.
        κ = |2·a|.
        """
        if grad_mag < 5.0:
            return 0.0

        rel = all_positions - pos
        rel_corr = rel.copy()
        rel_corr[:, 0] *= aspect_ratio

        dists = np.linalg.norm(rel_corr, axis=1)
        neighbours = np.where((dists > 0.01) & (dists <= self.spatial_radius))[0]

        if len(neighbours) < 4:
            return 0.0

        u_axis = np.array([np.cos(axis_phi), np.sin(axis_phi)])
        v_axis = np.array([-np.sin(axis_phi), np.cos(axis_phi)])

        rel_u = rel[neighbours] @ u_axis
        rel_v = rel[neighbours] @ v_axis

        if not (np.any(rel_u > 0.01) and np.any(rel_u < -0.01)):
            return 0.0

        u2 = rel_u ** 2
        u3 = rel_u ** 3
        u4 = rel_u ** 4

        A = np.array([
            [np.sum(u2), np.sum(u3)],
            [np.sum(u3), np.sum(u4)]
        ])
        b_vec = np.array([np.sum(rel_v * rel_u), np.sum(rel_v * u2)])

        if np.linalg.cond(A) > 1e6:
            return 0.0

        b, a = np.linalg.solve(A, b_vec)
        kappa = abs(2.0 * a)

        return float(round(min(kappa, CURVATURE_MAX), 3))

    def evaluate_ground_confidence(
        self,
        candidates: List[Dict],
        topo_list: List[Dict]
    ) -> Tuple[float, List[int]]:
        """Return ground confidence and indices of ground candidates."""
        bottom_idxs = [i for i, c in enumerate(candidates) if c.get("y", 0.0) >= 0.65]
        if not bottom_idxs:
            return 0.0, []

        valid_ground_idxs = []
        for i in bottom_idxs:
            t = topo_list[i]
            phi = t.get("axis_phi", 0.0)
            is_horizontal = (abs(phi) < 0.40) or (abs(phi - np.pi) < 0.40)
            is_real_content = (t.get("grad_magnitude", 0.0) > 2.0) and (t.get("density", 1.0) < 0.95)

            if (t.get("elongation", 0.0) > 0.35) and is_horizontal and is_real_content:
                valid_ground_idxs.append(i)

        if not valid_ground_idxs:
            return 0.0, []

        xs = [candidates[i]["x"] for i in valid_ground_idxs]
        span = max(xs) - min(xs) if len(xs) > 1 else 0.0

        confidence = 1.0 / (1.0 + np.exp(-15.0 * (span - 0.55)))
        return float(confidence), valid_ground_idxs

    def enforce_nms(
        self,
        scored_candidates: List[Tuple[float, int]],
        raw_candidates: List[Dict],
        target_count: int,
        aspect_ratio: float,
    ) -> List[int]:
        """Aspect-ratio corrected Non-Maximum Suppression."""
        selected = []

        for score, idx in scored_candidates:
            if len(selected) >= target_count:
                break

            c_pos = np.array([raw_candidates[idx]["x"], raw_candidates[idx]["y"]])

            too_close = False
            for sel_idx in selected:
                s_pos = np.array([raw_candidates[sel_idx]["x"], raw_candidates[sel_idx]["y"]])
                delta = c_pos - s_pos
                delta[0] *= aspect_ratio
                dist = np.linalg.norm(delta)

                if dist < self.nms_radius:
                    too_close = True
                    break

            if not too_close:
                selected.append(idx)

        if len(selected) < self.min_nodes:
            remaining = [idx for _, idx in scored_candidates if idx not in selected]
            needed = self.min_nodes - len(selected)
            selected.extend(remaining[:needed])

        return selected

    def process_and_pack_payload(
        self,
        raw_candidates: List[Dict],
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> Dict[str, Any]:
        """
        Full pipeline: extraction → scoring → NMS → ground injection → packing.
        Returns human‑readable nodes and binary payload.
        """
        aspect_ratio = float(image_width) / float(image_height)
        all_pos = np.array([[c.get("x", 0.0), c.get("y", 0.0)] for c in raw_candidates])
        topo_metrics = []

        for i, cand in enumerate(raw_candidates):
            axis_phi = cand.get("axis_phi", 0.0)
            grad_mag = cand.get("grad_magnitude", 0.0)

            kappa = self.compute_curvature(
                all_pos[i],
                all_pos,
                axis_phi,
                grad_mag,
                aspect_ratio,
            )

            topo_metrics.append({
                "grad_theta": cand.get("grad_theta", 0.0),
                "grad_magnitude": grad_mag,
                "elongation": cand.get("elongation", 0.0),
                "axis_phi": axis_phi,
                "primitive": int(cand.get("primitive", 0)),
                "density": cand.get("density", 0.0),
                "curvature": kappa,
            })

        # Scoring
        scored = []
        for i, cand in enumerate(raw_candidates):
            t = topo_metrics[i]
            struct_weight = (t["elongation"] * 1.5) + (t["curvature"] * 2.0)
            score = (cand.get("size", 0.1) * 0.3) + (struct_weight * 0.7)
            scored.append((score, i))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Dynamic budget
        high_struct = sum(1 for s in scored if s[0] > 0.30)
        target_budget = int(np.clip(high_struct + 6, self.min_nodes, self.max_nodes))

        # NMS
        selected = self.enforce_nms(scored, raw_candidates, target_budget, aspect_ratio)

        # Conditional ground injection
        ground_conf, ground_idxs = self.evaluate_ground_confidence(raw_candidates, topo_metrics)
        if ground_conf > GROUND_CONFIDENCE_THRESHOLD:
            has_ground = any(raw_candidates[idx].get("y", 0.0) >= 0.65 for idx in selected)
            if not has_ground and ground_idxs:
                anchor_idx = min(ground_idxs, key=lambda idx: abs(raw_candidates[idx].get("x", 0.5) - 0.5))
                if len(selected) >= self.max_nodes:
                    selected.pop(-1)
                selected.append(anchor_idx)

        selected = sorted(selected)

        # Pack payload (17 bytes per node)
        nodes_payload = []
        binary_buffer = bytearray()

        # Header: [Magic][Count][GroundConf][AspectRatio float16]
        binary_buffer.extend(struct.pack(
            ">BBB",
            MAGIC_BYTE,
            len(selected),
            int(np.clip(ground_conf * 255, 0, 255))
        ))
        binary_buffer.extend(np.float16(aspect_ratio).tobytes())

        for idx in selected:
            c = raw_candidates[idx]
            t = topo_metrics[idx]

            # Human‑readable node
            lab = c.get("lab", [50.0, 0.0, 0.0])
            nodes_payload.append({
                "x": round(c.get("x", 0.0), 3),
                "y": round(c.get("y", 0.0), 3),
                "size": round(c.get("size", 0.0), 3),
                "lab": [round(lab[0], 1), round(lab[1], 1), round(lab[2], 1)],
                "topo": [
                    round(t["grad_theta"], 2),
                    round(t["grad_magnitude"], 1),
                    round(t["elongation"], 2),
                    round(t["axis_phi"], 2),
                    t["primitive"],
                    round(t["density"], 2),
                    t["curvature"],
                ],
            })

            # 17 bytes: x(H), y(H), size(B), L*(B), a*(B), b*(B),
            # grad_theta(B), grad_mag(H), elong(B), axis_phi(B), prim(B), dens(B), kappa(H)
            node_bytes = struct.pack(
                ">HHBBBBBHHBBBBH",
                int(np.clip(c.get("x", 0.0) * 65535, 0, 65535)),
                int(np.clip(c.get("y", 0.0) * 65535, 0, 65535)),
                int(np.clip(c.get("size", 0.0) * 255, 0, 255)),
                int(np.clip(lab[0] * 255 / 100.0, 0, 255)),
                int(np.clip((lab[1] + 128) * 255 / 255.0, 0, 255)),
                int(np.clip((lab[2] + 128) * 255 / 255.0, 0, 255)),
                int(np.clip((t["grad_theta"] / (2 * np.pi)) * 255, 0, 255)),
                int(np.clip(t["grad_magnitude"] * 10, 0, 65535)),
                int(np.clip(t["elongation"] * 255, 0, 255)),
                int(np.clip((t["axis_phi"] / np.pi) * 255, 0, 255)),
                int(t["primitive"]),
                int(np.clip(t["density"] * 255, 0, 255)),
                int(np.clip((t["curvature"] / CURVATURE_MAX) * 65535, 0, 65535)),
            )
            binary_buffer.extend(node_bytes)

        return {
            "node_count": len(nodes_payload),
            "ground_confidence": round(ground_conf, 2),
            "bytes_size": len(binary_buffer),
            "binary_payload": bytes(binary_buffer),
            "nodes": nodes_payload,
        }