---
name: retouch-photo
description: Retouch, correct or improve an existing photo or picture - fix colour and exposure, remove a person or object, replace the sky or background, extend the frame, upscale, cut out a subject.
---
# Retouch a photo

1. Look first: `canvas_snapshot(max_size=768)` and `analyze_image` (exposure, colour cast, contrast).
2. Tone on a `duplicate_layer` copy: `auto_enhance`, then `adjust_image` with curves or levels; add
   hue_saturation and color_balance as filter masks (`non_destructive=true`) so the user can change them later.
3. Remove or replace something: `set_selection` around it (or `kind="color"`), then
   `generate_image(prompt describing what should be there, mode="inpaint")`.
4. New background: `remove_background(mode="mask")`, then a new layer below (`generate_image`, or
   `search_assets(kind="hdris")` + `import_asset` for a real sky).
5. Bigger canvas: `outpaint(prompt, left=..., right=...)`.
6. Unify the parts with `match_colors(layer, reference_layer)`; finish with `add_vignette`, and
   `upscale_image` last.
7. Never paint over the original layer: duplicate it or work on new layers.
