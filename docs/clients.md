# Connecting AI clients

Every client runs the `kritapilot` command and talks to it over stdio. The Krita panel
(**AI Bridge (MCP) ▸ Connect an AI client**) generates the snippets below for you, already filled in
with your command and port. If `kritapilot` is not on your PATH, use the full path, for example
`C:\Users\you\AppData\Local\Programs\Python\Python313\Scripts\kritapilot.exe`.

If you changed the port, add `KRITAPILOT_PORT` to the server's environment (the panel does this for you).

## Claude Code

```bash
claude mcp add krita -- kritapilot
```

## Claude Desktop

Edit `claude_desktop_config.json` (Settings ▸ Developer ▸ Edit Config):

```json
{ "mcpServers": { "krita": { "command": "kritapilot", "args": [] } } }
```

## Cursor / Windsurf

`~/.cursor/mcp.json` (Windsurf: its MCP settings file), with the same JSON as Claude Desktop.

## VS Code (GitHub Copilot agent mode)

`.vscode/mcp.json` in your workspace:

```json
{ "servers": { "krita": { "type": "stdio", "command": "kritapilot", "args": [] } } }
```

## Gemini CLI

`~/.gemini/settings.json`:

```json
{ "mcpServers": { "krita": { "command": "kritapilot", "args": [] } } }
```

## Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.krita]
command = "kritapilot"
args = []
```

## ChatGPT and other clients that need a URL

Run the HTTP transport:

```bash
KRITAPILOT_HTTP_TOKEN=<long-random-secret> kritapilot --transport http --port 8765
# endpoint: http://127.0.0.1:8765/mcp     header: Authorization: Bearer <secret>
```

Hosted clients need a public HTTPS URL, so you would have to put a tunnel or reverse proxy in front.
Before you do, remember that anyone who can reach that URL with the token can draw in your Krita and
write files in your workspace. Only expose it if your client can send the bearer header (or you add
your own auth proxy). The server refuses to bind a non-loopback address unless `KRITAPILOT_HTTP_TOKEN` is set.

## Troubleshooting

- **"Cannot reach Krita"**: Krita isn't running, the plugin isn't enabled, or the bridge is stopped
  (check the panel). Also check that the port matches.
- **"AuthError: invalid or missing token"**: the token changed (for example via **New** in the panel).
  Restart the AI client.
- **"paused" / "read-only" errors**: the access mode in the panel is not *Full access*.
- **Calls time out**: a modal dialog is open in Krita. Close it.
