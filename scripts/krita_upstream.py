#!/usr/bin/env python3
"""Turn a Krita source checkout into a merge-request branch that bundles the Krita MCP Bridge plugin.

    git clone https://invent.kde.org/<you>/krita.git        # your fork on KDE Invent
    python scripts/krita_upstream.py --krita path/to/krita    # this script: copies + wires the plugin
    cd path/to/krita && git push -u origin krita-mcp-bridge   # then open the MR against graphics/krita master

What it does in the Krita tree:
  * copies the plugin to plugins/python/krita_mcp_bridge/ (Python files + Manual.html, no caches/tests)
  * adds SPDX headers (MIT, which Krita's LICENSES/ already contains) to every Python file
  * writes kritapykrita_krita_mcp_bridge.desktop (the name Krita's CMake expects)
  * makes the bundled copy opt-in: no port is opened and no panel pops up until the user presses Start
  * adds install_pykrita_plugin(krita_mcp_bridge) to plugins/python/CMakeLists.txt
  * creates a branch and (with --commit) commits, using your git identity. It never pushes.
"""
import argparse
import datetime
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "plugin" / "pykrita"
NAME = "krita_mcp_bridge"


def git(cwd, *args, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def spdx(text, holder, year):
    lines = text.splitlines(keepends=True)
    head = "# SPDX-FileCopyrightText: %s %s\n# SPDX-License-Identifier: MIT\n\n" % (year, holder)
    if lines and lines[0].startswith("#!"):
        return lines[0] + head + "".join(lines[1:])
    return head + text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--krita", required=True, type=Path, help="path to a Krita git checkout (your fork)")
    ap.add_argument("--branch", default="krita-mcp-bridge")
    ap.add_argument("--author", help='"Name <email>" for the SPDX headers (default: your git identity)')
    ap.add_argument("--commit", action="store_true", help="commit on the branch (never pushes)")
    args = ap.parse_args()

    krita = args.krita.resolve()
    cmake = krita / "plugins" / "python" / "CMakeLists.txt"
    if not cmake.is_file():
        sys.exit("%s does not look like a Krita checkout (no plugins/python/CMakeLists.txt)" % krita)
    if not (krita / "LICENSES" / "MIT.txt").is_file():
        print("warning: LICENSES/MIT.txt not found in the checkout; REUSE lint may complain")
    holder = args.author or "%s <%s>" % (git(ROOT, "config", "user.name"), git(ROOT, "config", "user.email"))
    year = datetime.date.today().year

    if args.branch and git(krita, "rev-parse", "--abbrev-ref", "HEAD") != args.branch:
        git(krita, "checkout", "-B", args.branch)

    dest = krita / "plugins" / "python" / NAME
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for f in sorted((SRC / NAME).iterdir()):
        if f.suffix == ".py":
            text = f.read_text(encoding="utf-8")
            if f.name == "extension.py":  # bundled copy: opt-in, quiet
                text, n = re.subn(r"^AUTOSTART_DEFAULT = True$", "AUTOSTART_DEFAULT = False", text, flags=re.M)
                text, m = re.subn(r"^INTRODUCE_PANEL = True$", "INTRODUCE_PANEL = False", text, flags=re.M)
                # Krita ships updates of a bundled copy; it must not replace itself from GitHub
                text, u = re.subn(r"^UPDATER_ENABLED = True", "UPDATER_ENABLED = False", text, flags=re.M)
                if not (n and m and u):
                    sys.exit("extension.py no longer has the AUTOSTART_DEFAULT / INTRODUCE_PANEL switches")
            (dest / f.name).write_text(spdx(text, holder, year), encoding="utf-8", newline="\n")
        elif f.name == "Manual.html":
            shutil.copy2(f, dest / f.name)

    desktop = (SRC / (NAME + ".desktop")).read_text(encoding="utf-8")
    desktop = desktop.replace("Comment=Local, token-authenticated bridge that lets an MCP server (Claude, ChatGPT, "
                              "Gemini, ...) edit documents in Krita.",
                              "Comment=Lets AI assistants that speak the Model Context Protocol work in Krita "
                              "through a local, token-protected connection (off until started in its docker)")
    (dest / ("kritapykrita_%s.desktop" % NAME)).write_text(desktop, encoding="utf-8", newline="\n")

    text = cmake.read_text(encoding="utf-8")
    line = "install_pykrita_plugin(%s)" % NAME
    if line not in text:
        calls = [m for m in re.finditer(r"^install_pykrita_plugin\([^)]*\)[ \t]*$", text, flags=re.M)]
        if not calls:
            sys.exit("could not find install_pykrita_plugin(...) calls in %s" % cmake)
        at = calls[-1].end()
        text = text[:at] + "\n" + line + text[at:]
        cmake.write_text(text, encoding="utf-8", newline="\n")

    files = sorted(p.name for p in dest.iterdir())
    print("Prepared %s (%d files): %s" % (dest, len(files), ", ".join(files)))
    print("CMakeLists.txt: %s" % line)
    if args.commit:
        git(krita, "add", "plugins/python/CMakeLists.txt", "plugins/python/" + NAME)
        git(krita, "commit", "-q", "-m",
            "Add Krita MCP Bridge Python plugin\n\nA local, token-protected bridge that lets AI assistants using "
            "the Model Context Protocol\nwork in Krita. Off by default; started from Settings > Dockers > "
            "AI Bridge (MCP).")
        print("Committed on branch %s: %s" % (args.branch, git(krita, "log", "--oneline", "-1")))
    print("\nNext: review `git diff`, build Krita, then push the branch to your fork and open a merge request\n"
          "against graphics/krita master. Suggested description: docs/krita-upstream.md")


if __name__ == "__main__":
    main()
