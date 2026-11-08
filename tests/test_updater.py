"""Over-the-air updates: release lookup, verified downloads, the atomic plugin swap, the server upgrade."""
import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

from test_providers import Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("plugin_updater", ROOT / "plugin/pykrita/kritapilot_plugin/updater.py")
plugin_updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin_updater)  # standalone: no Krita needed


def plugin_zip(version="2099.1.1", extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("kritapilot_plugin.desktop", "[Desktop Entry]\nName=Krita MCP Bridge\n")
        z.writestr("kritapilot_plugin/__init__.py", "")
        z.writestr("kritapilot_plugin/handlers.py", 'BRIDGE_VERSION = "%s"\n' % version)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture()
def github(monkeypatch):
    m = Mock()
    zdata, wheel = plugin_zip(), b"PK-fake-wheel"
    m.raw("GET", "/dl/kritapilot-plugin-2099.1.1.zip", zdata, "application/zip")
    m.raw("GET", "/dl/kritapilot-2099.1.1-py3-none-any.whl", wheel, "application/octet-stream")
    m.json("GET", "/releases/latest", {
        "tag_name": "2099.01.01", "html_url": "https://github.com/x/releases/2099.01.01", "body": "notes",
        "assets": [
            {"name": "kritapilot-plugin-2099.1.1.zip", "size": len(zdata),
             "digest": "sha256:" + hashlib.sha256(zdata).hexdigest(),
             "browser_download_url": m.url + "/dl/kritapilot-plugin-2099.1.1.zip"},
            {"name": "kritapilot-2099.1.1-py3-none-any.whl", "size": len(wheel),
             "digest": "sha256:" + hashlib.sha256(wheel).hexdigest(),
             "browser_download_url": m.url + "/dl/kritapilot-2099.1.1-py3-none-any.whl"}]})
    monkeypatch.setenv("KRITAPILOT_UPDATE_API", m.url + "/releases/latest")
    yield m, zdata
    m.srv.shutdown()


def test_versions_compare_dates_and_semver():
    newer = plugin_updater.is_newer
    assert newer("2026.11.7", "1.0.4") and newer("2026.11.10", "2026.11.9") and newer("v1.0.10", "1.0.9")
    assert not newer("2026.11.07", "2026.11.7") and not newer("1.0.4", "2026.11.7")
    # several releases a day: date + build number
    assert newer("2026.11.7.158", "2026.11.7") and newer("2026.11.7.158", "2026.11.7.99")
    assert newer("2026.11.8.159", "2026.11.7.158") and not newer("2026.11.07.158", "2026.11.7.158")


def test_latest_release_and_verified_download(github):
    m, zdata = github
    info = plugin_updater.latest()
    assert info["version"] == "2099.1.1" and set(info["assets"]) == {"plugin", "server"}
    assert plugin_updater.download(info["assets"]["plugin"]) == zdata
    bad = dict(info["assets"]["plugin"], digest="sha256:" + "0" * 64)
    with pytest.raises(plugin_updater.UpdateError, match="checksum"):
        plugin_updater.download(bad)
    with pytest.raises(plugin_updater.UpdateError, match="refusing"):
        plugin_updater.download(dict(bad, browser_download_url="https://evil.example.com/x.zip"))


def test_plugin_swap_is_atomic_and_rejects_unsafe_zips(tmp_path):
    pykrita = tmp_path / "pykrita"
    old = pykrita / "kritapilot_plugin"
    old.mkdir(parents=True)
    (old / "handlers.py").write_text('BRIDGE_VERSION = "1.0.0"\n')
    (old / "stale.py").write_text("old file")
    plugin_updater.install_plugin(plugin_zip("2099.1.1"), pykrita)
    assert '2099.1.1' in (old / "handlers.py").read_text() and not (old / "stale.py").exists()
    assert (pykrita / "kritapilot_plugin.desktop").exists()
    assert sorted(p.name for p in pykrita.iterdir()) == ["kritapilot_plugin", "kritapilot_plugin.desktop"]
    for evil in ({"../outside.py": "x"}, {"kritapilot_plugin/../../outside.py": "x"}, {"other_plugin/x.py": "x"}):
        with pytest.raises(plugin_updater.UpdateError):
            plugin_updater.install_plugin(plugin_zip(extra=evil), pykrita)
    assert '2099.1.1' in (old / "handlers.py").read_text()  # a refused update changes nothing
    with pytest.raises(plugin_updater.UpdateError, match="not a valid zip"):
        plugin_updater.install_plugin(b"not a zip", pykrita)


def test_server_update_runs_pip_in_a_helper(github, monkeypatch, tmp_path):
    from kritapilot import updater
    monkeypatch.setenv("KRITAPILOT_HOME", str(tmp_path))
    launched = {}

    class FakePopen:
        def __init__(self, args, **kw):
            launched["args"] = args
    monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)
    info = updater.check()
    assert info["latest"] == "2099.1.1" and info["newer"] is True
    out = updater.update()
    cmds = json.loads(launched["args"][-1])  # the helper gets its commands as JSON
    assert len(cmds) == 1
    pip = cmds[0][cmds[0].index("-m"):]
    assert out["started"] and pip[:3] == ["-m", "pip", "install"] and "--upgrade" in pip
    wheel = Path(pip[-1].split("[")[0])
    assert wheel.name == "kritapilot-2099.1.1-py3-none-any.whl" and wheel.read_bytes() == b"PK-fake-wheel"
    assert out["log"].endswith("update.log")
    monkeypatch.setattr(updater, "__version__", "2099.1.1")
    assert updater.update()["started"] is False  # already current: nothing is installed


