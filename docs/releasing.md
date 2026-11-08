# Releasing

Every push to `main` publishes a release (`.github/workflows/release.yml`) - but only if **every test passes**.
There is nothing to do by hand.

## What happens on a push to `main`

Four jobs. **tests** runs the whole CI matrix (Linux and Windows, Python 3.10 and 3.13, PyQt5 and PyQt6); one
red test stops everything, and nothing is published. Then, as in PyPA's guide: **build** (steps 1-3),
**github-release** (4) and **pypi** (5).

1. **The next version.** Versions are [SemVer](https://semver.org) (`MAJOR.MINOR.PATCH`) and count up by one
   on their own: `MAJOR.MINOR` comes from `pyproject.toml`, and `PATCH` is one more than the highest version
   with that `MAJOR.MINOR` already published as a GitHub release or on PyPI (`scripts/release_version.py`).
   Each build also gets a **build id**: the UTC build date and the commit it was built from
   (`20261108-057a829`). Official names use only the version: the tag is `v1.0.4`, the release is
   *KritaPilot 1.0.4*, the package is `kritapilot 1.0.4`. The build id and commit are in the release notes
   and in the app: *1.0.4 (build 20261108-057a829)*. A copy run from source shows *1.0.0 (dev)*.
2. The version, build id and commit are stamped into the job's own checkout only (`pyproject.toml`,
   `__version__`/`__build__`/`__commit__`, the plugin's `BRIDGE_VERSION`/`BRIDGE_BUILD`/`BRIDGE_COMMIT`,
   `docs/tools.md`). **Nothing is committed to the repository.**
3. `kritapilot-plugin-<version>.zip` (the Krita plugin, for *Import Python Plugin from
   File*), the wheel and the sdist are built, and checked with `twine check --strict`.
4. They are published as the GitHub release `v<version>`. Every version stays available.
5. The same wheel and sdist go to PyPI (see below).

To start a new minor or major version, set it in `pyproject.toml` (`python scripts/bump_version.py minor`
does that) and push: the next release is `X.Y.0`, then `X.Y.1`, and so on.

A newer push while a release is still being built cancels that build; the newer one is published.

You can also publish by hand: *Actions ▸ Release ▸ Run workflow*.

Because every release has a higher version than the one before, the over-the-air updater in Krita and
`kritapilot --update` pick it up.

The same tests (`ci.yml`) also run on every pull request. They cannot run Krita itself: before pushing larger changes, run `python scripts/smoke_krita.py` against a real Krita.

## PyPI

Every release is also published to PyPI as [`kritapilot`](https://pypi.org/p/kritapilot), by the `pypi` job
of `release.yml` with [trusted publishing](https://docs.pypi.org/trusted-publishers/): PyPI checks the
workflow's identity, so no password or API token is stored anywhere, and the upload carries signed
attestations. It runs in the GitHub environment `pypi`.

One-time setup on PyPI (by the project's owner): *Your account ▸ Publishing ▸ Add a new pending publisher*
with project name `kritapilot`, owner `jowinjohnchemban`, repository `kritapilot`, workflow `release.yml`,
environment `pypi`. The first release after that creates the project.

The name `krita-mcp` on PyPI belongs to a different project; this one is `kritapilot`.

## Local builds

```bash
python scripts/bump_version.py 2026.11.7.1  # optional: stamp a version locally
python scripts/build_plugin.py --out dist   # plugin zip
python -m build --outdir dist               # wheel + sdist (pip install build)
```
