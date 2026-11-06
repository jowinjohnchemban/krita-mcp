"""scripts/release_plan.py on a throw-away git repository."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_plan  # noqa: E402


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    return tmp_path


def commit(repo, path, text, msg, version=None):
    if version:
        (repo / "pyproject.toml").write_text('[project]\nversion = "%s"\n' % version)
    p = repo / path
    p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def test_release_rules(repo):
    commit(repo, "src/a.py", "1", "first", version="0.3.0")
    assert release_plan.plan(repo) == dict(release_plan.plan(repo), publish=True, version="0.3.0", bump=False)
    git(repo, "tag", "v0.3.0")
    assert release_plan.plan(repo)["publish"] is False  # nothing new
    commit(repo, "docs/x.md", "doc", "docs only")
    assert release_plan.plan(repo)["publish"] is False  # docs do not release
    commit(repo, "src/a.py", "2", "fix a bug")
    p = release_plan.plan(repo)
    assert (p["publish"], p["bump"], p["version"], p["tag"]) == (True, True, "0.3.1", "v0.3.1")
    commit(repo, "src/b.py", "3", "AI chat panel [minor]")
    assert release_plan.plan(repo)["version"] == "0.4.0"
    commit(repo, "src/c.py", "4", "manual bump", version="1.0.0")
    p = release_plan.plan(repo)
    assert (p["version"], p["bump"]) == ("1.0.0", False)  # a version you set yourself is respected
    git(repo, "tag", "v1.0.0")
    commit(repo, "src/d.py", "5", "Docs mention markers\n\nnext version: patch, or [minor]/[major] if marked")
    assert release_plan.plan(repo)["version"] == "1.0.1"  # a body that only explains the rule is not a marker
    commit(repo, "src/e.py", "6", "Explain [minor]/[major] in the subject too")
    assert release_plan.plan(repo)["version"] == "1.0.1"  # not separate words
    commit(repo, "src/f.py", "7", "Big rewrite [major]")
    assert release_plan.plan(repo)["version"] == "2.0.0"


def test_github_output_lines(repo, tmp_path_factory):
    commit(repo, "src/a.py", "1", "first", version="0.3.0")
    out = tmp_path_factory.mktemp("o") / "out.txt"
    release_plan.main([str(repo), "--github-output", str(out)])
    assert out.read_text().splitlines() == ["version=0.3.0", "tag=v0.3.0", "publish=true", "bump=false"]
