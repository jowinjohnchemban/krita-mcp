---
name: paint-by-hand
description: Paint a picture yourself with Krita's real brushes (no image model) - illustration, painting, sketch, watercolor, oil, concept art, landscape or portrait painted stroke by stroke.
---
# Paint by hand

1. `create_artwork(prompt, approach="paint", style=...)` sets up the layers (Sketch, Base colours, Shading,
   Details, Light & effects), thirds guides and a plan.
2. Big to small: compose on Sketch with `draw_shapes` and `brush_stroke`, block flat colours, then shading
   (`update_layer(blend_mode="multiply")`), details, and light (`blend_mode="screen"` or `"add"`).
3. Choose brushes with `list_brush_presets` once; reuse the names. Send many strokes in one `batch` call
   instead of one call per stroke - it is faster and much cheaper.
4. Keep one light direction and a limited palette (`palette_colors`).
5. Look with `canvas_snapshot(max_size=512)` after each pass, not after each stroke; use `analyze_image`
   for values and colour when unsure. Fix the biggest problem first.
6. To make it photographic afterwards: `generate_image(control=[{"layer": "Sketch", "type": "scribble"}],
   style="photo")`.
