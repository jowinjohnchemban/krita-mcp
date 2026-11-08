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
| Anthropic Claude | An API key from the [Anthropic Console](https://console.anthropic.com/settings/keys) (needs `pip install anthropic`) |
| OpenAI / compatible | An OpenAI key, or the URL of any OpenAI-compatible service (a gateway, …) |
| OpenRouter | **Sign in with browser**, no key to copy. One account reaches Gemini, Claude, GPT and hundreds of other models; leave the model on Auto to use the best one your account offers that can use tools, or enter one such as `anthropic/claude-sonnet-4.6` |
| Mistral, Groq, DeepSeek, xAI Grok, Together, Fireworks, Cerebras, Hugging Face, NVIDIA, Qwen, Moonshot, SambaNova, Cohere, SiliconFlow, Z.AI, OpenCode Zen, Ollama Cloud, ModelScope, DeepInfra, Vercel AI Gateway, Requesty and more | An API key (**Get a key in the browser**); many have free tiers. See [more providers](ai-backends.md#more-providers-openai-compatible) |
| Local model | Nothing: point it at [Ollama](https://ollama.com) (`http://127.0.0.1:11434/v1`) or LM Studio (`http://127.0.0.1:1234/v1`) |
| **Kilo Gateway, OVHcloud, LLM7 (free)** | **Nothing.** Free models with no account; a key is optional. Only models that can use tools and cost nothing are offered without a key |
| **Pollinations (free)** | **Nothing.** Free models that can use tools, with no account. A free key from [enter.pollinations.ai](https://enter.pollinations.ai/keys) is optional and unlocks more models |
| **OmniRoute (free gateway)** | Nothing on this computer: run [OmniRoute](https://github.com/diegosouzapw/OmniRoute) (`npm install -g omniroute`, then `omniroute`) and pick it. It routes to 150+ providers, many of them free; its `auto` model chooses one that works |

The provider box starts on **Auto - all my providers**, which uses every provider you can use and switches
when one runs out of credit or fails (see [the provider menu](#the-provider-menu)).

The model box lists the models your account can use, loaded live from the provider (⟳ reloads it).
**Auto (newest)** picks the newest suitable model when a chat starts, keeps it for ten minutes (looking it
up again before every message would keep you waiting), and the chat shows which one it used. You can also
type any model name. The AI gets the same 86
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
Once on, they are used when no local or signed-in image service is available. AI Horde runs on volunteers'
GPUs: a picture usually takes a few minutes, so it makes at most two variations at a time.

What to expect from the free services: they work, but they are slower and simpler than paid ones. A free
model may stop answering for a while; the assistant then gives up on it after three minutes and, on Auto,
carries on with the next one. Some free models cannot look at pictures; they check the canvas with the
measuring tools (`analyze_image`, `read_pixels`) instead. For faster, better results, sign in to one paid
provider: Auto uses it first and falls back to the free ones when its credit runs out.

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
- Gemini 3 models are asked to think briefly (`KRITA_MCP_GEMINI_THINKING`: `low` by default, or `medium`,
  `high`, `off` for Google's default). Long thinking can take a minute per step and is billed as output.
- `create_artwork` makes **one** image on paid cloud services (four on your own GPU, where they are free;
  at most two on AI Horde). Ask for options and it makes more.
- Auto puts a provider that failed in the last 30 minutes at the end of its list, so a new chat does not
  wait on it again.
- After every answer, the chat shows the tokens it used, how many came from the cache, and the cost where
  the provider reports it (OpenRouter).

The AI is also told to work economically: batch its edits, look after a pass rather than after every
stroke, and never repeat a call that failed.

## Skills

Skills are step-by-step know-how for kinds of work, each a folder with a `SKILL.md`. KritaPilot ships 39:

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
  folder there. **Find skills online** opens [skills.sh](https://skills.sh/).
- Skills installed for coding assistants (`~/.claude/skills`, `~/.agents/skills`: code review, security
  audits and the like) are not used: they don't fit painting. To use a folder of skills anyway, list it in
  `KRITA_MCP_SKILLS_DIRS`, which replaces the list of folders.
- AI apps connected through MCP get the same skills with the `find_skills` and `use_skill` tools.
- Skills are read as text. KritaPilot never runs scripts that a skill folder contains, and it only reads
  files inside the skill's own folder.

## Two ways to sign in

- **Sign in with browser** (OpenRouter). Krita opens your browser, you approve "Krita MCP" on OpenRouter,
  and you are signed in. Under the hood this is OAuth with PKCE: OpenRouter redirects to a one-shot
  listener on `localhost`, and the code is exchanged for a key that only this computer stores. Approval
  links expire after 10 minutes, and a reply that does not match Krita's request is refused.
- **API key**, for every other service (Google, Anthropic, OpenAI, Stability, Replicate, fal, HeyGen).
  These providers do not offer browser sign-in to third-party apps. Click **Get a key in the browser**: the
  provider's key page opens, and when you copy the key there, the dialog recognises it from the clipboard
  and fills it in. You can also paste it by hand. Before saving, KritaPilot checks the key with the provider
  (where the provider allows a free check), so a mistyped key is reported instead of stored.

## Services: sign in once

**Services…** lists every AI service KritaPilot can use, grouped (chat models; images, video and more; on this
computer), with a search box (try `free`, `images`, `local`). Services you can use right now come first,
marked ✓, and each row says where it stands. Each one is set up the way it actually works - not everything
has an API key:

| Kind | Examples | What you do |
|---|---|---|
| **API key** | Gemini, Claude, OpenAI, Mistral, Groq, fal.ai, Stability, … | **Sign in**: **Get a key in the browser** opens the provider's key page; copy the key and it appears in the box by itself. **Change key** replaces it, **Sign out** deletes it. |
| **Browser sign-in** | OpenRouter | **Sign in with browser** and approve; nothing to copy (pasting a key also works). |
| **On this computer** | Ollama / LM Studio, ComfyUI, AUTOMATIC1111 / Forge, OmniRoute | **Set up**: the usual address is filled in; **Test connection** says whether the server answers and how many models it has. No account, no key. **Forget** removes the address. |
| **Free, no account** | Pollinations (chat), Kilo, OVHcloud, LLM7 | Nothing: they are ready. **Add key** (optional) raises the limits. |
| **Free, turn on** | Pollinations (pictures), AI Horde | **Turn on** / **Turn off** - one click. They send your prompts to a public service, so they are off until you turn them on. |

### The provider menu

1. **Auto - all my providers** (the default) uses every provider you can use, in turn: first the ones you
   signed in to or set up (Claude, OpenAI, Gemini, OpenRouter, then the others), then the free ones
   (Pollinations, Kilo, OVHcloud, LLM7). When one cannot go on - no credit left, a used-up quota or rate
   limit, a rejected key, the service down - the next one takes over the same task, and the chat says so
   ("OpenRouter could not go on (HTTP 402 ...); switched to Kilo Gateway"). Conversations cannot move between
   services as they are, so the next one gets a short recap of what you asked and what was already done, and
   carries on in the same document. A real mistake (a bad request) is reported, not hidden by switching.
   Hover over the provider count for the order; `KRITA_MCP_AUTO_ORDER` (comma-separated names, e.g.
   `anthropic,kilo`) sets your own. Existing installs are switched to Auto once; pick another provider and
   that choice stays.
2. The providers you can use now - signed in, set up, or free - always listed.
3. Every other provider, marked **set up…**: picking one opens its setup, and it is selected as soon as it
   is ready.
4. **Manage services…** opens Services.

Keys are stored only on your computer, in `~/.krita-mcp/agent_keys.json`. That file is readable only by
your user on Linux and macOS; on Windows it is protected by your Windows account's permissions. They are
used by:

- the AI Chat docker, and
- AI apps connected through MCP (Claude Desktop, Gemini CLI, Cursor, …). Signing in to fal.ai here, for
  example, lets Claude Desktop's `generate_image` use it too.

An explicit `KRITA_MCP_…` environment variable still wins over a saved key. **Sign out** deletes the key.

## When something goes wrong: Logs…

**Logs…** opens the assistant's log live: each message, which model answered and how long each step
took, every tool it called with the result, sign-ins, and errors with their trace, each line dated to the
millisecond. Switch to the **Krita** log to see every command it ran in Krita, panel actions and settings
changes. Type `✗` in the filter to see only what failed. Keys are masked, so **Copy** gives you text you
can paste into a bug report. See [the Krita panel](krita-panel.md#logs) for the details.

## How it works

```
AI Chat docker ──127.0.0.1:5680 + token──▶ kritapilot --agent ──model API──▶ Gemini / Claude / GPT / local
       ▲                                         │ tool calls
       └───────────── your canvas ◀── Krita plugin bridge (127.0.0.1:5679 + token) ◀┘
```

- The docker starts the assistant service (`kritapilot --agent`) when the AI Chat panel is shown, so the
  provider menu can list what you have set up. It uses the server command set in the *AI Bridge (MCP)*
  panel, so that command must be installed (see [installation](installation.md#2-install-the-mcp-server)). A hidden panel starts nothing.
- The service listens on loopback only and requires the same token as the bridge.
- All network calls run off Krita's main thread, so Krita stays responsive while the AI works.
- **Enter** sends, **Shift+Enter** starts a new line, and **Up** in an empty box brings back your last
  message. Your message appears in the chat at once. While the AI works, the line above the box says what
  it is doing (*sending*, *thinking*, or the tool it is using) and for how long, and you can keep typing:
  **Send** becomes **Queue**, and your messages wait under **Queued** and go out one after another when the
  AI is done. Double-click a queued message to take it back and edit it; **Clear** drops them.
- **Stop** ends the current task at once (queued messages come back into the box, so nothing is sent
  without you), even while a model is still thinking: the answer it was
  waiting for is dropped. A step already running inside Krita (a filter, say) finishes by itself. If the
  assistant does not confirm within 8 seconds, the panel stops waiting anyway. **New chat** stops the
  current task too and starts a fresh conversation.
- When the model service is briefly unavailable (HTTP 500/502/503/504, a timeout, a dropped connection),
  the assistant tries again after 3 and then 8 seconds and says so in the chat. A rejected key or missing
  credit is not retried (on Auto, the next provider takes over).
- One model answer may take at most three minutes (`KRITA_MCP_CHAT_TIMEOUT`, in seconds; default 180).
  Longer counts as the service being unavailable, so a stalled free service cannot hold the chat.
- A model that answers that it cannot take pictures gets them replaced by a short note, and the request
  is sent again. From then on it measures the canvas instead of looking at it.
- If the AI asks for an image service you have not set up (models sometimes invent one), the picture is
  made with the service you do have, and the chat says so.

### Settings for the assistant

| Variable | Default | Meaning |
|---|---|---|
| `KRITA_MCP_AUTO_ORDER` | yours, then the free ones | Auto's order, comma-separated (`anthropic,kilo`) |
| `KRITA_MCP_CHAT_TIMEOUT` | `180` | Seconds one model answer may take |
| `KRITA_MCP_CHAT_MAX_STEPS` | `40` | Steps per message before the AI stops and asks |
| `KRITA_MCP_CHAT_MAX_TOKENS` | `4096` (Claude `16000`) | Longest reply |
| `KRITA_MCP_CHAT_KEEP_IMAGES` | `2` | Pictures kept in the conversation |
| `KRITA_MCP_CHAT_IMAGE_SIZE` | `768` | Size of the snapshots sent to the model |
| `KRITA_MCP_GEMINI_THINKING` | `low` | How long Gemini 3 thinks: `low`, `medium`, `high`, `off` |
| `KRITA_MCP_<PROVIDER>_CHAT_MODEL` | Auto | Fix a provider's model, e.g. `KRITA_MCP_GEMINI_CHAT_MODEL` |
| `KRITA_MCP_SKILLS_DIRS` | `~/.krita-mcp/skills` + built-in | Folders to read skills from |
| `KRITA_MCP_AGENT_PORT` | `5680` | Port of the assistant service |

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
