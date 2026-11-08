# Undo, safety and limits

## Undo

Krita's scripting API records only some edits on Krita's own undo stack: adding or removing layers,
brush strokes, transforms, blend-mode changes, and selections. Pixel writes (`draw_shapes`, imports,
fills), filters, opacity changes and moves are **not** recorded. So the bridge keeps its own history
for AI edits:

- Before each editing command, it backs up whatever Krita won't record: layer pixels and position,
  layer properties, the selection, and vector shapes.
- It measures how many steps Krita itself recorded during the command.
- `undo` reverts exactly **one AI command**. It steps Krita's own undo for the recorded part, then
  restores the backups wherever the state still differs. `redo` re-applies it.

Each tool call is one undo step. A `batch` or `for_each_frame` is many steps.

**Your own edits are protected.** If the document was changed in Krita (by you) after the AI's last
edit, `undo` refuses rather than overwrite your work. `undo(native=true)` (or Krita's own Ctrl+Z)
steps through Krita's history instead.

History is per document, kept in memory (the last 100 edits / 256 MB compressed), and dropped when
the document closes.

## Security model

- **Loopback only.** The bridge refuses to bind anything except `127.0.0.1` / `::1`.
- **Token.** Every request must carry the token from `~/.kritapilot/token` (created with mode 0600),
  compared in constant time. You can regenerate it in the panel.
- **Workspace sandbox.** File access (open, import, export, video, frames) is confined to the
  workspace folder. Paths are resolved (symlinks, `..`) before the check, and file types are
  allow-listed.
- **No code execution.** There is no "run Python" or "run any Krita action" tool. `krita_action` uses
  a curated list of dialog-free commands. ffmpeg is called with fixed arguments and no shell.
- **SVG** must not contain DOCTYPE/ENTITY declarations or external references. `href` and `url()`
  may only point to `#ids`; images must be `data:` URIs. This stops SVG from pulling in local files.
- **URL imports** (`import_image_from_url`) fetch public hosts only, follow no redirects, and have a
  25 MB cap. DNS rebinding between the check and the fetch is not fully closed.
- **HTTP transport** requires a bearer token and refuses non-loopback binds without one.
- **Access modes** in the panel: *Read only* lets the AI look but not edit; *Paused* blocks it
  entirely.

## Limits

- Canvas ≤ 16384 px per side and 100 MP; ≤ 5000 shapes per `draw_shapes` call; 64 MB per bridge message.
- Pixel tools (`draw_shapes`, `adjust_image` tone tools, `read_pixels`, `find_color`, colour
  selections) need **RGBA 8-bit** layers. Documents from `document_new` are; otherwise use
  `convert_color_space`.
- Video import: up to 1000 frames per import. Rendering re-encodes video only (no audio).
- Commands run on Krita's GUI thread, so long filters and video work block the UI while they run
  (the window still repaints). A modal dialog open in Krita makes calls time out until it is closed.
- `brush_stroke` temporarily uses the view's brush settings and restores yours afterwards. Strokes
  with per-point pressure are drawn segment by segment.
- Vector bounding boxes include Krita's stroke/miter padding.
