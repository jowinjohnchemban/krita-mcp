# Installation

You need **Krita 5.2 or newer** and **Python 3.10 or newer** for the MCP server. End-to-end testing is done
on Krita 5.3.4; Krita 6 (PyQt6) is covered by unit tests only.

## 1. Install the Krita plugin

### From a release (recommended)

1. Download `kritapilot-plugin-<version>.zip` from the
   [Releases page](https://github.com/jowinjohnchemban/kritapilot/releases).
2. In Krita, choose **Tools ▸ Scripts ▸ Import Python Plugin from File…** and select the zip.
3. Restart Krita when it asks. Then make sure the plugin is ticked under **Settings ▸ Configure Krita ▸
   Python Plugin Manager** (*KritaPilot*), and restart once more if you had to tick it.

### From a source checkout

```bash
python scripts/install_plugin.py              # copies the plugin into Krita's pykrita folder
python scripts/install_plugin.py --dest DIR   # if Krita's resource folder is somewhere unusual
```

Then enable it in the Python Plugin Manager as above. To find the resource folder by hand, use
**Settings ▸ Manage Resources ▸ Open Resource Folder**; the plugin goes in its `pykrita/` subfolder.

### Check it

On start-up the plugin creates `~/.kritapilot/token` and listens on `127.0.0.1:5679`. Open the panel
with **Settings ▸ Dockers ▸ AI Bridge (MCP)** (it also opens by itself the first time). It should say
**● Running on 127.0.0.1:5679**. Dockers appear only while a document is open.

## 2. Install the MCP server

Pick one:

```bash
pip install kritapilot                                  # from PyPI
pip install "git+https://github.com/jowinjohnchemban/kritapilot"   # from GitHub (latest main)
pip install "git+https://github.com/jowinjohnchemban/kritapilot@v1.0.0"   # from GitHub, one release
pipx install kritapilot                                 # PyPI, in its own environment
uvx kritapilot                                          # run without installing
pip install kritapilot-<version>-py3-none-any.whl       # the wheel from a GitHub release
pip install .                                           # from a source checkout
```

This provides the `kritapilot` command. Don't run
`pip install krita-mcp`: that name on PyPI belongs to a different project. It talks MCP over stdio by default; `kritapilot --help` lists
the options.

## 3. Connect an AI client

In the Krita panel, choose your client under **Connect an AI client**, then copy the snippet. If
`kritapilot` is not on your PATH, put the full path to the command in **Server command** first. For
per-client details see [Connecting AI clients](clients.md).

Then try a prompt such as: *"Create a 1200×800 canvas, paint a sunset with mountains using real
brushes, look at it, and improve it."*

## Configuration

These can be set in the panel (they are saved in Krita's settings) or with environment variables,
which take priority:

| Setting | Environment variable | Default |
|---|---|---|
| Port | `KRITAPILOT_PORT` | `5679` |
| Start with Krita | `KRITAPILOT_AUTOSTART=0` to disable | on |
| Workspace folder (the only place the AI can read/write files) | `KRITAPILOT_WORKSPACE` | `~/KritaMCP` |
| Token folder | `KRITAPILOT_HOME` | `~/.kritapilot` |
| ffmpeg/ffprobe folder (video) | `KRITAPILOT_FFMPEG` | the copy bundled with Krita, then `PATH` |

The MCP server also reads `KRITAPILOT_PORT`, `KRITAPILOT_HOME`, `KRITAPILOT_HOST` and `KRITAPILOT_TOKEN`.
