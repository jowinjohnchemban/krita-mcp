---
name: comic-storyboard
description: Comic pages, manga, storyboards and panel layouts - panels, gutters, shot planning, speech balloons and lettering.
---
# Comic page / storyboard

1. Plan the beats first: one line per panel (shot: wide, medium or close-up; who; what happens).
2. Panels: a "Panels" vector layer with `add_svg` rects (black 3-6 px borders, even gutters), or
   `set_guides` for a grid. Vary panel size with the pace of the story.
3. Rough each panel on a "Sketch" layer in one `batch` per panel; keep characters on model (same
   proportions and costume) and respect the 180-degree rule between shots.
4. Ink on its own layer, flats with `colorize_lineart`, shadows on a multiply layer.
5. Lettering last with `add_svg`: ellipse or rounded-rect balloons with a tail path, white fill, <text> in a
   comic font. Reading order left to right, top to bottom; never cover faces.
6. For a storyboard, add a caption under each panel (action, camera move, duration).
