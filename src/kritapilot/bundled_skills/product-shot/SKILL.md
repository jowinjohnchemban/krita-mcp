---
name: product-shot
description: Product photography and mockups - clean studio shots, lifestyle scenes, packshots on white, shadows and reflections, putting a design onto a mockup.
---
# Product shot

1. Cut the product out: `remove_background(mode="mask")` (or `method="color"` on a plain background).
2. Studio packshot: a white or soft gradient background layer; a soft contact shadow below (multiply layer,
   blurred dark ellipse) and optionally a reflection (`duplicate_layer`, `flip(target="layer",
   direction="vertical")`, fade with low opacity).
3. Lifestyle scene: generate or import a background (`generate_image(style="product")` without the product,
   or a real photo), place the product, match light and colour (see photo-composite).
4. Mockups: put a flat design on a product photo with `import_image`, `transform_layer` to the surface's
   perspective, `blend_mode="multiply"` so fabric or paper texture shows through.
5. Sharp and clean: `adjust_image("sharpen")` gently; export PNG for transparent cut-outs.
