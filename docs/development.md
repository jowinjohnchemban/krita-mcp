# Development

## Layout

```
plugin/pykrita/
  kritapilot_plugin.desktop     Krita plugin manifest
  kritapilot_plugin/
    __init__.py                registers the extension (only inside Krita)
    extension.py               starts/stops the bridge, settings, Tools ▸ Scripts actions
    dock.py                    the "AI Bridge (MCP)" docker
    protocol.py                loopback socket server, token, newline-delimited JSON (Qt-free)
    handlers.py                documents, layers, drawing, brushes, selections, filters, dispatch
    analysis.py                reading images: analyze, read_pixels, find_color, workspace files
    media.py                   photo adjustments, video (ffmpeg), animation helpers
    controls.py                curated Krita actions, view, user tool state, colourise
    extras.py                  vectors (SVG), resources, palettes, guides, basic animation
    history.py                 undo/redo for AI edits
    genai.py                   generative round trips: region and mask out, results back as layers
    pixels.py                  Qt-free pixel maths (masks, flood fill, LUTs, statistics)
    util.py                    errors, workspace sandbox, colour parsing (Qt-free)
    chat.py                    the "AI Chat" docker: provider menu, Services, setup dialogs, queue
    dialogs.py                 popups Krita opens during a command: read, cancel, report
    logview.py                 the Logs window (follows a log file live)
    tracelog.py                one dated line per action, keys masked (same file as the server's)
    watchdog.py                main-thread heartbeat: a freeze writes every stack to hang.log
    autoupdate.py, updater.py  over-the-air updates from GitHub releases
src/kritapilot/
  server.py                    the MCP server: one @tool per command, batch, for_each_frame
  gen_tools.py                 generative tools: images, video, vision, assets, create_artwork, skills
  presets.py                   looks (styles) and camera moves for prompts
  agent.py                     the AI Chat assistant service: model adapters, Auto, the agent loop
  skills.py                    finding and reading SKILL.md skills (text only)
  oauth.py                     browser sign-in (OpenRouter, PKCE) and key checks
  tracelog.py                  the trace logs (identical copy in the plugin)
  updater.py                   server self-update
  bridge_client.py             tiny client for the bridge
  netfetch.py                  SSRF-safe image download
  providers/                   every outside service: keys.py (sign-in and setup kinds), images.py,
                               video.py, vision.py, compat.py (OpenAI-compatible services), free.py
                               (Pollinations, AI Horde), catalog.py (live model lists), http.py, assets.py
  bundled_skills/              the 39 built-in skills
scripts/
  install_plugin.py            copy the plugin into Krita's pykrita folder
  build_plugin.py              build the installable plugin zip
  gen_tool_docs.py             regenerate docs/tools.md
  smoke_krita.py               end-to-end test against a real running Krita
  paint_sunset.py              agent scenario: paint a sunset with real brushes, look, improve, check
  bump_version.py              set the version everywhere; the next push to main publishes it
  krita_upstream.py            prepare the plugin for bundling with Krita
```

## Tests

```bash
pip install -e .[test] PyQt6           # PyQt5 too, for the Krita 5 code path
QT_QPA_PLATFORM=offscreen pytest
KRITA_TEST_QT=5 QT_QPA_PLATFORM=offscreen pytest
```

The unit tests run the handlers against a fake `krita` module using real Qt. They cover pixel
layout, drawing, selections, the undo machinery, the tone curves, the access modes and the panel's
client snippets. `test_e2e.py` runs the real MCP server over stdio against the real bridge protocol.
`test_meta.py` checks that versions agree, that the plugin zip builds, and that `docs/tools.md` is
current.

### Against real Krita

With the plugin installed and Krita running:

```bash
python scripts/smoke_krita.py               # every command, 56 checks, snapshots in smoke_out/
python scripts/paint_sunset.py --out DIR    # the sunset scenario on its own (--keep leaves it open)
python scripts/smoke_krita.py --only video  # one step (plus app_info)
```

It creates and closes its own documents and leaves files in the workspace. It covers an undo matrix
over every kind of edit, all photo adjustments, generators, the curated actions (it fails if one
opens a dialog), video render and import, and colourise.

## Adding a tool

1. Bridge: add `@handler("area.verb", edit=True)` in the right module. Use `edit=True` if it changes a
   document. Before changing anything Krita won't record, call `history.backup_layer(node, doc)`,
   `backup_props(node)` or `backup_selection(doc)`. If it only reads, add it to `READS` in
   `handlers.py` so it works in read-only mode.
2. Server: add an `@tool` async function in `server.py` that calls `_call("area.verb", ...)`. Its
   docstring is what the model reads, so be specific.
3. Add it to a group in `scripts/gen_tool_docs.py`, then run the script.
4. Add a unit test (fake Krita) and a step in `scripts/smoke_krita.py` (real Krita).

## Krita API notes (5.3)

- `Node.setPixelData`, `Filter.apply`, `Node.setOpacity` and `Node.move` create **no** undo step;
  `setBlendingMode`, `addChildNode`, `remove`, `paintPath` and `rotateNode` do. Some steps arrive
  through queued signals, so flush events before reading the History docker.
- `Document.activeDocument()` follows the active **view**; `setActiveDocument` alone does not switch it.
- `Node.save` ignores the document's batch mode (it opens an options dialog). Set `Krita.setBatchmode(True)`.
- `ManagedColor.componentsOrdered()` can be linear; convert through `colorForCanvas(canvas)`.
- Closing a document while the Layers docker has a pending timer update can crash Krita. Settle the
  event loop before `Document.close()`.
- `addChildNode(node, None)` puts the node on **top**.
