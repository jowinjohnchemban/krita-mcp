#!/usr/bin/env python3
"""Build the installable Krita plugin zip (Tools > Scripts > Import Python Plugin from File...).

Usage:  python scripts/build_plugin.py [--out dist]
Prints the path of the zip. The zip holds krita_mcp_bridge.desktop + krita_mcp_bridge/ at its root.
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "plugin" / "pykrita"


def version():
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    if not m:
        sys.exit("version not found in pyproject.toml")
    return m.group(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / ("krita-mcp-bridge-%s.zip" % version())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(SRC / "krita_mcp_bridge.desktop", "krita_mcp_bridge.desktop")
        for f in sorted((SRC / "krita_mcp_bridge").rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix not in (".pyc", ".pyo"):
                z.write(f, "krita_mcp_bridge/" + f.relative_to(SRC / "krita_mcp_bridge").as_posix())
    print(target)


if __name__ == "__main__":
    main()
