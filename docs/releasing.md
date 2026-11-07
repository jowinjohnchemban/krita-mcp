# Releasing

Every push to `main` publishes a release (`.github/workflows/release.yml`). There is nothing to do by hand.

## What happens on a push to `main`

One job, modelled on the GeoLite.mmdb publisher:

1. **Version = the date.** The release and its tag are named `YYYY.MM.DD` (for example `2026.11.07`); the
   Python package and plugin get the same date as a version (`2026.11.7`).
2. The version is stamped into the job's own checkout only (`pyproject.toml`, `__version__`, the plugin's
   `BRIDGE_VERSION`, `docs/tools.md`). **Nothing is committed to the repository.**
3. The tests run, then `krita-mcp-bridge-<version>.zip` (the Krita plugin, for *Import Python Plugin from
   File*), the wheel and the sdist are built.
4. If a release for today already exists, it and its tag are deleted, and today's release is published
   again from the newest commit. Each day therefore has one release: the latest state of `main`.
5. **Only the 5 newest releases are kept** (older releases and their tags are deleted), and workflow runs
   older than 7 days are deleted (at least 2 are kept). Change `keep_latest` in the workflow to keep more.
6. If PyPI is enabled (see below), the same wheel and sdist go to PyPI.

A newer push while a release is still being built cancels that build; the newer one is published.

You can also publish by hand: *Actions ▸ Release ▸ Run workflow*.

Because a version is a date, two releases on the same day share a version number: someone who already
installed today's wheel needs `pip install --force-reinstall` to get a later build of the same day.

CI (`ci.yml`) still tests every push and pull request on Linux and Windows with PyQt5 and PyQt6. CI cannot
run Krita: before pushing larger changes, run `python scripts/smoke_krita.py` against a real Krita.

## PyPI (optional)

1. On PyPI, add a *trusted publisher* for this repository: workflow `release.yml`, environment `pypi`.
2. In the GitHub repository, create the environment `pypi` and set the repository variable
   `PUBLISH_PYPI` to `true`.

After that, every release is also published to PyPI, so `pip install krita-mcp` works.

## Local builds

```bash
python scripts/bump_version.py 2026.11.7    # optional: stamp a version locally
python scripts/build_plugin.py --out dist   # plugin zip
python -m build --outdir dist               # wheel + sdist (pip install build)
```
