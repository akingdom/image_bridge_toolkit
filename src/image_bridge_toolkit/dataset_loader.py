#!/usr/bin/env python3
"""Loads all image_cache.json files under a root into flat numpy vector arrays
for the matcher, skipping .ignore_subdir trees exactly like the builder does."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from .cache_io import load_cache
from .constants import CACHE_FILE_NAME, IGNORE_MARKER
from .logging_utils import get_logger

log = get_logger(__name__)


class Dataset:
    __slots__ = ("paths", "phash_luma", "phash_color", "waveform", "lab", "signatures")

    def __init__(self, paths, phash_luma, phash_color, waveform, lab, signatures):
        self.paths = paths
        self.phash_luma = phash_luma
        self.phash_color = phash_color
        self.waveform = waveform
        self.lab = lab
        self.signatures = signatures

    def __len__(self) -> int:
        return len(self.paths)


def load_dataset(root_dir: Path) -> Dataset:
    paths: List[str] = []
    phashes_luma: List[int] = []
    phashes_color: List[int] = []
    waveforms: List[List[float]] = []
    labs: List[List[float]] = []
    signatures: List[List[Dict[str, Any]]] = []

    root_dir = Path(root_dir).resolve()
    stack = [root_dir]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError as exc:
            log.warning("Cannot read directory %s (%s) - skipping", current, exc)
            continue

        if any(e.name == IGNORE_MARKER and e.is_file(follow_symlinks=False) for e in entries):
            continue

        has_cache = any(e.name == CACHE_FILE_NAME for e in entries)
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                stack.append(Path(e.path))

        if not has_cache:
            continue

        cache = load_cache(current)
        for fname, meta in cache.items():
            try:
                record = (
                    str(current / fname),
                    int(meta.get("phash_luma", "0") or "0", 16),
                    int(meta.get("phash_color", meta.get("phash_luma", "0")) or "0", 16),
                    meta.get("hist_waveform", [0.0] * 16) or [0.0] * 16,
                    [meta.get("lab_value", 0.0), meta.get("lab_hue", 0.0),
                     meta.get("lab_chroma", 0.0), meta.get("lab_warmth", 0.0)],
                    meta.get("visual_sig", []) or [],
                )
            except (TypeError, ValueError) as exc:
                log.warning("Skipping malformed cache entry %s/%s: %s", current, fname, exc)
                continue
            path, ph_luma, ph_color, wf, lab, sig = record
            paths.append(path)
            phashes_luma.append(ph_luma)
            phashes_color.append(ph_color)
            waveforms.append(wf)
            labs.append(lab)
            signatures.append(sig)

    return Dataset(
        paths=paths,
        phash_luma=np.array(phashes_luma, dtype=np.uint64),
        phash_color=np.array(phashes_color, dtype=np.uint64),
        waveform=np.array(waveforms, dtype=np.float32) if waveforms else np.zeros((0, 16), dtype=np.float32),
        lab=np.array(labs, dtype=np.float32) if labs else np.zeros((0, 4), dtype=np.float32),
        signatures=signatures,
    )
