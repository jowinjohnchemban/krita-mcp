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
    assert _grab("src/krita_mcp/__init__.py", r'__version__\s*=\s*"([^"]+)"') == v
    assert _grab("plugin/pykrita/krita_mcp_bridge/handlers.py", r'^BRIDGE_VERSION\s*=\s*"([^"]+)"') == v


def test_plugin_zip_layout(tmp_path):
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_plugin.py"), "--out", str(tmp_path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    names = zipfile.ZipFile(out).namelist()
    assert "krita_mcp_bridge.desktop" in names and "krita_mcp_bridge/__init__.py" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)


def test_tool_reference_is_current():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_tool_docs.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
