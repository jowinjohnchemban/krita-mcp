---
name: lineart-coloring
description: Colour line art - clean flats, then shading and highlights - for comics, anime, illustrations and colouring pages.
---
# Colour line art

1. Line art on its own layer with closed shapes; close small gaps with short `brush_stroke`s.
2. Flats: `colorize_lineart(regions=[{"color": "#e0b090", "points": [[x, y]]}, ...], layer="Lines")` - one
   scribble point inside each area. Check with `canvas_snapshot(max_size=768)`; add regions for missed areas.
3. Shadows: a new layer above the flats, `update_layer(blend_mode="multiply", clipped=true)`, paint with one
   cool shadow colour, light from one direction (cel shading: hard edges; painterly: soft brush).
4. Highlights: another clipped layer with `blend_mode="screen"` or `"add"`, small and only on round forms.
5. Keep the lines on top; tint them with a clipped layer for a softer look.
