---
name: color-palette
description: Choose and apply a colour palette - harmonious schemes, mood colours, brand colours, extracting a palette from a reference image.
---
# Colour palette

1. From a reference: open or import it and `analyze_image(palette_size=8)` to extract its colours.
2. From theory: pick a dominant hue (60%), a secondary (30%) and an accent (10%). Schemes: analogous (calm),
   complementary (vivid), split-complementary (balanced), triadic (playful).
3. Mood: warm and saturated = energy; cool and desaturated = calm or sad; low contrast = soft or foggy.
4. Krita palettes: `list_resources(type="palette")` and `palette_colors(name)`.
5. Show the palette to the user as swatches with `draw_shapes` rects on a separate layer, with hex labels
   (`add_svg` <text>).
6. Apply to an existing picture: a gradient map style recolour with `match_colors` from a palette image, or
   colour layers in `blend_mode="color"`.
