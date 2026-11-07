---
name: photoreal-image
description: Make a photorealistic or cinematic image from a prompt with an image model, then fix and finish it in Krita (photo, portrait, product, landscape, realistic scene).
---
# Photoreal image

1. `create_artwork(prompt, approach="generate", style=...)` - pick the style that fits: photo, cinematic,
   portrait, product, fashion, street, landscape, macro, food, architecture, drone. One image is made by
   default; ask for `count` 2-4 only when the user wants options (cloud services charge per image).
2. Write prompts like a photographer: subject, setting, light (golden hour, soft window light, rim light),
   lens (35mm, 85mm f/1.8), camera angle, mood. Put unwanted things in `negative_prompt`.
3. Look once with `canvas_snapshot(max_size=768)`. Check anatomy, hands, text, horizon, light direction.
4. Fix only what is wrong: `set_selection` around it, then `generate_image(prompt, mode="inpaint")`.
   Extend the frame with `outpaint`. Never regenerate the whole picture to fix a detail.
5. Finish: `auto_enhance`, `adjust_image` (curves / levels), `match_colors` to unify pasted parts,
   `add_vignette(strength=0.3)`, then `upscale_image` last (it is the most expensive step).
6. Keep each fix on its own layer and name it.
