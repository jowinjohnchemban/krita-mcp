---
name: textures-materials
description: Make surfaces look real with photo textures and materials - stone, wood, fabric, skin, metal, ground, skies - tiled, clipped to painted shapes and blended in.
---
# Textures and materials

1. Find real material: `search_assets("weathered wood", kind="textures")` (CC0, Poly Haven), or
   `kind="hdris"` for skies and backdrops, `kind="photos"` for reference (keep the attribution).
2. `import_asset(id, kind="textures", tile_size=...)` - a tile size that matches the object's real scale
   in the picture (bigger tiles for closer objects).
3. Put the texture directly above the painted shape in the same group and
   `update_layer(clipped=true, blend_mode="overlay" or "multiply", opacity=0.4-0.8)`, so it follows the shape
   and keeps your lighting.
4. Follow the form: `transform_layer` to match the perspective; darken and lighten with the scene's light
   instead of leaving it flat.
5. Check with `canvas_snapshot(max_size=768)`; if it looks pasted on, lower the opacity or `match_colors`.
