# Releasing

Releases are built and published automatically by GitHub Actions (`.github/workflows/release.yml`).

## Publishing a new version

```bash
python scripts/bump_version.py 0.4.0      # or: patch | minor | major
git commit -am "Release 0.4.0"
git push
```

`bump_version.py` sets the version in all three places that must agree (`pyproject.toml`,
`src/krita_mcp/__init__.py` and `BRIDGE_VERSION` in the plugin) and regenerates `docs/tools.md`.

When the push reaches `main`:

1. **CI** runs the test matrix (Linux and Windows, Python 3.10 and 3.13, PyQt5 and PyQt6) and builds the
   plugin zip.
2. When CI passes, the **Release** workflow checks whether `v<version>` already has a release.
   - If it does, nothing happens. Ordinary pushes never re-release.
   - If it does not, the workflow tests again, then builds `krita-mcp-bridge-<version>.zip` (the Krita
     plugin, for *Import Python Plugin from File*), the wheel and the sdist. It then **creates the tag
     on exactly the commit that passed CI** and publishes the GitHub Release. The release notes contain
     install instructions followed by a changelog that GitHub generates from the commits and pull
     requests since the previous release.
3. If PyPI publishing is enabled (see below), the same wheel and sdist are uploaded to PyPI.

Before bumping, ideally run `python scripts/smoke_krita.py` against a real Krita. CI cannot run Krita
itself.

## Other ways to publish

- **Push a tag by hand:** `git tag v0.4.0 && git push origin v0.4.0`. The tag must match the package
  version.
- **Re-publish an existing tag** (for example after fixing the workflow): *Actions ▸ Release ▸ Run
  workflow*, and enter the tag. The release's files are replaced.
- **Release `main` now:** *Actions ▸ Release ▸ Run workflow* with the tag left empty. It publishes if the
  version on `main` is new.

## PyPI (optional)

1. On PyPI, add a *trusted publisher* for this repository: workflow `release.yml`, environment `pypi`.
2. In the GitHub repository, create the environment `pypi` and set the repository variable
   `PUBLISH_PYPI` to `true`.

After that, every release is also published to PyPI, so `pip install krita-mcp` works.

## Local builds

```bash
python scripts/build_plugin.py --out dist   # plugin zip
python -m build --outdir dist               # wheel + sdist (pip install build)
```
