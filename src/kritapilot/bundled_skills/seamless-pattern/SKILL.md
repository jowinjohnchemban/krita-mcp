---
name: seamless-pattern
description: Make seamless tileable patterns and textures - fabric prints, wallpaper, game textures, backgrounds that repeat without visible seams.
---
# Seamless pattern

1. Choose the tile size first (e.g. 512 x 512) with `document_new`.
2. Motifs: arrange them with space in between; anything crossing an edge must continue on the opposite
   edge at the same position (draw it twice, offset by exactly the tile width or height).
3. With an image model: `generate_image("seamless tileable pattern of ..., flat repeat")`; models are not
   always truly seamless, so check.
4. Check the repeat: `export_image("tile.png")`, then on a bigger test document
   `import_image("tile.png", fit="tile", width=256)` and look with `canvas_snapshot(max_size=768)`.
   Seams show as lines or jumps.
5. Fix seams by inpainting across the visible joins on the test document, then re-crop one tile.
6. For realistic surfaces, Poly Haven textures are already tileable: `search_assets(kind="textures")`.
