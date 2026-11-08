---
name: game-assets
description: Make game art - sprites and sprite sheets, icons, items, UI elements, tiles, character portraits - with consistent size, style and transparency.
---
# Game assets

1. Agree on the format: size per asset (e.g. 128x128), transparent background
   (`document_new(background="transparent")`), style (pixel art, hand-painted, flat vector) and palette.
2. Sprite sheets: a canvas of N x M cells with `set_guides` on the cell borders; one frame per cell, same
   baseline and centre so the animation does not jump.
3. Icons and items: strong silhouette, readable at 32 px, light from the top-left for all assets.
4. Generated assets: same prompt template and seed for a set; `remove_background` for transparency;
   `match_colors` so a set looks related.
5. Export each asset with `export_image("assets/name.png", layer=..., full_canvas=false)` (cropped to the
   content) or the whole sheet.
