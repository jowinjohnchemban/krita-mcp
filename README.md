# krita-mcp

[![CI](https://github.com/jowinjohnchemban/krita-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/jowinjohnchemban/krita-mcp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/jowinjohnchemban/krita-mcp?sort=semver)](https://github.com/jowinjohnchemban/krita-mcp/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/jowinjohnchemban/krita-mcp/total)](https://github.com/jowinjohnchemban/krita-mcp/releases)
[![Krita](https://img.shields.io/badge/Krita-5.2%20%7C%205.3%20%7C%206-3daee9?logo=krita&logoColor=white)](https://krita.org)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-server-6b4fbb)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Let AI agents create and edit art, photos, animation and video inside a running [Krita](https://krita.org).**
It works with any MCP client: Claude, ChatGPT, Gemini, Cursor, VS Code Copilot, Codex and others.

```
AI client ──MCP (stdio or HTTP)──▶ krita-mcp ──127.0.0.1 + token──▶ Krita plugin ──▶ your canvas
```

## What the AI can do (71 tools)

- **Paint like an artist.** It uses Krita's real brush engine with any preset, pressure and eraser,
  plus crisp shapes and gradients, editable SVG vectors and text, procedural SeExpr textures,
  screentones, and colourising line art with Krita's Colourise Mask.
- **See and measure.** Snapshots (zoomed in, a single layer, any animation frame, or a labelled
  coordinate grid), plus `analyze_image` (palette, light, contrast, sharpness, layout), exact
  `read_pixels`, and `find_color`.
- **Edit photos.** Exact levels, curves, exposure and temperature; hue/saturation, colour balance,
  sharpen, denoise, blur and about 25 more; auto-enhance; vignette; non-destructive filter masks;
  16-bit colour spaces.
- **Work with generative models.** Colour-range and magic-wand selections, export a region plus its
  mask, then composite the generated result back through a soft selection. Images can also be
  imported from files or URLs.
- **Animate and edit video.** Keyframes, onion skin, frame contact sheets, importing video from
  mp4/mov/webm/gif, grading every frame (`for_each_frame`), and rendering to mp4/webm/gif/ProRes with
  Krita's bundled ffmpeg.
- **Full layer control.** Paint, vector, group, fill, filter, clone and file layers; masks;
  clipping; blend modes; arranging; merging; transforms. About 70 of Krita's own commands via
  `krita_action`.
- **Real undo for AI edits,** even for edits Krita itself doesn't record. It never overwrites your
  own work.
- **`batch` and `for_each_frame`** let the AI do many steps in one call.

An **AI Bridge (MCP)** panel inside Krita shows the status and a live activity log. From it you can
make the AI read-only or pause it, undo AI edits, set the workspace and token, and copy a ready-made
config for your AI client.

## Quick start

1. **Plugin.** Download `krita-mcp-bridge-*.zip` from
   [Releases](https://github.com/jowinjohnchemban/krita-mcp/releases). In Krita choose
   *Tools ▸ Scripts ▸ Import Python Plugin from File…*, enable the plugin and restart Krita.
2. **Server.** Run `pip install krita-mcp`, or install the `.whl` from the release, or use
   `uvx --from git+https://github.com/jowinjohnchemban/krita-mcp krita-mcp`.
3. **Connect.** Open *Settings ▸ Dockers ▸ AI Bridge (MCP)*, pick your client and copy the snippet.
   For example, in Claude Code:
   ```bash
   claude mcp add krita -- krita-mcp
   ```
4. Ask: *"Create a 1200×800 canvas, paint a sunset over mountains with real brushes, look at it and
   improve it."*

Details: [installation](docs/installation.md) · [clients](docs/clients.md) · [the panel](docs/krita-panel.md)

## Documentation

| | |
|---|---|
| [Workflows](docs/workflows.md) | Painting, photo retouching, inpainting, line art, animation, video |
| [Tool reference](docs/tools.md) | All 71 tools and their parameters |
| [Undo, safety and limits](docs/undo-and-safety.md) | How undo works, the security model, access modes |
| [Development](docs/development.md) | Architecture, tests, real-Krita smoke test, adding tools |
| [Releasing](docs/releasing.md) | How plugin zips, wheels and GitHub/PyPI releases are published |

## Safety in one paragraph

The bridge listens on loopback only and requires a secret token. The AI can read and write files only
in one workspace folder (`~/KritaMCP` by default). There is no code-execution tool. You can switch
the AI to read-only or pause it from the panel at any time. Full details:
[security model](docs/undo-and-safety.md#security-model).

## Status

The project is tested against **Krita 5.3.4 on Windows**. `scripts/smoke_krita.py` runs 53 end-to-end
checks against a running Krita, including an undo matrix over every kind of edit. Unit tests run on
Linux and Windows with PyQt5 and PyQt6 in CI. Krita 6 is covered by the PyQt6 unit tests but has not
yet been run end to end.

## Develop

```bash
pip install -e .[test] PyQt6
QT_QPA_PLATFORM=offscreen pytest
python scripts/smoke_krita.py        # against a real running Krita with the plugin enabled
```

See [development](docs/development.md).
