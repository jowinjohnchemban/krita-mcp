---
name: extend-canvas
description: Extend or reframe a picture - outpaint beyond the edges, change aspect ratio (square to wide or tall), add space around a subject, uncrop.
---
# Extend the canvas

1. Decide the new size or ratio, e.g. 1:1 to 16:9 or 9:16 for stories.
2. `outpaint(prompt describing the surroundings, left=..., right=..., top=..., bottom=...)` - pixels to add on
   each side; it continues the scene and blends the overlap.
3. Big extensions in steps (no more than half the image per side at a time) look better than one huge jump.
4. Check the seams with `canvas_snapshot(max_size=768)`; inpaint visible joins with a selection along them.
5. Without an image model: `resize_canvas` and paint the new area, or fill with a blurred, stretched copy
   for plain backgrounds.
