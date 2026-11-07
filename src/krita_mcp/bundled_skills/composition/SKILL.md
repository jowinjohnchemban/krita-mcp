---
name: composition
description: Plan or improve a picture's composition - focal point, rule of thirds, leading lines, balance, framing, cropping and aspect ratio.
---
# Composition

1. One focal point: place it on a thirds intersection (`set_guides` at 1/3 and 2/3) or the golden ratio;
   the highest contrast and sharpest detail go there.
2. Lead the eye: lines (roads, rivers, arms, light) point towards the focal point; avoid lines leading out.
3. Depth: foreground, middle ground and background in different value groups; overlap shapes.
4. Balance: a big quiet mass against a small busy one; leave breathing room in the direction a subject looks
   or moves.
5. Check: `canvas_snapshot(max_size=384, grid=...)` - at thumbnail size a good composition still reads.
6. Improve an existing picture with `crop_image` (to a better ratio: 4:5, 16:9, 1:1) or extend it with
   `outpaint` to give the subject room.
