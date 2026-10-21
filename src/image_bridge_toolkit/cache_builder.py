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

from . import calculators, legacy, __version__
from .legacy import read_writer_version
from .cache_io import load_cache, write_cache_atomic
from .constants import ALL_GROUPS, VIDEO_EXTENSIONS
from .logging_utils import get_logger
from .planner import plan_entry
from .scanner import scan_tree

log = get_logger(__name__)

# What a worker process needs to do one file, and what it hands back.
_Job = Tuple[str, FrozenSet[str]]  # (image_path, groups_to_compute)


def _compute_groups(image_path: str, groups: FrozenSet[str]) -> Dict[str, Any]:
    """Runs in a worker process. Never raises.

    Dispatches on file extension so image-only calculators never touch a
    video container, and file-level groups (exact hashes, EXIF) still run
    even when the image decoder fails -- a file that cannot be decoded can
    still have a valid hash, which is itself a useful fact to record.
    """
    if not groups:
        return {}

    out: Dict[str, Any] = {}
    suffix = os.path.splitext(image_path)[1].lower()
    is_video = suffix in VIDEO_EXTENSIONS

    # File-level groups -- no decoding required.
    if "exact" in groups:
        out.update(calculators.compute_exact_hashes(image_path))
    if "exif" in groups:
        out.update(calculators.compute_exif(image_path))

    if is_video:
        if "video" in groups:
            out.update(calculators.compute_video_signature(image_path))
        return out

    # Image-based groups. A decoder failure here is logged but does not
    # discard the exact hash / EXIF values already collected above.
    if any(g in groups for g in ("hash", "sig", "lab", "waveform")):
        try:
            with Image.open(image_path) as img:
                img.load()
                if "hash" in groups:
                    out.update(calculators.compute_hashes(img))
                if "sig" in groups:
                    out.update(calculators.compute_visual_signature(img))
                if "lab" in groups:
                    out.update(calculators.compute_lab_metrics(img))
                if "waveform" in groups:
                    out.update(calculators.compute_hist_waveform(img))
        except Exception as exc:
            log.error("Failed to process %s: %s", image_path, exc)
    return out


def _looks_like_modern_cache(cache: Dict[str, Any]) -> bool:
    """True if the cache carries IBT post-0.3.0 fields but no version stamp.

    Distinguishes "cache predates meta tracking" (safe to re-migrate fully)
    from "foreign writer stripped meta.versions" (cache is almost certainly
    0.3.x: recompute sig only, and skip the pre-0.2.7 coordinate reflection,
    which would corrupt a modern cache on decode failure). Requires md5
    *and* sha256 together, since a writer that strips one may strip both,
    and a 0.2.x cache has neither.
    """
    for key, entry in cache.items():
        if key == legacy.META_KEY or not isinstance(entry, dict):
            continue
        if "md5" in entry and "sha256" in entry:
            return True
    return False


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

            # Read the writer version BEFORE migrate() restamps it. This
            # is what lets the planner distinguish a cache whose sig
            # group was computed by an older algorithm from one whose
            # sig group is current.
            writer_version = read_writer_version(cache) if cache else None

            # Migrate a cache written by an older version of this toolkit.
            # `migrated` forces a write even if no images were recomputed.
            # If the cache has IBT fields but no version stamp, a foreign
            # writer is stripping meta.versions. Treat as 0.3.0: recompute
            # sig only, skip the pre-0.2.7 reflection (which would corrupt
            # a modern cache), skip exact/exif/hash/lab/waveform.
            if cache and writer_version is None and _looks_like_modern_cache(cache):
                log.warning(
                    "%s: IBT fields present but meta.versions.ibt is missing; "
                    "recomputing sig only (conservatively assuming 0.3.0). "
                    "A foreign writer is stripping meta.versions - until "
                    "that writer is fixed, this will recur on every run.",
                    dir_path,
                )
                writer_version = "0.3.0"
                cache, migrated = cache, False
            else:
                cache, migrated = legacy.migrate(cache)
            if migrated:
                log.info("%s: migrated cache from %s → %s", dir_path,
                         writer_version or "unversioned", __version__)

            new_cache: Dict[str, Any] = {}
            jobs: List[_Job] = []
            job_meta: Dict[str, Dict[str, Any]] = {}

            for img_path, mtime in images:
                stats["files_seen"] += 1
                fname = img_path.name
                suffix = img_path.suffix.lower()
                needed, base_entry = plan_entry(
                    None if self.force else cache.get(fname),
                    mtime, suffix, writer_version,
                )
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
            # The comparison ignores the top-level "meta" key (added by
            # write_cache_atomic, not part of the image-entry set).
            old_image_count = sum(1 for k in cache.keys() if k != legacy.META_KEY)
            new_image_count = len(new_cache)
            if (jobs
                    or migrated
                    or new_image_count != old_image_count
                    or self.force):
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
    p.add_argument("--version", action="version", version=f"image-bridge-toolkit {__version__}")
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
