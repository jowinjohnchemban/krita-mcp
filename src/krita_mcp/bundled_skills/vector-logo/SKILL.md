---
name: vector-logo
description: Logos, icons, badges and posters with clean editable vector shapes and text - flat design, typography, simple geometric marks.
---
# Vector logo and graphics

1. Keep everything editable: build with `add_svg` (paths, circles, rects, gradients, <text>) on a vector
   layer, never by painting.
2. Start from geometry: a square canvas, `set_guides` at the centre and margins; 1-3 simple shapes that
   still read at 32 px; a strong silhouette; even stroke widths.
3. 2-3 colours plus one neutral. Check contrast: the mark must work in one colour too.
4. Text: `<text>` with font-family, font-size and font-weight; at most two typefaces; letter-spacing for
   capitals.
5. Look at it small and large: `canvas_snapshot(max_size=256)` and `canvas_snapshot(max_size=768)`.
6. Variations on separate vector layers (horizontal, stacked, icon only, on dark). Adjust with
   `list_vector_shapes` / `remove_vector_shapes`. `export_image` to PNG keeps the transparency.
