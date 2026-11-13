# Privacy

KritaPilot is software that runs on your own computer. It has no account system, no analytics and no
telemetry, and the author runs no server that receives your data.

## What stays on your computer

- Your artwork. The Krita plugin and the MCP server talk to each other over a loopback connection
  (`127.0.0.1`) protected by a token. Nothing is exposed to the network unless you start the HTTP
  transport on another address yourself, and that requires a token.
- Settings, API keys you save in *AI Chat > Services*, the AI Chat history and the logs live in
  `~/.kritapilot/` (or `KRITAPILOT_HOME`). Keys are in `agent_keys.json`, a file only your user can read.
  Logs mask keys and tokens.
- Files the AI reads or writes are limited to the workspace folder.

## What can leave your computer, and only because you chose it

- **AI apps you connect.** If you connect Claude, ChatGPT, Gemini, Cursor or another MCP client, what you
  and the AI exchange goes to that app's provider under that provider's own privacy policy. KritaPilot does
  not control that.
- **AI Chat in Krita.** Messages, and the pictures the AI looks at, go to the model service you sign in to
  (OpenAI, Anthropic, Gemini, OpenRouter, a local server, and so on).
- **Generative tools.** When you use a cloud image, video, vision or background-removal service, the region
  of the canvas it works on (with the mask or start frame where that applies) and your prompt are uploaded to
  that service, under its terms. Cloud services are used only with a key you gave KritaPilot. Keys already
  in your environment for other programs are ignored unless you set `KRITAPILOT_USE_ENV_KEYS=1`. Free
  services that need no key are used only after you turn them on.
- **Local services** (ComfyUI, AUTOMATIC1111, Ollama, LM Studio) run on your machine.
- **Assets.** `search_assets` and `import_asset` fetch public textures and photos (Poly Haven, Openverse)
  and send only your search words.
- **Updates.** `kritapilot --check-update` and `--update` contact GitHub (releases and their files) and
  send nothing about you beyond what any web request includes.

The AI cannot choose which servers KritaPilot talks to. Only you can, through settings and environment
variables. Result files are downloaded only from public addresses.

## Deleting your data

Remove the `~/.kritapilot/` folder (or sign out in *AI Chat > Services* to forget a key). Uninstalling the
plugin and the `kritapilot` package removes the software. Data held by the services you used is governed
by their policies; delete it there.

## Questions

Open an issue at <https://github.com/jowinjohnchemban/kritapilot/issues>. For security problems see
[SECURITY.md](SECURITY.md).
