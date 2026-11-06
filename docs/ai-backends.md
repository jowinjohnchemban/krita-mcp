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

The easiest way to connect a service is to sign in inside Krita: *AI Chat ▸ Services…* (see
[AI Chat](ai-chat.md)). Keys saved there serve both the chat docker and MCP clients. Alternatively, the
settings below are **environment variables for the MCP server** (`krita-mcp`). Most MCP clients
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
- Upscaling uses the WebUI's upscalers (by default the first one it offers; `model=` picks one).
- Background removal needs the *sd-webui-rembg* extension.

### OpenAI and OpenAI-compatible APIs

| Variable | Default |
|---|---|
| `KRITA_MCP_OPENAI_API_KEY` | (none) |
| `KRITA_MCP_OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `KRITA_MCP_OPENAI_IMAGE_MODEL` | the newest image model your key can use (listed live) |
| `KRITA_MCP_OPENAI_SIZES` | `1024x1024,1536x1024,1024x1536` |

- Masked edits use `/images/edits`.
- Point the base URL at any compatible service, such as a gateway or a local server. A loopback URL
  needs no key.

### Google Gemini

Set `KRITA_MCP_GEMINI_API_KEY`. The image model is set by `KRITA_MCP_GEMINI_IMAGE_MODEL` (default
the newest Gemini image model your key can use, listed live).

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
- Without `model`, a model is found in Replicate's own curated collections (text-to-image, image editing,
  inpainting, super-resolution, background removal, text-to-video), checking each model's inputs so that it
  fits the task (for example, inpainting needs an image and a mask input).

### fal.ai

Set `KRITA_MCP_FAL_KEY`.

- Pass any endpoint as `model="fal-ai/..."`. Requests go through fal's queue, so long jobs are fine.
- fal.ai has no API for listing models, so choose the endpoint with `model` for every task (browse
  [fal.ai/models](https://fal.ai/models)). krita-mcp never guesses a fal model name.

## Video backends

`generate_video` saves the video to `<workspace>/generated/` and, by default, opens it in Krita as an
animation, so the agent can paint over it, grade it (`for_each_frame`) and render it again
(`render_video`). With `use_canvas=true` (the default), the current picture is sent as the start frame.

| Backend | Variables | Notes |
|---|---|---|
| `heygen` | `KRITA_MCP_HEYGEN_API_KEY` | Talking avatars. Needs `script`, `avatar_id` and `voice_id` (list them with `ai_backends(details=true)`). The canvas becomes the background. With `character="photo"`, the character painted on the canvas itself talks. |
| `openai` | `KRITA_MCP_OPENAI_API_KEY`, `KRITA_MCP_OPENAI_VIDEO_MODEL` (default: newest video model listed) | 4, 8 or 12 seconds, landscape or portrait. |
| `gemini` | `KRITA_MCP_GEMINI_API_KEY`, `KRITA_MCP_GEMINI_VIDEO_MODEL` (default: newest Veo listed) | Veo; text-to-video or image-to-video. |
| `replicate` / `fal` | as for images | Any text- or image-to-video model; pass `model`. |

`KRITA_MCP_VIDEO_BACKEND` sets the default video backend. `KRITA_MCP_VIDEO_TIMEOUT` (seconds, default
1800) limits how long to wait.

## Vision models (`ask_vision`)

`ask_vision` sends a snapshot of the canvas (or of a region or a single layer) to another model and
returns its answer as text. Use it for a second opinion from a different AI, for captions, or to have
one model write a detailed prompt for an image model.

| Provider | Variables | Model when none is chosen (listed live) |
|---|---|---|
| `anthropic` (Claude) | `KRITA_MCP_ANTHROPIC_API_KEY`; needs `pip install "krita-mcp[anthropic]"` | newest Claude, Opus first (`KRITA_MCP_ANTHROPIC_MODEL`) |
| `openai` | `KRITA_MCP_OPENAI_API_KEY` or a local `KRITA_MCP_OPENAI_BASE_URL` (Ollama: `http://127.0.0.1:11434/v1`) | newest chat model, or the newest installed local model (`KRITA_MCP_OPENAI_VISION_MODEL`) |
| `gemini` | `KRITA_MCP_GEMINI_API_KEY` | newest Gemini Flash (`KRITA_MCP_GEMINI_VISION_MODEL`) |

