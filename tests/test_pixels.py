"""Pure-Python pixel maths used for colour selection and image analysis."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin" / "pykrita"))

from krita_mcp_bridge.pixels import (analyze, apply_luts, brightness_contrast_lut, color_mask, components,  # noqa: E402
                                     curve_lut, flood_region, histogram, levels_lut, mask_bounds,
                                     percentile_bounds)


def bgra(rows):
    return b"".join(bytes((b, g, r, a)) for row in rows for (r, g, b, a) in row)


R, W, B = (255, 0, 0, 255), (255, 255, 255, 255), (0, 0, 255, 255)


def test_color_mask_tolerance():
    raw = bgra([[R, (250, 5, 3, 255), W, B]])
    assert color_mask(raw, R, 0) == b"\xff\x00\x00\x00"
    assert color_mask(raw, R, 6) == b"\xff\xff\x00\x00"


def test_flood_region_is_contiguous_only():
    m = bytes([255, 255, 0, 255,
               0, 255, 0, 255,
               0, 0, 0, 255])
    out = flood_region(m, 4, 3, 0, 0)
    assert out == bytes([255, 255, 0, 0, 0, 255, 0, 0, 0, 0, 0, 0])
    assert flood_region(m, 4, 3, 2, 0) == bytes(12)  # seed outside the mask
    snake = bytes([255] * 5 + [0] * 4 + [255] + [255] * 5)  # U-turn through the right edge
    assert flood_region(snake, 5, 3, 0, 2) == snake


def test_mask_bounds_and_components():
    m = bytes([0, 0, 0, 0,
               0, 255, 255, 0,
               0, 0, 0, 255])
    assert mask_bounds(m, 4, 3) == [1, 1, 3, 2]
    assert mask_bounds(bytes(12), 4, 3) is None
    regs, total = components(m, 4, 3)
    assert total == 2 and regs[0]["bbox"] == [1, 1, 2, 1] and regs[0]["area"] == 2


def test_analyze_palette_and_layout():
    raw = bgra([[R, R, W, W], [R, R, W, W]])
    a = analyze(raw, 4, 2, palette_size=4, grid=2)
    assert [c["hex"] for c in a["palette"]] == ["#ff0000", "#ffffff"]
    assert a["palette"][0]["share"] == 0.5
    assert a["layout"][0][0]["hex"] == "#ff0000" and a["layout"][0][1]["hex"] == "#ffffff"
    assert a["coverage"] == 1.0 and a["sharpness"] > 0


def test_levels_and_brightness_contrast_luts():
    lut = levels_lut(50, 200)
    assert lut[50] == 0 and lut[200] == 255 and lut[125] in (127, 128) and lut[0] == 0 and lut[255] == 255
    assert levels_lut(gamma=2.0)[64] > 64  # gamma > 1 brightens midtones
    assert brightness_contrast_lut(0, 0) == list(range(256))
    assert brightness_contrast_lut(50, 0)[100] > 100 and brightness_contrast_lut(0, 50)[200] > 200


def test_curve_is_monotone_and_hits_points():
    lut = curve_lut([[0, 0], [64, 40], [192, 220], [255, 255]])
    assert lut[0] == 0 and lut[64] == 40 and lut[192] == 220 and lut[255] == 255
    assert all(a <= b for a, b in zip(lut, lut[1:]))


def test_apply_luts_keeps_alpha_and_targets_channels():
    raw = bgra([[(10, 20, 30, 77)]])
    out = apply_luts(raw, r=[255] * 256)
    assert out == bgra([[(255, 20, 30, 77)]])


def test_percentile_bounds_for_auto_levels():
    hist = [0] * 256
    hist[40], hist[120], hist[210] = 1, 998, 1
    assert percentile_bounds(hist, 0.0) == (40, 210)
    assert percentile_bounds(hist, 0.01) == (120, 121)  # one stray pixel each side is clipped away
    raw = bgra([[R, W]])
    assert histogram(raw, 2)[255] == 2 and histogram(raw, 1)[0] == 1
