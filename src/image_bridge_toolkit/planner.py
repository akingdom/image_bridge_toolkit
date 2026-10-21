"""
Decides, per image, which calculation groups (if any) need to run.

Rule (this is the spec you gave, made explicit):
  - No cache entry at all                -> compute every applicable group.
  - Cache entry exists but mtime differs -> the file content changed, so
    all applicable groups are stale.
  - Cache entry exists and mtime matches, but the cache was written by a
    toolkit version older than GROUP_MIN_VERSION[group] for some group
    -> that group is stale even though its keys are present. This is what
    makes algorithm changes propagate without requiring --force.
  - Cache entry exists, mtime matches, writer version is current
    -> only groups whose keys are missing get (re)computed.

`writer_version` should be read from the cache meta BEFORE any migration
that restamps it. See cache_builder.ImageCacheBuilder.run().
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional, Tuple

from .constants import ALL_GROUPS, GROUP_KEYS, GROUP_MIN_VERSION, VIDEO_EXTENSIONS
from .legacy import _version_ge


def _groups_for_suffix(suffix: str) -> FrozenSet[str]:
    """All groups applicable to a file with the given lowercased suffix
    (including the leading dot). Video files get every group; image files
    get every group except "video"."""
    if suffix in VIDEO_EXTENSIONS:
        return ALL_GROUPS
    return frozenset(g for g in ALL_GROUPS if g != "video")


def plan_entry(
    cached_entry: Optional[Dict[str, Any]],
    mtime: int,
    suffix: str = "",
    writer_version: Optional[str] = None,
) -> Tuple[FrozenSet[str], Dict[str, Any]]:
    """Returns (groups_needing_compute, base_entry_to_extend).

    `suffix` selects which groups are applicable to this file.
    `writer_version` is the `ibt` version recorded in the cache's meta
    block, or None if unrecorded (a pre-0.2.7 cache). If None, every
    applicable group is treated as stale.
    """
    applicable = _groups_for_suffix(suffix)
    if not cached_entry or cached_entry.get("mtime") != mtime:
        return applicable, {"mtime": mtime}

    missing = frozenset(
        group for group, keys in GROUP_KEYS.items()
        if group in applicable
        and not all(k in cached_entry for k in keys)
    )

    if writer_version is None:
        stale = applicable
    else:
        stale = frozenset(
            group for group in applicable
            if (min_ver := GROUP_MIN_VERSION.get(group))
            and not _version_ge(writer_version, min_ver)
        )

    return missing | stale, dict(cached_entry)