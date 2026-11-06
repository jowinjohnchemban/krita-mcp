# Releasing

Releases build and publish themselves (`.github/workflows/release.yml`). Pushing to `main` is enough.

## What happens on every push to `main`

1. **CI** runs the test matrix (Linux and Windows, Python 3.10 and 3.13, PyQt5 and PyQt6) and builds the
   plugin zip.
2. When CI passes, the **Release** workflow runs `scripts/release_plan.py` to decide what to do:

   | Since the last release… | Result |
   |---|---|
   | plugin or server code changed (`src/`, `plugin/`, `pyproject.toml`) | the **next version** is released: a patch bump by default; put `[minor]` or `[major]` in a commit message for a bigger step |
   | you raised the version yourself (`python scripts/bump_version.py 1.0.0`) | **that version** is released |
   | only docs, tests or CI changed | nothing is released |

3. For an automatic version, **github-actions[bot] commits "Release vX.Y.Z"** to `main`. This sets the
   version in `pyproject.toml`, `__version__` and the plugin's `BRIDGE_VERSION`, and refreshes
   `docs/tools.md`. A commit made by the bot does not start CI again. If someone pushed to `main` while
   this was running, the bot leaves it, and the next CI run releases those commits after they have been
   tested.
4. The workflow tests again, then builds `krita-mcp-bridge-<version>.zip` (the Krita plugin, for
   *Import Python Plugin from File*), the wheel and the sdist. It tags the commit and publishes the
   GitHub Release. The release notes contain install instructions followed by GitHub's changelog since
   the previous release.
5. If PyPI is enabled (see below), the same wheel and sdist go to PyPI.

After a release, run `git pull` to get the bot's version commit.

CI cannot run Krita. Before pushing larger changes, run `python scripts/smoke_krita.py` against a real
Krita.

## Other ways to publish

- **Choose the version yourself:** `python scripts/bump_version.py 0.5.0`, then commit and push.
- **Push a tag by hand:** `git tag v0.5.0 && git push origin v0.5.0`. The tag must match the package
  version.
- **Re-publish an existing tag** (for example after fixing the workflow): *Actions ▸ Release ▸ Run
  workflow*, and enter the tag. The release's files are replaced.
- **Release `main` now:** *Actions ▸ Release ▸ Run workflow* with the tag left empty. This follows the
  same rules as above.

## If the bot cannot push

If `main` is protected so that only pull requests can change it, the bot's version commit is refused, and
the release stops at that step. Either allow `github-actions[bot]` to push to `main`, or bump the version
yourself in your pull request (`bump_version.py`), which is then released as is.

## PyPI (optional)

1. On PyPI, add a *trusted publisher* for this repository: workflow `release.yml`, environment `pypi`.
2. In the GitHub repository, create the environment `pypi` and set the repository variable
   `PUBLISH_PYPI` to `true`.

After that, every release is also published to PyPI, so `pip install krita-mcp` works.

## Local builds

```bash
python scripts/build_plugin.py --out dist   # plugin zip
python -m build --outdir dist               # wheel + sdist (pip install build)
python scripts/release_plan.py              # what the next release would be
```
