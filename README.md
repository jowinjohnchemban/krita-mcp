# krita-mcp

[![CI](https://github.com/jowinjohnchemban/krita-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/jowinjohnchemban/krita-mcp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/jowinjohnchemban/krita-mcp?sort=semver)](https://github.com/jowinjohnchemban/krita-mcp/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/jowinjohnchemban/krita-mcp/total)](https://github.com/jowinjohnchemban/krita-mcp/releases)
[![Tested on Krita 5.3.4](https://img.shields.io/badge/tested%20on-Krita%205.3.4-3daee9?logo=krita&logoColor=white)](#status)
[![Python](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjowinjohnchemban%2Fkrita-mcp%2Fmain%2Fpyproject.toml&query=%24.project%5B'requires-python'%5D&label=python&logo=python&logoColor=white&color=3776ab)](pyproject.toml)
[![MCP SDK](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjowinjohnchemban%2Fkrita-mcp%2Fmain%2Fpyproject.toml&query=%24.project.dependencies%5B0%5D&label=MCP%20SDK&color=6b4fbb)](https://modelcontextprotocol.io)
[![License](https://img.shields.io/github/license/jowinjohnchemban/krita-mcp)](LICENSE)

**Let AI agents create and edit art, photos, animation and video inside a running [Krita](https://krita.org).**
It works with any MCP client: Claude, ChatGPT, Gemini, Cursor, VS Code Copilot, Codex and others.

```
AI client ──MCP (stdio or HTTP)──▶ krita-mcp ──127.0.0.1 + token──▶ Krita plugin ──▶ your canvas
```

## What the AI can do (86 tools + 7 workflow prompts)

- **Paint like an artist.** It uses Krita's real brush engine with any preset, pressure and eraser,
  plus crisp shapes and gradients, editable SVG vectors and text, procedural SeExpr textures,
  screentones, and colourising line art with Krita's Colourise Mask.
- **See and measure.** Snapshots (zoomed in, a single layer, any animation frame, or a labelled
  coordinate grid), plus `analyze_image` (palette, light, contrast, sharpness, layout), exact
  `read_pixels`, and `find_color`.
- **Edit photos.** Exact levels, curves, exposure and temperature; hue/saturation, colour balance,
  sharpen, denoise, blur and about 25 more; auto-enhance; vignette; non-destructive filter masks;
  16-bit colour spaces.
- **Chat with an AI inside Krita.** The *AI Chat* docker lets you sign in to Gemini, Claude, OpenAI,
  OpenRouter (with your browser, no key to copy), Mistral, Groq, DeepSeek, xAI Grok, Together, Fireworks,
  Cerebras, Hugging Face, NVIDIA, Qwen, Moonshot or a local model (Ollama, LM Studio), or use it **free
  with no sign-in** (Pollinations, or the OmniRoute gateway), type what you want and watch it work in your
  document. It keeps spending down (only the newest pictures are resent, Claude's prompt cache is used,
  repeated failures stop) and shows the tokens each answer used. **Skills** (39 built in - photoreal images,
  retouching, pixel art, logos, comics, animation, saving credits, ... - plus any you install) are found and
  loaded only when a request needs them. Models are always listed live from each provider; nothing is
  hard-coded. One
  **Services** list holds every key and server URL, and AI apps connected through MCP use the same
  sign-in.
- **Real-world materials.** CC0 photo textures and HDRI skies from Poly Haven, and openly licensed photos
  from Openverse (with attribution), tiled or placed on layers to make work look real.
- **Just give it a prompt.** `create_artwork` decides: realistic or photographic requests are
  **generated** (with looks like `photo`, `cinematic`, `portrait` or `product`, plus negative prompts
  that remove AI artefacts). Hand-made requests, or setups with no model connected, are **painted**
  in Krita, with prepared layers, guides and a painting plan.
- **Generate with any image model, right on the canvas.** It works with ComfyUI (including your own
  workflows: Flux, ControlNet, IP-Adapter, …), AUTOMATIC1111/Forge, OpenAI, Gemini, Stability AI,
  Replicate and fal.ai. You can generate from text, rework a region (img2img), inpaint a selection,
  outpaint beyond the edges, upscale, and remove backgrounds. Several variations at once come with a
  numbered comparison sheet. You can also paint a sketch layer and let ControlNet follow it.
  `match_colors` blends a generated element into the scene. Results always
  land on new layers, cut to your selection, and each placement is one undo step.
- **Video and a second opinion.** Generate video with Sora, Veo, HeyGen talking avatars (they can even
  make your painted character talk) or any Replicate/fal video model. It opens in Krita as an animation
  you can edit. Cinematic camera moves (dolly, orbit, crane, FPV, crash zoom…) steer video models,
  and `camera_move` animates any still inside Krita without AI. `ask_vision` gets a critique, caption or prompt from Claude, GPT or Gemini (or a local
  Ollama model).
- **Animate and edit video.** Keyframes, onion skin, frame contact sheets, importing video from
  mp4/mov/webm/gif, grading every frame (`for_each_frame`), and rendering to mp4/webm/gif/ProRes with
  Krita's bundled ffmpeg.
- **Full layer control.** Paint, vector, group, fill, filter, clone and file layers; masks;
  clipping; blend modes; arranging; merging; transforms. About 70 of Krita's own commands via
  `krita_action`.
- **Real undo for AI edits,** even for edits Krita itself doesn't record. It never overwrites your
  own work.
- **`batch` and `for_each_frame`** let the AI do many steps in one call.

Built-in **prompts** (`create`, `generative_inpaint`, `concept_variations`, `retouch_photo`, `product_shot`,
`paint_scene`, `animate`) appear as slash commands in clients that support MCP prompts.

The plugin **updates itself** from this project's GitHub releases: it tells you when a new version is out
(or installs it automatically, if you choose), checks every download against GitHub's published checksum,
and updates the `krita-mcp` server too.

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

## Command-line flags

```
krita-mcp [--transport {stdio,http}] [--host HOST] [--port PORT] [--agent [--agent-port N]]
          [--check-update | --update] [--version]
```

| Flag | Default | Meaning |
|---|---|---|
| `--transport` | `stdio` | `stdio` for local clients (Claude Code/Desktop, Cursor, VS Code, Gemini CLI, Codex). `http` for clients that need a URL. |
| `--host` | `127.0.0.1` | HTTP bind address. Binding anything other than loopback requires `KRITA_MCP_HTTP_TOKEN`. |
| `--port` | `8765` | HTTP port (the endpoint is `http://HOST:PORT/mcp`). |
| `--agent` | | Run the in-Krita assistant service for the *AI Chat* docker instead of an MCP server. The docker starts it by itself. |
| `--agent-port` | `5680` | Port of that service (loopback only, bridge-token auth). Also `KRITA_MCP_AGENT_PORT`. |
| `--check-update` | | Ask GitHub whether a newer release exists (prints JSON). |
| `--update` | | Download, verify and install the newest release of the server. |
| `--version` | | Print the version and exit. |

Environment variables:

| Variable | Used by | Default | Meaning |
|---|---|---|---|
| `KRITA_MCP_HTTP_TOKEN` | server | random, printed at start | Bearer token HTTP clients must send (`Authorization: Bearer …`) |
| `KRITA_MCP_PORT` | server + plugin | `5679` | Port of the Krita bridge (not the HTTP port above) |
| `KRITA_MCP_HOST` | server | `127.0.0.1` | Host of the Krita bridge |
| `KRITA_MCP_HOME` | server + plugin | `~/.krita-mcp` | Folder holding the bridge token |
| `KRITA_MCP_TOKEN` | server | read from `KRITA_MCP_HOME` | Bridge token, instead of reading the file |
| `KRITA_MCP_WORKSPACE` | plugin | `~/KritaMCP` | The only folder the AI may read/write (overrides the panel setting) |
| `KRITA_MCP_AUTOSTART` | plugin | on | `0` = don't start the bridge with Krita |
| `KRITA_MCP_FFMPEG` | plugin | Krita's bundled copy | Folder with `ffmpeg`/`ffprobe` for video |

Plugin settings can also be changed in Krita's *AI Bridge (MCP)* panel; environment variables take priority.

Generative services are configured with more server variables (`KRITA_MCP_COMFYUI_URL`,
`KRITA_MCP_A1111_URL`, `KRITA_MCP_OPENAI_API_KEY`, `KRITA_MCP_GEMINI_API_KEY`, `KRITA_MCP_STABILITY_API_KEY`,
`KRITA_MCP_REPLICATE_API_TOKEN`, `KRITA_MCP_FAL_KEY`, `KRITA_MCP_HEYGEN_API_KEY`,
`KRITA_MCP_ANTHROPIC_API_KEY`, `KRITA_MCP_IMAGE_BACKEND`, …). Cloud services are used only with a
`KRITA_MCP_…` key, so a key that happens to be in your environment never uploads your art by surprise.
See [Connecting AI services](docs/ai-backends.md).

## Documentation

| | |
|---|---|
| [AI Chat inside Krita](docs/ai-chat.md) | The chat docker, signing in to services, asset libraries |
| [Connecting AI services](docs/ai-backends.md) | ComfyUI, A1111, OpenAI, Gemini, Stability, Replicate, fal, HeyGen, Sora, Veo, Claude |
| [Workflows](docs/workflows.md) | Painting, photo retouching, inpainting, line art, animation, video |
| [Tool reference](docs/tools.md) | All 86 tools, their parameters, and the prompts |
| [Undo, safety and limits](docs/undo-and-safety.md) | How undo works, the security model, access modes |
| [Development](docs/development.md) | Architecture, tests, real-Krita smoke test, adding tools |
| [Krita upstream](docs/krita-upstream.md) | Proposing the plugin for bundling with Krita itself |
| [Releasing](docs/releasing.md) | How plugin zips, wheels and GitHub/PyPI releases are published |

## Safety in one paragraph

The bridge listens on loopback only and requires a secret token. The AI can read and write files only
in one workspace folder (`~/KritaMCP` by default). There is no code-execution tool. You can switch
the AI to read-only or pause it from the panel at any time. Full details:
[security model](docs/undo-and-safety.md#security-model).

## Status

The project is tested against **Krita 5.3.4 on Windows**. `scripts/smoke_krita.py` runs 56 end-to-end
checks against a running Krita, including an undo matrix over every kind of edit and the Quick start's
sunset prompt. `scripts/paint_sunset.py` paints the sunset with real brushes, looks at it, improves it and
checks the result. Unit tests run on
Linux and Windows with PyQt5 and PyQt6 in CI. Krita 6 is covered by the PyQt6 unit tests but has not
yet been run end to end.

## Develop

```bash
pip install -e .[test] PyQt6
QT_QPA_PLATFORM=offscreen pytest
python scripts/smoke_krita.py        # against a real running Krita with the plugin enabled
```

See [development](docs/development.md).
