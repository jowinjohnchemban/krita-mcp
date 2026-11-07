"""The AI Chat docker's Qt side, with a fake krita module and a fake assistant service."""
import os
import sys
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.environ.get("KRITA_TEST_QT") == "5":  # Krita 5 ships PyQt5
    sys.modules["PyQt6"] = None
    qtw = pytest.importorskip("PyQt5.QtWidgets", reason="PyQt5 not installed")
else:
    qtw = pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def chat(monkeypatch, tmp_path):
    app = qtw.QApplication.instance() or qtw.QApplication([])
    settings = {}

    class FakeKrita:
        @staticmethod
        def instance():
            return FakeKrita

        @staticmethod
        def readSetting(group, key, default):
            return settings.get(key, default)

        @staticmethod
        def writeSetting(group, key, value):
            settings[key] = value

        @staticmethod
        def addExtension(ext):
            pass

        @staticmethod
        def addDockWidgetFactory(factory):
            pass

    mod = types.ModuleType("krita")
    mod.Krita, mod.InfoObject, mod.ManagedColor, mod.Selection = FakeKrita, object, object, object
    mod.Extension = type("Extension", (), {"__init__": lambda self, parent=None: None})
    mod.DockWidget = qtw.QDockWidget
    mod.DockWidgetFactory = lambda *a: None
    mod.DockWidgetFactoryBase = types.SimpleNamespace(DockRight=1)
    monkeypatch.setitem(sys.modules, "krita", mod)
    monkeypatch.setenv("KRITA_MCP_HOME", str(tmp_path))
    saved = {m: sys.modules.pop(m) for m in [m for m in sys.modules if m.startswith("krita_mcp_bridge")]}
    monkeypatch.syspath_prepend(str(ROOT / "plugin" / "pykrita"))
    import importlib
    c = importlib.import_module("krita_mcp_bridge.chat")
    sent = []
    events = [{"type": "user", "n": 0, "text": "paint a fox"},
              {"type": "tool", "n": 1, "name": "create_artwork", "args": '{"prompt": "a fox"}'},
              {"type": "tool_result", "n": 2, "name": "create_artwork", "ok": False, "text": "error: no model"},
              {"type": "assistant", "n": 3, "text": "I set up the canvas & will paint it <now>."},
              {"type": "done", "n": 4}]

    def fake_request(method, path, body=None, timeout=10):
        sent.append((method, path, body))
        if path == "/health":
            return {"providers": {
                "gemini": {"signed_in": True, "needs_key": True, "chat": True, "label": "Google Gemini",
                           "use": "chat", "default_model": "newest available"},
                "fal": {"signed_in": False, "needs_key": True, "label": "fal.ai", "use": "images"},
                "comfyui": {"signed_in": False, "needs_key": False, "has_url": True, "label": "ComfyUI"}}}
        if path == "/chat":
            return {"session": "s1"}
        if path.startswith("/events"):
            since = int(path.rsplit("since=", 1)[1])
            return {"events": events[since:], "busy": False}
        if path == "/signout":
            return {"providers": {"gemini": {"signed_in": False, "needs_key": True, "chat": True}}}
        return {}
    monkeypatch.setattr(c, "_request", fake_request)
    c._real_ensure_service = c._ensure_service  # for the test that exercises the real start-up
    monkeypatch.setattr(c, "_ensure_service", lambda: fake_request("GET", "/health"))
    dock = c.ChatDocker()
    yield app, c, dock, sent
    dock.timer.stop()
    dock.poll_timer.stop()
    for m in [m for m in sys.modules if m.startswith("krita_mcp_bridge")]:  # drop what we imported ...
        del sys.modules[m]
    sys.modules.update(saved)  # ... and put back what was there


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def test_chat_round_trip_renders_the_conversation(chat):
    app, c, dock, sent = chat
    pump(app, 0.5)
    assert "signed in" in dock.account.text() and "newest" in dock.model.lineEdit().placeholderText()
    dock.input.setPlainText("paint a fox")
    dock._send()
    assert not dock.send_btn.isEnabled()
    pump(app, 1.5)
    text = dock.log.toPlainText()
    assert "You: paint a fox" in text and "create_artwork" in text and "error: no model" in text
    assert "Gemini: I set up the canvas & will paint it <now>." in text  # escaped, not interpreted as HTML
    assert dock.send_btn.isEnabled() and dock.session == "s1"
    chat_body = next(b for m, p, b in sent if p == "/chat")
    assert chat_body == {"session": None, "provider": "gemini", "model": None, "text": "paint a fox"}


