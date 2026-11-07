---
name: landscape-environment
description: Paint or generate landscapes and environments - mountains, forests, seas, skies, cities - with depth, atmosphere and weather.
---
# Landscape and environment

1. Horizon and composition first (`set_guides` at thirds): sky or land dominant, never 50/50.
2. Layers by distance: Sky, Far, Middle, Near. Far layers lighter, cooler and less contrasty (atmospheric
   perspective); near layers darker, warmer, more detailed.
3. Sky first - it sets the light and colour of everything else. A real sky: `search_assets(kind="hdris")`.
4. Big shapes with `draw_shapes` polygons, then texture with brushes or `import_asset` textures clipped to
   the shapes (see textures-materials).
5. Atmosphere: mist between layers (soft white gradient, low opacity), light rays, reflections in water
   (flipped, darker, blurred copy).
6. Realistic version: `generate_image(control=[{"layer": "Sketch", "type": "scribble"}], style="landscape")`.
