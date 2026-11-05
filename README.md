# krita-mcp

Let any MCP-capable AI agent (Claude, ChatGPT, Gemini, Cursor, …) see, edit and generate art **inside a running Krita**.

```
 AI agent ──MCP (stdio or HTTP)──▶ krita-mcp ──127.0.0.1 + token──▶ Krita plugin ──▶ your canvas
```

Two small pieces:

| Piece | Where it runs | What it does |
|---|---|---|
| `plugin/pykrita/krita_mcp_bridge` | inside Krita (Python plugin) | Local socket server. Executes commands on Krita's GUI thread. |
| `src/krita_mcp` | a normal Python process | The MCP server. Turns tool calls into bridge commands. |

## Install

**1. Plugin** (Krita 5.2+ or 6):
```bash
python scripts/install_plugin.py        # copies into Krita's pykrita folder
```
Restart Krita → *Settings ▸ Configure Krita ▸ Python Plugin Manager* → tick **Krita MCP Bridge** → restart Krita again.
(Manual route: *Settings ▸ Manage Resources ▸ Open Resource Folder*, copy the contents of `plugin/pykrita/` into its `pykrita/` folder.)
On start-up it creates `~/.krita-mcp/token` (mode 0600) and listens on `127.0.0.1:5679`.

**2. MCP server** (Python ≥ 3.10):
```bash
pip install .            # or:  uvx --from . krita-mcp
```

## Connect an agent

**Claude Code** — `claude mcp add krita -- krita-mcp`

**Claude Desktop / Cursor / Gemini CLI / any stdio client** — add to the client's MCP config
(`claude_desktop_config.json`, `~/.gemini/settings.json`, …):
```json
{ "mcpServers": { "krita": { "command": "krita-mcp" } } }
```

**Clients that only accept a remote URL (e.g. ChatGPT connectors)** — run the HTTP transport:
```bash
KRITA_MCP_HTTP_TOKEN=<long-random-secret> krita-mcp --transport http --port 8765
# endpoint: http://127.0.0.1:8765/mcp   header: Authorization: Bearer <secret>
```
Such clients need a public HTTPS URL, so you would put a tunnel/reverse proxy in front. Think before you do:
whoever can reach that URL can draw on your canvas and write files in your workspace. Only expose it if your
client can send the bearer header (or you add your own auth proxy). I have not tested any particular hosted client.

Then ask your agent: *"Create a 1200×800 canvas, paint a sunset gradient with a sun and mountains, look at it, and improve it."*

## Tools (55)

**Documents** — `krita_status` · `document_new/open/activate/info/close` · `save_document` · `export_image`
(whole image, one layer, or a region; jpg/webp quality) · `resize_canvas` · `scale_image` · `crop_image` ·
`rotate_image` · `flip` (image or layer) · `flatten_image` · `set_guides`

**Seeing and reading the image** — the model can *look* and also *measure*:
- `canvas_snapshot` — the picture (or a zoomed region, or a single layer); `grid=100` overlays labelled
  canvas coordinates so the model can read off exact positions.
- `analyze_image` — dominant palette with shares, brightness, contrast, sharpness, coverage, content bounds,
  and a coarse layout grid of mean colours.
- `read_pixels` (exact colours of a small block) · `sample_color` (one pixel) ·
  `find_color` (where a colour occurs: regions with bbox / centroid / share).
- `view_image` / `list_workspace` — look at reference images or generated files without importing them.

**Layers** — `list/create/update/delete/duplicate/transform_layer` · `arrange_layer` · `merge_down`.
`create_layer` makes paint, vector, group, **fill** (colour/pattern), **filter** (non-destructive
adjustment), **clone**, **file** layers and **filter / transparency masks**. `update_layer` covers opacity,
blend mode, visibility, lock, alpha lock, clipping ("inherit alpha"), colour label and position.

**Painting** —
- `brush_stroke` — Krita's **real brush engine** with any preset (`list_brush_presets`), size, colour,
  opacity, flow, pressure per point, eraser, smoothing, closed/filled paths.
