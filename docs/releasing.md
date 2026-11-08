# Releasing

Every push to `main` publishes a release (`.github/workflows/release.yml`). There is nothing to do by hand.

## What happens on a push to `main`

One job:

1. **Version = the date + the build number.** The build number is the workflow's run number, which grows
   with every build, so several releases on one day each get their own, increasing version. The tag is
   `YYYY.MM.DD.BUILD` (for example `2026.11.07.158`); the Python package and the plugin get
   `2026.11.7.158`. The release title and notes name the build and the git commit it was built from.
2. The version is stamped into the job's own checkout only (`pyproject.toml`, `__version__`, the plugin's
   `BRIDGE_VERSION`, `docs/tools.md`). **Nothing is committed to the repository.**
3. The tests run, then `krita-mcp-bridge-<version>.zip` (the Krita plugin, for *Import Python Plugin from
   File*), the wheel and the sdist are built.
4. Every build is its own release. Only a re-run of the same build replaces its release (same tag).
5. **Only the 5 newest releases are kept** (older releases and their tags are deleted), and workflow runs
   older than 7 days are deleted (at least 2 are kept). Change `keep_latest` in the workflow to keep more.
6. If PyPI is enabled (see below), the same wheel and sdist go to PyPI.

A newer push while a release is still being built cancels that build; the newer one is published.

You can also publish by hand: *Actions ▸ Release ▸ Run workflow*.

Because every build has a higher version than the one before, the over-the-air updater in Krita and
`krita-mcp --update` pick up a second or third release of the same day like any other update.

CI (`ci.yml`) still tests every push and pull request on Linux and Windows with PyQt5 and PyQt6. CI cannot
run Krita: before pushing larger changes, run `python scripts/smoke_krita.py` against a real Krita.

## PyPI (optional)

The name `krita-mcp` on PyPI is taken by a different project, so publishing there needs another
distribution name in `pyproject.toml` first (the `krita-mcp` command can stay the same).

1. On PyPI, add a *trusted publisher* for this repository: workflow `release.yml`, environment `pypi`.
2. In the GitHub repository, create the environment `pypi` and set the repository variable
   `PUBLISH_PYPI` to `true`.

After that, every release is also published to PyPI under that name.

## Local builds

```bash
python scripts/bump_version.py 2026.11.7.1  # optional: stamp a version locally
python scripts/build_plugin.py --out dist   # plugin zip
python -m build --outdir dist               # wheel + sdist (pip install build)
```
