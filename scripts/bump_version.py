#!/usr/bin/env python3
"""Stamp a version into every place it lives (pyproject.toml, __version__, the plugin's BRIDGE_VERSION, and
the tool reference).

    python scripts/bump_version.py 2026.11.7.158   # or 2026.11.7, or: patch | minor | major

The release workflow runs this on its own build copy on every push to main, with the date and the build
number as the version (YYYY.M.D.BUILD); nothing is committed. Run it locally only to build a versioned
package yourself.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ("pyproject.toml", r'^(version\s*=\s*")([^"]+)(")'),
    ("src/krita_mcp/__init__.py", r'^(__version__\s*=\s*")([^"]+)(")'),
    ("plugin/pykrita/krita_mcp_bridge/handlers.py", r'^(BRIDGE_VERSION\s*=\s*")([^"]+)(")'),
]


def current():
    text = (ROOT / FILES[0][0]).read_text(encoding="utf-8")
    return re.search(FILES[0][1], text, re.M).group(2)


def next_version(arg, cur):
    if re.fullmatch(r"\d+\.\d+\.\d+(\.\d+)?([.-]?(a|b|rc|dev)\d*)?", arg):
        return arg
    major, minor, patch = (int(x) for x in re.match(r"(\d+)\.(\d+)\.(\d+)", cur).groups())
    if arg == "patch":
        return "%d.%d.%d" % (major, minor, patch + 1)
    if arg == "minor":
        return "%d.%d.0" % (major, minor + 1)
    if arg == "major":
        return "%d.0.0" % (major + 1)
    sys.exit("give a version like 0.4.0, or patch / minor / major")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    cur = current()
    new = next_version(sys.argv[1], cur)
    for rel, pattern in FILES:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        text, n = re.subn(pattern, lambda m: m.group(1) + new + m.group(3), text, count=1, flags=re.M)
        if n != 1:
            sys.exit("could not find the version in %s" % rel)
        path.write_text(text, encoding="utf-8", newline="\n")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_tool_docs.py")], check=True)
    print("%s -> %s (stamped in this copy; releases stamp their own build with the date and build number)."
          % (cur, new))


if __name__ == "__main__":
    main()
