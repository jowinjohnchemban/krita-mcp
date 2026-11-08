---
name: color-grade
description: Colour grading and mood - cinematic looks, warm or cool tones, teal and orange, film look, day to night, matching colours across images or video frames.
---
# Colour grade

1. Read the picture first: `analyze_image` (exposure, contrast, palette, colour cast).
2. Correct before you style: `auto_enhance`, or `adjust_image` levels / temperature, until neutrals are
   neutral and nothing clips.
3. Style on a `duplicate_layer` copy (curves and levels change pixels), and use filter masks the user can
   tune later for hue_saturation and color_balance (`adjust_image(..., non_destructive=true)`):
   - cinematic teal and orange: cool the shadows, warm the highlights (curves per channel), calm the greens;
   - film: lifted blacks (curves), soft contrast, a little noise, `add_vignette(strength=0.25)`;
   - night: lower exposure, cooler temperature, keep light sources warm on their own layer.
4. Unify composited parts with `match_colors(layer, reference_layer)`.
5. Compare with `canvas_snapshot(max_size=512)` before and after; small steps, not one extreme setting.
6. Video: the same grade on every frame with `for_each_frame([{"tool": "adjust_image", "args": {...}}])`.
