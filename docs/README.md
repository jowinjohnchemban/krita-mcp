# krita-mcp documentation

krita-mcp lets AI agents work inside a running [Krita](https://krita.org). It covers painting, photo
editing, vector art, animation and video, through the
[Model Context Protocol](https://modelcontextprotocol.io).

| Guide | What it covers |
|---|---|
| [Installation](installation.md) | Install the Krita plugin and the MCP server, then check that they work |
| [Connecting AI clients](clients.md) | Claude Code / Desktop, Cursor, VS Code, Gemini CLI, Codex CLI, ChatGPT and other HTTP clients |
| [The Krita panel](krita-panel.md) | The *AI Bridge (MCP)* docker: status, access modes, workspace, token, activity log, undo |
| [Workflows](workflows.md) | Recipes for painting, photo retouching, inpainting with an image model, line art, animation and video |
| [Tool reference](tools.md) | Every tool and its parameters (generated from the code) |
| [Undo, safety and limits](undo-and-safety.md) | How undo works, the security model, access modes, limits |
| [Development](development.md) | Architecture, tests, the real-Krita smoke test, adding tools |
| [Releasing](releasing.md) | Cutting a release: plugin zip, wheel, GitHub release, optional PyPI |

## How it fits together

```
AI client ──MCP (stdio or HTTP)──▶ krita-mcp server ──127.0.0.1 + token──▶ Krita plugin ──▶ your canvas
```

There are two parts:

- **The Krita plugin** (`plugin/pykrita/krita_mcp_bridge`) runs inside Krita. It listens on
  `127.0.0.1` only, checks a secret token on every request, and runs commands on Krita's GUI thread.
  It also adds the *AI Bridge (MCP)* panel.
- **The MCP server** (`src/krita_mcp`) is a normal Python program that your AI client starts. It turns
  MCP tool calls into commands for the plugin.
