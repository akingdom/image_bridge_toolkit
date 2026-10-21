CACHE_FILE_NAME = "image_cache.json"
IGNORE_MARKER = ".ignore_subdir"
VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}

# Container formats the video group can fingerprint. Requires OpenCV; when
# it is absent, compute_video_signature() returns an "empty" record whose
# keys are still present, so the planner does not re-run the group forever.
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"}

# ----------------------------------------------------------------------
# Cache-level metadata.
#
# Every written cache starts with a "meta" key:
#     "meta": {
#         "versions": {
#             "ibt": "0.2.7",
#             ...    # other tools that write into this cache may add their own
#         }
#     }
#
# Consumers read meta.versions.ibt to decide whether the cache needs
# migration. See legacy.py.
# ----------------------------------------------------------------------
META_KEY = "meta"
META_VERSIONS_KEY = "versions"
META_TOOL_ID = "ibt"

GROUP_KEYS = {
    # Exact identity. MD5 is the hash most commonly exposed by remote
    # listings (rclone, S3 ETag, many object stores); SHA-256 is the strong
    # local hash. Storing both lets a consumer compare against whichever
    # the other side happens to publish. size is a free early-out.
    "exact": ("md5", "sha256", "size"),
    "hash": ("phash_luma", "phash_color"),
    # Ground detection is part of the "sig" group because it is computed by
    # compute_visual_signature() from the same scaffold data. Listing it here
    # means an entry missing those fields is (correctly) flagged for rebuild —
    # this is what lets legacy.migrate() safely drop stale ground fields and
    # have the next build recompute them from the image.
    "sig": ("visual_sig", "ground_confidence", "ground_detected"),
    "lab": ("lab_value", "lab_hue", "lab_chroma", "lab_warmth"),
    "waveform": ("hist_waveform",),
    # EXIF is best-effort. Pillow cannot read HEIC without pillow-heif, and
    # cannot read video containers at all, so for those the fields are
    # present but empty/zero -- which is a valid "computed" state and stops
    # the planner from re-flagging the group on every build. Adding
    # pillow-heif (and register_heif_opener() at import) fills them in for
    # modern phone output; that is a separate decision, not baked in here.
    "exif": ("exif_datetime", "exif_make", "exif_model",
             "exif_gps_lat", "exif_gps_lon"),
    # Video metadata + sampled-frame signatures. Applies only to files with
    # a video extension -- planner._groups_for_suffix enforces that, so an
    # image is never flagged for a video pass and vice versa.
    "video": ("video_duration", "video_fps", "video_frame_count",
              "video_codec", "video_width", "video_height", "video_sig"),
}
# The minimum toolkit version whose algorithm for each group produces
# values compatible with the current algorithm. A cache written by an
# earlier version has stale values in any group whose algorithm changed
# after its writer version, and those groups must be recomputed even if
# the entry's keys are present.
#
# When a group's algorithm changes in a way that affects the values (not
# just adds fields), update this table with the new version. The planner
# uses it to force recomputation on the next build.
GROUP_MIN_VERSION = {
    "exact":    "0.3.0",   # introduced 0.3.0
    "hash":     "0.1.0",   # stable
    "sig":      "0.3.1",   # histogram-anchored selection
    "lab":      "0.1.0",   # stable
    "waveform": "0.1.0",   # stable
    "exif":     "0.3.0",   # introduced 0.3.0
    "video":    "0.3.0",   # introduced 0.3.0
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

# Video frame sampling. Short videos use percentage-of-duration (a fixed
# 1s sample on a 3s clip wastes half the budget on the first third); long
# videos mix fixed early cues with two percentage cues near the tail. The
# two lists are combined and capped at VIDEO_MAX_SAMPLES.
VIDEO_SAMPLE_PERCENTAGES = (0.05, 0.25, 0.50, 0.75, 0.95)
VIDEO_SAMPLE_FIXED_TIMES = (1.0, 10.0, 600.0)
VIDEO_MAX_SAMPLES = 7

# Extended constants (kept for compatibility, not used)
EXTENDED_VERSION = "2.0"
MAGIC_BYTE = 0xEB
NODE_BYTES = 17
MAX_NODES = 20
MIN_NODES = 10
NMS_RADIUS = 0.04
CURVATURE_MAX = 2.5