def test_sign_out_and_new_chat(chat):
    app, c, dock, sent = chat
    pump(app, 0.3)
    dock.sign_in_service("gemini")  # signed in -> signs out without a dialog
    pump(app, 0.5)
    assert ("POST", "/signout", {"provider": "gemini"}) in sent and "not signed in" in dock.account.text()
    dock.session = "s1"
    dock._new_chat()
    pump(app, 0.3)
    assert dock.session is None and dock.log.toPlainText() == ""
    assert ("POST", "/reset", {"session": "s1"}) in sent


def test_services_dialog_lists_every_service(chat):
    app, c, dock, sent = chat
    pump(app, 0.4)
    dlg = c.ServicesDialog(dock)
    text = " ".join(w.text() for w in dlg.rows.findChildren(qtw.QLabel))
    buttons = [b.text() for b in dlg.rows.findChildren(qtw.QPushButton)]
    assert "Google Gemini" in text and "fal.ai" in text and "ComfyUI" in text
    assert buttons.count("Sign out") == 1 and buttons.count("Sign in") == 2  # gemini is signed in
    info = dock.providers["comfyui"]
    sign = c.SignInDialog(dock, "comfyui", info)
    labels = [w.text() for w in sign.findChildren(qtw.QLabel)]
    assert "API key" not in labels and "Server URL" in labels  # local servers need a URL, not a key


