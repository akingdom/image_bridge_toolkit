"""
imgmatch-bridge: cache-backed multi-tier matcher that builds a bridge map
from a (watermarked / derivative) dataset to a much larger originals dataset.

Tiers, cheapest -> most expensive, each narrowing the candidate pool:
  1. Dual pHash (luma+color) Hamming distance -- vectorised over the whole
     originals array, this is what makes 1,000 x 50,000 tractable at all.
  2. DCT histogram waveform distance (within Tier-1 survivors).
  3. LAB perceptual colour distance.
  4. Visual-signature spatial/colour layout distance (watermark-robust).
  5. Optional direct pixel SSIM re-ranking of the surviving near-ties.

Output is streamed to disk as JSON Lines (one query result per line) so a
50k x 1k run is resumable and never holds the whole result set in memory,
and a crash partway through doesn't lose completed work.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Set

import numpy as np

from . import __version__
from .dataset_loader import Dataset, load_dataset
from .logging_utils import get_logger
from .similarity import direct_ssim, hamming_matrix, visual_sig_distance

log = get_logger(__name__)


# Populated once per worker process via ProcessPoolExecutor(initializer=...)
# so the (potentially large, 50k-entry) datasets are pickled to each worker
# exactly once, not re-serialised on every one of the 1,000 submitted tasks.
_WORKER_DA: Dataset = None
_WORKER_ORIG: Dataset = None
_WORKER_ARGS: Dict[str, Any] = {}


def _init_worker(da: Dataset, orig: Dataset, max_hamming: int, candidate_threshold: float) -> None:
    global _WORKER_DA, _WORKER_ORIG, _WORKER_ARGS
    _WORKER_DA, _WORKER_ORIG = da, orig
    _WORKER_ARGS = {"max_hamming": max_hamming, "candidate_threshold": candidate_threshold}


def _worker_match(q_idx: int) -> Dict[str, Any]:
    return match_one(q_idx, _WORKER_DA, _WORKER_ORIG, **_WORKER_ARGS)


def _already_done(output_path: Path) -> Set[str]:
    done: Set[str] = set()
    if not output_path.exists():
        return done
    with open(output_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["query_path"])
            except Exception:
                continue
    return done

def compute_similarity(
    q_luma: int,
    q_color: int,
    q_wf: np.ndarray,
    q_lab: np.ndarray,
    q_sig: List[Dict[str, float]],
    t_luma: int,
    t_color: int,
    t_wf: np.ndarray,
    t_lab: np.ndarray,
    t_sig: List[Dict[str, float]],
) -> Tuple[float, Dict[str, float]]:
    """
    Compute a composite similarity score and return detailed metrics.
    Returns (confidence, metrics) where metrics contains raw distances.
    """
    # 1. Hamming
    h_luma = bin(q_luma ^ t_luma).count('1')
    h_color = bin(q_color ^ t_color).count('1')
    h_combined = 0.7 * (h_luma / 64.0) + 0.3 * (h_color / 64.0)  # normalised 0-1

    # 2. Waveform
    wf_dist = float(np.linalg.norm(q_wf - t_wf))

    # 3. LAB
    lab_dist = float(np.linalg.norm(q_lab - t_lab) / 100.0)

    # 4. Signature
    sig_dist = visual_sig_distance(q_sig, t_sig) if q_sig and t_sig else 0.0

    # 5. Similarity scores (clipped)
    s_h = max(0.0, 1.0 - (h_combined * 20.0))   # h_combined in [0,1], scale to [0,20] bits
    s_w = max(0.0, 1.0 - (wf_dist / 0.5))
    s_l = max(0.0, 1.0 - lab_dist)
    s_sig = max(0.0, 1.0 - sig_dist)

    confidence = 0.35 * s_h + 0.35 * s_w + 0.15 * s_l + 0.15 * s_sig

    metrics = {
        "hamming_luma": h_luma,
        "hamming_color": h_color,
        "waveform_dist": wf_dist,
        "lab_dist": lab_dist,
        "sig_dist": sig_dist,
        "s_h": s_h,
        "s_w": s_w,
        "s_l": s_l,
        "s_sig": s_sig,
    }
    return confidence, metrics

def match_one(
    q_idx: int,
    da: Dataset,
    orig: Dataset,
    max_hamming: int,
    candidate_threshold: float,
) -> Dict[str, Any]:
    q_path = da.paths[q_idx]
    q_luma = int(da.phash_luma[q_idx])
    q_color = int(da.phash_color[q_idx])
    q_wf = da.waveform[q_idx]
    q_lab = da.lab[q_idx]
    q_sig = da.signatures[q_idx]

    dist_luma = hamming_matrix(q_luma, orig.phash_luma)
    dist_color = hamming_matrix(q_color, orig.phash_color)
    hamming_combined = 0.7 * dist_luma + 0.3 * dist_color

    cand_indices = np.where(dist_luma <= max_hamming)[0]
    if len(cand_indices) == 0:
        return {"query_path": q_path, "top_matches": []}

    wf_dists = np.linalg.norm(orig.waveform[cand_indices] - q_wf, axis=1)
    lab_dists = np.linalg.norm(orig.lab[cand_indices] - q_lab, axis=1) / 100.0

    scores = []
    for pos, orig_idx in enumerate(cand_indices):
        confidence, metrics = compute_similarity(
            q_luma, q_color, q_wf, q_lab, q_sig,
            int(orig.phash_luma[orig_idx]), int(orig.phash_color[orig_idx]),
            orig.waveform[orig_idx], orig.lab[orig_idx],
            orig.signatures[orig_idx]
        )
        scores.append({
            "orig_idx": int(orig_idx),
            "match_path": orig.paths[orig_idx],
            "confidence": confidence,
            "metrics": metrics,
        })

    scores.sort(key=lambda c: c["confidence"], reverse=True)
    best = scores[0]["confidence"]
    eligible = [c for c in scores if (best - c["confidence"]) <= candidate_threshold]
    return {"query_path": q_path, "top_matches": eligible, "_q_idx": q_idx}


def _finalize(result: Dict[str, Any], da: Dataset, direct_compare: bool) -> Dict[str, Any]:
    q_idx = result.pop("_q_idx", None)
    if direct_compare and result["top_matches"] and q_idx is not None:
        q_path = da.paths[q_idx]
        for c in result["top_matches"]:
            ssim_val = direct_ssim(q_path, c["match_path"])
            c["metrics"]["ssim"] = round(ssim_val, 4)
            c["confidence"] = round(0.5 * c["confidence"] + 0.5 * ssim_val, 4)
        result["top_matches"].sort(key=lambda c: c["confidence"], reverse=True)
    else:
        for c in result["top_matches"]:
            c["confidence"] = round(c["confidence"], 4)
    for c in result["top_matches"]:
        c.pop("orig_idx", None)
    return result


def run_match(
    da_root: str,
    orig_root: str,
    output_path: str,
    max_hamming: int = 16,
    candidate_threshold: float = 0.03,
    do_direct_compare: bool = False,
    workers: int = 0,
    resume: bool = True,
) -> None:
    da = load_dataset(Path(da_root))
    orig = load_dataset(Path(orig_root))
    log.info("Loaded %d derivative images, %d original images", len(da), len(orig))

    if len(da) == 0 or len(orig) == 0:
        log.warning("One of the datasets is empty (no image_cache.json entries found) - nothing to do.")
        Path(output_path).touch()
        return

    out_path = Path(output_path)
    done = _already_done(out_path) if resume else set()
    if done:
        log.info("Resuming: %d/%d already completed", len(done), len(da))

    pending = [i for i in range(len(da)) if da.paths[i] not in done]
    t0 = time.time()

    with open(out_path, "a", encoding="utf-8") as out_f:
        if workers and workers > 1 and not do_direct_compare:
            # Direct-compare (SSIM) reads image bytes and is worth parallelising
            # separately; the vectorised numpy stages below are already fast
            # enough single-threaded and cheaper to run in-process.
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_init_worker,
                initargs=(da, orig, max_hamming, candidate_threshold),
            ) as pool:
                futures = {pool.submit(_worker_match, i): i for i in pending}
                for n, fut in enumerate(as_completed(futures), 1):
                    result = _finalize(fut.result(), da, do_direct_compare)
                    out_f.write(json.dumps(result) + "\n")
                    out_f.flush()
                    if n % 50 == 0:
                        log.info("Matched %d/%d", n, len(pending))
        else:
            for n, i in enumerate(pending, 1):
                result = match_one(i, da, orig, max_hamming, candidate_threshold)
                result = _finalize(result, da, do_direct_compare)
                out_f.write(json.dumps(result) + "\n")
                out_f.flush()
                if n % 50 == 0:
                    log.info("Matched %d/%d", n, len(pending))

    log.info("Done in %.1fs -> %s", time.time() - t0, out_path)

def compute_topological_similarity(q_sigs: List[Dict], i_sigs: List[Dict]) -> float:
    """
    Experimental: similarity using topology fields (curvature, elongation, gradient)
    in addition to position and colour.

    Greedy per‑node matching: each query node picks its best match among index nodes.
    Returns 0.0–1.0, higher = more similar.
    """
    if not q_sigs or not i_sigs:
        return 0.0

    matched_scores = []
    for q in q_sigs:
        best_score = 0.0
        q_pos = np.array([q["x"], q["y"]])
        for idx_node in i_sigs:
            i_pos = np.array([idx_node["x"], idx_node["y"]])
            # spatial distance (normalised)
            dist = np.linalg.norm(q_pos - i_pos) / 141.42  # ~ sqrt(2)*100
            if dist > 0.20:
                continue
            spatial_sim = max(0.0, 1.0 - (dist / 0.20))

            # curvature similarity (κ)
            kappa_diff = abs(q.get("curvature", 0.0) - idx_node.get("curvature", 0.0))
            kappa_sim = max(0.0, 1.0 - (kappa_diff / 2.5))

            # elongation similarity
            elong_diff = abs(q.get("elongation", 0.0) - idx_node.get("elongation", 0.0))
            elong_sim = max(0.0, 1.0 - elong_diff)

            # orientation similarity (axis_phi)
            phi_diff = abs(q.get("axis_phi", 0.0) - idx_node.get("axis_phi", 0.0))
            phi_sim = np.cos(phi_diff) ** 2

            # Combine
            node_score = (spatial_sim * 0.25) + (kappa_sim * 0.30) + (elong_sim * 0.25) + (phi_sim * 0.20)
            best_score = max(best_score, node_score)
        matched_scores.append(best_score)

    return float(np.mean(matched_scores)) if matched_scores else 0.0

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="imgmatch-bridge", description="Build a confidence-scored bridge map between two cached image datasets.")
    p.add_argument("da_root", help="Root of the derivative/watermarked dataset (e.g. DeviantArt scrape).")
    p.add_argument("orig_root", help="Root of the originals dataset.")
    p.add_argument("-o", "--output", default="bridge_map.jsonl", help="Output JSONL path (default: bridge_map.jsonl).")
    p.add_argument("--max-hamming", type=int, default=16, help="Tier-1 pHash-luma Hamming cutoff (default: 16).")
    p.add_argument("--candidate-threshold", type=float, default=0.03, help="Keep candidates within this confidence gap of the best match (default: 0.03).")
    p.add_argument("--direct-compare", action="store_true", help="Run Tier-5 pixel SSIM re-ranking on surviving candidates (slow).")
    p.add_argument("-w", "--workers", type=int, default=0, help="Process pool size for per-query matching (default: single process).")
    p.add_argument("--no-resume", action="store_true", help="Ignore/overwrite any existing output file instead of resuming.")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"image-bridge-toolkit {__version__}")
    return p


def main(argv: List[str] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verbose:
        get_logger(__name__, verbose=True)

    if args.no_resume and Path(args.output).exists():
        Path(args.output).unlink()

    for root_name, root in (("da_root", args.da_root), ("orig_root", args.orig_root)):
        if not Path(root).is_dir():
            print(f"Not a directory: {root} ({root_name})", file=sys.stderr)
            return 2

    run_match(
        args.da_root, args.orig_root, args.output,
        max_hamming=args.max_hamming,
        candidate_threshold=args.candidate_threshold,
        do_direct_compare=args.direct_compare,
        workers=args.workers,
        resume=not args.no_resume,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
