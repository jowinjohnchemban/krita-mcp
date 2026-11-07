---
name: print-ready
description: Prepare artwork for printing - size and DPI for posters, cards, books and merch, bleed and safe area, colour mode, and the right export format.
---
# Print ready

1. Size at 300 ppi: `document_new(width, height, resolution_ppi=300)`. A4 = 2480x3508, A3 = 3508x4961,
   US Letter = 2550x3300, 4x6 in = 1200x1800, business card 3.5x2 in = 1050x600.
2. Bleed: add 3 mm (about 36 px at 300 ppi) on every side with `resize_canvas` and extend the background
   into it (`outpaint` or painting); keep text 5 mm inside the trim (`set_guides`).
3. Too small? `upscale_image` (AI) for photos and paintings; vector parts stay sharp at any size.
4. Colour: work in RGB; convert only if the printer asks for CMYK: `convert_color_space(model="CMYKA")`
   on a copy, check that saturated blues and greens still look right.
5. Export: `export_image("print.tif")` or PDF via the printer's needs; PNG for print-on-demand shops;
   JPEG quality 95+ if they want JPEG.