- `draw_shapes` — crisp anti-aliased geometry: line, polyline, bezier, rect, ellipse, polygon, text,
  linear/radial gradients.
- `add_svg` / `list_vector_shapes` / `remove_vector_shapes` — **editable vector shapes and text** from SVG.
- `apply_filter` (any of Krita's ~50 filters; `list_filters`, `describe_filter`) · `fill_selection`.

**Selections** — `set_selection`: rect, ellipse, polygon, a layer's opaque pixels, **colour range** or
**magic wand** (`contiguous=true`); add / subtract / intersect · `modify_selection` (grow, shrink, feather,
border, smooth, invert) · `export_selection_mask`.

**Generative-AI round trip (inpainting)** — select the area → `export_image(x, y, width, height)` +
`export_selection_mask` → run any inpainting / image model → `import_image(fit="selection")` composites
the result back through the (soft) selection. `import_image` / `import_image_from_url` also bring in
whole generated images as layers.

**History** — `undo` / `redo` revert whole AI edits (pixels, properties, layers, selection, vectors; see
below) · `history`.

**Resources and animation** — `list_resources` (presets, patterns, gradients, palettes…) · `palette_colors` ·
`animation_info` · `animation_set` (fps, range, current frame) · `add_keyframe` · `export_animation_frames`.

**`batch`** — run many tool calls in one request (`[{"tool": "...", "args": {...}}, ...]`), with images
returned at the end. Fewer round trips for the model.

## Security model

- Bridge binds **loopback only** (it refuses anything else) and every request needs the secret token; compared in constant time.
- File access is confined to a **workspace folder** (`~/KritaMCP`, override with `KRITA_MCP_WORKSPACE`). Paths are resolved (symlinks, `..`) before the check, and extensions are allow-listed. The agent cannot read or write elsewhere.
- No generic "run any Krita action" or "run Python" tool exists — only the commands above.
- `add_svg` rejects DOCTYPE/ENTITY declarations and external references (`href`/`url()` must point to `#ids`; images only as `data:` URIs), so SVG cannot pull in local files.
- `import_image_from_url` fetches public hosts only, no redirects, 25 MB cap. (DNS-rebinding between check and fetch is not fully closed.)
- Input limits: canvas ≤ 16384 px / 100 MP, ≤ 5000 shapes per call, 64 MB per bridge message.
- HTTP transport requires a bearer token and refuses non-loopback binds without one.

Env vars: `KRITA_MCP_HOME` (token dir), `KRITA_MCP_PORT`, `KRITA_MCP_WORKSPACE`, `KRITA_MCP_AUTOSTART=0` (don't auto-start the bridge; use *Tools ▸ Scripts ▸ Krita MCP Bridge: start / stop*).

## Undo

Krita's scripting API puts only some edits on Krita's undo stack (adding/removing layers, brush
strokes, transforms, blend-mode changes, selections). Pixel writes, filters, opacity and moves are
**not** recorded. So the bridge keeps its own history: before each edit it backs up what Krita will not
record (pixels, layer properties, selection), and it measures which steps Krita did record. `undo`
then reverts exactly one AI edit, and `redo` re-applies it.

If you edit the document in Krita between AI edits, `undo` refuses rather than overwrite your work.
`undo(native=true)` steps through Krita's own history instead. History is kept in memory (last 100
edits / 256 MB compressed) and is per document.

## Status & limitations

- **Tested against Krita 5.3.4 on Windows** with `scripts/smoke_krita.py` (47 checks, including an undo
  matrix that verifies every kind of edit is restored exactly). Krita 6 / PyQt6 is covered by the unit tests
  only.
- Raster edits require **RGBA 8-bit** layers (documents from `document_new` are). Other modes return a clear error.
- `brush_stroke` uses the brush settings of the view showing the document. It temporarily sets preset,
  size, colour, opacity and flow, then restores yours. Strokes with per-point pressure are drawn segment by
  segment.
- Commands run on Krita's GUI thread; a long filter blocks the UI for its duration. If a modal dialog is
  open, calls time out until it is closed.
- Vector bounding boxes include Krita's stroke/miter padding.

