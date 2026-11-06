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
                           "use": "chat", "default_model": "gemini-2.5-flash"},
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
    assert "signed in" in dock.account.text() and dock.model.placeholderText() == "gemini-2.5-flash"
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
