# Connecting AI services

krita-mcp works with two kinds of AI:

1. **The agent that drives Krita.** This is any MCP client: Claude (Desktop, Code, claude.ai), ChatGPT,
   Gemini CLI, Cursor, VS Code Copilot, Codex, Windsurf, LM Studio, and others. See
   [Connecting AI clients](clients.md).
2. **Generative services the agent can call from Krita.** These are image models, video models and
   vision models. This page explains how to connect them.

All of them are optional. Without any of them, the agent still paints with Krita's own brushes,
filters and tools.

| Kind | Services | Tools |
|---|---|---|
| Image | ComfyUI, AUTOMATIC1111 / Forge / SD.Next, OpenAI (and any OpenAI-compatible images API), Google Gemini, Stability AI, Replicate, fal.ai | `generate_image`, `outpaint`, `upscale_image`, `remove_background`, `pick_variation` |
| Video | HeyGen, OpenAI Sora, Google Veo, any video model on Replicate or fal.ai | `generate_video` |
| Vision | Claude (Anthropic), GPT (OpenAI, or any OpenAI-compatible server such as Ollama or LM Studio), Gemini | `ask_vision` |

`ai_backends` shows what is connected, which models and samplers each service offers, and which
ComfyUI workflows you have saved.

The settings below are **environment variables for the MCP server** (`krita-mcp`). Most MCP clients
let you set them in the server entry. For example, in Claude Desktop's config:

```json
{ "mcpServers": { "krita": { "command": "krita-mcp",
    "env": { "KRITA_MCP_COMFYUI_URL": "http://127.0.0.1:8188", "KRITA_MCP_OPENAI_API_KEY": "sk-..." } } } }
```

## Privacy: nothing leaves your computer unless you say so

- Local backends (ComfyUI, AUTOMATIC1111) run on your machine. Their requests never go through a proxy.
- A cloud service is used only if you give it a key **in a `KRITA_MCP_…` variable**. Generic keys that
  may already be in your environment for other reasons (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `GEMINI_API_KEY`, `REPLICATE_API_TOKEN`, `FAL_KEY`, `STABILITY_API_KEY`, `HEYGEN_API_KEY`) are
  ignored unless you also set `KRITA_MCP_USE_ENV_KEYS=1`.
- When a cloud tool is used, the region of the canvas it works on is uploaded to that service, along
  with the mask (and, for video, the start frame).
- The AI cannot choose service URLs. Only you can, through these variables. Result files are downloaded
  only from public addresses.

## Image backends

When `backend` is not given, the server uses `KRITA_MCP_IMAGE_BACKEND`. If that is not set either, it
uses the first local service that answers (ComfyUI, then AUTOMATIC1111), and then the first cloud
service that has a key.

### ComfyUI (local, recommended)

Start ComfyUI as usual, then set `KRITA_MCP_COMFYUI_URL` (default `http://127.0.0.1:8188`).

- **Built-in graph.** Uses any checkpoint in `models/checkpoints` (SD 1.5, SDXL, Pony, …) for txt2img,
  img2img and inpainting (`SetLatentNoiseMask`, so normal checkpoints inpaint well). `model` picks the
  checkpoint; `sampler`, `scheduler`, `steps`, `cfg` and `seed` are passed through.
- **Your own workflows.** Build anything in ComfyUI (Flux, ControlNet, IP-Adapter, LoRAs, regional
  prompts, …), then choose *Workflow ▸ Export (API)* and save the file as
  `<workspace>/workflows/NAME.json`. Put placeholders where the values should go:
  `{{prompt}}`, `{{negative}}`, `{{seed}}`, `{{width}}`, `{{height}}`, `{{count}}`, `{{steps}}`,
  `{{cfg}}`, `{{strength}}`/`{{denoise}}`, `{{model}}`, `{{image}}` (the region, on a `LoadImage` node)
  and `{{mask}}` (on a `LoadImageMask` node). A field that is exactly one placeholder keeps its type,
  so `"seed": "{{seed}}"` becomes a number. Then call `generate_image(prompt=..., workflow="NAME")`.
- **Upscaling** uses a model from `models/upscale_models` (`model=` picks one).

### AUTOMATIC1111 / Forge / SD.Next (local)

Start the WebUI with `--api`, then set `KRITA_MCP_A1111_URL` (default `http://127.0.0.1:7860`).
If you use `--api-auth`, also set `KRITA_MCP_A1111_AUTH=user:password`.

- Supports txt2img, img2img and inpainting.
- `model` overrides the checkpoint for one request.
- Upscaling uses the WebUI's upscalers (default `R-ESRGAN 4x+`).
- Background removal needs the *sd-webui-rembg* extension.

### OpenAI and OpenAI-compatible APIs

