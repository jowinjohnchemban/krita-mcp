---
name: photo-composite
description: Combine several photos or elements into one believable picture - put a person or object into a new scene, matching light, colour, perspective, shadows and focus.
---
# Photo composite

1. Background first, then each element on its own layer (`import_image`, `import_image_from_url`,
   `import_asset`). Cut elements out with `remove_background(mode="mask")`.
2. Scale and place: `transform_layer` so sizes agree with the scene's perspective (eye level / horizon).
3. Light: find the background's light direction and colour (`analyze_image`); match colours with
   `match_colors(element, reference_layer=background)`.
4. Contact shadows: a new layer under the element, a dark soft shape painted with `brush_stroke`,
   `update_layer(blend_mode="multiply", opacity=0.5)`; a longer cast shadow in the light's direction.
5. Focus and grain: blur distant elements slightly (`adjust_image("blur")`), add matching noise.
6. Tie it together with one overall grade on top (see color-grade) and `add_vignette`.
7. Optionally a light img2img pass (`strength=0.25-0.35`) over the whole picture to unify it.
