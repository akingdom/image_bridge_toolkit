"""
Decides, per image, which calculation groups (if any) need to run.

Rule (this is the spec you gave, made explicit):
  - No cache entry at all                -> compute every group.
  - Cache entry exists but mtime differs -> the file content changed, so ALL
    previously-computed groups are stale and must be recomputed. (A naive
    "only fill in missing keys" approach would silently keep stale phash/lab/
    etc. values for a changed file just because the keys happen to already
    exist -- that's a correctness bug, not a caching optimisation, so it's
    deliberately not what this does.)
  - Cache entry exists and mtime matches -> only groups whose keys are
    missing get (re)computed; everything else is reused untouched. This is
    what lets you cheaply backfill a new field type across a huge existing
    cache without re-hashing every image.
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional, Tuple

from .constants import ALL_GROUPS, GROUP_KEYS


def plan_entry(cached_entry: Optional[Dict[str, Any]], mtime: int) -> Tuple[FrozenSet[str], Dict[str, Any]]:
    """Returns (groups_needing_compute, base_entry_to_extend)."""
    if not cached_entry or cached_entry.get("mtime") != mtime:
        return ALL_GROUPS, {"mtime": mtime}

    missing = frozenset(
        group for group, keys in GROUP_KEYS.items()
        if not all(k in cached_entry for k in keys)
    )
    return missing, dict(cached_entry)
