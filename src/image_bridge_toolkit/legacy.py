"""Migration for image_cache.json files written before the current version.

Prior to 0.2.7, `compute_visual_signature` had x and y swapped in the emitted
nodes (a coordinate-convention bug, not a data-loss bug). Every visual_sig
written before that release has coordinates reflected across the main
diagonal, and every `ground_detected` value is unreliable because the ground
test filtered on the wrong axis.

This module:

  - `read_writer_version(cache)` — look up which tool version wrote a cache.
  - `migrate(cache)` — in-place correction: swap x/y, reflect the directional
    fields (grad_theta, axis_phi) by pi/2, drop the ground-detection fields
    so the next build recomputes them, and stamp the current version.
  - A standalone `imgcache-migrate` CLI that walks a tree and migrates every
    cache it finds, without touching any image bytes.

The migration is lossless for x, y, grad_theta, axis_phi. It drops
`ground_confidence` and `ground_detected` because recomputing them from the
selected nodes alone is not equivalent to recomputing them from the full
scaffold, which the migration does not have. The next `imgcache-build` run
recomputes the sig group and restores them.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

from .constants import (
    CACHE_FILE_NAME,
    META_KEY,
    META_VERSIONS_KEY,
    META_TOOL_ID,
)


# ======================================================================
# Version helpers
# ======================================================================

def _version_tuple(v: str):
    parts = []
    for p in str(v).split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _version_ge(a: str, b: str) -> bool:
    return _version_tuple(a) >= _version_tuple(b)


def current_version() -> str:
    """The version this installation of the toolkit stamps into caches."""
    from . import __version__
    return __version__


# ======================================================================
# Read / write meta
# ======================================================================

def read_writer_version(cache: dict) -> Optional[str]:
    """Return the `ibt` version recorded in the cache's meta block, or None."""
    if not isinstance(cache, dict):
        return None
    meta = cache.get(META_KEY)
    if not isinstance(meta, dict):
        return None
    versions = meta.get(META_VERSIONS_KEY)
    if not isinstance(versions, dict):
        return None
    v = versions.get(META_TOOL_ID)
    return v if isinstance(v, str) else None


def _stamp(cache: dict, version: str) -> dict:
    """Ensure the meta block records `version`, preserving any other tools'
    version entries. Returns the cache with `meta` moved to the front."""
    meta = cache.get(META_KEY)
    if not isinstance(meta, dict):
        meta = {}
    versions = meta.get(META_VERSIONS_KEY)
    if not isinstance(versions, dict):
        versions = {}
    versions[META_TOOL_ID] = version
    meta[META_VERSIONS_KEY] = versions

    # Rebuild with meta first.
    ordered = {META_KEY: meta}
    for k, v in cache.items():
        if k != META_KEY:
            ordered[k] = v
    return ordered


def stamp_meta(cache: dict, version: Optional[str] = None) -> dict:
    """Public: stamp the current version and return a dict with meta first.

    Called by cache_io.write_cache_atomic so every written cache carries the
    marker. Idempotent.
    """
    if version is None:
        version = current_version()
    return _stamp(cache, version)


# ======================================================================
# Node-level correction
# ======================================================================

def _reflect_direction(angle: float, period: float) -> float:
    """Reflect an angle under the (x,y) -> (y,x) swap.

    Under the swap, atan2(a, b) becomes atan2(b, a). Using the identity
        atan2(b, a) = pi/2 - atan2(a, b)   (mod period)
    the corrected angle is (pi/2 - angle) modulo the angle's natural period.
    """
    return (math.pi / 2.0 - angle) % period


