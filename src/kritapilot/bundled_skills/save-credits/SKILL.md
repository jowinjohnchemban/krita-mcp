---
name: save-credits
description: Spend as little as possible on paid AI - choose free or local services, small previews, one image at a time, the cheapest step first; use when the user mentions cost, credits, budget or free.
---
# Save credits

1. Check what is connected: `ai_backends()`. Local services (ComfyUI, AUTOMATIC1111) cost nothing; the free
   ones (Pollinations, AI Horde) cost nothing once the user turns them on in AI Chat > Services.
   Prefer them for drafts: `generate_image(..., backend="pollinations")`.
2. Draft small, finish big: generate at a modest size first, and `upscale_image` only the final pick.
3. One image unless the user wants options (`count=1`); refine with inpaint on a selection instead of
   regenerating the whole picture.
4. Free tools first: camera moves (`camera_move`) before AI video; Krita filters and `adjust_image` before
   AI edits; `remove_background(method="color")` on plain backgrounds.
5. Look less: one `canvas_snapshot(max_size=512)` per pass of work, and `analyze_image` (numbers, no
   picture) when you only need colours or values.
6. Batch edits (`batch`) and never repeat a call that failed the same way - change the approach.
7. Video models are the most expensive calls: confirm with the user before generating a clip.
