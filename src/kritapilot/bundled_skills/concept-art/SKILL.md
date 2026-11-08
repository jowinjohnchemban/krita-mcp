---
name: concept-art
description: Concept art and ideation - thumbnails, silhouettes, value studies, colour keys, environment or prop design, exploring several ideas fast before rendering one.
---
# Concept art

1. Thumbnails first: a wide canvas (`document_new(width=1200, height=600)`), `set_guides` to split it into
   4-6 boxes, and one quick silhouette per box with `draw_shapes` / `brush_stroke` in greys only.
   Send each thumbnail's strokes in one `batch`.
2. `canvas_snapshot(max_size=512)` once; pick the strongest read (clear silhouette, a focal point, depth in
   3 value groups: dark foreground, mid, light background). Ask the user if they want to choose.
3. Value study at full size on its own layer: 3-5 values, light from one direction, atmosphere lightens
   distant forms.
4. Colour key: a new layer with `blend_mode="color"` or `"overlay"` over the values; a limited palette
   (2 main hues + 1 accent near the focal point).
5. Render only the chosen idea: details where the eye lands, loose elsewhere. With an image model,
   `generate_image(control=[{"layer": "Values", "type": "scribble"}], style="concept_art")` gives a polished
   pass to paint over.
