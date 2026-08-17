CACHE_FILE_NAME = "image_cache.json"
IGNORE_MARKER = ".ignore_subdir"
VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}

# Field groups. A cache entry is only "complete" once every group's keys are
# present; mtime mismatch invalidates ALL groups (the file content changed),
# while a missing group with a matching mtime just means the schema grew
# (e.g. you added hist_waveform after caches already existed) and only that
# group needs to be (re)computed.
GROUP_KEYS = {
    "hash": ("phash_luma", "phash_color"),
    "sig": ("visual_sig",),
    "lab": ("lab_value", "lab_hue", "lab_chroma", "lab_warmth"),
    "waveform": ("hist_waveform",),
}
ALL_GROUPS = frozenset(GROUP_KEYS.keys())
REQUIRED_KEYS = frozenset(k for keys in GROUP_KEYS.values() for k in keys)
