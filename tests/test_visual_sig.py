# tests/test_visual_sig.py

import pytest
from PIL import Image
import math
import numpy as np
from image_bridge_toolkit.calculators import compute_visual_signature

def test_visual_sig_has_new_fields():
    # Create a simple synthetic image
    arr = np.zeros((64, 64, 3), dtype=np.uint8)
    arr[16:48, 16:48] = [255, 0, 0]  # red square
    img = Image.fromarray(arr)

    result = compute_visual_signature(img)
    sigs = result.get("visual_sig", [])

    assert len(sigs) >= 10 and len(sigs) <= 20
    
    # Check new fields exist
    for sig in sigs:
        assert "lab" in sig
        assert "grad_theta" in sig
        assert "grad_mag" in sig
        assert "elongation" in sig
        assert "axis_phi" in sig
        assert "primitive" in sig
        assert "density" in sig
        assert "curvature" in sig

        # Check values are reasonable
        assert isinstance(sig["lab"], list)
        assert len(sig["lab"]) == 3
        assert -1e-4 <= sig["grad_theta"] <= 2 * math.pi + 1e-4
        assert sig["grad_mag"] >= 0
        assert 0 <= sig["elongation"] <= 1
        assert sig["primitive"] in (0, 1)
        assert 0 <= sig["density"] <= 1
        assert sig["curvature"] >= 0