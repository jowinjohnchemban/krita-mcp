# The Krita panel: *AI Bridge (MCP)*

Open it with **Settings ▸ Dockers ▸ AI Bridge (MCP)** or **Tools ▸ Scripts ▸ Krita MCP Bridge: show
panel**. It opens by itself the first time Krita starts with the plugin. Like all dockers, it is only
shown while a document is open.

## Status

The top line shows **● Running on 127.0.0.1:5679** or **● Stopped** (with the reason if starting
failed). **Start / Stop** turns the bridge on and off. While it is stopped, AI clients get "Cannot
reach Krita".

## AI access

| Mode | What the AI can do |
|---|---|
| **Full access** | Everything. |
| **Read only** | Look only: snapshots, analysis, pixel reads, layer lists, workspace files. Any edit is refused with a clear message. |
| **Paused** | Nothing. Every request is refused (handy while you work by hand). |

The mode is remembered between sessions.

## Settings

- **Port**: changing it restarts the bridge. Clients need the same port (`KRITA_MCP_PORT`).
- **Start with Krita**: start the bridge automatically.
- **Workspace**: the only folder the AI may read or write (imports, exports, videos, frames).
  **…** picks a folder; **Open** shows it in your file manager. `KRITA_MCP_WORKSPACE`, if set, wins.
- **Token**: the shared secret clients use. **Show** and **Copy** reveal and copy it; **New**
  generates a new one, and connected clients must then be restarted.

## Connect an AI client

Pick your client, set **Server command** if `krita-mcp` isn't on your PATH, and copy the generated
snippet. See [Connecting AI clients](clients.md).

## AI edits in this document

This shows how many AI edits can be undone or redone in the active document, and the most recent
ones. **Undo AI edit** and **Redo** step through them, exactly like the agent's own `undo` and `redo`
tools. See [Undo](undo-and-safety.md#undo).

## Updates

The plugin updates itself over the air from this project's GitHub releases.

| Setting | What happens |
|---|---|
| **Tell me when an update is available** (default) | Shortly after Krita starts (at most every 6 hours) the panel checks GitHub. A newer release shows a message on the canvas and an **Update now** button here. |
| **Install updates automatically** | A newer release is downloaded and installed without asking; a message on the canvas says so. |
| **Never check** | No requests to GitHub. |

**Check now** checks at any time. An update:

1. downloads the release's plugin zip from GitHub (only from GitHub's own addresses, over HTTPS);
2. checks it against the SHA-256 checksum GitHub publishes for it, and refuses files with unexpected or unsafe
   paths;
3. swaps the plugin folder in one step (the old one comes back if anything fails); the new version is
   used after Krita restarts;
4. also updates the `krita-mcp` server (`krita-mcp --update`): it stops the assistant service, downloads and
   checks the server package, and upgrades it in the background once `krita-mcp` has exited (on Windows,
   files of a running program are locked). Progress is written to `~/.krita-mcp/update.log`.

From a terminal: `krita-mcp --check-update` and `krita-mcp --update`.

A copy of the plugin bundled with Krita itself has no updater: it is updated with Krita.

## Activity

A live log of every request: time, ✓/✗, command, duration and any error. This includes requests
refused by the access mode.
