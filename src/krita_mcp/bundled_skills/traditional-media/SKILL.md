---
name: traditional-media
description: Make digital work look like traditional media - watercolor, gouache, oil, pencil, charcoal, ink - with real Krita brushes, paper texture and imperfect edges.
---
# Traditional media look

1. Pick matching Krita presets: `list_resources(type="preset", filter="watercolor")` (or "oil", "pencil",
   "charcoal", "ink"); use them for every stroke.
2. Paper: `search_assets("paper", kind="textures")` + `import_asset`, at the top with
   `blend_mode="multiply"` and low opacity; a warm off-white background instead of pure white.
3. Watercolor: light to dark in transparent layers (multiply), soft wet edges, leave paper white for
   highlights. Oil and gouache: opaque, dark to light, visible brush direction.
4. Pencil and charcoal: varied pressure (size/opacity changes along `brush_stroke`), hatching for values.
5. Imperfection sells it: slight colour variation, uneven edges, no perfect gradients.
6. With an image model: `generate_image(mode="img2img", style="watercolor" | "oil_painting" | "sketch",
   strength=0.5)` over your painting, then paint back on top.