def test_key_detection_and_browser_flows(chat, monkeypatch):
    app, c, dock, sent = chat
    pump(app, 0.3)
    assert c.find_key("gemini", "here: AIzaSyA" + "b" * 33 + " thanks") == "AIzaSyA" + "b" * 33
    assert c.find_key("openai", "sk-ant-" + "x" * 30) is None and c.find_key("anthropic", "sk-ant-" + "x" * 30)
    assert c.find_key("openrouter", "sk-or-v1-" + "f" * 64).startswith("sk-or-v1-")
    opened = []
    monkeypatch.setattr(c.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    dlg = c.SignInDialog(dock, "gemini", {"label": "Google Gemini", "needs_key": True,
                                          "key_url": "https://aistudio.google.com/apikey"})
    clip = c.QGuiApplication.clipboard()
    clip.setText("something unrelated")
    dlg._open_key_page()
    assert opened == ["https://aistudio.google.com/apikey"] and dlg.clip_timer.isActive()
    clip.setText("AIzaSyC" + "k" * 33)
    pump(app, 1.0)
    assert dlg.key.text() == "AIzaSyC" + "k" * 33 and "Key found" in dlg.hint.text()
    # OpenRouter: a real browser sign-in
    state = {"n": 0}
    real = c._request

    def oauth_request(method, path, body=None, timeout=10):
        if path == "/oauth/start":
            sent.append((method, path, body))
            return {"id": "f1", "url": "https://openrouter.ai/auth?x=1"}
        if path.startswith("/oauth/status"):
            state["n"] += 1
            return {"status": "done" if state["n"] > 1 else "pending", "error": None}
        return real(method, path, body, timeout)
    monkeypatch.setattr(c, "_request", oauth_request)
    dock.browser_sign_in("openrouter")
    pump(app, 3.0)
    assert ("POST", "/oauth/start", {"provider": "openrouter"}) in sent
    assert "https://openrouter.ai/auth?x=1" in opened and not dock.oauth_timer.isActive()
    assert "approve Krita" in dock.log.toPlainText()


def test_key_page_button_without_a_running_service(chat):
    """Opening Krita does not start the assistant service: sign-in must still offer the key page."""
    app, c, dock, sent = chat
    dock.providers = {}  # nothing reported yet
    for name, expected in (("gemini", "Get a key in the browser"), ("anthropic", "Get a key in the browser"),
                           ("fal", "Get a key in the browser"), ("openrouter", "Sign in with browser")):
        dlg = c.SignInDialog(dock, name, c.service_info(name))
        assert expected in [b.text() for b in dlg.findChildren(qtw.QPushButton)], name
    assert c.service_info("gemini")["key_url"] == "https://aistudio.google.com/apikey"
    assert c.service_info("gemini", {"signed_in": True, "key_url": None})["key_url"]  # reported None keeps ours
    local = [w.text() for w in c.SignInDialog(dock, "ollama", c.service_info("ollama")).findChildren(qtw.QLabel)]
    assert "API key" not in local and "Server URL" in local


def test_an_outdated_service_is_replaced(chat, monkeypatch):
    """A service left over from before an update (other version) is stopped and the current one started."""
    app, c, dock, sent = chat
    state, calls = {"version": "0.0.1-old", "up": True}, []

    def fake_request(method, path, body=None, timeout=10):
        calls.append(path)
        if path == "/shutdown":
            state["up"] = False
            return {"ok": True}
        if not state["up"]:
            raise OSError("connection refused")
        return {"version": state["version"], "providers": {}}

    class FakeProc:
        def __init__(self, args, env=None, **kw):
            state.update(up=True, version=c._plugin_version())

        def poll(self):
            return None
    monkeypatch.setattr(c, "_request", fake_request)
    monkeypatch.setattr(c.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    monkeypatch.setitem(c._SERVICE, "proc", None)
    monkeypatch.setitem(c._SERVICE, "checked", False)
    health = c._real_ensure_service()
    assert "/shutdown" in calls and health["version"] == c._plugin_version()


def test_starting_the_assistant_service_for_real(chat, monkeypatch):
    """The real _ensure_service: starts `<server command> --agent` and hands it the bridge's actual port."""
    app, c, dock, sent = chat
    launched, state = {}, {"up": False}

    def fake_request(method, path, body=None, timeout=10):
        if not state["up"]:
            raise OSError("connection refused")
        return {"providers": {"gemini": {"signed_in": False}}}

    class FakeProc:
        def __init__(self, args, env=None, **kw):
            launched.update(args=args, env=env)
            state["up"] = True

        def poll(self):
            return None
    running = type("Ext", (), {"server": type("Server", (), {"port": 5676})(), "port": lambda self: 5679})()
    monkeypatch.setattr(c.extension, "INSTANCE", running)
    monkeypatch.setattr(c, "_request", fake_request)
    monkeypatch.setattr(c.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    monkeypatch.setitem(c._SERVICE, "proc", None)
    health = c._real_ensure_service()
    assert health["providers"]["gemini"]["signed_in"] is False
    assert launched["args"][0] == "krita-mcp" and launched["args"][1:] == ["--agent", "--agent-port", "5680"]
    assert launched["env"]["KRITA_MCP_PORT"] == "5676"  # the port the bridge really listens on
    assert c.extension.bridge_port() == 5676
    monkeypatch.setattr(c.extension, "INSTANCE", None)
    assert c.extension.bridge_port() == 5679  # nothing running: the configured / default port


def test_provider_list_comes_from_the_service(chat):
    app, c, dock, sent = chat
    dock.providers = {"gemini": {"chat": True, "label": "Google Gemini"},
                      "mistral": {"chat": True, "label": "Mistral AI"},
                      "fal": {"label": "fal.ai"}}  # not a chat provider
    dock._sync_providers()
    assert [dock.provider.itemText(i) for i in range(dock.provider.count())] == ["Google Gemini", "Mistral AI"]
    assert dock._key() == "gemini"  # the current choice is kept


def test_free_services_turn_on_without_a_key(chat):
    app, c, dock, sent = chat
    pump(app, 0.3)
    dock.providers = dict(dock.providers, pollinations={
        "signed_in": False, "needs_key": False, "optional_key": True, "chat": True,
        "label": "Pollinations (free, no sign-in)", "key_url": "https://enter.pollinations.ai/keys"},
        horde={"signed_in": True, "needs_key": False, "optional_key": True, "label": "AI Horde"})
    dlg = c.ServicesDialog(dock)
    buttons = [b.text() for b in dlg.rows.findChildren(qtw.QPushButton)]
    assert "Turn on" in buttons and "Turn off" in buttons
    sign = c.SignInDialog(dock, "pollinations", c.service_info("pollinations", dock.providers["pollinations"]))
    labels = " ".join(w.text() for w in sign.findChildren(qtw.QLabel))
    assert "works without a key" in labels and "API key" in labels
    assert "Get a free key in the browser" in [b.text() for b in sign.findChildren(qtw.QPushButton)]
    assert c.service_info("horde")["optional_key"] and c.service_info("omniroute")["has_url"]
    assert "pollinations" in dict(c.PROVIDERS)  # offered even before the service reports its list


def test_skills_panel_lists_what_the_assistant_can_use(chat, monkeypatch, tmp_path):
    app, c, dock, sent = chat
    opened = []
    monkeypatch.setattr(c.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    listing = {"skills": [{"name": "photoreal-image", "description": "Make a photo <real>", "source": "built-in"}],
               "user_dir": str(tmp_path / "skills")}
    dlg = c.SkillsDialog(dock, listing)
    text = dlg.findChildren(qtw.QTextBrowser)[0].toPlainText()
    assert "photoreal-image" in text and "Make a photo <real>" in text and "built-in" in text
    buttons = {b.text(): b for b in dlg.findChildren(qtw.QPushButton)}
    buttons["Open my skills folder"].click()
    buttons["Find skills online"].click()
    assert (tmp_path / "skills").is_dir() and opened[1] == c.SKILLS_SITE
    assert opened[0].startswith("file:")
