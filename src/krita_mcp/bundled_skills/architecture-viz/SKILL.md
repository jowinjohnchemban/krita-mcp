---
name: architecture-viz
description: Architecture and interior visualization - render a room, building or floor plan sketch into a realistic image, try materials, furniture and lighting, day or night versions.
---
# Architecture visualization

1. Start from structure: a perspective sketch or a photo of the space on its own layer.
2. Render with structure locked: `generate_image(prompt with style, materials and light,
   control=[{"layer": "Sketch", "type": "lineart"}], style="architecture")`; for straight lines `"mlsd"`
   control if the backend has it.
3. Prompts like an architectural photographer: "interior, Scandinavian, oak floor, linen sofa, large window,
   soft morning light, 24mm lens, eye level".
4. Variations: keep the seed and change one material at a time; inpaint a selection for single objects
   (a new sofa, a different floor).
5. Day/night: duplicate and grade (see color-grade), or img2img with "dusk, warm interior lights".
6. Straighten verticals with `transform_layer` if needed; export at full size.
