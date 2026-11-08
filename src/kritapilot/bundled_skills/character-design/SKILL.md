---
name: character-design
description: Design a character and keep it consistent - turnaround or model sheet (front, side, back), expressions, outfits, and the same character across several images.
---
# Character design

1. Brief: age, build, personality, setting, 3 key shapes (e.g. triangle hat, round body) and a palette of
   3-5 colours. State them before drawing.
2. Model sheet: a wide canvas, `set_guides` for equal columns (front, 3/4, side, back) and horizontal guides
   for head, shoulders, waist, knees and feet, so proportions match across views.
3. Draw the front view first on a "Sketch" layer, then each other view on its own layer, aligned to the
   same guides. Flat colours with `colorize_lineart` (scribble a point in each area) on clean line art.
4. Consistency with an image model: keep one written description and reuse it word for word; fix the
   `seed`; guide new poses with the finished front view (`control=[{"layer": "Front", "type": "lineart"}]`);
   repaint drifted details with inpaint.
5. Expressions and outfits: `duplicate_layer` the head or body and change one thing at a time.
6. Label the views with `add_svg` text so the sheet reads clearly.
