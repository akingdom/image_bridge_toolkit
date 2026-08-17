"""Loading / writing image_cache.json with corruption tolerance and atomic writes."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from .constants import CACHE_FILE_NAME
from .logging_utils import get_logger

log = get_logger(__name__)


def load_cache(dir_path: Path) -> Dict[str, Any]:
    """Returns {} for a missing OR corrupt cache file. Corruption is logged,
    never raised -- a bad JSON file must not abort the whole run, and per the
    spec it is treated exactly like "no cache" (everything gets recomputed)."""
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


def write_cache_atomic(dir_path: Path, cache: Dict[str, Any]) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    cache_path = dir_path / CACHE_FILE_NAME
    fd, tmp_name = tempfile.mkstemp(prefix=f".{CACHE_FILE_NAME}.", suffix=".tmp", dir=str(dir_path))
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
