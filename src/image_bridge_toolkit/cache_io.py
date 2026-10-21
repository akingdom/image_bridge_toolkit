"""Loading / writing image_cache.json with corruption tolerance and atomic writes.

Every write stamps a `meta` block with the writer's version and places it as
the first key in the file, so a reader can decide whether the cache needs
migration before interpreting any entry. See legacy.py.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from .constants import CACHE_FILE_NAME
from .logging_utils import get_logger

log = get_logger(__name__)


def detect_cache_format(cache_data: dict) -> str:
    version = cache_data.get("version", "1.0")
    if isinstance(version, str) and version.startswith("2."):
        return "extended"
    return "standard"


def load_cache(dir_path: Path) -> Dict[str, Any]:
    """Returns {} for a missing OR corrupt cache file.

    Corruption is logged, never raised — a bad JSON file must not abort the
    whole run, and per the spec it is treated exactly like "no cache"
    (everything gets recomputed).
    """
    cache_path = dir_path / CACHE_FILE_NAME
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("cache root is not a JSON object")
        return data
    except Exception as exc:
        log.warning("Corrupt cache at %s (%s) - treating as empty", cache_path, exc)
        return {}


def _stamp_meta_first(cache: Dict[str, Any]) -> Dict[str, Any]:
    """Return a dict with a fresh `meta` block first.

    Uses a lazy import of `legacy` so that `cache_io` can be imported before
    `legacy` finishes loading. Circular-import-safe.
    """
    from . import legacy
    return legacy.stamp_meta(cache)


def write_cache_atomic(dir_path: Path, cache: Dict[str, Any]) -> None:
    """Write the cache atomically. Stamps `meta` and orders it first."""
    cache = _stamp_meta_first(cache)

    dir_path.mkdir(parents=True, exist_ok=True)
    cache_path = dir_path / CACHE_FILE_NAME
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{CACHE_FILE_NAME}.", suffix=".tmp", dir=str(dir_path)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, cache_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
