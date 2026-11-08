---
name: restyle-image
description: Restyle an existing image while keeping its content - turn a photo into a painting, anime, watercolor, 3D render or another style; style variations with img2img.
---
# Restyle an image

1. `generate_image(prompt describing the content + the new look, mode="img2img", style="watercolor" |
   "oil_painting" | "anime" | "3d_render" | "comic" | ..., strength=...)`.
2. Strength decides how much changes: 0.3-0.45 keeps composition and faces, 0.5-0.65 a clear restyle,
   0.7+ a reinterpretation.
3. Keep structure exactly with a control guide: `control=[{"layer": "Photo", "type": "lineart"}]` and a
   higher strength.
4. Without any image model, Krita can approximate: `adjust_image("oil_paint")`, `("posterize")`,
   edge detection for ink lines, `("pixelize")`.
5. Compare with the original (`canvas_snapshot`) and mix them with layer opacity if the user wants
   something in between.
