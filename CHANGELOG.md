# Changelog

## 0.3.2

Detect and recover from caches whose `meta.versions.ibt` stamp has been omitted or stripped by a foreign writer: assume 0.3.0 (recompute `sig` only), skip the pre-0.2.7 coordinate reflection that was corrupting modern caches, and warn that the stripping tool needs to preserve `meta` on write. Migration log now names the source version. Paired with an imgdesc-side fix that preserves `meta.versions` on write.

## 0.3.0 — 2026-10-10

### Added

- **Exact identity.** New `exact` group stores `md5`, `sha256`, and
  `size` per file. Two hashes because consumers differ: MD5 is the one
  most commonly published by remote listings (rclone, S3 ETag, many
  object stores); SHA-256 is the strong local identity. Computed in a
  single read pass.
- **EXIF.** New `exif` group stores `exif_datetime`, `exif_make`,
  `exif_model`, and signed-decimal `exif_gps_lat` / `exif_gps_lon`.
  Best-effort: unsupported formats yield empty/zero values, which is
  still a valid "computed" state and stops the planner retrying them.
- **Video support.** New `video` group stores `video_duration`,
  `video_fps`, `video_frame_count`, `video_codec`, `video_width`,
  `video_height`, and `video_sig` — a list of sampled-frame signatures
  (`t`, `phash_luma`, `phash_color`, `lab_*`, `hist_waveform`) at
  percentage-of-duration times for short videos and fixed+percentage
  times for long ones. Frames are processed in memory; no JPEGs are
  written next to the source.
- `VIDEO_EXTENSIONS` in `constants.py`; scanner, planner and
  cache_builder all dispatch on it.

### Changed

- `planner.plan_entry()` is now suffix-aware: video files get the
  `video` group, image files do not. The existing missing-group backfill
  behaviour is unchanged, so existing caches pick up the new fields
  without recomputing `hash` / `sig` / `lab` / `waveform`.
- `legacy.migrate()` is gated on the *writer* version. Pre-0.2.7 caches
  still get the x/y reflection; 0.2.7+ caches only get the version
  stamp. Without this gate, the version bump would have re-applied the
  reflection and corrupted every 0.2.7 cache.

## 0.2.7 — 2026-09-27

### Fixed

- **`visual_sig` coordinates were reflected across the main diagonal.**
  `compute_visual_signature` built its scaffold coordinates as `(row, col)`
  but named them `(x, y)`, so every node written before this release has
  swapped x and y. Directional fields (`grad_theta`, `axis_phi`) were
  rotated accordingly. Downstream consumers that compared signatures
  between two 0.2.6 caches were unaffected (the error cancelled), but any
  comparison between a signature and an externally-supplied region of
  interest was mirrored.

### Added

- **Cache metadata.** Every written `image_cache.json` now begins with
  `"meta": {"versions": {"ibt": "<version>"}}`. Consumers can check the
  writer version before interpreting entries.
- **`legacy.py`** — migration for pre-0.2.7 caches. `legacy.migrate(cache)`
  swaps x/y, reflects `grad_theta` / `axis_phi`, drops the (unreliable)
  `ground_confidence` / `ground_detected` fields so the next build
  recomputes them, and stamps the current version.
- **`imgcache-migrate` CLI** — walks a tree and migrates every cache it
  finds, without touching image bytes. `--dry-run` reports without writing.
- **`cache_builder` now calls `legacy.migrate` on load**, so old caches
  self-heal on the next `imgcache-build`.

### Changed

- `GROUP_KEYS["sig"]` now also requires `ground_confidence` and
  `ground_detected`, so an entry missing those fields is (correctly)
  flagged for rebuild.
- `write_cache_atomic` stamps the meta block and orders it first.

