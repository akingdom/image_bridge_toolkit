<p align="center">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="100%" height="400">
    <defs>
      <linearGradient id="leftNodeGrad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#06B6D4" stop-opacity="0.25"/>
        <stop offset="100%" stop-color="#0F172A" stop-opacity="0.8"/>
      </linearGradient>

      <linearGradient id="rightNodeGrad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#EC4899" stop-opacity="0.3"/>
        <stop offset="100%" stop-color="#06B6D4" stop-opacity="0.15"/>
      </linearGradient>

      <linearGradient id="bridgeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
        <stop offset="0%" stop-color="#06B6D4"/>
        <stop offset="50%" stop-color="#FFFFFF"/>
        <stop offset="100%" stop-color="#EC4899"/>
      </linearGradient>

      <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="8" result="blur"/>
        <feMerge>
          <feMergeNode in="blur"/>
          <feMergeNode in="SourceGraphic"/>
        </feMerge>
      </filter>

      <filter id="glow-subtle" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="4" result="blur"/>
        <feMerge>
          <feMergeNode in="blur"/>
          <feMergeNode in="SourceGraphic"/>
        </feMerge>
      </filter>

      <pattern id="fingerprintGrid" width="16" height="16" patternUnits="userSpaceOnUse">
        <path d="M 16 0 L 0 0 0 16" fill="none" stroke="#06B6D4" stroke-width="0.8" opacity="0.3"/>
        <circle cx="8" cy="8" r="1.5" fill="#06B6D4" opacity="0.4"/>
      </pattern>
    </defs>

    <circle cx="280" cy="300" r="160" fill="#06B6D4" opacity="0.05" filter="url(#glow)"/>
    <circle cx="520" cy="280" r="170" fill="#EC4899" opacity="0.05" filter="url(#glow)"/>

    <path d="M 140 420 L 220 420 L 260 460" stroke="#06B6D4" stroke-width="1.5" fill="none" opacity="0.3" stroke-dasharray="4 4"/>
    <path d="M 660 180 L 580 180 L 540 140" stroke="#EC4899" stroke-width="1.5" fill="none" opacity="0.3" stroke-dasharray="4 4"/>

    <g transform="translate(180, 190)">
      <rect x="0" y="0" width="200" height="240" rx="16" fill="#000000" opacity="0.4"/>
      <rect x="0" y="0" width="200" height="240" rx="16" fill="url(#leftNodeGrad)" stroke="#06B6D4" stroke-width="2" opacity="0.9"/>
      <rect x="16" y="16" width="168" height="208" rx="8" fill="url(#fingerprintGrid)"/>
      <path d="M 40 60 L 90 60 L 130 100 L 130 160 L 80 180" stroke="#06B6D4" stroke-width="1.5" fill="none" opacity="0.6"/>
      <path d="M 90 60 L 70 120 L 140 120" stroke="#06B6D4" stroke-width="1.5" fill="none" stroke-dasharray="2 2" opacity="0.5"/>
      <circle cx="40" cy="60" r="4" fill="#FFFFFF" filter="url(#glow-subtle)"/>
      <circle cx="90" cy="60" r="3.5" fill="#06B6D4"/>
      <circle cx="130" cy="100" r="3.5" fill="#06B6D4"/>
      <circle cx="70" cy="120" r="3.5" fill="#EC4899"/>
      <circle cx="140" cy="120" r="3.5" fill="#06B6D4"/>
      <circle cx="130" cy="160" r="4" fill="#FFFFFF" filter="url(#glow-subtle)"/>
      <circle cx="80" cy="180" r="3.5" fill="#06B6D4"/>
      <path d="M 24 36 L 24 24 L 36 24" stroke="#06B6D4" stroke-width="2" fill="none"/>
      <path d="M 176 36 L 176 24 L 164 24" stroke="#06B6D4" stroke-width="2" fill="none"/>
      <path d="M 24 184 L 24 196 L 36 196" stroke="#06B6D4" stroke-width="2" fill="none"/>
      <path d="M 176 184 L 176 196 L 164 196" stroke="#06B6D4" stroke-width="2" fill="none"/>
    </g>

    <path d="M 330 330 C 400 420, 440 180, 500 270" stroke="url(#bridgeGrad)" stroke-width="12" fill="none" opacity="0.3" filter="url(#glow)"/>
    <path d="M 320 340 C 390 440, 430 170, 510 260" stroke="url(#bridgeGrad)" stroke-width="4" fill="none" stroke-dasharray="12 6 4 6" stroke-linecap="round" filter="url(#glow-subtle)"/>
    <path d="M 350 290 C 410 230, 430 350, 480 300" stroke="#FFFFFF" stroke-width="1.5" fill="none" stroke-dasharray="6 6" opacity="0.7"/>

    <circle cx="375" cy="355" r="3" fill="#06B6D4" filter="url(#glow-subtle)"/>
    <circle cx="415" cy="310" r="4.5" fill="#FFFFFF" filter="url(#glow)"/>
    <circle cx="445" cy="255" r="3" fill="#EC4899" filter="url(#glow-subtle)"/>
    <rect x="470" y="270" width="5" height="5" transform="rotate(45 472.5 272.5)" fill="#06B6D4"/>

    <g transform="translate(420, 150)">
      <rect x="0" y="0" width="220" height="260" rx="16" fill="#000000" opacity="0.5"/>
      <rect x="0" y="0" width="220" height="260" rx="16" fill="url(#rightNodeGrad)" stroke="#EC4899" stroke-width="2.5" opacity="0.95"/>
      <rect x="14" y="14" width="192" height="232" rx="10" fill="#0F172A" stroke="#FFFFFF" stroke-width="1" stroke-opacity="0.2"/>
      <g opacity="0.9">
        <circle cx="150" cy="75" r="18" fill="#EC4899" filter="url(#glow-subtle)"/>
        <circle cx="150" cy="75" r="10" fill="#FFFFFF"/>
        <polygon points="40,190 100,100 160,190" fill="#06B6D4" opacity="0.4"/>
        <polygon points="80,200 140,120 200,200" fill="#EC4899" opacity="0.6"/>
        <polygon points="140,120 200,200 140,200" fill="#FFFFFF" opacity="0.15"/>
        <line x1="14" y1="160" x2="206" y2="160" stroke="#06B6D4" stroke-width="1" opacity="0.4" stroke-dasharray="4 4"/>
        <line x1="80" y1="14" x2="80" y2="246" stroke="#06B6D4" stroke-width="1" opacity="0.2" stroke-dasharray="4 4"/>
      </g>
      <rect x="-5" y="-5" width="10" height="10" fill="#FFFFFF" stroke="#EC4899" stroke-width="2"/>
      <rect x="215" y="-5" width="10" height="10" fill="#FFFFFF" stroke="#EC4899" stroke-width="2"/>
      <rect x="-5" y="255" width="10" height="10" fill="#FFFFFF" stroke="#EC4899" stroke-width="2"/>
      <rect x="215" y="255" width="10" height="10" fill="#FFFFFF" stroke="#EC4899" stroke-width="2"/>
    </g>

    <circle cx="320" cy="340" r="6" fill="#06B6D4" stroke="#FFFFFF" stroke-width="2" filter="url(#glow-subtle)"/>
    <circle cx="510" cy="260" r="6" fill="#EC4899" stroke="#FFFFFF" stroke-width="2" filter="url(#glow-subtle)"/>
  </svg>
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

## License & Copyright

* **Source Code (`src/`)**: Licensed under the [MIT License](https://www.google.com/search?q=LICENSE).
* **Documentation & Branding**: Copyright © 2026 Andrew Kingdom. Licensed under the [Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International License (CC BY-NC-ND 4.0)](https://creativecommons.org/licenses/by-nc-nd/4.0/).
* **Translation Permission**: Permission is explicitly granted to translate this documentation into other languages, provided that full attribution to Andrew Kingdom is maintained, a direct link to the original repository is included, and no non-linguistic modifications or structural alterations are made to the content.