`KRITA_MCP_VISION_PROVIDER` sets the default. Claude requests use Anthropic's server-side refusal
fallback, so a declined request is retried on a suitable model within the same call.

## More providers (OpenAI-compatible)

These services speak the OpenAI API, so one connector serves them all: chat with tools in AI Chat,
`ask_vision`, and image generation where the service offers it. Sign in under *AI Chat ▸ Services…*, or set
the variable. Models are listed live from each service; **KRITA_MCP_<NAME>_BASE_URL** changes the address
(for a proxy, or a regional endpoint).

| Service | Variable | Offers |
|---|---|---|
| Mistral AI | `KRITA_MCP_MISTRAL_API_KEY` | chat, vision |
| Groq | `KRITA_MCP_GROQ_API_KEY` | very fast chat |
| DeepSeek | `KRITA_MCP_DEEPSEEK_API_KEY` | chat |
| xAI Grok | `KRITA_MCP_XAI_API_KEY` | chat, vision, **images** (`backend="xai"`) |
| Together AI | `KRITA_MCP_TOGETHER_API_KEY` | chat, **images** with open models (`backend="together"`) |
| Fireworks AI | `KRITA_MCP_FIREWORKS_API_KEY` | chat with open models |
| Cerebras | `KRITA_MCP_CEREBRAS_API_KEY` | very fast chat |
| Hugging Face Inference Providers | `KRITA_MCP_HUGGINGFACE_API_KEY` | chat with thousands of open models |
| NVIDIA NIM | `KRITA_MCP_NVIDIA_API_KEY` | chat |
| Alibaba Qwen (Model Studio) | `KRITA_MCP_QWEN_API_KEY` | chat, vision |
| Moonshot Kimi | `KRITA_MCP_MOONSHOT_API_KEY` | chat |

xAI and Together image generation is text-to-image; use another backend to edit or inpaint.

## Models are never hard-coded

When you do not name a model, krita-mcp asks the provider **at that moment** which models your account can
use, and picks the newest suitable one. Nothing is cached, so a newly released model is used as soon as your
account has it, and a retired model is never requested again:

| Provider | How the model is chosen |
|---|---|
| Gemini | Google's model list: the newest Flash (chat, vision), Flash Image, or Veo model |
| OpenAI and compatible services | `/models`, ranked by release date (or the model type the service reports): newest chat, image or video model; embedding, speech and similar models are skipped |
| Claude | Anthropic's model list: the newest Opus, then the newest other model |
| OpenRouter | Your account's default model |
| Local servers (Ollama, LM Studio) | The newest installed model |
| Replicate | Its curated collections, checked against each model's inputs |
| Stability | The service's own default for each endpoint |
| AUTOMATIC1111, ComfyUI | Your loaded checkpoint and the first upscaler, sampler and scheduler the server offers |
| HeyGen | The first avatar and voice on your account |
| fal.ai | Must be chosen (fal has no model list API) |