def test_server_update_refuses_a_tampered_wheel(github, monkeypatch):
    from kritapilot import updater
    m, _ = github
    m.raw("GET", "/dl/kritapilot-2099.1.1-py3-none-any.whl", b"tampered", "application/octet-stream")
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: pytest.fail("must not install"))
    with pytest.raises(updater.UpdateError, match="checksum"):
        updater.update()
    assert json.loads(json.dumps({"ok": True}))


def test_update_helper_never_breaks_a_locked_installation(tmp_path, monkeypatch):
    """While another kritapilot runs (files locked on Windows) the helper waits, then skips - never half-installs."""
    import subprocess
    import sys
    from kritapilot import updater
    log = tmp_path / "update.log"
    fake_pip = [sys.executable, "-c", "open(r'%s', 'w').write('pip ran')" % (tmp_path / "pip_ran")]

    def helper(still_running):  # the same helper, with "is kritapilot running?" answered for the test (any OS)
        stub = "def running():\n    return %s\n\n\ndef _real():" % still_running
        return updater.HELPER.replace("def running():", stub).replace("time.sleep(", "(lambda s: None)(")
    cmds = json.dumps([fake_pip])
    script = helper(True)
    code = subprocess.call([sys.executable, "-c", script, str(log), "2099.1.1", cmds])
    assert code == 2 and "skipped" in log.read_text() and not (tmp_path / "pip_ran").exists()
    script = helper(False)
    code = subprocess.call([sys.executable, "-c", script, str(log), "2099.1.1", cmds])
    assert code == 0 and "result: updated" in log.read_text() and (tmp_path / "pip_ran").exists()


def test_an_update_is_a_newer_version_or_the_same_version_from_another_commit():
    from kritapilot import updater as server
    for upd in (plugin_updater, server):
        assert upd.update_available("1.0.5", "1.0.4")
        assert not upd.update_available("1.0.4", "1.0.5")
        assert upd.update_available("1.0.4", "1.0.4", "abc1234def", "0123456")  # rebuilt from another commit
        assert not upd.update_available("1.0.4", "1.0.4", "abc1234def", "abc1234")  # the same build
        assert upd.update_available("1.0.4", "1.0.4", "abc1234def", "")  # a copy run from source
        assert not upd.update_available("1.0.4", "1.0.4", "", "abc1234")  # nothing to compare
