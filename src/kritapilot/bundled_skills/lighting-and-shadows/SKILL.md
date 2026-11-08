---
name: lighting-and-shadows
description: Paint believable light - light direction, cast and form shadows, rim light, glow, god rays, time of day - on an illustration or photo.
---
# Lighting and shadows

1. Decide one key light: direction, colour (warm sun, cool sky fill) and hardness. Write it down.
2. Shadows on a "Shadows" layer (`blend_mode="multiply"`), in a cool desaturated colour, cast away from the
   light; form shadows follow the object's shape; contact shadows darkest where objects touch the ground.
3. Light on a "Light" layer (`blend_mode="screen"` or `"add"`): highlights face the light; rim light along
   edges opposite the viewer; a soft glow around bright sources (big soft brush, low opacity).
4. God rays and haze: long soft polygons from the source with `draw_shapes`, then `adjust_image("blur")`.
5. Check values in greyscale: `analyze_image` or a temporary `adjust_image("desaturate")` on a copy.
6. For photos: `adjust_image("dodge")` / `("burn")` locally on a selection.
