---
name: sketch-to-render
description: Turn a rough sketch, doodle or line drawing into a finished realistic or illustrated image while keeping its composition - sketch to photo, lineart to render.
---
# Sketch to render

1. Make sure the sketch is on its own layer (e.g. "Sketch") with clear, dark lines on a light background.
2. `generate_image(prompt describing the finished picture, control=[{"layer": "Sketch", "type": "scribble"}],
   style="photo" | "concept_art" | "illustration" | "anime")`. Clean line art: `"type": "lineart"`.
   Strength 0.6-0.9: higher follows the sketch more closely.
3. The result lands on a new layer above. Compare with `canvas_snapshot(max_size=768)`; if the shapes
   drifted, raise the control strength, if it looks stiff, lower it.
4. Without ControlNet backends, use img2img: `generate_image(prompt, mode="img2img", strength=0.55-0.75)`
   with the sketch visible.
5. Fix parts with inpaint (`set_selection`, `mode="inpaint"`), then paint over by hand on a new layer.
