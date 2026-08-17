"""
Fast filesystem discovery.

Why this is faster than a plain os.walk + Path.stat() pass: os.scandir's
DirEntry caches the stat result from the readdir call on most platforms, so
entry.stat() below is (usually) free -- no extra stat(2) syscall per file the
way Path(f).stat() or os.stat(f) would cost. We grab mtime here, once, while
we're already iterating the directory to decide what's an image / a
subdirectory / the ignore marker, instead of doing a second full walk later
just to stat files for cache invalidation.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, List, Tuple

from .constants import IGNORE_MARKER, VALID_EXTENSIONS
from .logging_utils import get_logger

log = get_logger(__name__)

# (absolute path, mtime as int seconds)
ImageEntry = Tuple[Path, int]


def scan_tree(root_dir: Path) -> Iterator[Tuple[Path, List[ImageEntry]]]:
    """Yields (directory, [ (image_path, mtime), ... ]) for every directory
    under root_dir that contains at least one image, skipping any directory
    (and everything beneath it) that contains an IGNORE_MARKER file."""
    stack = [Path(root_dir)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError as exc:
            log.warning("Cannot read directory %s (%s) - skipping", current, exc)
            continue

        if any(e.name == IGNORE_MARKER and e.is_file(follow_symlinks=False) for e in entries):
            continue  # this directory and its subtree are opted out

        images: List[ImageEntry] = []
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                    continue
                suffix = os.path.splitext(entry.name)[1].lower()
                if suffix in VALID_EXTENSIONS:
                    mtime = int(entry.stat(follow_symlinks=False).st_mtime)
                    images.append((Path(entry.path), mtime))
            except OSError as exc:
                log.warning("Cannot stat %s (%s) - skipping", entry.path, exc)

        if images:
            yield current, images
