"""Tests for image_bridge_toolkit.legacy — version-aware cache migration."""
import math

from image_bridge_toolkit import __version__, legacy
from image_bridge_toolkit.constants import (
    META_KEY, META_VERSIONS_KEY, META_TOOL_ID,
)


def _legacy_node():
    """A node as written by 0.2.6, with the swapped convention."""
    return {
        "x": 80.0, "y": 20.0,
        "size": 12.0, "hex": "#ff0000", "lab": [50.0, 40.0, 30.0],
        "grad_theta": 0.0, "grad_mag": 5.0,
        "elongation": 0.3, "axis_phi": 0.0,
        "primitive": 1, "density": 0.02, "curvature": 0.5,
    }


def _legacy_cache():
    return {
        "a.jpg": {
            "mtime": 1700000000,
            "phash_luma": "fef8f9f8e04090a8",
            "phash_color": "fdfc8be648c00267",
            "visual_sig": [_legacy_node()],
            "ground_confidence": 0.8,
            "ground_detected": True,
            "lab_value": 50.0, "lab_hue": 1.0,
            "lab_chroma": 20.0, "lab_warmth": 15.0,
            "hist_waveform": [0.0] * 16,
        },
    }


def test_read_writer_version_missing():
    assert legacy.read_writer_version({}) is None


def test_read_writer_version_present():
    cache = {META_KEY: {META_VERSIONS_KEY: {META_TOOL_ID: "0.2.6"}}}
    assert legacy.read_writer_version(cache) == "0.2.6"


def test_stamp_meta_idempotent():
    cache = {"a.jpg": {"mtime": 1}}
    stamped = legacy.stamp_meta(cache)
    assert META_KEY in stamped
    assert stamped[META_KEY][META_VERSIONS_KEY][META_TOOL_ID] == __version__
    assert next(iter(stamped.keys())) == META_KEY, "meta must be first"


def test_is_current_false_for_legacy():
    assert not legacy.is_current(_legacy_cache())


def test_is_current_true_for_stamped():
    cache = legacy.stamp_meta(_legacy_cache())
    assert legacy.is_current(cache)


def test_migrate_swaps_xy():
    cache = _legacy_cache()
    migrated, changed = legacy.migrate(cache)
    assert changed
    node = migrated["a.jpg"]["visual_sig"][0]
    assert node["x"] == 20.0
    assert node["y"] == 80.0


def test_migrate_rotates_directions():
    cache = _legacy_cache()
    legacy.migrate(cache)
    node = cache["a.jpg"]["visual_sig"][0]
    # _reflect_direction rounds to 4 decimal places (deliberate: keeps cache
    # files tidy), so the tolerance must match that precision.
    assert abs(node["grad_theta"] - math.pi / 2) < 1e-3
    assert abs(node["axis_phi"] - math.pi / 2) < 1e-3


def test_migrate_drops_ground_fields():
    cache = _legacy_cache()
    legacy.migrate(cache)
    assert "ground_confidence" not in cache["a.jpg"]
    assert "ground_detected" not in cache["a.jpg"]


def test_migrate_preserves_invariant_fields():
    cache = _legacy_cache()
    before = _legacy_node()
    legacy.migrate(cache)
    node = cache["a.jpg"]["visual_sig"][0]
    for field in ("size", "hex", "lab", "grad_mag",
                  "elongation", "primitive", "density", "curvature"):
        assert node[field] == before[field], field


def test_migrate_idempotent():
    cache = _legacy_cache()
    legacy.migrate(cache)
    snapshot = {
        "node": dict(cache["a.jpg"]["visual_sig"][0]),
        "version": legacy.read_writer_version(cache),
    }
    _, changed = legacy.migrate(cache)
    assert not changed, "second pass should be a no-op"
    assert cache["a.jpg"]["visual_sig"][0] == snapshot["node"]
    assert legacy.read_writer_version(cache) == snapshot["version"]


def test_migrate_noop_on_current_cache():
    cache = legacy.stamp_meta(_legacy_cache())
    _, changed = legacy.migrate(cache)
    assert not changed
