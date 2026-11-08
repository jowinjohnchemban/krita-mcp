# KritaPilot documentation (Krita MCP server and AI Chat)

KritaPilot lets AI work inside a running [Krita](https://krita.org): AI agents through the
[Model Context Protocol](https://modelcontextprotocol.io) (MCP), and an AI Chat inside Krita itself. It covers
painting, photo editing, vector art, animation and video.

| Guide | What it covers |
|---|---|
| [Installation](installation.md) | Install the Krita plugin and the MCP server, then check that they work |
| [Connecting AI clients](clients.md) | Claude Code / Desktop, Cursor, VS Code, Gemini CLI, Codex CLI, ChatGPT and other HTTP clients |
| [AI Chat inside Krita](ai-chat.md) | Chatting with an AI in Krita: Auto, 40+ providers, free models, setting up services, skills, the queue |
| [Connecting AI services](ai-backends.md) | Image, video and vision models: ComfyUI, AUTOMATIC1111, OpenAI, Gemini, Stability, Replicate, fal, Pollinations, AI Horde, HeyGen |
| [The Krita panel](krita-panel.md) | The *AI Bridge (MCP)* docker: status, access modes, popups, workspace, token, updates, activity, Logs, undo |
| [Workflows](workflows.md) | Recipes for painting, photo retouching, inpainting with an image model, line art, animation and video |
| [Tool reference](tools.md) | Every tool and its parameters (generated from the code) |
| [Undo, safety and limits](undo-and-safety.md) | How undo works, the security model, access modes, limits |
| [Development](development.md) | Architecture, tests, the real-Krita smoke test, adding tools |
| [Releasing](releasing.md) | Cutting a release: plugin zip, wheel, GitHub release |

## How it fits together

```
AI client ──MCP (stdio or HTTP)──▶ KritaPilot server ──127.0.0.1 + token──▶ Krita plugin ──▶ your canvas
```

There are two parts:

- **The Krita plugin** (`plugin/pykrita/krita_mcp_bridge`) runs inside Krita. It listens on
  `127.0.0.1` only, checks a secret token on every request, and runs commands on Krita's GUI thread.
  It also adds the *AI Bridge (MCP)* panel.
- **The MCP server** (`src/kritapilot`) is a normal Python program that your AI client starts. It turns
  MCP tool calls into commands for the plugin.
