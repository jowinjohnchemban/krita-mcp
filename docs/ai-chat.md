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
| OpenAI / compatible | An OpenAI key, or the URL of any OpenAI-compatible service (a gateway, …) |
| OpenRouter | **Sign in with browser**, no key to copy. One account reaches Gemini, Claude, GPT and hundreds of other models; leave the model empty to use your OpenRouter default, or enter one such as `anthropic/claude-sonnet-4.6` |
| Local model | Nothing: point it at [Ollama](https://ollama.com) (`http://127.0.0.1:11434/v1`) or LM Studio (`http://127.0.0.1:1234/v1`) |

The model box lists the models your account can use, loaded live from the provider (⟳ reloads it).
**Auto (newest)** picks the newest suitable model fresh for every message, and the chat shows which one
it used. You can also type any model name. The AI gets the same 84
tools as MCP clients: painting with real brushes, seeing and measuring the canvas, layers, photo editing,
animation and video, generative AI and the asset libraries. It looks at its work and improves it before it
answers.

## Two ways to sign in

- **Sign in with browser** (OpenRouter). Krita opens your browser, you approve "Krita MCP" on OpenRouter,
  and you are signed in. Under the hood this is OAuth with PKCE: OpenRouter redirects to a one-shot
  listener on `localhost`, and the code is exchanged for a key that only this computer stores. Approval
  links expire after 10 minutes, and a reply that does not match Krita's request is refused.
- **API key**, for every other service (Google, Anthropic, OpenAI, Stability, Replicate, fal, HeyGen).
  These providers do not offer browser sign-in to third-party apps. Click **Get a key in the browser**: the
  provider's key page opens, and when you copy the key there, the dialog recognises it from the clipboard
  and fills it in. You can also paste it by hand. Before saving, krita-mcp checks the key with the provider
  (where the provider allows a free check), so a mistyped key is reported instead of stored.

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
