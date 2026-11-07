---
name: art-critique
description: Critique and improve artwork - an honest review of composition, values, colour, anatomy, perspective and finish, then a short list of fixes; use when asked for feedback.
---
# Art critique

1. Look: `canvas_snapshot(max_size=1024)` and `analyze_image` (palette, contrast, light, sharpness, layout).
2. Optionally a second opinion: `ask_vision("Critique this artwork: composition, values, colour, anatomy,
   perspective. List the 3 biggest problems.")`.
3. Judge in this order (the big things first): read at thumbnail size, value structure, composition,
   colour harmony, drawing and anatomy, edges, details.
4. Report kindly and concretely: what works, then the top 3 fixes with where and how ("the face is lost in
   the mid-tones: darken the background behind it by about 20%").
5. Offer to apply them, each on its own layer so the user can compare and undo.
