#!/usr/bin/env python3
"""Stamp a version into every place it lives (pyproject.toml, __version__, the plugin's BRIDGE_VERSION and the
tool reference), and optionally the build id and the commit.

    python scripts/bump_version.py 1.0.4 --build 20261108-057a829 --commit 057a829
    python scripts/bump_version.py next --published 1.0.3 1.0.2   # the next patch after what is published
    python scripts/bump_version.py minor                          # or: patch | major | an exact version

Versions are SemVer (MAJOR.MINOR.PATCH) and official names use only them. The release workflow runs this on
its own build copy on every push to main with `next`: MAJOR.MINOR come from pyproject.toml and PATCH is one more
than the highest already published for that MAJOR.MINOR (on GitHub or PyPI), so releases count up by one. The
build id (YYYYMMDD-COMMIT) and the commit are stamped too.
Nothing is committed. To start a new minor or major version, set it in pyproject.toml (`minor` / `major`
here do that) and push: the next release is X.Y.0.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ("pyproject.toml", r'^(version\s*=\s*")([^"]+)(")'),
    ("src/kritapilot/config.py", r'^(VERSION\s*=\s*")([^"]+)(")'),
    ("plugin/pykrita/kritapilot_plugin/config.py", r'^(VERSION\s*=\s*")([^"]+)(")'),
]
BUILD_FILES = [  # (file, build-id pattern, commit pattern)
    ("src/kritapilot/config.py", r'^(BUILD\s*=\s*")([^"]*)(")', r'^(COMMIT\s*=\s*")([^"]*)(")'),
    ("plugin/pykrita/kritapilot_plugin/config.py", r'^(BUILD\s*=\s*")([^"]*)(")', r'^(COMMIT\s*=\s*")([^"]*)(")'),
]
SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def current():
    text = (ROOT / FILES[0][0]).read_text(encoding="utf-8")
    return re.search(FILES[0][1], text, re.M).group(2)


def next_published(base, published):
    """MAJOR.MINOR of `base`, PATCH one more than the highest published for that MAJOR.MINOR (else PATCH of base)."""
    major, minor, patch = (int(x) for x in SEMVER.match(base).groups())
    same = [int(m.group(3)) for m in (SEMVER.fullmatch(v.strip()) for v in published)
            if m and (int(m.group(1)), int(m.group(2))) == (major, minor)]
    return "%d.%d.%d" % (major, minor, max(same) + 1 if same else patch)


def next_version(arg, cur, published=()):
    if arg == "next":
        return next_published(cur, published)
    if SEMVER.fullmatch(arg):
        return arg
    major, minor, patch = (int(x) for x in SEMVER.match(cur).groups())
    if arg == "patch":
        return "%d.%d.%d" % (major, minor, patch + 1)
    if arg == "minor":
        return "%d.%d.0" % (major, minor + 1)
    if arg == "major":
        return "%d.0.0" % (major + 1)
    sys.exit("give a version like 1.0.4, or next / patch / minor / major")


def _stamp(rel, pattern, value):
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    text, n = re.subn(pattern, lambda m: m.group(1) + value + m.group(3), text, count=1, flags=re.M)
    if n != 1:
        sys.exit("could not find %s in %s" % (pattern, rel))
    path.write_text(text, encoding="utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", help="next, patch, minor, major, or an exact version like 1.0.4")
    ap.add_argument("--published", nargs="*", default=[], help="versions already published (for `next`)")
    ap.add_argument("--build", help="the build id, YYYYMMDD-COMMIT")
    ap.add_argument("--commit", help="the git commit (short)")
    args = ap.parse_args()
    cur = current()
    new = next_version(args.version, cur, args.published)
    for rel, pattern in FILES:
        _stamp(rel, pattern, new)
    for rel, build_pattern, commit_pattern in BUILD_FILES:
        if args.build:
            _stamp(rel, build_pattern, args.build)
        if args.commit:
            _stamp(rel, commit_pattern, args.commit)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_tool_docs.py")], check=True)
    print(new)


if __name__ == "__main__":
    main()
