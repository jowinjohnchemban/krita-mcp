# Contributing

Thanks for helping. Bug reports, ideas, new skills, docs fixes and code are all welcome.

## Reporting a bug

Open an [issue](https://github.com/jowinjohnchemban/kritapilot/issues/new/choose) and include:

- the versions: Krita, the plugin and the server (the **AI Bridge (MCP)** panel shows them, or run
  `kritapilot --version`);
- what you asked the AI and what happened instead;
- the relevant lines from **Logs…** (in the AI Chat or AI Bridge panel). Keys are masked, so the text is
  safe to paste. **Copy** copies what the window shows; type `✗` in its filter to see only failures;
- if Krita froze: `~/.kritapilot/hang.log`.

Security problems: please don't open a public issue; see [SECURITY.md](SECURITY.md).

## Setting up

```bash
git clone https://github.com/jowinjohnchemban/kritapilot
cd KritaPilot
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[test]" PyQt6 PyQt5
python scripts/install_plugin.py                   # copy the plugin into Krita, then restart Krita
```

## Tests

```bash
QT_QPA_PLATFORM=offscreen pytest                   # PyQt6 (Krita 6 code path)
KRITA_TEST_QT=5 QT_QPA_PLATFORM=offscreen pytest   # PyQt5 (Krita 5 code path)
python scripts/smoke_krita.py                      # against a running Krita: 56 end-to-end checks
```

CI runs the unit tests on Linux and Windows with both PyQt versions. CI cannot run Krita, so for changes
to the plugin, please run the smoke test against a real Krita too.

## Making a change

- Keep a change focused, with tests for what it fixes or adds.
- New tool: see [Adding a tool](docs/development.md#adding-a-tool). Run `python scripts/gen_tool_docs.py`
  so `docs/tools.md` stays current (a test checks it).
- New skill: add a folder with a `SKILL.md` under `src/kritapilot/bundled_skills/` (a name, a one-line
  description, then the steps). Name only tools that exist; a test checks that too.
- New AI service: most are OpenAI-compatible and need one line in
  `src/kritapilot/providers/compat.py`. See [Connecting AI services](docs/ai-backends.md).
- Write docs and messages in plain words for artists, not developers.
- The plugin runs inside Krita on its main thread: never block it (network calls go to a worker thread).
  Never add a way for the AI to run code, and never log or show API keys.

The project layout and Krita API notes are in [docs/development.md](docs/development.md).

## Pull requests

Describe what changed and how you tested it. A maintainer reviews it, and once it is merged into `main`,
it ships in the next release automatically.

By contributing you agree that your work is released under the [MIT licence](LICENSE), and to follow the
[code of conduct](CODE_OF_CONDUCT.md).