| Variable | Default |
|---|---|
| `KRITA_MCP_OPENAI_API_KEY` | (none) |
| `KRITA_MCP_OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `KRITA_MCP_OPENAI_IMAGE_MODEL` | `gpt-image-1` |
| `KRITA_MCP_OPENAI_SIZES` | `1024x1024,1536x1024,1024x1536` |

- Masked edits use `/images/edits`.
- Point the base URL at any compatible service, such as a gateway or a local server. A loopback URL
  needs no key.

### Google Gemini

Set `KRITA_MCP_GEMINI_API_KEY`. The image model is set by `KRITA_MCP_GEMINI_IMAGE_MODEL` (default
`gemini-2.5-flash-image`).

Gemini edits by instruction and has no mask input. krita-mcp sends the mask as a second image, then cuts
the result back to your selection, so nothing outside the selection changes.

### Stability AI

Set `KRITA_MCP_STABILITY_API_KEY`.

| Task | What runs |
|---|---|
| Text to image | Stable Image Core by default; `model="ultra"` for Ultra, or an `sd3…` model name |
| img2img | SD 3.5 |
| Inpainting | Inpaint endpoint |
| Upscaling | Fast 4× upscale |
| Background removal | Remove-background endpoint |

### Replicate (thousands of models)

Set `KRITA_MCP_REPLICATE_API_TOKEN`.

- Pass any model as `model="owner/name"` or `"owner/name:version"`.
- krita-mcp reads the model's input schema and maps the prompt, image, mask, size or aspect ratio, seed,
  output count, strength, steps and guidance onto the input names that model uses. `extra` adds or
  overrides inputs.
- Defaults: `black-forest-labs/flux-schnell` (txt2img), `flux-dev` (img2img), `flux-fill-dev` (inpaint).

### fal.ai

Set `KRITA_MCP_FAL_KEY`.

- Pass any endpoint as `model="fal-ai/..."`. Requests go through fal's queue, so long jobs are fine.
- Defaults: `fal-ai/flux/dev`, `fal-ai/flux/dev/image-to-image`, `fal-ai/flux-pro/v1/fill`.
- Upscaling uses `fal-ai/clarity-upscaler`; background removal uses `fal-ai/birefnet`.

## Video backends

`generate_video` saves the video to `<workspace>/generated/` and, by default, opens it in Krita as an
animation, so the agent can paint over it, grade it (`for_each_frame`) and render it again
(`render_video`). With `use_canvas=true` (the default), the current picture is sent as the start frame.

| Backend | Variables | Notes |
|---|---|---|
| `heygen` | `KRITA_MCP_HEYGEN_API_KEY` | Talking avatars. Needs `script`, `avatar_id` and `voice_id` (list them with `ai_backends(details=true)`). The canvas becomes the background. With `character="photo"`, the character painted on the canvas itself talks. |
| `openai` | `KRITA_MCP_OPENAI_API_KEY`, `KRITA_MCP_OPENAI_VIDEO_MODEL` (default `sora-2`) | 4, 8 or 12 seconds, landscape or portrait. |
| `gemini` | `KRITA_MCP_GEMINI_API_KEY`, `KRITA_MCP_GEMINI_VIDEO_MODEL` (default `veo-3.0-generate-001`) | Veo; text-to-video or image-to-video. |
| `replicate` / `fal` | as for images | Any text- or image-to-video model; pass `model`. |

`KRITA_MCP_VIDEO_BACKEND` sets the default video backend. `KRITA_MCP_VIDEO_TIMEOUT` (seconds, default
1800) limits how long to wait.

## Vision models (`ask_vision`)

`ask_vision` sends a snapshot of the canvas (or of a region or a single layer) to another model and
returns its answer as text. Use it for a second opinion from a different AI, for captions, or to have
one model write a detailed prompt for an image model.

| Provider | Variables | Default model |
|---|---|---|
| `anthropic` (Claude) | `KRITA_MCP_ANTHROPIC_API_KEY`; needs `pip install "krita-mcp[anthropic]"` | `claude-opus-5-5` (`KRITA_MCP_ANTHROPIC_MODEL`) |
| `openai` | `KRITA_MCP_OPENAI_API_KEY` or a local `KRITA_MCP_OPENAI_BASE_URL` (Ollama: `http://127.0.0.1:11434/v1`) | `gpt-5-mini` (`KRITA_MCP_OPENAI_VISION_MODEL`) |
| `gemini` | `KRITA_MCP_GEMINI_API_KEY` | `gemini-2.5-flash` (`KRITA_MCP_GEMINI_VISION_MODEL`) |

`KRITA_MCP_VISION_PROVIDER` sets the default. Claude requests use Anthropic's server-side refusal
fallback, so a declined request is retried on a suitable model within the same call.

## Other settings

| Variable | Default | Meaning |
|---|---|---|
| `KRITA_MCP_IMAGE_BACKEND` | auto | Default image backend |
| `KRITA_MCP_GEN_TIMEOUT` | `900` | Seconds to wait for an image job |
| `KRITA_MCP_*_BASE_URL` | the official API | Override any service's address (gateways, proxies, self-hosted) |

## How results come back

- Every image result is placed on a **new layer**, stretched over the region it was generated for and
  cut to the selection with its soft edges. Your existing layers are never painted over.
- Each placement is **one undo step**.
- With `count` > 1, you get a group of variations (only the first is visible) plus a numbered sheet
  that the agent can look at. `pick_variation` shows one variation or keeps only one.
- Follow-up tools make a generated element fit in: `match_colors` adjusts its colours to the scene,
  and `remove_background` cuts out a subject (locally, or with an AI model).
