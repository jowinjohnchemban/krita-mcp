---
name: photo-restoration
description: Restore old or damaged photos - remove scratches, dust and tears, fix fading, reduce noise, sharpen, upscale, and optionally colourise a black-and-white photo.
---
# Photo restoration

1. Work on a copy (`duplicate_layer`). Straighten (`rotate_image`) and crop the border (`crop_image`).
2. Tone: `auto_enhance`, then levels to restore blacks and whites; remove colour fading with
   `adjust_image("color_balance")` or desaturate for a clean black-and-white.
3. Scratches, dust and tears: select each defect (`set_selection`), `modify_selection("grow", 3)`, then
   `generate_image("old photo texture, matching surroundings", mode="inpaint", strength=0.9)`.
4. Noise and blur: `adjust_image("denoise")`, then gentle `adjust_image("sharpen")`.
5. Resolution: `upscale_image(2)`; faces may need an inpaint pass at a larger size.
6. Colourise (if asked): a new layer with `blend_mode="color"`, flat plausible colours per area (skin, sky,
   clothes); or img2img at low strength with a colour description.
