#!/usr/bin/env python3
"""For the release workflow: work out the next version, stamp it with the build id and commit, and print the
values for $GITHUB_OUTPUT (only `key=value` lines go to stdout).

    python scripts/release_version.py --repo OWNER/NAME --sha 057a829...

The next version is MAJOR.MINOR from pyproject.toml and PATCH one more than the highest version with that
MAJOR.MINOR already published - as a GitHub release (tag vX.Y.Z) or on PyPI - so releases count up by one.
The build id is the UTC build date and the commit (YYYYMMDD-COMMIT).
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bump_version  # noqa: E402


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers=dict({"User-Agent": "kritapilot-release"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001 - nothing published yet is fine
        print("note: %s: %s" % (url, e), file=sys.stderr)
        return None


def published(repo):
    """Every version already published: GitHub release tags (vX.Y.Z) and PyPI releases."""
    found = set()
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    page = 1
    while True:
        rels = _get_json("https://api.github.com/repos/%s/releases?per_page=100&page=%d" % (repo, page), headers)
        if not rels:
            break
        found.update((r.get("tag_name") or "").lstrip("v") for r in rels)
        page += 1
    pypi = _get_json("https://pypi.org/pypi/kritapilot/json")
    if pypi:
        found.update(pypi.get("releases") or {})
    return sorted(v for v in found if bump_version.SEMVER.fullmatch(v))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True)
    args = ap.parse_args()
    seen = published(args.repo)
    version = bump_version.next_published(bump_version.current(), seen)
    commit = args.sha[:7]
    build = "%s-%s" % (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d"), commit)
    for rel, pattern in bump_version.FILES:
        bump_version._stamp(rel, pattern, version)
    for rel, build_pattern, commit_pattern in bump_version.BUILD_FILES:
        bump_version._stamp(rel, build_pattern, build)
        bump_version._stamp(rel, commit_pattern, commit)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_tool_docs.py")], check=True, stdout=sys.stderr)
    print("published: %s -> %s (build %s)" % (", ".join(seen) or "nothing", version, build),
          file=sys.stderr)
    print("version=%s" % version)
    print("build=%s" % build)
    print("commit=%s" % commit)


if __name__ == "__main__":
    main()
