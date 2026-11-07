---
name: portrait-retouch
description: Retouch portraits naturally - skin, blemishes, under-eye shadows, eyes, teeth, hair flyaways, face light - without the plastic look.
---
# Portrait retouch

1. Work on copies (`duplicate_layer`) and keep the original visible underneath to compare.
2. Blemishes: small selections over each spot, then inpaint with "natural skin texture" at low strength, or
   sample nearby skin (`sample_color`) and dab with a soft brush at low opacity.
3. Skin: never blur the whole face. Even out tones with a soft brush on a low-opacity layer; keep pores.
4. Eyes: a little brightness on the irises and whites (`adjust_image("dodge")` on a selection, small amounts),
   sharpen the eyes slightly.
5. Under-eye shadows and teeth: lighten gently, never to pure white.
6. Light: dodge and burn on a soft-light layer to sculpt the face; finish with a subtle overall grade.
7. Check at 100% on the face region with `canvas_snapshot(x, y, width, height)`.
