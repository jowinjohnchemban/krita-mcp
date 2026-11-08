---
name: pixel-art
description: Pixel art - sprites, tiles, icons, retro game art, crisp low-resolution images with a limited palette, scaled up without blur.
---
# Pixel art

1. Work tiny: `document_new(width=64, height=64, background="transparent")` (16, 32, 64 or 128 px).
   Every pixel counts - think in single pixels.
2. Hard edges only: `draw_shapes` rects and polygons, and pixel-sized `brush_stroke` with a pixel preset
   (`list_resources(type="preset", filter="pixel")`) and `smooth=false`. No soft brushes, no blur.
3. Limited palette: 4-16 colours chosen up front (`palette_colors` or your own list); shade with a darker,
   slightly hue-shifted colour, not black.
4. Outline with a dark tone of the fill, clean single-pixel lines (no doubled corners), light from the
   top-left.
5. Check with `canvas_snapshot(grid=8)` - small images are cheap to look at.
6. Only at the end, enlarge for display with `scale_image(width*8, height*8, method="NearestNeighbor")`
   and export PNG. With an image model: `generate_image(style="pixel_art")`, then scale down and up again
   with NearestNeighbor to clean it.
