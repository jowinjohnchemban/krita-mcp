"""`kritapilot install` and `kritapilot doctor` - only ever in temporary folders."""
import pytest

from kritapilot import config, install, server


def test_the_plugin_ships_with_the_command():
    src = install.plugin_source()
    assert src is not None
    assert (src / "kritapilot_plugin" / "__init__.py").is_file()
    assert (src / "kritapilot_plugin.desktop").is_file()


def test_install_copies_the_plugin_and_turns_it_on(tmp_path, capsys):
    pykrita, kritarc = tmp_path / "pykrita", tmp_path / "kritarc"
    kritarc.write_text("[General]\nfoo=1\r\n\r\n[python]\nenable_other=true\r\n\r\n[kritapilot]\nport=5676\r\n",
                       encoding="utf-8", newline="")
    server.main(["install", "--dest", str(pykrita), "--kritarc", str(kritarc)])
    out = capsys.readouterr().out
    assert (pykrita / "kritapilot_plugin" / "__init__.py").is_file()
    assert (pykrita / "kritapilot_plugin.desktop").is_file()
    assert not list(pykrita.rglob("__pycache__"))
    assert install.installed_version(pykrita) == config.VERSION
    text = kritarc.read_bytes().decode("utf-8")
    assert "[python]\r\nenable_other=true\r\nenable_kritapilot_plugin=true\r\n\r\n[kritapilot]" in text
    assert "foo=1" in text and install.is_enabled(kritarc)
    assert "turned on" in out


def test_install_replaces_an_older_plugin(tmp_path, capsys):
    pykrita = tmp_path / "pykrita"
    (pykrita / "kritapilot_plugin").mkdir(parents=True)
    (pykrita / "kritapilot_plugin" / "config.py").write_text('VERSION = "0.9.0"\n', encoding="utf-8")
    (pykrita / "kritapilot_plugin" / "stale.py").write_text("", encoding="utf-8")
    server.main(["install", "--dest", str(pykrita), "--no-enable"])
    assert "(was 0.9.0)" in capsys.readouterr().out
    assert not (pykrita / "kritapilot_plugin" / "stale.py").exists()
    assert not [p for p in pykrita.iterdir() if p.name.startswith(".")]


@pytest.mark.parametrize("before, after", [
    ("", "[python]\nenable_kritapilot_plugin=true\n"),
    ("[General]\na=1\n", "[General]\na=1\n\n[python]\nenable_kritapilot_plugin=true\n"),
    ("[python]\nenable_kritapilot_plugin=false\n[x]\n", "[python]\nenable_kritapilot_plugin=true\n[x]\n"),
])
def test_enable_keeps_the_other_settings(tmp_path, before, after):
    rc = tmp_path / "kritarc"
    if before:
        rc.write_text(before, encoding="utf-8", newline="")
    assert not install.is_enabled(rc)
    install.enable(rc)
    assert rc.read_bytes().decode("utf-8") == after
    assert install.is_enabled(rc)


def test_setting_reads_one_value(tmp_path):
    rc = tmp_path / "kritarc"
    rc.write_text("[a]\nport=1\n[kritapilot]\nport = 5676\n", encoding="utf-8")
    assert install.setting(rc, "kritapilot", "port") == "5676"
    assert install.setting(rc, "kritapilot", "nope") is None
    assert install.setting(tmp_path / "missing", "kritapilot", "port") is None


@pytest.fixture()
def machine(tmp_path, monkeypatch):
    """A pretend computer: Krita's folders in tmp_path, Krita running, the plugin installed and turned on."""
    pykrita, rc = tmp_path / "pykrita", tmp_path / "kritarc"
    install.copy_plugin(install.plugin_source(), pykrita)
    rc.write_text("[python]\nenable_kritapilot_plugin=true\n[kritapilot]\nport=5676\n", encoding="utf-8")
    monkeypatch.setattr(install, "find_pykrita", lambda: pykrita)
    monkeypatch.setattr(install, "find_kritarc", lambda: rc)
    monkeypatch.setattr(install, "krita_running", lambda: True)
    (config.home()).mkdir(parents=True, exist_ok=True)
    (config.home() / "token").write_text("t" * 20, encoding="utf-8")
    monkeypatch.setattr(install, "_service", lambda token: (5680, {"version": config.VERSION, "providers": {
        "auto": {"chat": True, "ready": True}, "kilo": {"chat": True, "ready": True},
        "horde": {"ready": True}, "openai": {"chat": True, "ready": False}}}))
    return pykrita, rc


def test_doctor_all_good(machine, monkeypatch, capsys):
    monkeypatch.setattr(install, "_bridge", lambda port=None: (type("C", (), {"port": 5679})(), {
        "krita_version": "5.3", "bridge_version": config.VERSION, "documents": []}))
    server.main(["doctor"])
    out = capsys.readouterr().out
    assert "Everything works." in out and "!!" not in out
    assert "chat: kilo; pictures: horde" in out


def test_doctor_finds_the_bridge_on_the_port_set_in_krita(machine, monkeypatch, capsys):
    def bridge(port=None):
        if port != 5676:
            raise ConnectionRefusedError("refused")
        return type("C", (), {"port": 5676})(), {"documents": [{}]}
    monkeypatch.setattr(install, "_bridge", bridge)
    with pytest.raises(SystemExit):
        server.main(["doctor"])
    out = capsys.readouterr().out
    assert "Bridge answers on port 5676" in out and "KRITAPILOT_PORT=5676" in out


def test_doctor_explains_what_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install, "find_pykrita", lambda: tmp_path / "pykrita")
    monkeypatch.setattr(install, "find_kritarc", lambda: None)
    monkeypatch.setattr(install, "krita_running", lambda: False)

    def refused(port=None):
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(install, "_bridge", refused)
    with pytest.raises(SystemExit) as e:
        server.main(["doctor"])
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "kritapilot install" in out and "No bridge token" in out and "Bridge does not answer" in out