Some limits cannot be looked up from a provider and are settings instead (today's values are the defaults):
OpenAI image sizes (`KRITA_MCP_OPENAI_SIZES`) and Sora video sizes and lengths
(`KRITA_MCP_OPENAI_VIDEO_SIZES`, `KRITA_MCP_OPENAI_VIDEO_SECONDS`). A `size` or `seconds` passed in `extra`
is sent exactly as given.

If Google answers that a model is no longer available and names its replacement, the request is retried
with that model and the chat says so. In the AI Chat panel, the model box lists your account's models
live (it reloads when you switch provider, sign in, or click ⟳). **Auto (newest)** keeps the automatic
choice.

## Other settings

| Variable | Default | Meaning |
|---|---|---|
| `KRITA_MCP_IMAGE_BACKEND` | auto | Default image backend |
| `KRITA_MCP_GEN_TIMEOUT` | `900` | Seconds to wait for an image job |
| `KRITA_MCP_*_BASE_URL` | the official API | Override any service's address (gateways, proxies, self-hosted) |

## Just give it a prompt: `create_artwork`

`create_artwork("realistic photo of a lighthouse at dusk", aspect="16:9")` creates the canvas and then
decides how to make the picture:

- **Generate** when the request is photographic or realistic, or when an image model is connected. It
  makes several variations and a comparison sheet.
- **Paint** when the request is hand-made or graphic ("paint it by hand", line art, pixel art, logos), or
  when no model is connected. It sets up layers (Sketch, Base colours, Shading, Details, Light &
  effects) and thirds guides, and returns a step-by-step painting plan. The agent then paints with
  Krita's brushes.
- **Hybrid** generates first, then adds paint-over layers for refining by hand.

Pass `approach` to force one of these.

## Looks (`style`)

`generate_image`, `create_artwork` and `generate_video` accept a `style`. A style adds a proven prompt
recipe and a matching negative prompt (which removes the plastic-skin and extra-finger failures of
realistic images).

| | Styles |
|---|---|
| Realistic | `photo`, `cinematic`, `portrait`, `product`, `fashion`, `street`, `landscape`, `macro`, `food`, `architecture`, `drone` |
| Artistic | `concept_art`, `illustration`, `anime`, `watercolor`, `oil_painting`, `3d_render`, `pixel_art`, `comic`, `sketch` |

For the most realistic results, use a photoreal checkpoint (for example an SDXL realism model in ComfyUI
or A1111), or a current hosted model from OpenAI, Gemini, Stability, Replicate or fal.

## Sketch to image (`control`)

Paint a rough sketch, pose or depth layer and let the model follow it:

```
generate_image("a cozy log cabin in a pine forest", style="photo",
               control=[{"layer": "Sketch", "type": "scribble", "strength": 0.8}])
```

| Backend | How the guide is used |
|---|---|
| ComfyUI, A1111 | Real ControlNet (up to 4 guides). The model is picked by `type` from the installed ControlNet models, or set with `model`. Line types are sent as white lines on black. `preprocess` runs an A1111 preprocessor (for example `depth_midas` on a photo layer). In your own ComfyUI workflows, use `{{control}}` and `{{control_1}}`, `{{control_2}}`, … |
| Stability | Its *sketch* (line types) or *structure* control endpoint |
| OpenAI, Gemini | The guide is sent as the image to follow |
| Replicate | Models with a `control_image`-style input |

## Camera moves

- **Video models:** `generate_video(camera="dolly_in")` writes a cinematic camera instruction into the
  prompt. The moves are dolly in/out, push in, pull back, crash zoom, pan, tilt, orbit, crane, handheld,
  FPV drone, whip pan, bullet time, Ken Burns and static.
- **No video model?** `camera_move("orbit_left", seconds=3)` animates the current picture inside Krita
  with a virtual camera (zoom, pan, tilt, orbit, crane, handheld, crash zoom, Ken Burns) without any AI.
  Add `render_path="shot.mp4"` to export it.

## How results come back

- Every image result is placed on a **new layer**, stretched over the region it was generated for and
  cut to the selection with its soft edges. Your existing layers are never painted over.
- Each placement is **one undo step**.
- With `count` > 1, you get a group of variations (only the first is visible) plus a numbered sheet
  that the agent can look at. `pick_variation` shows one variation or keeps only one.
- Follow-up tools make a generated element fit in: `match_colors` adjusts its colours to the scene,
  and `remove_background` cuts out a subject (locally, or with an AI model).
