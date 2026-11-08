# Connecting AI services

KritaPilot works with two kinds of AI:

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
settings below are **environment variables for the MCP server** (`kritapilot`). Most MCP clients
let you set them in the server entry. For example, in Claude Desktop's config:

```json
{ "mcpServers": { "krita": { "command": "kritapilot",
    "env": { "KRITAPILOT_COMFYUI_URL": "http://127.0.0.1:8188", "KRITAPILOT_OPENAI_API_KEY": "sk-..." } } } }
```

## Privacy: nothing leaves your computer unless you say so

- Local backends (ComfyUI, AUTOMATIC1111) run on your machine. Their requests never go through a proxy.
- A cloud service is used only if you give it a key **in a `KRITAPILOT_…` variable**. Generic keys that
  may already be in your environment for other reasons (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `GEMINI_API_KEY`, `REPLICATE_API_TOKEN`, `FAL_KEY`, `STABILITY_API_KEY`, `HEYGEN_API_KEY`) are
  ignored unless you also set `KRITAPILOT_USE_ENV_KEYS=1`.
- When a cloud tool is used, the region of the canvas it works on is uploaded to that service, along
  with the mask (and, for video, the start frame).
- The AI cannot choose service URLs. Only you can, through these variables. Result files are downloaded
  only from public addresses.
- Free services that need no key (Pollinations, AI Horde) are used only after you turn them on in
  *AI Chat ▸ Services…*, or list them in `KRITAPILOT_FREE_SERVICES=pollinations,horde`.

## Image backends

When `backend` is not given, the server uses `KRITAPILOT_IMAGE_BACKEND`. If that is not set either, it
uses the first local service that answers (ComfyUI, then AUTOMATIC1111), then the first cloud
service that has a key, and last the free services you turned on (Pollinations, then AI Horde).

