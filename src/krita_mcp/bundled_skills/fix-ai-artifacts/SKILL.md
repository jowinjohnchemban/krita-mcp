---
name: fix-ai-artifacts
description: Fix typical AI image problems - bad hands and fingers, warped faces or eyes, garbled text, extra limbs, melted objects, seams - without regenerating the whole picture.
---
# Fix AI artifacts

1. Find the problems: `canvas_snapshot(max_size=1024)` and zoom in on suspicious areas with a region
   (`canvas_snapshot(x=..., y=..., width=..., height=...)`): hands, eyes, teeth, text, edges of objects.
2. Fix one area at a time: `set_selection` a generous box or polygon around it, `modify_selection("feather",
   12)`, then `generate_image(prompt describing just that part, e.g. "a relaxed hand with five fingers",
   mode="inpaint")`. Keep the rest of the picture untouched.
3. Hands: describe the pose simply; if it keeps failing, hide the hand (pocket, behind an object) or paint
   it by hand on a new layer.
4. Text: AI text is rarely right - remove it with inpaint and set real text with `add_svg` <text>.
5. Faces: inpaint at a larger generation size (`size=1024`) so the face gets enough pixels.
6. Blend the fix: `match_colors(fixed_layer, reference_layer)` and a soft eraser edge.
