# Releasing

Every push to `main` publishes a release (`.github/workflows/release.yml`). There is nothing to do by hand.

## What happens on a push to `main`

Three jobs, as in PyPA's guide: **build** (steps 1-3), **github-release** (4-5) and **pypi** (6).

1. **Version = the date + the build number.** The build number is the workflow's run number, which grows
   with every build, so several releases on one day each get their own, increasing version. The tag is
   `YYYY.MM.DD.BUILD` (for example `2026.11.07.158`); the Python package and the plugin get
   `2026.11.7.158`. The release title and notes name the build and the git commit it was built from.
2. The version is stamped into the job's own checkout only (`pyproject.toml`, `__version__`, the plugin's
   `BRIDGE_VERSION`, `docs/tools.md`). **Nothing is committed to the repository.**
3. The tests run, then `krita-mcp-bridge-<version>.zip` (the Krita plugin, for *Import Python Plugin from
   File*), the wheel and the sdist are built, and checked with `twine check --strict`.
4. Every build is its own release. Only a re-run of the same build replaces its release (same tag).
5. **Only the 5 newest releases are kept** (older releases and their tags are deleted), and workflow runs
   older than 7 days are deleted (at least 2 are kept). Change `keep_latest` in the workflow to keep more.
6. The same wheel and sdist go to PyPI (see below).

A newer push while a release is still being built cancels that build; the newer one is published.

You can also publish by hand: *Actions ▸ Release ▸ Run workflow*.

Because every build has a higher version than the one before, the over-the-air updater in Krita and
`kritapilot --update` pick up a second or third release of the same day like any other update.

CI (`ci.yml`) still tests every push and pull request on Linux and Windows with PyQt5 and PyQt6. CI cannot
run Krita: before pushing larger changes, run `python scripts/smoke_krita.py` against a real Krita.

## PyPI

Every release is also published to PyPI as [`kritapilot`](https://pypi.org/p/kritapilot), by the `pypi` job
of `release.yml` with [trusted publishing](https://docs.pypi.org/trusted-publishers/): PyPI checks the
workflow's identity, so no password or API token is stored anywhere, and the upload carries signed
attestations. It runs in the GitHub environment `pypi`.

One-time setup on PyPI (by the project's owner): *Your account ▸ Publishing ▸ Add a new pending publisher*
with project name `kritapilot`, owner `jowinjohnchemban`, repository `kritapilot`, workflow `release.yml`,
environment `pypi`. The first release after that creates the project.

The name `krita-mcp` on PyPI belongs to a different project. This project's package was called `krita-mcp`
on GitHub before it became `kritapilot`; the updater removes that old package when it installs the new one,
and the `krita-mcp` command still works.

## Local builds

```bash
python scripts/bump_version.py 2026.11.7.1  # optional: stamp a version locally
python scripts/build_plugin.py --out dist   # plugin zip
python -m build --outdir dist               # wheel + sdist (pip install build)
```
