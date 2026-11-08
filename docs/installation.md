# Installation

You need **Krita 5.2 or newer** and **Python 3.10 or newer** for the MCP server. End-to-end testing is done
on Krita 5.3.4; Krita 6 (PyQt6) is covered by unit tests only.

## 1. Install the Krita plugin

### From a release (recommended)

1. Download `krita-mcp-bridge-<version>.zip` from the
   [Releases page](https://github.com/jowinjohnchemban/krita-mcp/releases).
2. In Krita, choose **Tools ▸ Scripts ▸ Import Python Plugin from File…** and select the zip.
3. Restart Krita when it asks. Then make sure the plugin is ticked under **Settings ▸ Configure Krita ▸
   Python Plugin Manager** (*Krita MCP Bridge*), and restart once more if you had to tick it.

### From a source checkout

```bash
python scripts/install_plugin.py              # copies the plugin into Krita's pykrita folder
python scripts/install_plugin.py --dest DIR   # if Krita's resource folder is somewhere unusual
```

Then enable it in the Python Plugin Manager as above. To find the resource folder by hand, use
**Settings ▸ Manage Resources ▸ Open Resource Folder**; the plugin goes in its `pykrita/` subfolder.

### Check it

On start-up the plugin creates `~/.krita-mcp/token` and listens on `127.0.0.1:5679`. Open the panel
with **Settings ▸ Dockers ▸ AI Bridge (MCP)** (it also opens by itself the first time). It should say
**● Running on 127.0.0.1:5679**. Dockers appear only while a document is open.

## 2. Install the MCP server

Pick one:

```bash
pip install "git+https://github.com/jowinjohnchemban/krita-mcp"   # the latest source
pip install krita_mcp-<version>-py3-none-any.whl       # the wheel from a GitHub release
uvx --from git+https://github.com/jowinjohnchemban/krita-mcp krita-mcp   # run without installing
pip install .                                          # from a source checkout
```

This provides the `krita-mcp` command. Don't run `pip install krita-mcp`: that name on PyPI belongs to a
different project. It talks MCP over stdio by default; `krita-mcp --help` lists
the options.

## 3. Connect an AI client

In the Krita panel, choose your client under **Connect an AI client**, then copy the snippet. If
`krita-mcp` is not on your PATH, put the full path to the command in **Server command** first. For
per-client details see [Connecting AI clients](clients.md).

Then try a prompt such as: *"Create a 1200×800 canvas, paint a sunset with mountains using real
brushes, look at it, and improve it."*

## Configuration

These can be set in the panel (they are saved in Krita's settings) or with environment variables,
which take priority:

| Setting | Environment variable | Default |
|---|---|---|
| Port | `KRITA_MCP_PORT` | `5679` |
| Start with Krita | `KRITA_MCP_AUTOSTART=0` to disable | on |
| Workspace folder (the only place the AI can read/write files) | `KRITA_MCP_WORKSPACE` | `~/KritaMCP` |
| Token folder | `KRITA_MCP_HOME` | `~/.krita-mcp` |
| ffmpeg/ffprobe folder (video) | `KRITA_MCP_FFMPEG` | the copy bundled with Krita, then `PATH` |

The MCP server also reads `KRITA_MCP_PORT`, `KRITA_MCP_HOME`, `KRITA_MCP_HOST` and `KRITA_MCP_TOKEN`.
