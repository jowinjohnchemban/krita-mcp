# Releasing

Releases are built by GitHub Actions (`.github/workflows/release.yml`) when a version tag is pushed.

1. Bump the version in **three places**: `pyproject.toml`, `src/krita_mcp/__init__.py` (`__version__`)
   and `plugin/pykrita/krita_mcp_bridge/handlers.py` (`BRIDGE_VERSION`). `tests/test_meta.py` fails if
   they disagree.
2. Regenerate the tool reference: `python scripts/gen_tool_docs.py`.
3. Run the tests. Ideally also run `python scripts/smoke_krita.py` against a real Krita.
4. Commit, then tag and push:
   ```bash
   git tag v0.2.0
   git push origin main v0.2.0
   ```

The workflow then:

- checks that the tag matches the package version;
- runs the tests;
- builds `krita-mcp-bridge-<version>.zip` (the Krita plugin, for *Import Python Plugin from File*),
  plus the wheel and sdist;
- creates the GitHub Release with those files attached and install notes. Re-running it for an
  existing tag replaces the files.

To publish the existing tag again, use **Actions ▸ Release ▸ Run workflow** with the tag name.

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
