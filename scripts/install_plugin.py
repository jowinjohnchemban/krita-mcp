#!/usr/bin/env python3
"""Copy the Krita MCP Bridge plugin into Krita's pykrita folder.

Usage:  python scripts/install_plugin.py [--dest /custom/pykrita]
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "pykrita"


def candidates():
    home = Path.home()
    if sys.platform.startswith("win"):
        yield Path(os.environ.get("APPDATA", home)) / "krita" / "pykrita"
    elif sys.platform == "darwin":
        yield home / "Library" / "Application Support" / "krita" / "pykrita"
    else:
        yield home / ".local" / "share" / "krita" / "pykrita"
        yield home / ".var" / "app" / "org.kde.krita" / "data" / "krita" / "pykrita"  # Flatpak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path)
    args = ap.parse_args()
    dest = args.dest or next((c for c in candidates() if c.parent.exists()), None)
    if dest is None:
        sys.exit("Could not find Krita's data folder. In Krita: Settings > Manage Resources > Open Resource "
                 "Folder, then re-run with --dest <that folder>/pykrita")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC / "krita_mcp_bridge.desktop", dest / "krita_mcp_bridge.desktop")
    target = dest / "krita_mcp_bridge"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SRC / "krita_mcp_bridge", target, ignore=shutil.ignore_patterns("__pycache__"))
    print("Installed to", dest)
    print("Next: restart Krita, then Settings > Configure Krita > Python Plugin Manager > enable "
          "'Krita MCP Bridge', and restart Krita once more.")


if __name__ == "__main__":
    main()
