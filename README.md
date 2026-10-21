<p align="center">
  <img src="https://raw.githubusercontent.com/akingdom/image_bridge_toolkit/main/logo.png" alt="image-bridge-toolkit logo" width="600"/>
</p>

# image-bridge-toolkit

[![PyPI version](https://img.shields.io/pypi/v/image-bridge-toolkit.svg)](https://pypi.org/project/image-bridge-toolkit/)
[![License: MIT](https://img.shields.io/badge/Code_License-MIT-yellow.svg)](LICENSE)
[![License: CC BY-NC-ND 4.0](https://img.shields.io/badge/Docs_License-CC_BY--NC--ND_4.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc-nd/4.0/)

`image-bridge-toolkit` provides cache-backed perceptual image fingerprinting and cross-dataset bridge matching designed to handle large scale (50,000+ image) libraries. It finds identical or highly similar images across disparate folders while remaining resilient to scale shifts, aspect ratio changes, heavy compression, and intrusive watermarks.

---

## Key Features

- **Per-directory JSON caching.** Generates and maintains an `image_cache.json` inside each directory, keyed by filename and modification time (`mtime`).
- **Exact identity.** MD5, SHA-256, and byte size per file, computed in a single read pass. MD5 matches what most remote listings publish (rclone, S3 ETag, many object stores); SHA-256 is the strong local hash.
- **Perceptual fingerprints.**
  - 64-bit pHash luma and colour.
  - 16-float DCT histogram waveform, robust to compression artefacts and local watermark spikes.
  - CIELAB mean colour, hue, chroma, warmth.
  - `visual_sig` — 10–20 spatially-localised points with colour and neighbour topology.
- **EXIF metadata.** Capture datetime, camera make and model, GPS as signed decimal degrees (best-effort; formats Pillow cannot decode yield empty values, which is a valid computed state).
- **Video support.** Duration, fps, frame count, codec, dimensions, plus perceptual signatures of sampled frames. Frames are processed in memory; no stills are written next to the source.
- **5-tier matching funnel** for the bridge matcher:
  1. **Tier 1** — dual 64-bit pHash Hamming distance.
  2. **Tier 2** — DCT histogram waveform distance.
  3. **Tier 3** — LAB perceptual colour distance.
  4. **Tier 4** — `visual_sig` spatial/colour layout distance.
  5. **Tier 5 (optional)** — full pixel SSIM on surviving near-ties.
- **Version-gated group regeneration.** When a calculation group's algorithm changes, caches written by earlier toolkit versions are automatically recomputed for that group on the next build. No `--force` required.
- **Resumable output.** Bridge matching streams as JSONL; interrupted runs resume without reprocessing completed queries.
- **Zero-dependency fallback.** OpenCV is used for acceleration when available; the toolkit falls back to pure `NumPy` + `Pillow`.

---

## Installation

```bash
# Minimal (NumPy + Pillow)
pip install image-bridge-toolkit

# Recommended (adds OpenCV acceleration and progress bars)
pip install "image-bridge-toolkit[all]"
```

For development:

```bash
git clone https://github.com/akingdom/image_bridge_toolkit.git
cd image_bridge_toolkit
pip install -e ".[all]"
```

---

## Usage

### 1. Build or update metadata caches (`imgcache-build`)

Walks a root directory, scanning subdirectories (skipping any directory containing `.ignore_subdir`), and generates or updates `image_cache.json`.

```bash
imgcache-build /path/to/thumbnails_set
imgcache-build /path/to/originals_set -w 8
imgcache-build /path/to/collection --force      # ignore existing caches
```

### 2. Match datasets (`imgmatch-bridge`)

Matches images from the query dataset against the target library and streams a bridge map of confidence-scored candidates.

```bash
imgmatch-bridge /path/to/thumbnails_set /path/to/originals_set \
    -o bridge_map.jsonl \
    --max-hamming 16 \
    --candidate-threshold 0.03 \
    --direct-compare
```

| Flag | Default | Description |
| --- | --- | --- |
| `-o`, `--output` | `bridge_map.jsonl` | Output path for streamed results. |
| `--max-hamming` | `16` | Tier-1 pHash Hamming cutoff. |
| `--candidate-threshold` | `0.03` | Confidence band for including secondary candidates. |
| `--direct-compare` | `False` | Run Tier-5 pixel SSIM re-ranking on candidates. |
| `-w`, `--workers` | single process | Process pool size for per-query matching. |
| `--no-resume` | `False` | Ignore and overwrite an existing output file. |

---

## Cache Groups

Every `image_cache.json` entry is a flat mapping of key → value, plus an `mtime`. Keys are organised into groups; the group is the unit of computation and invalidation.

| Group | Fields | Notes |
| --- | --- | --- |
| `exact` | `md5`, `sha256`, `size` | File-level. Computed even when the image cannot be decoded. |
| `hash` | `phash_luma`, `phash_color` | 64-bit hexadecimal. |
| `sig` | `visual_sig`, `ground_confidence`, `ground_detected` | Perceptual point layout. |
| `lab` | `lab_value`, `lab_hue`, `lab_chroma`, `lab_warmth` | Mean colour in CIELAB. |
| `waveform` | `hist_waveform` | 16-float DCT of the grayscale histogram. |
| `exif` | `exif_datetime`, `exif_make`, `exif_model`, `exif_gps_lat`, `exif_gps_lon` | Best-effort. Empty on unsupported formats. |
| `video` | `video_duration`, `video_fps`, `video_frame_count`, `video_codec`, `video_width`, `video_height`, `video_sig` | Video files only. `video_sig` is a list of sampled-frame signatures. |

A directory's cache also carries a top-level `meta` block recording the writer's toolkit version:

```json
{
  "meta": { "versions": { "ibt": "0.3.1" } },
  "photo.jpg": { "mtime": 1700000000, "md5": "...", ... }
}
```

The writer version is what drives automatic recomputation of changed groups on subsequent builds.

---

## Cache Lifecycle — what is preserved, replaced, and erased

This section is normative. It describes exactly what happens to an existing `image_cache.json` when `imgcache-build` runs.

### Preserved as-is

An entry survives unmodified when all of the following hold:

- The image file is still on disk in the same directory, with the same filename.
- The file's `mtime` (integer seconds) is unchanged.
- Every group applicable to the file's type is present in the entry.
- The cache's recorded writer version is at least `GROUP_MIN_VERSION[group]` for every group (see "Replaced due to algorithm changes" below).

The file is not re-read. No calculation runs. The entry is copied verbatim into the new cache.

### Replaced, values only — entry survives

The entry survives but one or more groups are recomputed when any of the following hold. Fields outside the affected groups are preserved untouched.

- **The file changed on disk.** `mtime` differs from the cached value → all groups applicable to the file's type are recomputed, because a content change invalidates every derived value.
- **A group's keys are missing.** The cache predates that group's introduction, or was written by a consumer that stripped the keys. Only the missing groups run; the rest are reused.
- **The writer version predates a group's current algorithm.** The toolkit records, per group, the minimum version whose algorithm produces values compatible with the current one. If the cache's `meta.versions.ibt` is older than that minimum for some group, that group is recomputed even though its keys are present. The entry itself is not deleted. Other groups are untouched.

This last rule is what makes algorithm improvements propagate across large collections without `--force`. When `compute_visual_signature` changes, the version number in `__init__.py` and the corresponding entry in `GROUP_MIN_VERSION` are bumped together; on the next `imgcache-build`, every cache that predates the change re-signs its `sig` group and nothing else.

### Erased — entry disappears

An entry is removed from the cache when any of the following hold.

- **The image file is no longer on disk.** The builder only writes entries for files it finds during the directory scan. A file deleted between builds disappears from the cache on the next build. There is no tombstone and no warning — the entry is silently dropped. If you need a record of what was once present, copy the cache elsewhere before removing the file.
- **`--force` is used.** The existing cache is ignored entirely and rebuilt from the files currently on disk. Any entry without a corresponding file is not carried over.
- **The cache file is corrupt.** A cache that cannot be parsed as JSON is treated as empty. On the next write it is replaced with a fresh cache containing only current files. Entries from the corrupt file are unrecoverable.

There is no scenario in which `imgcache-build` deletes image files. It only reads them. The only file it writes is `image_cache.json` inside each scanned directory, and that write is atomic — a crash mid-write leaves the previous cache intact.

### Not recomputed when you might expect it to be

- **Sub-second edits.** `mtime` is recorded as integer seconds. Two edits within the same second leave the `mtime` unchanged and the entry is not recomputed. If your workflow produces many rapid edits, delete the cache file or use `--force`.
- **Content changes that preserve `mtime`.** `touch -r` or a `cp --preserve=timestamps` that keeps the original `mtime` will not trigger recomputation. The builder trusts `mtime` as a proxy for content; it does not hash every file to check.

### Practical recommendations

- **Backing up before big changes.** The cache is not journaled. Before running `--force`, or before any upgrade you are unsure about, copy the tree or snapshot the cache files.
- **Version bumps for algorithm changes.** When you change a group's algorithm, bump `__version__` in `__init__.py` and the corresponding `GROUP_MIN_VERSION` entry in `constants.py` in the same commit. The planner will then re-run only the affected group across every existing cache.
- **Diagnosing unexpected erasure.** If entries vanish without an obvious cause, check whether the files were moved, renamed, or if the directory was itself renamed. The cache is per-directory; a directory rename leaves a stale cache behind and starts a fresh one under the new name.

---

## `visual_sig` Schema

Each `visual_sig` entry is a list of signature points. Every point contains:

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `x`, `y` | float | 0–100 | Normalised position (percentage). |
| `size` | float | 0–100 | Voronoi spatial weight (percentage of scaffold assigned to this point). |
| `hex` | str | `#rrggbb` | Sampled colour of the cell. |
| `lab` | [float, float, float] | L: 0–100, a/b: −128–127 | CIELAB colour. |
| `grad_theta` | float | 0–2π | Gradient direction. |
| `grad_mag` | float | ≥0 | Gradient magnitude (luma-weighted). |
| `elongation` | float | 0–1 | 0 = round, 1 = line. Content-weighted covariance. |
| `axis_phi` | float | 0–π | Principal orientation. |
| `primitive` | int | 0 or 1 | 0 = plane, 1 = edge (quadrant luma variance). |
| `density` | float | 0–1 | Fraction of scaffold within the adaptive radius. |
| `curvature` | float | 0–2.5 | Local bend, clamped to `CURVATURE_MAX`. |

Consumers that read only `x`, `y`, `hex`, and `size` continue to work unchanged. The remaining fields are additive.

Example access:

```python
import json
cache = json.load(open("image_cache.json"))
points = cache["photo.jpg"]["visual_sig"]
for p in points:
    print(f"{p['hex']} at ({p['x']:.1f}, {p['y']:.1f}) k={p['curvature']:.2f}")
```

### Selection algorithm

Selection is **histogram-anchored**, not saliency-first:

1. The image is resized to a 16×16 scaffold (256 cells), each carrying sampled colour and luma.
2. Every cell's CIELAB value is quantised into a fixed 6×6×6 grid (216 bins). Fixed boundaries mean the same colour lands in the same bin regardless of what else is in the image.
3. Bins holding at least 2% of the scaffold (and no fewer than three cells) are "significant". Everything else is noise.
4. The point budget is allocated across significant bins, one per bin minimum, with the remainder distributed proportionally to bin size.
5. Within each bin, the highest structural-score cells are picked, with non-maximum suppression on a radius of 0.04 to spread representatives across a region rather than clustering on its single most-structural cell.

The consequence is that every significant colour region receives representation, and the selection is stable under minor editing: the histogram barely moves between two versions of the same image, so the picks barely move either. On a controlled test pair (same photo, one with a light colour grade), the LAB histograms shared 99.6% of their mass and 9 of 9 significant bins, where the previous colour-first greedy selection diverged substantially.

### How the topology fields are computed

For every scaffold cell, the following are computed from its adaptive-radius neighbourhood:

1. **Gradient** (`grad_theta`, `grad_mag`). Luma differences to each neighbour, weighted by neighbour offset.
2. **Elongation and principal axis** (`elongation`, `axis_phi`). Eigen-decomposition of the neighbour-position covariance matrix, where each neighbour's contribution is weighted by the square of its luma difference from the centre. A uniform neighbourhood — flat interior, empty background — produces near-zero covariance and therefore near-zero elongation, regardless of where in the grid the cell sits.
3. **Curvature** (`curvature`). Weighted least-squares fit of `v = a·u² + b·u` in local `(u, v)` coordinates aligned with the principal axis, where `u` is the along-axis projection and `v` the perpendicular. `κ = |2a|`, clamped to `CURVATURE_MAX`. Neighbourhoods that do not surround the centre are rejected: a parabola fitted to a one-sided neighbourhood is extrapolation, not observation. The test is angular coverage — the largest gap between consecutive neighbour bearings around the centre. A full-disc neighbourhood has a small maximum gap; a truncated border neighbourhood has a gap of π or more.
4. **Primitive** (`primitive`). 1 if the variance of neighbour luma across the four quadrants around the centre exceeds twice the minimum quadrant variance, otherwise 0.
5. **Density** (`density`). Number of neighbours within the adaptive radius, as a fraction of the total scaffold.
6. **Size** (`size`). Assigned after selection: the fraction of scaffold cells whose nearest selected point is this one (Voronoi).

---

## Cache Integrity

- **`mtime` matching.** An entry is preserved so long as the file's modification time matches the record in `image_cache.json`. See *Cache Lifecycle* above for the full rules.
- **Partial recomputation.** When a group is stale, only that group re-runs. Existing values in other groups are preserved.
- **Version-gated regeneration.** Algorithm changes to a group are propagated automatically on the next build. Cache entries written by an older toolkit version are recomputed for exactly the affected groups.
- **Corrupt cache handling.** Unparseable `image_cache.json` files are treated as empty and rebuilt. The corruption is logged; traversal continues.
- **Subdirectory exclusion.** A `.ignore_subdir` marker inside any directory causes both the builder and matcher to ignore that directory and its subtree.

---

## Programming Usage

```python
from pathlib import Path
from image_bridge_toolkit.cache_builder import ImageCacheBuilder
from image_bridge_toolkit.matcher import run_match
```

### Build or refresh caches

```python
builder = ImageCacheBuilder("/data/deviantart", workers=8)
stats = builder.run()      # {'dirs', 'files_seen', 'files_updated', 'files_reused'}
print(stats)

builder = ImageCacheBuilder("/data/originals", workers=16)
builder.run()
```

### Match datasets

```python
run_match(
    da_root="/data/deviantart",
    orig_root="/data/originals",
    output_path="bridge_map.jsonl",
    max_hamming=16,
    candidate_threshold=0.03,
    do_direct_compare=True,
    workers=8,
    resume=True,
)
```

### Read results back

```python
import json
with open("bridge_map.jsonl") as f:
    for line in f:
        result = json.loads(line)
        print(result["query_path"], "->", len(result["top_matches"]), "candidates")
```

---

## License & Copyright

* **Source Code (`src/`)**: Licensed under the [MIT License](LICENSE).
* **Documentation & Branding**: Copyright © 2026 Andrew Kingdom. Licensed under the [Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International License (CC BY-NC-ND 4.0)](https://creativecommons.org/licenses/by-nc-nd/4.0/).
* **Translation Permission**: Permission is explicitly granted to translate this documentation into other languages, provided that full attribution to Andrew Kingdom is maintained, a direct link to the original repository is included, and no non-linguistic modifications or structural alterations are made to the content.
