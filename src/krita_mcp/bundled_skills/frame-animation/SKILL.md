---
name: frame-animation
description: Hand-drawn frame-by-frame animation in Krita - loops, simple character motion, a bouncing ball, blinking, flipbook clips rendered to GIF or MP4.
---
# Frame animation

1. Set up: `animation_set(fps=12, start=0, end=23)` (12 fps is plenty for most loops).
2. Key poses first: `keyframe(frame, "blank", layer)` at the extremes (e.g. 0, 6, 12, 18), go to each with
   `animation_set(frame=...)` and draw the pose. Then the in-betweens.
3. Reuse drawings: `keyframe(frame, "duplicate")` copies the previous drawing so you change only what
   moves; `hold` keeps a pose longer.
4. Motion: ease in and out (closer spacing near the extremes), squash and stretch, overlapping action for
   hair or tails. In a loop the last frame must lead into the first.
5. Review with `animation_contact_sheet` (cheaper than a snapshot per frame) and fix the odd frames.
6. The background goes on its own, non-animated layer. Render with `render_video("loop.gif")` or `.mp4`,
   or `export_animation_frames`.
