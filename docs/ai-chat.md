# AI Chat inside Krita

The **AI Chat** docker (*Settings ▸ Dockers ▸ AI Chat*) lets you talk to an AI from inside Krita. Pick a
model, sign in, and type what you want. For example: *"paint a misty pine forest at dawn"*, *"make this photo
warmer and lift the shadows"*, *"tile a realistic rock texture over the cliff layer"* or *"animate this with
a slow dolly-in"*. The AI works directly in your open document while you watch. Every tool it uses is shown in
the chat, and its edits can be undone like any other AI edit.

## Models

| Choice | Sign in with |
|---|---|
| Google Gemini | An API key from [Google AI Studio](https://aistudio.google.com/apikey) |
| Anthropic Claude | An API key from the [Anthropic Console](https://console.anthropic.com/settings/keys) (needs `pip install "krita-mcp[anthropic]"`) |
| OpenAI / compatible | An OpenAI key, or the URL of any OpenAI-compatible service (OpenRouter, a gateway, …) |
| Local model | Nothing: point it at [Ollama](https://ollama.com) (`http://127.0.0.1:11434/v1`) or LM Studio (`http://127.0.0.1:1234/v1`) |

The model box can be left empty to use the default model, or set to any model name. The AI gets the same 84
tools as MCP clients: painting with real brushes, seeing and measuring the canvas, layers, photo editing,
animation and video, generative AI and the asset libraries. It looks at its work and improves it before it
answers.

## Services: sign in once

**Services…** lists every AI service krita-mcp can use:

- **Chat models:** Gemini, Claude, OpenAI, local models.
- **Image and video generators:** Stability, Replicate, fal.ai, HeyGen.
- **Local servers:** ComfyUI and AUTOMATIC1111/Forge, configured by URL.

Click **Sign in** next to a service and paste its key, or its server URL.

Keys are stored only on your computer, in `~/.krita-mcp/agent_keys.json`. That file is readable only by
your user on Linux and macOS; on Windows it is protected by your Windows account's permissions. They are
used by:

- the AI Chat docker, and
- AI apps connected through MCP (Claude Desktop, Gemini CLI, Cursor, …). Signing in to fal.ai here, for
  example, lets Claude Desktop's `generate_image` use it too.

An explicit `KRITA_MCP_…` environment variable still wins over a saved key. **Sign out** deletes the key.

## How it works

```
AI Chat docker ──127.0.0.1:5680 + token──▶ krita-mcp --agent ──model API──▶ Gemini / Claude / GPT / local
       ▲                                         │ tool calls
       └───────────── your canvas ◀── Krita plugin bridge (127.0.0.1:5679 + token) ◀┘
```

- The docker starts the assistant service (`krita-mcp --agent`) the first time you send a message or sign
  in. It uses the server command set in the *AI Bridge (MCP)* panel, so that command must be installed
  (`pip install krita-mcp`). Opening Krita alone never starts it.
- The service listens on loopback only and requires the same token as the bridge.
- All network calls run off Krita's main thread, so Krita stays responsive while the AI works.
- **Stop** ends the current task after the step in progress. **New chat** starts a fresh conversation.

## Asset libraries

The AI, in the chat or through MCP, can pull in free real-world material to make work look realistic:

| Tool | Source | Licence |
|---|---|---|
| `search_assets(kind="textures")` | [Poly Haven](https://polyhaven.com) photo textures (rock, wood, bark, fabric, ground, …) | CC0, no attribution needed |
| `search_assets(kind="hdris")` | Poly Haven HDRI panoramas, used as photographic skies and backdrops | CC0 |
| `search_assets(kind="photos")` | [Openverse](https://openverse.org) openly licensed photos | Per photo; the attribution text is returned and added to the layer name |

`import_asset` tiles a texture across the canvas (or covers the canvas with a sky or photo) on a new layer.
To texture a painted shape, put the texture layer above the shape in the same group and set it to
`clipped` with an `overlay` or `multiply` blend mode.
