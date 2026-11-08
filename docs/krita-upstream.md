# Proposing the plugin for inclusion in Krita

Krita bundles Python plugins from `plugins/python/` in its source tree. Each plugin is installed by an
`install_pykrita_plugin(<name>)` line in `plugins/python/CMakeLists.txt`. `scripts/krita_upstream.py`
prepares a Krita checkout so the KritaPilot plugin is bundled the same way. Whether it is accepted is
up to the Krita maintainers. This page covers how to propose it and what they are likely to ask.

## Steps

1. Create a KDE Identity account and sign in to <https://invent.kde.org>.
2. Fork <https://invent.kde.org/graphics/krita> and clone your fork:
   ```bash
   git clone https://invent.kde.org/<you>/krita.git
   ```
3. From this repository, run:
   ```bash
   python scripts/krita_upstream.py --krita path/to/krita --commit
   ```
   This creates the branch `kritapilot` with:
   - `plugins/python/kritapilot_plugin/`: the plugin, with SPDX MIT headers, plus
     `kritapykrita_kritapilot_plugin.desktop` and `Manual.html`
   - one new line in `plugins/python/CMakeLists.txt`

   In this copy the over-the-air updater is removed (Krita ships the updates) and the bridge is **off by
   default**. Nothing listens and no panel opens until the user
   presses *Start* in *Settings ▸ Dockers ▸ AI Bridge (MCP)*.
4. Build Krita from the branch and check that the plugin shows up in the Python Plugin Manager, that the
   docker opens, and that `python scripts/smoke_krita.py` passes against it. (The bundled copy needs
   *Start* to be pressed first.)
5. Push the branch to your fork (`git push -u origin kritapilot`) and open a merge request into
   `graphics/krita` **master**. Mark it as a draft and add the **Needs Review** label, as the Krita
   hacking guide asks.

## Suggested merge request description

> **Add KritaPilot: let AI assistants work in Krita through the Model Context Protocol**
>
> This adds an optional Python plugin that exposes Krita's scripting API to AI assistants that speak the
> [Model Context Protocol](https://modelcontextprotocol.io) (Claude, ChatGPT, Gemini, Cursor, VS Code,
> local models, …). An assistant can create and edit documents, layers, selections, filters, vector
> shapes, animation frames and brush strokes with Krita's own brush engine. It can also look at the
> result (snapshots, pixel statistics) and undo its own edits.
>
> **What runs inside Krita:** only this plugin. It is a loopback-only (127.0.0.1) JSON bridge that
> requires a random token stored in the user's home folder. The AI client and any image/video model
> services run *outside* Krita in a separate program, `kritapilot`. The plugin itself makes no network
> requests.
>
> **Off by default.** When bundled, the bridge does not start until the user presses *Start* in its
> docker (*Settings ▸ Dockers ▸ AI Bridge (MCP)*). The docker shows status and an activity log. It can
> switch the AI to read-only or pause it, and it undoes AI edits separately from the user's own.
>
> **Safety:**
> - There is no code-execution command; only a fixed set of operations is exposed.
> - File access is limited to one workspace folder.
> - Krita actions are limited to a curated list that opens no dialogs.
> - AI edits are recorded so they can be undone, and the plugin refuses to undo over edits the user made
>   by hand in the meantime.
>
> **Tests:** unit tests run with PyQt5 and PyQt6 against a fake `krita` module. There is also an
> end-to-end smoke test with 56 steps, last run against Krita 5.3.4 on Windows. Upstream project:
> https://github.com/jowinjohnchemban/kritapilot (MIT; Krita's `LICENSES/` already contains MIT).

## What reviewers are likely to ask

- **Translations.** The docker's and actions' strings go through Krita's `i18n()`, like other bundled
  plugins. Reviewers may still ask for context strings (`i18nc`) in places.
- **Scope.** This is a large plugin (about 5,000 lines). Maintainers may prefer it to stay a separately
  installed plugin. In that case, share the release zip on
  [krita-artists.org](https://krita-artists.org) (*Resources ▸ Plugins*) and the KDE Store. Users
  install it with *Tools ▸ Scripts ▸ Import Python Plugin from File*.
- **Policy on AI features.** That decision belongs to the Krita project. Be prepared to explain that the
  plugin is a local automation bridge, off unless the user turns it on, and that no generative model is
  bundled or contacted by Krita.
- **Maintenance.** The plugin needs someone to keep it working as Krita's Python API changes.
