---
name: poster-design
description: Design posters, flyers, covers and title cards - layout grid, hierarchy of headline and details, image and type together.
---
# Poster design

1. Format and grid: size (A3, 18x24 in, or a screen size) and `set_guides` for margins and columns.
2. Hierarchy: one dominant element (image or huge headline), then the subheading, then the details (date,
   place) small. At most two typefaces.
3. Image: generate or paint the key visual leaving calm space for text (say so in the prompt:
   "empty sky at the top for a title").
4. Type with `add_svg` <text>: big, tight headline; contrast against the image (a gradient shape behind if
   needed); align everything to the guides.
5. Check at a glance: `canvas_snapshot(max_size=384)` - the headline must read instantly.
6. Export for print (see print-ready) or screen (PNG/JPEG).
