---
name: sticker-design
description: Design stickers and cut-out graphics - bold shapes, white die-cut border, transparent background, ready for print or messaging apps.
---
# Sticker design

1. `document_new(width=1024, height=1024, background="transparent")`.
2. A simple, bold subject with thick outlines and flat colours (vector with `add_svg`, or painted).
   Generated art: `generate_image(..., style="illustration")`, then `remove_background(mode="mask")`.
3. White border: select the subject's shape (`set_selection(kind="layer", layer=...)` or by alpha),
   `modify_selection("grow", 20)`, `modify_selection("smooth", 8)`, then on a new layer below
   `fill_selection("#ffffff")`; add a subtle grey outline or shadow under it.
4. Check at small size: `canvas_snapshot(max_size=256)`.
5. Export PNG with transparency (`export_image("sticker.png")`); for printing see print-ready.
