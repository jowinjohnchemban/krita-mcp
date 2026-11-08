# Changelog

Releases are published automatically from `main` with [SemVer](https://semver.org) versions that count up by one
(see [releases](https://github.com/jowinjohnchemban/kritapilot/releases) and [PyPI](https://pypi.org/p/kritapilot)).
The plugin and the server update themselves. This file lists what changed, newest first.

## 1.0.3

- **Edit by describing the change**: `edit_image("make it night")` - Gemini, OpenAI and Flux Kontext edit by
  instruction; other services rework the picture. The selection or the whole picture, onto a new layer.
- **Select by describing it**: `select_by_description("the sky")` - pixel-accurate masks with Gemini or fal,
  an approximate outline from any vision model (free with Pollinations).
- **History…**: every generated picture is kept; add one back, or reuse its prompt
  (`generation_history`, `place_generation`).
- **Getting started**: the first time the AI Chat opens - start free, sign in, or use your own GPU (it finds a
  ComfyUI server, including krita-ai-diffusion's).
- **Commands for people and scripts**: `kritapilot status`, `chat`, `snapshot`, `tools`, `call`, `history`.
- **About KritaPilot**, with the version, build and system details to copy into a bug report.

## 2026-11-08

**New name: KritaPilot** (formerly *krita-mcp*). The project is more than an MCP server now, so it has a name of its own: the repository is `jowinjohnchemban/kritapilot`, the package is [`kritapilot` on PyPI](https://pypi.org/p/kritapilot) and the command is `kritapilot`. Versions are SemVer from 1.0.0 and count up by one with each release.

**AI Chat**
- **Auto - all my providers** is the default: it uses the providers you signed in to or set up, then the
  free ones, and when one runs out of credit, hits a quota, rejects its key or stops answering, the next
  one carries on with the same task (with a recap of what was done).
- The provider menu always lists what you can use now; every other provider is one pick away and opens
  its setup.
- Services are set up the way each one works: an API key, a browser sign-in, a server address with
  **Test connection**, or nothing at all for the free ones. Sign out, Change key, Turn on and Turn off work
  at once.
- A chat like a coding assistant's: Enter sends, Shift+Enter adds a line, Up recalls; messages typed while
  the AI works are queued; your message shows at once; a status line says what the AI is doing.
- A model answer may take at most three minutes; models that cannot see pictures get text instead; an
  image service the AI invents is replaced by one you have; AI Horde makes at most two variations, each
  with its own seed.

**Reliability**
- A freeze watchdog: if Krita is ever frozen for a minute, every thread's stack goes to
  `~/.kritapilot/hang.log`.
- Fixed: the Send button, a panel that could stop updating while a dialog was open, and skills meant for
  coding assistants being offered to the painting assistant.

## 2026-11-07

**AI Chat**
- Free providers with no sign-in (Pollinations, Kilo Gateway, OVHcloud, LLM7, OmniRoute) and free image
  services (Pollinations, AI Horde).
- 39 built-in skills, found and loaded only when a request needs them.
- Fewer tokens per step (older pictures dropped, prompt caching, repeated errors stop), and the cost of
  each answer shown.
- Stop works at once; retries when a model service is briefly down; readable errors.
- **Logs…**: every command, chat step, setting change and error, live, with keys masked.

**Krita**
- Fixed the "Waiting for image operation to complete… Scripted Brush Stroke" freeze.
- Popups Krita opens while the AI works (such as "Missing Colour Profile") are cancelled and reported to
  the AI instead of blocking it.
- Copy or cut to a new layer no longer goes through your clipboard.
- Over-the-air updates for the plugin and the server, checked against GitHub's checksums.

## 2026-11-06

First public version:
- 86 MCP tools: real brushes, shapes, vectors and text; layers, masks and selections; photo adjustments
  and filters; animation and video; seeing and measuring the canvas; undo for AI edits.
- Generative AI on the canvas: ComfyUI, AUTOMATIC1111/Forge, OpenAI, Gemini, Stability, Replicate and
  fal.ai for images; Sora, Veo, HeyGen, Replicate and fal for video; vision models for a second opinion.
- `create_artwork`: one prompt, generated or painted by hand.
- The AI Chat docker inside Krita with browser sign-in for OpenRouter and live model lists from each
  provider.
- The AI Bridge panel: status, access modes (full, read-only, paused), workspace, token and client
  snippets.
