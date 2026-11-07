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
| Mistral, Groq, DeepSeek, xAI Grok, Together, Fireworks, Cerebras, Hugging Face, NVIDIA, Qwen, Moonshot, SambaNova, Cohere, SiliconFlow, Z.AI, OpenCode Zen, Ollama Cloud, ModelScope, DeepInfra, Vercel AI Gateway, Requesty and more | An API key (**Get a key in the browser**); many have free tiers. See [more providers](ai-backends.md#more-providers-openai-compatible) |
| Local model | Nothing: point it at [Ollama](https://ollama.com) (`http://127.0.0.1:11434/v1`) or LM Studio (`http://127.0.0.1:1234/v1`) |
| **Kilo Gateway, OVHcloud, LLM7 (free)** | **Nothing.** Free models with no account; a key is optional. Only models that can use tools and cost nothing are offered without a key |
| **Pollinations (free)** | **Nothing.** Free models that can use tools, with no account. A free key from [enter.pollinations.ai](https://enter.pollinations.ai/keys) is optional and unlocks more models |
| **OmniRoute (free gateway)** | Nothing on this computer: run [OmniRoute](https://github.com/diegosouzapw/OmniRoute) (`npm install -g omniroute`, then `omniroute`) and pick it. It routes to 150+ providers, many of them free; its `auto` model chooses one that works |

The model box lists the models your account can use, loaded live from the provider (⟳ reloads it).
**Auto (newest)** picks the newest suitable model fresh for every message, and the chat shows which one
it used. You can also type any model name. The AI gets the same 86
tools as MCP clients: painting with real brushes, seeing and measuring the canvas, layers, photo editing,
animation and video, generative AI and the asset libraries. It looks at its work and improves it before it
answers.

## Free, no sign-in

These work with no account at all: **Kilo Gateway**, **OVHcloud AI Endpoints** and **LLM7** (pick one in
the provider box and type), and:

- **Pollinations**: choose it in the provider box and type. Free models are listed live, so you always get
  ones that can use Krita's tools. They are smaller than paid flagship models, so expect simpler results.
- **OmniRoute**: a gateway you run on your computer. Connect your free provider accounts to it once, in its
  own dashboard, and Krita reaches all of them through one address (`http://localhost:20128/v1`).

Free **image** services are in *Services…*: **Pollinations** (fast) and **AI Horde** (crowdsourced, with a
queue). Click **Turn on**. They stay off until you do, so no prompt is sent anywhere without your say-so.
Once on, they are used when no local or signed-in image service is available.

## Spending: tokens and credits

Every step of the AI resends the conversation to the model, so the assistant keeps that cheap:

- Only the **two newest pictures** stay in the conversation (`KRITA_MCP_CHAT_KEEP_IMAGES`). Older snapshots
  are replaced by a short note, and the AI takes a new snapshot if it needs to look again.
- Snapshots go to the model at **768 px** (`KRITA_MCP_CHAT_IMAGE_SIZE`) unless it asks for more.
- Long tool results from earlier messages are shortened when resent.
- **Claude** uses prompt caching: the tool list and the conversation so far are re-read from Anthropic's
  cache at a fraction of the price. OpenAI, Gemini and DeepSeek cache automatically. Claude models through
  OpenRouter get the system prompt cached too.
- A message stops when **the same error repeats three times**, instead of retrying until it reaches the step
  limit (`KRITA_MCP_CHAT_MAX_STEPS`, default 40).
- Each reply is capped (`KRITA_MCP_CHAT_MAX_TOKENS`), and on OpenRouter it is fitted to a low balance.
- `create_artwork` makes **one** image on paid cloud services (four on your own GPU, where they are free).
  Ask for options and it makes more.
- After every answer, the chat shows the tokens it used, how many came from the cache, and the cost where
  the provider reports it (OpenRouter).

The AI is also told to work economically: batch its edits, look after a pass rather than after every
stroke, and never repeat a call that failed.

## Skills

Skills are step-by-step know-how for kinds of work, each a folder with a `SKILL.md`. krita-mcp ships 39:

| Area | Skills |
|---|---|
| Making pictures | `photoreal-image`, `paint-by-hand`, `sketch-to-render`, `concept-art`, `character-design`, `landscape-environment`, `anime-illustration`, `traditional-media`, `pixel-art`, `architecture-viz`, `product-shot` |
| Fixing and improving | `retouch-photo`, `portrait-retouch`, `photo-restoration`, `fix-ai-artifacts`, `photo-composite`, `extend-canvas`, `restyle-image`, `color-grade`, `lighting-and-shadows` |
| Design | `vector-logo`, `poster-design`, `social-media-graphics`, `sticker-design`, `comic-storyboard`, `seamless-pattern`, `game-assets`, `print-ready`, `color-palette`, `lineart-coloring`, `textures-materials` |
| Motion | `still-to-video`, `frame-animation`, `video-editing`, `avatar-video` |
| Working well | `composition`, `art-critique`, `save-credits`, `find-skills` (search the installed skills, and point you to [skills.sh](https://skills.sh/) when none fits) |

- For every message, the assistant looks for the few skills that fit what you asked and names them to the
  model. The model loads a skill's full text (`use_skill`) only when it helps. Unused skills cost nothing.
- Skills are found fresh every time, nothing is cached. Add one and it is used from the next message on.
- **Skills…** in the chat lists them. **Open my skills folder** opens `~/.krita-mcp/skills`: copy a skill
  folder there. **Find skills online** opens [skills.sh](https://skills.sh/). Skills you install there for
  other agents (`npx skills add <owner/repo> -g`, into `~/.agents/skills` or `~/.claude/skills`) are found
  too. `KRITA_MCP_SKILLS_DIRS` replaces the list of folders.
- AI apps connected through MCP get the same skills with the `find_skills` and `use_skill` tools.
- Skills are read as text. krita-mcp never runs scripts that a skill folder contains, and it only reads
  files inside the skill's own folder.

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

- **Chat models:** Gemini, Claude, OpenAI, OpenRouter, Mistral, Groq, DeepSeek, xAI, Together, Fireworks,
  Cerebras, Hugging Face, NVIDIA, Qwen, Moonshot, local models. The provider list comes from the assistant
  service, so new providers appear without updating the plugin.
- **Image and video generators:** Stability, Replicate, fal.ai, HeyGen, xAI, Together.
- **Local servers:** ComfyUI and AUTOMATIC1111/Forge, configured by URL.
- **Free services:** Pollinations, AI Horde and OmniRoute. They work without a key: **Turn on** /
  **Turn off**. A free key is optional and raises their limits.

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
