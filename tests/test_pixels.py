"""Pure-Python pixel maths used for colour selection and image analysis."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin" / "pykrita"))

from kritapilot_plugin.pixels import (analyze, apply_luts, brightness_contrast_lut, color_mask, components,  # noqa: E402
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


# ---------------------------------------------------------------- game art
from kritapilot_plugin import pixels as P  # noqa: E402


def grid(mask, w):
    return ["".join("#" if mask[y * w + x] else "." for x in range(w)) for y in range(len(mask) // w)]


def test_dilate_shapes_and_no_wrap_across_rows():
    m = bytearray(25)
    m[12] = 255
    assert grid(P.dilate(m, 5, 5, 1), 5) == [".....", "..#..", ".###.", "..#..", "....."]  # no corners
    assert grid(P.dilate(m, 5, 5, 1, "square"), 5) == [".....", ".###.", ".###.", ".###.", "....."]
    edge = bytearray(25)
    edge[4] = 255  # the last pixel of the first row must not leak into the next row's first pixel
    assert grid(P.dilate(edge, 5, 5, 1, "square"), 5)[:3] == ["...##", "...##", "....."]
    assert P.erode(P.dilate(m, 5, 5, 1), 5, 5, 1) == bytes(m)


def test_outline_ring_and_paint():
    m = bytearray(9)
    m[4] = 255
    ring = P.mask_and_not(P.dilate(m, 3, 3, 1), m)
    assert grid(ring, 3) == [".#.", "#.#", ".#."]
    px = P.paint_mask(ring, (10, 20, 30, 255))
    assert px[4:8] == bytes((30, 20, 10, 255)) and px[0:4] == bytes(4)
    assert P.alpha_mask(bytes((0, 0, 0, 200, 0, 0, 0, 50)), 128) == b"\xff\x00"


def test_quantize_to_a_palette_with_and_without_dither():
    raw = bgra([[(20, 20, 20, 255), (240, 230, 250, 255), (0, 0, 0, 0), (130, 120, 140, 100)]])
    out = P.quantize(raw, 4, 1, [(0, 0, 0), (255, 255, 255)])
    assert out == bgra([[(0, 0, 0, 255), (255, 255, 255, 255), (0, 0, 0, 0), (255, 255, 255, 100)]])
    crisp = P.quantize(raw, 4, 1, [(0, 0, 0), (255, 255, 255)], alpha_threshold=128)
    assert crisp[12:16] == bytes(4)  # half-transparent pixel dropped
    gray = bgra([[(128, 128, 128, 255)] * 16] * 4)
    for d in ("ordered", "floyd"):  # a mid grey dithers into a mix of black and white
        q = P.quantize(gray, 16, 4, [(0, 0, 0), (255, 255, 255)], d)
        values = set(q[0::4])
        assert values == {0, 255}, d


def test_median_cut_finds_the_main_colours():
    raw = bgra([[(250, 0, 0, 255)] * 50 + [(0, 0, 250, 255)] * 50 + [(0, 250, 0, 0)] * 10])
    pal = P.median_cut(raw, 2)
    assert sorted(pal) == [(0, 0, 250), (250, 0, 0)]  # the transparent green is not counted
    assert P.median_cut(bgra([[(0, 0, 0, 0)]]), 4) == []


def test_swap_colors_keeps_alpha_and_counts():
    raw = bgra([[(255, 0, 0, 255), (250, 4, 0, 128), (0, 255, 0, 255), (255, 0, 0, 0)]])
    out, n = P.swap_colors(raw, [((255, 0, 0, 255), (0, 0, 255, 255))], tolerance=5)
    assert n == 2
    assert out == bgra([[(0, 0, 255, 255), (0, 0, 255, 128), (0, 255, 0, 255), (255, 0, 0, 0)]])
    assert sorted(P.used_colors(raw)) == [(0, 255, 0), (250, 4, 0), (255, 0, 0)]


def test_tile_grid():
    assert P.tile_grid(10, 4, 4, 4) == [(0, 0, 0, 0), (4, 0, 0, 1)]  # partial tiles are left out
    assert P.tile_grid(11, 11, 4, 4, margin=1, spacing=1) == [(1, 1, 0, 0), (6, 1, 0, 1), (1, 6, 1, 0),
                                                              (6, 6, 1, 1)]
