"""
Single source of truth for the optional OpenCV backend.

Every other module imports HAS_OPENCV / cv2 from here instead of doing its
own try/except, so the fallback decision is made exactly once per process.
"""
try:
    import cv2  # noqa: F401
    HAS_OPENCV = True
except ImportError:  # pragma: no cover - exercised only when cv2 absent
    cv2 = None
    HAS_OPENCV = False
