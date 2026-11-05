# Workflows

These are recipes that work well with an AI agent. Tool names refer to the [tool reference](tools.md).
In every workflow, have the agent **look** (`canvas_snapshot`) and **measure** (`analyze_image`)
after each meaningful step.

## Paint a picture

1. `document_new(width=1600, height=1000)`, then `create_layer` for each element (sky, background,
   subject, details).
2. Block in shapes and gradients with `draw_shapes`. Paint with `brush_stroke` using real presets
   (`list_brush_presets(filter="ink")`, `"pencil"`, `"airbrush"`, `"wet"`). Per-point pressure
   tapers strokes.
3. Use `update_layer(clipped=true)` to paint shading clipped to the shape below, and
   `alpha_locked=true` to recolour without going outside the lines.
4. Check the result: `canvas_snapshot(grid=100)` shows exact coordinates; `analyze_image` reports the
   palette, contrast and layout.
5. Export: `export_image("art.png")` or `save_document("art.kra")`.

## Retouch a photo

1. `document_open("photo.jpg")` (put the file in the workspace first).
2. `auto_enhance()` for a quick tonal and colour-cast fix, then fine-tune with `adjust_image`:
   - `levels`, `curves {points: [[0,0],[64,50],[192,210],[255,255]]}` (an S-curve), `exposure`, `temperature`
   - `hue_saturation`, `color_balance`, `sharpen`, `denoise`
3. To affect only part of the image, select it first: `set_selection(kind="ellipse"...)`, a colour
   range, or `contiguous=true` (magic wand). Then `modify_selection("feather", 20)`.
4. For edits you can tweak later, use `adjust_image(..., non_destructive=true)`. It adds an editable
   filter mask instead of changing pixels.
5. Finishing touches: `add_vignette()`. For high-precision grading, use
   `convert_color_space(depth="U16")` first; convert back to U8 before using the pixel tools.

## Inpainting with an external image model

1. Select the area to replace (`set_selection`), feather it a little, and grow it slightly.
2. `export_image("region.png", x, y, width, height)` exports the area around it;
   `export_selection_mask("mask.png")` exports the mask.
3. Run any inpainting or image-to-image model on those files.
4. `import_image(path="result.png", fit="selection")` stretches the result over the selection and
   blends it in through the soft mask, on a new layer.
5. `canvas_snapshot` to check, then adjust or `undo`.

## Vector art and text

`add_svg` accepts normal SVG, for example
`<path d="..." fill="#e0603a"/><text x="40" y="80" font-size="48">Title</text>`, and creates editable
Krita vector shapes. Bare elements use canvas pixel coordinates. `list_vector_shapes` and
`remove_vector_shapes` manage them afterwards.

## Line art and flat colours

1. Draw or import line art on a paint layer.
2. `colorize_lineart(layer="Lines", regions=[{"color": "#f0c8a0", "points": [[x, y], ...]}, ...])`
   needs only one scribble per area. Krita's Colourise Mask floods each colour up to the lines.
3. Paint shading on clipped layers above.

## Procedural textures and tones

`create_layer(type="filllayer", generator=...)`:
- `generator="seexpr", generator_config={"script": "$c = noise(8*$u, 8*$v); [$c, $c*0.6, 1-$c]"}`
  gives procedural textures (SeExpr).
- `generator="screentone"` gives manga tones (`frequency_x`, `rotation`, `foreground_color`, ...).
- `generator="multigrid"`, `"gradient"` and `pattern="<pattern name>"` are also available.

## Frame-by-frame animation

1. `animation_set(fps=12, start=0, end=23)`.
2. For each key pose, call `keyframe(frame=N, kind="blank", layer="Character")` and then draw on that
   layer. Drawing tools act on the current frame.
3. `krita_action("toggle_onion_skin")` shows neighbouring frames; `animation_contact_sheet()` shows
   the whole sequence in one image so the agent can check motion.
4. `render_video("walk.mp4")` (or `.gif`, `.webm`, `.mov`) or `export_animation_frames()`.

## Edit a video

1. Put the clip in the workspace. `video_info("clip.mp4")`, then
   `import_video("clip.mp4", start=2, duration=5, fps=12, width=960)` loads it as an animated layer.
2. Grade every frame:
   `for_each_frame([{"tool": "adjust_image", "args": {"operation": "temperature", "settings": {"temperature": 25}}}])`.
3. Paint on single frames (`animation_set(frame=N)` then drawing tools), or add titles on a separate
   layer.
4. `render_video("clip_graded.mp4")`.

Krita's bundled ffmpeg encodes H.264 (OpenH264), VP9, ProRes, GIF and APNG. Audio is not carried over.

## Talk to the user

- `notify_user("Done - check the sky layer")` shows a message on the canvas.
- `view_control(fit=true)` or `view_control(zoom=2, center_x=..., center_y=...)` shows the user what
  changed.
- `user_tool_state(foreground="#e0603a", preset="Ink")` hands the user the colour or brush you used.
- `krita_action("create_snapshot")` saves a version in Krita's Snapshot docker before risky changes.
