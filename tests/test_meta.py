"""Release hygiene: versions agree, the plugin zip builds, the tool reference is current."""
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _grab(path, pattern):
    return re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), re.M).group(1)


def test_versions_agree():
    v = _grab("pyproject.toml", r'^version\s*=\s*"([^"]+)"')
    assert _grab("src/kritapilot/config.py", r'^VERSION\s*=\s*"([^"]+)"') == v
    assert _grab("plugin/pykrita/kritapilot_plugin/config.py", r'^VERSION\s*=\s*"([^"]+)"') == v


def test_plugin_zip_layout(tmp_path):
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_plugin.py"), "--out", str(tmp_path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    names = zipfile.ZipFile(out).namelist()
    assert "kritapilot_plugin.desktop" in names and "kritapilot_plugin/__init__.py" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)


def test_tool_reference_is_current():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_tool_docs.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_tests_never_see_the_users_real_keys():
    """conftest isolates every test: the key store must not be the real ~/.kritapilot."""
    sys.path.insert(0, str(ROOT / "src"))
    from kritapilot.providers import keys
    real = (Path.home() / ".kritapilot").resolve()
    assert real not in keys.path().resolve().parents



def test_the_server_and_plugin_configs_agree():
    """kritapilot/config.py and kritapilot_plugin/config.py are twins: every value they share must match."""
    import importlib.util
    mods = []
    for rel in ("src/kritapilot/config.py", "plugin/pykrita/kritapilot_plugin/config.py"):
        spec = importlib.util.spec_from_file_location("cfg%d" % len(mods), ROOT / rel)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mods.append(mod)
    server, plugin = mods
    shared = [n for n in vars(server) if n.isupper() and n in vars(plugin)]
    assert len(shared) >= 18
    for name in shared:
        assert getattr(server, name) == getattr(plugin, name), name
    assert server.version_text() == plugin.version_text() and server.home() == plugin.home()
