---
name: anime-illustration
description: Anime and manga style illustration - characters with clean lines, cel shading, expressive eyes, vivid colours, backgrounds in anime style.
---
# Anime illustration

1. Sketch proportions on a "Sketch" layer: large head and eyes, simple nose and mouth, clear pose line.
2. Clean line art on its own layer with a thin inking preset; thicker outer lines, thinner inner ones.
3. Flats with `colorize_lineart`; a limited palette with saturated hair and eye colours.
4. Cel shading: one hard-edged shadow layer (`blend_mode="multiply"`, clipped) and small highlights on hair
   and eyes (`blend_mode="screen"`).
5. With an image model: `generate_image(style="anime", control=[{"layer": "Lines", "type": "lineart_anime"}])`
   to render from the line art, then fix faces with inpaint.
6. Background: simplified shapes, soft gradients, light bloom (`blend_mode="add"`, blurred).
