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

## Tools

`krita_status` · `document_new/open/activate/info/close` · `save_document` · `export_image` · `resize_canvas` · `scale_image` ·
`canvas_snapshot` (returns an image so a multimodal model can *see* the result, optionally zoomed into a region) ·
`list/create/update/delete/duplicate/transform_layer` · `draw_shapes` (line, polyline, bezier, rect, ellipse, polygon, text, linear/radial gradients, opacity) ·
`import_image` / `import_image_from_url` (**bring in output from an image-generation model** and keep editing it as a layer) ·
`set_selection` · `modify_selection` · `fill_selection` · `list_filters` · `describe_filter` · `apply_filter` · `undo` · `redo`

Typical "generate art" loop: generate with any image model → `import_image` → `apply_filter` / `draw_shapes` / adjust layers → `canvas_snapshot` to check → `export_image`.

## Security model

- Bridge binds **loopback only** (it refuses anything else) and every request needs the secret token; compared in constant time.
- File access is confined to a **workspace folder** (`~/KritaMCP`, override with `KRITA_MCP_WORKSPACE`). Paths are resolved (symlinks, `..`) before the check, and extensions are allow-listed. The agent cannot read or write elsewhere.
- No generic "run any Krita action" or "run Python" tool exists — only the commands above.
- `import_image_from_url` fetches public hosts only, no redirects, 25 MB cap. (DNS-rebinding between check and fetch is not fully closed.)
- Input limits: canvas ≤ 16384 px / 100 MP, ≤ 5000 shapes per call, 64 MB per bridge message.
- HTTP transport requires a bearer token and refuses non-loopback binds without one.

Env vars: `KRITA_MCP_HOME` (token dir), `KRITA_MCP_PORT`, `KRITA_MCP_WORKSPACE`, `KRITA_MCP_AUTOSTART=0` (don't auto-start the bridge; use *Tools ▸ Scripts ▸ Krita MCP Bridge: start / stop*).

## Status & limitations — please read

- **Not yet run inside a real Krita.** The sandbox this was built in has no Krita. What *is* tested (`pytest`): the bridge protocol, auth, path sandbox, the MCP server end-to-end over stdio, the HTTP auth, and the drawing/import/snapshot/layer code against a fake `krita` module using real Qt under **both PyQt5 and PyQt6**. What is *not*: calls into the real Krita API (`createDocument`, `Filter.apply`, `Selection.*`, `Node.scaleNode/rotateNode`, `doc.exportImage`, etc.). Expect to fix a few API-detail mismatches on first contact; the handlers are short and isolated in `handlers.py`.
- Raster edits require **RGBA 8-bit** layers (documents from `document_new` are). Other modes return a clear error.
- Drawing uses Qt painting, not Krita's brush engine, so there are no pressure/texture brush strokes. For painterly results, generate with an image model and import.
- Whether API pixel writes land as individual undo steps is unverified; `undo` triggers Krita's normal undo action.
- Commands run on Krita's GUI thread; a long filter blocks the UI for its duration, and an open modal dialog makes calls time out.

## Develop

```bash
pip install -e .[test] PyQt6
QT_QPA_PLATFORM=offscreen pytest                 # PyQt6 path
KRITA_TEST_QT=5 QT_QPA_PLATFORM=offscreen pytest # PyQt5 path (needs PyQt5)
```
Adding a tool = one `@handler("x.y")` in `handlers.py` + one `@mcp.tool()` in `server.py`.
