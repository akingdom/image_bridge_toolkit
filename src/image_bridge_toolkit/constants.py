CACHE_FILE_NAME = "image_cache.json"
IGNORE_MARKER = ".ignore_subdir"
VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}

GROUP_KEYS = {
    "hash": ("phash_luma", "phash_color"),
    "sig": ("visual_sig",),
    "lab": ("lab_value", "lab_hue", "lab_chroma", "lab_warmth"),
    "waveform": ("hist_waveform",),
}
ALL_GROUPS = frozenset(GROUP_KEYS.keys())
REQUIRED_KEYS = frozenset(k for keys in GROUP_KEYS.values() for k in keys)

# visual_sig topology constants
SIG_POINTS_MIN = 10          # minimum selected points
SIG_POINTS_MAX = 20          # maximum selected points
SIG_POINTS_DEFAULT = 16      # fallback
SCAFFOLD_SIZE = 16           # grid resolution for scaffold (16x16 = 256 points)
SPATIAL_RADIUS = 0.08        # initial radius for neighbour search (adaptive)
GROUND_CONFIDENCE_THRESHOLD = 0.60  # minimum confidence for ground detection

# Extended constants (kept for compatibility, not used)
EXTENDED_VERSION = "2.0"
MAGIC_BYTE = 0xEB
NODE_BYTES = 17
MAX_NODES = 20
MIN_NODES = 10
NMS_RADIUS = 0.04
CURVATURE_MAX = 2.5