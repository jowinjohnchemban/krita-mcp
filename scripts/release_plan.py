#!/usr/bin/env python3
"""Decide whether main should be released, and as which version (used by .github/workflows/release.yml).

    python scripts/release_plan.py            # prints JSON: {"publish", "bump", "version", "tag", "last", "reason"}

Rules:
  * the version in pyproject.toml is newer than the latest v* tag  -> release it as is (a manual bump);
  * otherwise, if plugin / server code changed since the latest tag  -> release the next version:
    patch by default, minor if a commit message since then contains [minor], major for [major];
  * otherwise (docs, tests, CI only)                                 -> nothing to release.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS = ["src", "plugin", "pyproject.toml", "scripts/build_plugin.py"]


def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def parse(v):
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def current_version(root=ROOT):
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)


def bump(v, level):
    major, minor, patch = v
    if level == "major":
        return "%d.0.0" % (major + 1)
    if level == "minor":
        return "%d.%d.0" % (major, minor + 1)
    return "%d.%d.%d" % (major, minor, patch + 1)


def plan(root=ROOT):
    cur = current_version(root)
    tags = [t for t in git("tag", "-l", "v*", cwd=root).split() if parse(t)]
    last = max(tags, key=parse) if tags else None
    out = {"last": last, "bump": False}
    if last is None or parse(cur) > parse(last):
        return dict(out, publish=True, version=cur, tag="v" + cur,
                    reason="version %s is newer than %s" % (cur, last or "any release"))
    changed = git("diff", "--name-only", "%s..HEAD" % last, "--", *CODE_PATHS, cwd=root)
    if not changed:
        return dict(out, publish=False, version=cur, tag="v" + cur,
                    reason="no plugin or server changes since %s" % last)
    messages = git("log", "--format=%s%n%b", "%s..HEAD" % last, cwd=root)
    level = "major" if "[major]" in messages else "minor" if "[minor]" in messages else "patch"
    nxt = bump(max(parse(cur), parse(last)), level)
    return dict(out, publish=True, bump=True, version=nxt, tag="v" + nxt,
                reason="%s release: %d changed files since %s" % (level, len(changed.splitlines()), last))


def main(argv):
    out_file = None
    if "--github-output" in argv:
        i = argv.index("--github-output")
        out_file = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    p = plan(Path(argv[0]) if argv else ROOT)
    print(json.dumps(p))
    if out_file:  # key=value lines for the workflow; booleans as true/false
        with open(out_file, "a", encoding="utf-8") as f:
            for k in ("version", "tag", "publish", "bump"):
                v = p[k]
                f.write("%s=%s\n" % (k, str(v).lower() if isinstance(v, bool) else v))
        print("::notice::" + p["reason"])


if __name__ == "__main__":
    main(sys.argv[1:])
