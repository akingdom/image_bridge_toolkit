#!/usr/bin/env python3
"""
imgcache-build: production entry point for (re)generating image_cache.json
files across a directory tree.

Pipeline:
  1. scanner.scan_tree      -> fast, single-pass directory discovery + mtimes
  2. cache_io.load_cache    -> per-directory cache load (corruption-safe)
  3. planner.plan_entry     -> per-file: which calc groups actually need work
  4. worker processes       -> run only the needed calculators, in parallel
  5. cache_io.write_cache_atomic -> durable, corruption-proof write back
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Tuple

from PIL import Image

from . import calculators
from .cache_io import load_cache, write_cache_atomic
from .constants import ALL_GROUPS
from .logging_utils import get_logger
from .planner import plan_entry
from .scanner import scan_tree

log = get_logger(__name__)

# What a worker process needs to do one file, and what it hands back.
_Job = Tuple[str, FrozenSet[str]]  # (image_path, groups_to_compute)


def _compute_groups(image_path: str, groups: FrozenSet[str]) -> Dict[str, Any]:
    """Runs in a worker process. Never raises -- an unreadable/corrupt image
    yields an empty update rather than killing the whole batch."""
    if not groups:
        return {}
    try:
        with Image.open(image_path) as img:
            img.load()
            out: Dict[str, Any] = {}
            if "hash" in groups:
                out.update(calculators.compute_hashes(img))
            if "sig" in groups:
                out.update(calculators.compute_visual_signature(img))
            if "lab" in groups:
                out.update(calculators.compute_lab_metrics(img))
            if "waveform" in groups:
                out.update(calculators.compute_hist_waveform(img))
            return out
    except Exception as exc:
        log.error("Failed to process %s: %s", image_path, exc)
        return {}


def _worker(job: _Job) -> Tuple[str, Dict[str, Any]]:
    path, groups = job
    return path, _compute_groups(path, groups)


class ImageCacheBuilder:
    def __init__(self, root_dir: str, workers: int = 0, force: bool = False):
        self.root_dir = Path(root_dir).resolve()
        self.workers = workers or max(1, os.cpu_count() or 1)
        self.force = force

    def run(self) -> Dict[str, int]:
        stats = {"dirs": 0, "files_seen": 0, "files_updated": 0, "files_reused": 0}
        t0 = time.time()

        for dir_path, images in scan_tree(self.root_dir):
            stats["dirs"] += 1
            cache = {} if self.force else load_cache(dir_path)
            new_cache: Dict[str, Any] = {}
            jobs: List[_Job] = []
            job_meta: Dict[str, Dict[str, Any]] = {}

            for img_path, mtime in images:
                stats["files_seen"] += 1
                fname = img_path.name
                needed, base_entry = plan_entry(None if self.force else cache.get(fname), mtime)
                if not needed:
                    new_cache[fname] = base_entry
                    stats["files_reused"] += 1
                    continue
                jobs.append((str(img_path), needed))
                job_meta[str(img_path)] = {"fname": fname, "base": base_entry}

            if jobs:
                new_cache.update(self._run_jobs(jobs, job_meta))
                stats["files_updated"] += len(jobs)

            # Only touch disk if something actually changed for this dir.
            if jobs or len(new_cache) != len(cache) or self.force:
                write_cache_atomic(dir_path, new_cache)

            log.info("%s: %d image(s), %d updated", dir_path, len(images), len(jobs))

        log.info(
            "Done in %.1fs - dirs=%d files_seen=%d updated=%d reused=%d",
            time.time() - t0, stats["dirs"], stats["files_seen"],
            stats["files_updated"], stats["files_reused"],
        )
        return stats

    def _run_jobs(self, jobs: List[_Job], job_meta: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        if self.workers <= 1 or len(jobs) < 4:
            for path, groups in jobs:
                meta = job_meta[path]
                entry = dict(meta["base"])
                entry.update(_compute_groups(path, groups))
                results[meta["fname"]] = entry
            return results

        with ProcessPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(_worker, job): job[0] for job in jobs}
            for fut in as_completed(futures):
                path = futures[fut]
                meta = job_meta[path]
                try:
                    _, computed = fut.result()
                except Exception as exc:
                    log.error("Worker failed for %s: %s", path, exc)
                    computed = {}
                entry = dict(meta["base"])
                entry.update(computed)
                results[meta["fname"]] = entry
        return results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="imgcache-build", description="Build/update image_cache.json across a directory tree.")
    p.add_argument("root", help="Root directory to scan (recursively).")
    p.add_argument("-w", "--workers", type=int, default=0, help="Process pool size (default: CPU count).")
    p.add_argument("--force", action="store_true", help="Ignore existing caches and recompute everything.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: List[str] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verbose:
        get_logger(__name__, verbose=True)
    root = Path(args.root)
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2
    builder = ImageCacheBuilder(str(root), workers=args.workers, force=args.force)
    builder.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