def _fix_visual_sig_node(node: dict) -> None:
    """Correct one node in place: swap x/y and reflect the directional fields.

    Fields touched:
      x, y                — swapped
      grad_theta (0..2pi) — reflected
      axis_phi   (0..pi)  — reflected
    Untouched (invariant under a 90-degree reflection):
      size, hex, lab, grad_mag, elongation, primitive, density, curvature.
    """
    if "x" in node and "y" in node:
        node["x"], node["y"] = node["y"], node["x"]

    gt = node.get("grad_theta")
    if isinstance(gt, (int, float)):
        node["grad_theta"] = round(_reflect_direction(float(gt), 2 * math.pi), 4)

    ap = node.get("axis_phi")
    if isinstance(ap, (int, float)):
        node["axis_phi"] = round(_reflect_direction(float(ap), math.pi), 4)


# ======================================================================
# Cache-level migration
# ======================================================================

def is_current(cache: dict, target: Optional[str] = None) -> bool:
    """True if the cache carries a writer version >= target (default current)."""
    if target is None:
        target = current_version()
    v = read_writer_version(cache)
    return v is not None and _version_ge(v, target)


def migrate(cache: dict, target: Optional[str] = None) -> Tuple[dict, bool]:
    """Migrate a cache dict in place to `target` (default: current version).

    Returns (cache, changed). When changed is True the caller should write
    the cache back to disk.
    """
    if not isinstance(cache, dict):
        return cache, False

    if target is None:
        target = current_version()

    if is_current(cache, target):
        return cache, False

    writer = read_writer_version(cache) or "0.0.0"
    changed = False

    # The reflected-coordinate bug and the unreliable ground fields were
    # both introduced before 0.2.7 and fixed in it. Re-running the
    # reflection on a 0.2.7+ cache would double-correct every node, so
    # gate the structural migration on the *writer* version, not on the
    # current installation version.
    if not _version_ge(writer, "0.2.7"):
        for key in list(cache.keys()):
            if key == META_KEY:
                continue
            entry = cache.get(key)
            if not isinstance(entry, dict):
                continue

            vs = entry.get("visual_sig")
            if isinstance(vs, list):
                for node in vs:
                    if isinstance(node, dict):
                        _fix_visual_sig_node(node)
                changed = True

            # Ground detection depends on the old orientation. We cannot
            # safely recompute it from the selected nodes alone, so drop
            # it. The expanded sig group in constants.GROUP_KEYS means the
            # next build recomputes the full sig group from the image.
            for stale in ("ground_confidence", "ground_detected"):
                if stale in entry:
                    del entry[stale]
                    changed = True

    # 0.3.0 adds groups (exact / exif / video) but rewrites no existing
    # field. The planner backfills the new keys on the next build; the
    # only thing this function needs to do for 0.2.7 -> 0.3.0 is restamp
    # the version, done below.

    stamped = _stamp(cache, target)
    # Mutate the caller's dict in place (preserving identity).
    cache.clear()
    cache.update(stamped)
    changed = True

    return cache, changed


# ======================================================================
# CLI: imgcache-migrate
# ======================================================================

def _iter_cache_dirs(root: Path):
    for dir_path, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if CACHE_FILE_NAME in filenames:
            yield Path(dir_path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="imgcache-migrate",
        description="Migrate image_cache.json files to the current schema.",
    )
    parser.add_argument("root", help="Root directory to scan (recursively).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing.")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2

    # Import here to avoid a circular import at module load.
    from .cache_io import load_cache, write_cache_atomic

    total = 0
    already = 0
    migrated = 0

    for dir_path in _iter_cache_dirs(root):
        cache = load_cache(dir_path)
        if not cache:
            continue
        total += 1

        if is_current(cache):
            already += 1
            continue

        new_cache, changed = migrate(cache)
        if changed:
            migrated += 1
            prefix = "[dry] " if args.dry_run else ""
            print(f"{prefix}migrated {dir_path}")
            if not args.dry_run:
                write_cache_atomic(dir_path, new_cache)

    print()
    print(f"{total} cache(s) found — {already} already current, {migrated} migrated.")
    if args.dry_run:
        print("(dry run — nothing was written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