If the service it chose refuses the account (a key that is no longer valid, no credit left, a rate
limit), it moves on to the next one in that order. The result names the service that made the picture,
and the AI tells you which one was skipped and why so you can fix it in **AI Chat ▸ Services**. A
service you name with `backend` (or `KRITAPILOT_IMAGE_BACKEND`) is never swapped for another one once it is
set up. If the name is unknown, or it is a cloud service without a key (models sometimes ask for a service
you don't have), the service is chosen automatically as above, any `model` named for the missing service
is dropped, and the result says so. If `create_artwork` decided by itself to generate and no service
accepts the account, it sets the document up for painting by hand instead.

### ComfyUI (local, recommended)

Start ComfyUI as usual, then set `KRITAPILOT_COMFYUI_URL` (default `http://127.0.0.1:8188`).

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

Start the WebUI with `--api`, then set `KRITAPILOT_A1111_URL` (default `http://127.0.0.1:7860`).
If you use `--api-auth`, also set `KRITAPILOT_A1111_AUTH=user:password`.

- Supports txt2img, img2img and inpainting.
- `model` overrides the checkpoint for one request.
- Upscaling uses the WebUI's upscalers (by default the first one it offers; `model=` picks one).
- Background removal needs the *sd-webui-rembg* extension.

### OpenAI and OpenAI-compatible APIs

| Variable | Default |
|---|---|
| `KRITAPILOT_OPENAI_API_KEY` | (none) |
| `KRITAPILOT_OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `KRITAPILOT_OPENAI_IMAGE_MODEL` | the newest image model your key can use (listed live) |
| `KRITAPILOT_OPENAI_SIZES` | `1024x1024,1536x1024,1024x1536` |

- Masked edits use `/images/edits`.
- Point the base URL at any compatible service, such as a gateway or a local server. A loopback URL
  needs no key.

### Google Gemini

Set `KRITAPILOT_GEMINI_API_KEY`. The image model is set by `KRITAPILOT_GEMINI_IMAGE_MODEL` (default
the newest Gemini image model your key can use, listed live).

Gemini edits by instruction and has no mask input. KritaPilot sends the mask as a second image, then cuts
the result back to your selection, so nothing outside the selection changes.

### Stability AI

Set `KRITAPILOT_STABILITY_API_KEY`.

| Task | What runs |
|---|---|
| Text to image | Stable Image Core by default; `model="ultra"` for Ultra, or an `sd3…` model name |
| img2img | SD 3.5 |
| Inpainting | Inpaint endpoint |
| Upscaling | Fast 4× upscale |
| Background removal | Remove-background endpoint |

### Replicate (thousands of models)

Set `KRITAPILOT_REPLICATE_API_TOKEN`.

- Pass any model as `model="owner/name"` or `"owner/name:version"`.
- KritaPilot reads the model's input schema and maps the prompt, image, mask, size or aspect ratio, seed,
  output count, strength, steps and guidance onto the input names that model uses. `extra` adds or
  overrides inputs.
- Without `model`, a model is found in Replicate's own curated collections (text-to-image, image editing,
  inpainting, super-resolution, background removal, text-to-video), checking each model's inputs so that it
  fits the task (for example, inpainting needs an image and a mask input).

### fal.ai

Set `KRITAPILOT_FAL_KEY`.

- Pass any endpoint as `model="fal-ai/..."`. Requests go through fal's queue, so long jobs are fine.
- fal.ai has no API for listing models, so choose the endpoint with `model` for every task (browse
  [fal.ai/models](https://fal.ai/models)). KritaPilot never guesses a fal model name.

### Pollinations (free, no account)

Turn it on in *AI Chat ▸ Services…* (or `KRITAPILOT_FREE_SERVICES=pollinations`).

- Text-to-image, free, with no key. The free models are listed live. A free key from
  [enter.pollinations.ai](https://enter.pollinations.ai/keys) (`KRITAPILOT_POLLINATIONS_API_KEY`) moves it
  to the newer API with more models.
- `backend="pollinations"`; `KRITAPILOT_POLLINATIONS_IMAGE_MODEL` fixes the model.
- Text-to-image only: use another backend to edit or inpaint.

### AI Horde (free, crowdsourced)

Turn it on in *AI Chat ▸ Services…* (or `KRITAPILOT_FREE_SERVICES=horde`).

- Stable Diffusion models on volunteers' GPUs ([aihorde.net](https://aihorde.net)). Requests wait in a
  queue, so a picture usually takes a few minutes. The model with the most workers online is used unless
  you pass `model` (or set `KRITAPILOT_HORDE_MODEL`); a model no worker serves right now is ignored rather
  than refused.
- At most two variations per request, because of the queue. Each variation gets its own seed (the next
  one after the seed you give), so they differ.
- Text-to-image and img2img. Without an account, images are kept small (576 px). A free key from
  [aihorde.net/register](https://aihorde.net/register) (`KRITAPILOT_HORDE_API_KEY`) moves you up the queue
  and allows larger images.
- A request that waits longer than 10 minutes (`KRITAPILOT_HORDE_TIMEOUT`) is cancelled.

## Editing by instruction (`edit_image`)

`edit_image("make it night")` changes the selection - or the whole picture - the way you describe, onto a
new layer above, so the original stays. It tries the models that edit by instruction first: Gemini, OpenAI
(gpt-image), and Flux Kontext on fal (`KRITAPILOT_FAL_EDIT_MODEL`, default `fal-ai/flux-pro/kontext`) or
Replicate (`KRITAPILOT_REPLICATE_EDIT_MODEL`, default `black-forest-labs/flux-kontext-pro`); then any other
connected service, which reworks the picture with img2img (`strength` 0..1 sets how much changes). A
service that refuses the account is skipped for the next one, as for `generate_image`.

## Selecting by description (`select_by_description`)

`select_by_description("the sky")` makes a Krita selection of what you name (`mode`: replace, add,
subtract, intersect). It uses, best first:

| Method | Needs | Edge |
|---|---|---|
| `gemini` | a Gemini key | pixel-accurate masks, one per object |
| `fal` | a fal key (`KRITAPILOT_FAL_SEGMENT_MODEL`, default `fal-ai/evf-sam`) | pixel-accurate |
| `vision` | any vision chat model - free with Pollinations | an outline, approximate |

Check the result with `canvas_snapshot`, and refine it with `modify_selection` (grow, shrink, feather).

## Video backends

`generate_video` saves the video to `<workspace>/generated/` and, by default, opens it in Krita as an
animation, so the agent can paint over it, grade it (`for_each_frame`) and render it again
(`render_video`). With `use_canvas=true` (the default), the current picture is sent as the start frame.

| Backend | Variables | Notes |
|---|---|---|
| `heygen` | `KRITAPILOT_HEYGEN_API_KEY` | Talking avatars. Needs `script`, `avatar_id` and `voice_id` (list them with `ai_backends(details=true)`). The canvas becomes the background. With `character="photo"`, the character painted on the canvas itself talks. |
| `openai` | `KRITAPILOT_OPENAI_API_KEY`, `KRITAPILOT_OPENAI_VIDEO_MODEL` (default: newest video model listed) | 4, 8 or 12 seconds, landscape or portrait. |
| `gemini` | `KRITAPILOT_GEMINI_API_KEY`, `KRITAPILOT_GEMINI_VIDEO_MODEL` (default: newest Veo listed) | Veo; text-to-video or image-to-video. |
| `replicate` / `fal` | as for images | Any text- or image-to-video model; pass `model`. |

`KRITAPILOT_VIDEO_BACKEND` sets the default video backend. `KRITAPILOT_VIDEO_TIMEOUT` (seconds, default
1800) limits how long to wait.

## Vision models (`ask_vision`)

`ask_vision` sends a snapshot of the canvas (or of a region or a single layer) to another model and
returns its answer as text. Use it for a second opinion from a different AI, for captions, or to have
one model write a detailed prompt for an image model.

| Provider | Variables | Model when none is chosen (listed live) |
|---|---|---|
| `anthropic` (Claude) | `KRITAPILOT_ANTHROPIC_API_KEY`; needs `pip install anthropic` | newest Claude, Opus first (`KRITAPILOT_ANTHROPIC_MODEL`) |
| `openai` | `KRITAPILOT_OPENAI_API_KEY` or a local `KRITAPILOT_OPENAI_BASE_URL` (Ollama: `http://127.0.0.1:11434/v1`) | newest chat model, or the newest installed local model (`KRITAPILOT_OPENAI_VISION_MODEL`) |
| `gemini` | `KRITAPILOT_GEMINI_API_KEY` | newest Gemini Flash (`KRITAPILOT_GEMINI_VISION_MODEL`) |

`KRITAPILOT_VISION_PROVIDER` sets the default. Claude requests use Anthropic's server-side refusal
fallback, so a declined request is retried on a suitable model within the same call.

## More providers (OpenAI-compatible)

These services speak the OpenAI API, so one connector serves them all: chat with tools in AI Chat,
`ask_vision`, and image generation where the service offers it. Sign in under *AI Chat ▸ Services…*, or set
the variable. Models are listed live from each service; **KRITAPILOT_<NAME>_BASE_URL** changes the address
(for a proxy, or a regional endpoint).

| Service | Variable | Offers |
|---|---|---|
| Mistral AI | `KRITAPILOT_MISTRAL_API_KEY` | chat, vision |
| Groq | `KRITAPILOT_GROQ_API_KEY` | very fast chat |
| DeepSeek | `KRITAPILOT_DEEPSEEK_API_KEY` | chat |
| xAI Grok | `KRITAPILOT_XAI_API_KEY` | chat, vision, **images** (`backend="xai"`) |
| Together AI | `KRITAPILOT_TOGETHER_API_KEY` | chat, **images** with open models (`backend="together"`) |
| Fireworks AI | `KRITAPILOT_FIREWORKS_API_KEY` | chat with open models |
| Cerebras | `KRITAPILOT_CEREBRAS_API_KEY` | very fast chat |
| Hugging Face Inference Providers | `KRITAPILOT_HUGGINGFACE_API_KEY` | chat with thousands of open models |
| NVIDIA NIM | `KRITAPILOT_NVIDIA_API_KEY` | chat |
| Alibaba Qwen (Model Studio) | `KRITAPILOT_QWEN_API_KEY` | chat, vision |
| Moonshot Kimi | `KRITAPILOT_MOONSHOT_API_KEY` | chat |
| OmniRoute (free gateway on your computer) | none needed; `KRITAPILOT_OMNIROUTE_API_KEY` if you set one | chat through 150+ providers, many free; `auto` first |
| Pollinations (free) | none needed; `KRITAPILOT_POLLINATIONS_API_KEY` optional | chat with free models that use tools |
| Kilo Gateway (free) | none needed; `KRITAPILOT_KILO_API_KEY` optional | free models with no account (`kilo-auto/free` first); a key unlocks all |
| OVHcloud AI Endpoints (free) | none needed; `KRITAPILOT_OVHCLOUD_API_KEY` optional | open models (gpt-oss, Qwen, Llama), rate-limited |
| LLM7 (free) | none needed; `KRITAPILOT_LLM7_API_KEY` optional | free chat (`default` routes to a free model) |
| SambaNova | `KRITAPILOT_SAMBANOVA_API_KEY` | very fast open models, free tier |
| Cohere | `KRITAPILOT_COHERE_API_KEY` | Command models, free trial key |
| SiliconFlow | `KRITAPILOT_SILICONFLOW_API_KEY` | DeepSeek, Qwen, GLM; some free models |
| Z.AI GLM | `KRITAPILOT_ZAI_API_KEY` | GLM models, free Flash models |
| OpenCode Zen | `KRITAPILOT_OPENCODE_API_KEY` | curated models, some free |
| Ollama Cloud | `KRITAPILOT_OLLAMACLOUD_API_KEY` | large open models, free tier |
| ModelScope | `KRITAPILOT_MODELSCOPE_API_KEY` | open models, free daily quota |
| Tencent Hunyuan | `KRITAPILOT_TENCENT_API_KEY` | Hunyuan, free lite model |
| Baidu Qianfan (ERNIE) | `KRITAPILOT_QIANFAN_API_KEY` | ERNIE, free speed models |
| Scaleway, Hyperbolic, Nebius, Nscale | `KRITAPILOT_SCALEWAY_API_KEY`, `..._HYPERBOLIC_...`, `..._NEBIUS_...`, `..._NSCALE_...` | open models, starter credit |
| DeepInfra, Novita, Featherless, Inference.net, Lambda, Chutes | `KRITAPILOT_DEEPINFRA_API_KEY`, `..._NOVITA_...`, `..._FEATHERLESS_...`, `..._INFERENCENET_...`, `..._LAMBDA_...`, `..._CHUTES_...` | open models at low prices |
| Requesty, Vercel AI Gateway | `KRITAPILOT_REQUESTY_API_KEY`, `KRITAPILOT_VERCEL_API_KEY` | one key for hundreds of models (Vercel: monthly free credit) |
| Venice, MiniMax, StepFun, Meta Llama API | `KRITAPILOT_VENICE_API_KEY`, `..._MINIMAX_...`, `..._STEPFUN_...`, `..._LLAMA_...` | their own models |

These come from [OmniRoute](https://github.com/diegosouzapw/OmniRoute)'s provider registry: the services that
speak the OpenAI API directly with an ordinary key, or with none. Services that only work through a
browser cookie, a reverse-engineered web app or a third-party CLI are left out (OmniRoute itself marks
many of them as against the provider's terms). To reach the rest of OmniRoute's 270+ providers, run
OmniRoute and choose it in Krita.

xAI and Together image generation is text-to-image; use another backend to edit or inpaint.

## Models are never hard-coded

When you do not name a model, KritaPilot asks the provider **at that moment** which models your account can
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
OpenAI image sizes (`KRITAPILOT_OPENAI_SIZES`) and Sora video sizes and lengths
(`KRITAPILOT_OPENAI_VIDEO_SIZES`, `KRITAPILOT_OPENAI_VIDEO_SECONDS`). A `size` or `seconds` passed in `extra`
is sent exactly as given.

If Google answers that a model is no longer available and names its replacement, the request is retried
with that model and the chat says so. In the AI Chat panel, the model box lists your account's models
live (it reloads when you switch provider, sign in, or click ⟳). **Auto (newest)** keeps the automatic
choice: the newest suitable model, looked up when a chat starts and again every ten minutes. For the chat
providers and the **Auto** provider that switches between them, see [AI Chat](ai-chat.md#the-provider-menu).

## Other settings

| Variable | Default | Meaning |
|---|---|---|
| `KRITAPILOT_IMAGE_BACKEND` | auto | Default image backend |
| `KRITAPILOT_GEN_TIMEOUT` | `900` | Seconds to wait for an image job |
| `KRITAPILOT_*_BASE_URL` | the official API | Override any service's address (gateways, proxies, self-hosted) |

## Just give it a prompt: `create_artwork`

`create_artwork("realistic photo of a lighthouse at dusk", aspect="16:9")` creates the canvas and then
decides how to make the picture:

- **Generate** when the request is photographic or realistic, or when an image model is connected. It
  makes one picture on paid cloud services, four variations with a comparison sheet on your own GPU
  (ComfyUI, AUTOMATIC1111), and at most two on AI Horde; `count` asks for a different number.
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
