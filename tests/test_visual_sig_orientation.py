"""Regression test for the pre-0.2.7 x/y swap in compute_visual_signature."""
from PIL import Image

from image_bridge_toolkit.calculators import compute_visual_signature


def _make_top_right_red():
    """64x64 dark image with a red square occupying the top-right quadrant."""
    img = Image.new("RGB", (64, 64), color=(30, 30, 30))
    for x in range(48, 64):
        for y in range(0, 16):
            img.putpixel((x, y), (230, 30, 30))
    return img


def test_top_right_red_has_top_right_coordinates():
    """A distinctive feature in the top-right of the image must produce a
    visual_sig node whose coordinates place it in the top-right of the
    [0, 100] node space."""
    result = compute_visual_signature(_make_top_right_red())
    nodes = result["visual_sig"]
    assert nodes, "expected at least one node"

    # Red square centre in node space is approximately (87.5, 12.5).
    def dist2(n):
        return (n["x"] - 87.5) ** 2 + (n["y"] - 12.5) ** 2

    closest = min(nodes, key=dist2)
    assert closest["x"] > 60, f"x should be right-of-centre, got {closest}"
    assert closest["y"] < 40, f"y should be above centre, got {closest}"


def test_corner_features_are_where_expected():
    """Four distinctly-coloured squares, one per corner, must produce nodes
    in each corresponding corner of node space."""
    img = Image.new("RGB", (64, 64), color=(20, 20, 20))
    corners = {
        "tl": ((0, 0),    (255, 0, 0)),    # red, top-left
        "tr": ((48, 0),   (0, 255, 0)),    # green, top-right
        "bl": ((0, 48),   (0, 0, 255)),    # blue, bottom-left
        "br": ((48, 48),  (255, 255, 0)),  # yellow, bottom-right
    }
    for _, ((x0, y0), colour) in corners.items():
        for x in range(x0, x0 + 16):
            for y in range(y0, y0 + 16):
                img.putpixel((x, y), colour)

    result = compute_visual_signature(img)
    nodes = result["visual_sig"]
    assert len(nodes) >= 4

    def nearest(cx, cy):
        return min(nodes, key=lambda n: (n["x"] - cx) ** 2 + (n["y"] - cy) ** 2)

    # Centres of the four corners in [0,100] node space.
    assert nearest(12.5, 12.5)["x"] < 40,  "expected a node in the top-left"
    assert nearest(87.5, 12.5)["x"] > 60,  "expected a node in the top-right"
    assert nearest(12.5, 87.5)["y"] > 60,  "expected a node in the bottom-left"
    assert nearest(87.5, 87.5)["y"] > 60,  "expected a node in the bottom-right"
