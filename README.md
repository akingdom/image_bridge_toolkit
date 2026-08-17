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

- **Per-Directory JSON Caching**: Generates and maintains lightweight `image_cache.json` files within each directory, keyed by filename and modification time (`mtime`).
- **Scale-Invariant Waveform Approximations**: Uses 16-float 1D Discrete Cosine Transform (DCT) grayscale histogram vectors to ignore local watermark text spikes and compression artifacts.
- **5-Tier Matching Funnel**:
  1. **Tier 1 (Bitwise pHash Luma + Color)**: Generous 64-bit Hamming filtering to eliminate obvious non-matches.
  2. **Tier 2 (Histogram Waveform)**: Euclidean distance on 16 low-frequency DCT coefficients to filter lighting distribution changes.
  3. **Tier 3 (LAB Perceptual Metrics)**: Vector distance across `lab_value`, `lab_hue`, `lab_chroma`, and `lab_warmth`.
  4. **Tier 4 (Spatial Layout Tie-Breaker)**: 16-point color spatial coordinates down-weighting image outer margins where signatures and borders reside.
  5. **Tier 5 (Optional SSIM Direct Comparison)**: Full structural similarity pixel comparison on top candidate matches.
- **Resumable & High Performance**: Output streams as `.jsonl` objects, allowing runs over huge original sets to resume instantly without re-processing.
- **Zero-Dependency Fallback**: Runs with `OpenCV` if available for acceleration, or falls back to pure `NumPy` + `Pillow`.

---

## Installation

Install via PyPI:

```bash
# Minimal installation (NumPy + Pillow)
pip install image-bridge-toolkit

# Recommended installation (includes OpenCV acceleration & progress bars)
pip install "image-bridge-toolkit[all]"

```

For development:

```bash
git clone [https://github.com/akingdom/image_bridge_toolkit.git](https://github.com/akingdom/image_bridge_toolkit.git)
cd image_bridge_toolkit
pip install -e ".[all]"

```

---

## Usage

### 1. Build or Update Metadata Caches (`imgcache-build`)

Walks a root directory, scanning all subdirectories (skipping any directory containing `.ignore_subdir`), and generates or updates `image_cache.json`.

```bash
# Build cache for a query dataset (e.g., 1,000 watermarked images)
imgcache-build /path/to/thumbnails_set

# Build cache for a target dataset (e.g., 50,000 original images)
imgcache-build /path/to/originals_set

```

### 2. Match Datasets (`imgmatch-bridge`)

Matches images from the query dataset against the target library, generating a bridge mapping file with confidence scores and top candidate lists.

```bash
imgmatch-bridge /path/to/thumbnails_set /path/to/originals_set \
    -o bridge_map.jsonl \
    --max-hamming 16 \
    --candidate-threshold 0.03 \
    --direct-compare

```

#### Command-Line Options

| Flag | Default | Description |
| --- | --- | --- |
| `-o`, `--output` | `bridge_map.jsonl` | File path for output streaming results. |
| `--max-hamming` | `16` | Maximum allowed Tier 1 pHash Hamming distance threshold. |
| `--candidate-threshold` | `0.03` | Percentage band (e.g., 3%) to include close secondary matches. |
| `--direct-compare` | `False` | Performs Tier 5 pixel-level SSIM re-ranking on candidates. |

---

## Cache Integrity & Updating

* **`mtime` Matching**: A cached entry is preserved as long as the file's modification time matches the record in `image_cache.json`.
* **Partial Recomputation**: If an existing cache entry is missing a newly added metric type (e.g., `hist_waveform`), only the missing calculation is executed; existing valid metrics are preserved.
* **Corrupt Cache Handling**: Invalid or unparseable JSON files are automatically caught and rebuilt without interrupting directory traversal.
* **Subdirectory Exclusion**: Placing a `.ignore_subdir` marker inside any directory causes the builder and matcher to ignore that folder and all nested subdirectories.

---

## Programming Usage

### Includes
```python
from pathlib import Path
from image_bridge_toolkit.cache_builder import ImageCacheBuilder
from image_bridge_toolkit.matcher import run_match
```

### --- Tool A: build/refresh caches ---
```python
builder = ImageCacheBuilder("/data/deviantart", workers=8)
stats = builder.run()          # returns dict: dirs, files_seen, files_updated, files_reused
print(stats)

builder = ImageCacheBuilder("/data/originals", workers=16)
builder.run()
```

### --- Tool B: match ---
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

### read results back in
```python
import json
with open("bridge_map.jsonl") as f:
    for line in f:
        result = json.loads(line)
        print(result["query_path"], "->", len(result["top_matches"]), "candidates")
```

---

## License & Copyright

* **Source Code (`src/`)**: Licensed under the [MIT License](https://www.google.com/search?q=LICENSE).
* **Documentation & Branding**: Copyright © 2026 Andrew Kingdom. Licensed under the [Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International License (CC BY-NC-ND 4.0)](https://creativecommons.org/licenses/by-nc-nd/4.0/).
* **Translation Permission**: Permission is explicitly granted to translate this documentation into other languages, provided that full attribution to Andrew Kingdom is maintained, a direct link to the original repository is included, and no non-linguistic modifications or structural alterations are made to the content.
