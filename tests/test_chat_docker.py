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
    settings = {"chat_provider": "gemini"}  # these tests chat with Gemini (new installs start on Auto)

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
    c._real_request = c._request  # for the test of what calls to the service log
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
    assert dock.send_btn.text() == "Queue"  # while the AI works, Send queues the next message
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


def test_services_dialog_lists_every_service(chat, monkeypatch):
    app, c, dock, sent = chat
    pump(app, 0.4)
    dlg = c.ServicesDialog(dock)
    rows = dlg.scroll.widget()  # a long list scrolls instead of growing taller than the screen
    text = " ".join(w.text() for w in rows.findChildren(qtw.QLabel))
    buttons = [b.objectName() for b in rows.findChildren(qtw.QPushButton)]
    assert "Google Gemini" in text and "fal.ai" in text and "ComfyUI" in text
    assert "Chat models" in text and "On this computer" in text  # grouped
    # signed in: change the key or sign out, both right there; others: sign in
    assert {"gemini:signin", "gemini:signout", "fal:signin", "comfyui:signin"} == set(buttons)
    assert dlg.height() <= 760 and dlg.scroll.widgetResizable()
    dlg.search.setText("fal")
    assert [b.objectName() for b in dlg.scroll.widget().findChildren(qtw.QPushButton)] == ["fal:signin"]
    dlg.search.setText("nothing like this")
    assert "No service matches" in " ".join(w.text() for w in dlg.scroll.widget().findChildren(qtw.QLabel))
    dlg.search.setText("")
    info = dock.providers["comfyui"]
    real_dialog = c.SignInDialog
    # "Sign out" signs out at once; "Change" opens the key dialog even though it is signed in
    opened = []
    monkeypatch.setattr(c, "SignInDialog", lambda *a, **k: opened.append(a[1]) or types.SimpleNamespace(exec=lambda: 0))
    dlg.scroll.widget().findChild(qtw.QPushButton, "gemini:signin").click()
    assert opened == ["gemini"]
    dlg.scroll.widget().findChild(qtw.QPushButton, "gemini:signout").click()
    pump(app, 0.4)
    assert ("POST", "/signout", {"provider": "gemini"}) in sent
    assert "Google Gemini: not signed in" in dock.log.toPlainText()  # names the service that changed
    sign = real_dialog(dock, "comfyui", info)
    labels = [w.text() for w in sign.findChildren(qtw.QLabel)]
    assert "API key" not in labels and "Key" not in labels and "Address" in labels  # local: an address, no key
    assert sign.base.text() == "http://127.0.0.1:8188"  # the usual address is filled in
    assert "Test connection" in [b.text() for b in sign.findChildren(qtw.QPushButton)]


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
        if path == "/health" and state["n"] > 1:  # approved: the service now holds an OpenRouter key
            h = real(method, path, body, timeout)
            h["providers"]["openrouter"] = {"signed_in": True, "needs_key": True, "chat": True,
                                            "label": "OpenRouter (sign in with browser)"}
            return h
        return real(method, path, body, timeout)
    monkeypatch.setattr(c, "_request", oauth_request)
    dock.browser_sign_in("openrouter")
    pump(app, 3.0)
    assert ("POST", "/oauth/start", {"provider": "openrouter"}) in sent
    assert "https://openrouter.ai/auth?x=1" in opened and not dock.oauth_timer.isActive()
    assert "approve Krita" in dock.log.toPlainText()
    # clicking browser sign-in twice used to leave the first timer running: "signed in" every second, forever
    state["n"] = 0
    dock.log.clear()
    dock.browser_sign_in("openrouter")
    dock.browser_sign_in("openrouter")
    pump(app, 4.0)
    log = dock.log.toPlainText()
    assert log.count("✓ signed in") == 1 and not dock.oauth_timer.isActive()
    assert "OpenRouter" in log.splitlines()[-1]  # names the service signed in to, not the selected one


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
    assert "API key" not in local and "Address" in local


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
    dock.providers = {"gemini": {"chat": True, "label": "Google Gemini", "ready": True},
                      "mistral": {"chat": True, "label": "Mistral AI", "ready": True},
                      "fal": {"label": "fal.ai", "ready": True}}  # not a chat provider
    dock._sync_providers()
    assert [dock.provider.itemText(i) for i in range(dock.provider.count()) if dock.provider.itemData(i)] == [
        "Google Gemini", "Mistral AI", "Manage services…"]
    assert dock._key() == "gemini"  # the current choice is kept


def test_free_services_turn_on_without_a_key(chat):
    app, c, dock, sent = chat
    pump(app, 0.3)
    dock.providers = dict(dock.providers, pollinations={
        "signed_in": False, "needs_key": False, "optional_key": True, "toggle": True, "chat": True,
        "label": "Pollinations (free, no sign-in)", "key_url": "https://enter.pollinations.ai/keys"},
        horde={"signed_in": True, "needs_key": False, "optional_key": True, "toggle": True, "label": "AI Horde"},
        kilo={"signed_in": False, "needs_key": False, "optional_key": True, "chat": True, "label": "Kilo Gateway"})
    dlg = c.ServicesDialog(dock)
    buttons = [b.text() for b in dlg.scroll.widget().findChildren(qtw.QPushButton)]
    assert "Turn on" in buttons and "Turn off" in buttons and "Add key" in buttons  # free chat needs no switch
    sign = c.SignInDialog(dock, "pollinations", c.service_info("pollinations", dock.providers["pollinations"]))
    labels = " ".join(w.text() for w in sign.findChildren(qtw.QLabel))
    assert "no account" in labels and "API key" in labels and "optional" in labels
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


def test_logs_window_follows_the_log_live(chat):
    """Logs… shows the end of a log and every new line as it is written; the filter keeps matching lines."""
    app, c, dock, sent = chat
    tl = c.logview.tracelog
    tl.write("krita.log", "command", "✓ layer.create 5ms {}")
    dlg = c.logview.show(dock, "logs/krita.log")
    pump(app, 0.7)
    assert "layer.create" in dlg.view.toPlainText()
    tl.write("krita.log", "command", "✗ brush.stroke 9ms {} - No active document")
    pump(app, 0.8)
    assert "brush.stroke" in dlg.view.toPlainText()
    dlg.filter.setText("✗")
    pump(app, 0.3)
    assert "brush.stroke" in dlg.view.toPlainText() and "layer.create" not in dlg.view.toPlainText()
    assert c.logview.show(dock) is dlg  # one window, raised again
    dlg.close()


def test_actions_settings_and_commands_are_logged_without_keys(chat):
    app, c, dock, sent = chat
    log = Path(os.environ["KRITA_MCP_HOME"]) / "logs" / "krita.log"
    c.extension.set_setting("dialogs", "leave")
    c.extension.set_setting("dialogs", "leave")  # unchanged: no second line
    h = sys.modules["krita_mcp_bridge.handlers"]
    h.HANDLERS["test.echo"] = lambda p: p
    h.dispatch("test.echo", {"x": 1, "api_key": "sk-or-v1-" + "a" * 40})
    c._send_request = lambda *a: {"session": "s1"}
    c._real_request("POST", "/chat", {"provider": "gemini", "text": "paint a sunrise"})
    c._real_request("POST", "/signin", {"provider": "gemini", "key": "AIzaSyC" + "k" * 33})
    text = log.read_text(encoding="utf-8")
    assert text.count("dialogs: 'cancel' -> 'leave'") + text.count("dialogs: '' -> 'leave'") == 1
    assert "✓ test.echo" in text and '"x": 1' in text and "sk-or" not in text and "aaaaaaaa" not in text
    assert "POST /chat" in text and "paint a sunrise" in text and "POST /signin" in text and "kkkk" not in text
    line = text.strip().splitlines()[-1]
    assert line[:4].isdigit() and line[10] == " " and line[19] == "."  # date, time and milliseconds


def test_stop_responds_at_once_and_never_leaves_the_panel_stuck(chat, monkeypatch):
    app, c, dock, sent = chat
    monkeypatch.setattr(c, "STOP_WAIT_MS", 300)
    dock.session = "s-gone"
    dock._set_busy(True)
    dock._stop()
    assert not dock.stop_btn.isEnabled() and "Stopping" in dock.status.text()  # visible at once
    pump(app, 0.7)
    assert ("POST", "/stop", {"session": "s-gone"}) in sent
    assert not dock.busy and dock.send_btn.isEnabled() and "stopped waiting" in dock.log.toPlainText()


def test_stop_before_the_service_answered_still_stops(chat):
    app, c, dock, sent = chat
    dock.session = None
    dock._set_busy(True)
    dock._stop()
    dock._started({"session": "s2"})  # the message reached the service after Stop was pressed
    pump(app, 0.4)
    assert ("POST", "/stop", {"session": "s2"}) in sent


def test_a_lost_service_does_not_leave_the_panel_working_forever(chat, monkeypatch):
    app, c, dock, sent = chat

    def gone(method, path, body=None, timeout=10):
        raise RuntimeError("no such session")
    monkeypatch.setattr(c, "_request", gone)
    dock.session = "s-old"
    dock._set_busy(True)
    dock.poll_timer.start(50)
    pump(app, 0.8)
    assert not dock.busy and not dock.poll_timer.isActive()
    assert "Send your message again" in dock.log.toPlainText()



def _setup_dialog(c, dock, name, **info):
    return c.SignInDialog(dock, name, c.service_info(name, info), docker=dock)


def test_each_service_is_set_up_the_way_it_works(chat):
    """Not everything has an API key: cloud services ask for a key, OpenRouter signs in with the browser,
    servers on this computer ask for an address (and can be tested), free services need nothing."""
    app, c, dock, sent = chat

    def parts(dlg):
        return ([w.text() for w in dlg.findChildren(qtw.QLabel)],
                [b.text() for b in dlg.findChildren(qtw.QPushButton)], dlg.windowTitle())
    labels, buttons, title = parts(_setup_dialog(c, dock, "mistral", label="Mistral AI", use="chat", setup="key",
                                                 key_url="https://console.mistral.ai/api-keys"))
    assert "API key" in labels and "Address" not in labels and "Get a key in the browser" in buttons
    assert title == "Sign in to Mistral AI"
    labels, buttons, title = parts(_setup_dialog(c, dock, "openrouter", label="OpenRouter", setup="browser",
                                                 browser_sign_in=True))
    assert "Sign in with browser" in buttons and "Address" not in labels
    labels, buttons, title = parts(_setup_dialog(c, dock, "ollama", label="Local model", use="chat",
                                                 setup="local"))
    assert "Address" in labels and "API key" not in labels and "Test connection" in buttons
    assert title == "Set up Local model" and "Save" in buttons
    labels, buttons, title = parts(_setup_dialog(c, dock, "kilo", label="Kilo", use="chat", setup="free",
                                                 optional_key=True, key_url="https://app.kilo.ai"))
    assert "API key" in labels and any("no account needed" in t for t in labels)
    assert "Get a free key in the browser" in buttons and "optional" in title


def test_test_connection_reports_whether_the_local_server_answers(chat, monkeypatch):
    app, c, dock, sent = chat
    real = c._request

    def answer(method, path, body=None, timeout=10):
        if path == "/check":
            sent.append((method, path, body))
            return {"ok": False, "detail": "nothing answers at http://127.0.0.1:11434/v1"}
        return real(method, path, body, timeout)
    monkeypatch.setattr(c, "_request", answer)
    dlg = _setup_dialog(c, dock, "ollama", label="Local model", setup="local")
    dlg.test_btn.click()
    pump(app, 0.5)
    assert ("POST", "/check", {"provider": "ollama", "base_url": "http://127.0.0.1:11434/v1"}) in sent
    assert "✗" in dlg.hint.text() and "nothing answers" in dlg.hint.text() and dlg.test_btn.isEnabled()


def test_turn_on_needs_no_dialog_and_the_list_updates_while_open(chat, monkeypatch):
    """Turn on acts at once; the open Services window shows the change (it used to wait until it closed)."""
    app, c, dock, sent = chat
    pump(app, 0.3)
    state = {"on": False}
    real = c._request

    def service(method, path, body=None, timeout=10):
        if path == "/signin" and body["provider"] == "horde":
            sent.append((method, path, body))
            state["on"] = True
        if path in ("/signin", "/signout", "/health"):
            h = real("GET", "/health")
            h["providers"]["horde"] = {"signed_in": state["on"], "ready": state["on"], "setup": "toggle",
                                       "toggle": True, "optional_key": True, "label": "AI Horde", "use": "images"}
            return h
        return real(method, path, body, timeout)
    monkeypatch.setattr(c, "_request", service)
    monkeypatch.setattr(c, "_ensure_service", lambda: service("GET", "/health"))
    dock.providers = service("GET", "/health")["providers"]
    monkeypatch.setattr(c, "SignInDialog", lambda *a, **k: pytest.fail("Turn on must not ask anything"))
    seen = {}

    def press_then_look():
        d = dock.services_dialog
        d.scroll.widget().findChild(qtw.QPushButton, "horde:on").click()

        def look():
            seen["buttons"] = [b.objectName() for b in d.scroll.widget().findChildren(qtw.QPushButton)
                               if b.objectName().startswith("horde")]
            d.accept()
        c.QTimer.singleShot(600, look)
    c.QTimer.singleShot(300, press_then_look)
    dock._services()  # modal, like in Krita
    assert ("POST", "/signin", {"provider": "horde", "key": "", "verify": False}) in sent
    assert seen["buttons"] == ["horde:signin", "horde:signout"]  # updated while the window was open


def test_the_provider_menu_lists_what_can_be_used_now(chat, monkeypatch):
    """Auto first, then what you can use now (always shown), then every other provider - picking one of
    those opens its setup and selects it once it is ready."""
    app, c, dock, sent = chat
    pump(app, 0.3)  # the fake service's own first answer arrives before this test's list
    dock.providers = {
        "auto": {"chat": True, "label": "Auto - all my providers", "ready": True, "setup": "auto",
                 "order": ["mistral", "kilo"]},
        "gemini": {"chat": True, "label": "Google Gemini", "signed_in": False, "ready": False, "setup": "key"},
        "mistral": {"chat": True, "label": "Mistral AI", "signed_in": True, "ready": True, "setup": "key"},
        "kilo": {"chat": True, "label": "Kilo Gateway", "ready": True, "setup": "free"},
        "groq": {"chat": True, "label": "Groq", "signed_in": False, "ready": False, "setup": "key"},
        "fal": {"label": "fal.ai", "signed_in": True, "ready": True, "setup": "key"}}
    dock._sync_providers()
    items = [dock.provider.itemText(i) for i in range(dock.provider.count()) if dock.provider.itemData(i)]
    assert items == ["Auto - all my providers", "Mistral AI", "Kilo Gateway", "Google Gemini - set up…",
                     "Groq - set up…", "Manage services…"]  # every chat provider can be picked; fal is no chat
    assert dock._key() == "auto"  # Gemini (the old choice) is not usable now
    assert "2 providers" in dock.account.text() and "1. Mistral AI" in dock.account.toolTip()
    asked = []
    monkeypatch.setattr(dock, "sign_in_service", lambda name, parent=None, action=None: asked.append((name, action)))
    dock.provider.setCurrentIndex(dock.provider.findData(dock.SETUP + "groq"))
    pump(app, 0.2)
    assert asked == [("groq", "signin")] and dock._key() == "auto"  # its setup opens; the choice stays for now
    ready = dict(dock.providers, groq=dict(dock.providers["groq"], signed_in=True, ready=True))
    dock._signed({"providers": ready}, "groq")
    assert dock._key() == "groq"  # signed in: selected
    opened = []
    dock._services = lambda: opened.append(1)
    dock.provider.setCurrentIndex(dock.provider.findData(dock.MANAGE))
    pump(app, 0.2)
    assert opened == [1] and dock._key() == "groq"


def test_messages_typed_while_the_ai_works_are_queued_and_sent_in_order(chat):
    app, c, dock, sent = chat
    dock.session = "s1"
    dock._set_busy(True)
    for text in ("make the sky warmer", "then add birds"):
        dock.input.setPlainText(text)
        dock._send()
    assert dock.queue == ["make the sky warmer", "then add birds"] and dock.queue_box.isVisibleTo(dock)
    assert "Queued (2)" in dock.queue_label.text() and dock.input.toPlainText() == ""
    assert not [x for x in sent if x[1] == "/chat"]  # nothing sent while busy
    assert "Working" in dock.status.text()
    dock._events({"events": [{"type": "done", "n": 0}]})  # the AI finished
    pump(app, 0.4)
    assert [x[2]["text"] for x in sent if x[1] == "/chat"] == ["make the sky warmer"]
    assert dock.queue == ["then add birds"]
    dock._events({"events": [{"type": "done", "n": 1}]})
    pump(app, 0.4)
    assert [x[2]["text"] for x in sent if x[1] == "/chat"] == ["make the sky warmer", "then add birds"]
    assert dock.queue == [] and not dock.queue_box.isVisibleTo(dock)


def test_stop_returns_queued_messages_to_the_box_and_queued_ones_can_be_taken_back(chat, monkeypatch):
    app, c, dock, sent = chat
    monkeypatch.setattr(c, "STOP_WAIT_MS", 200)
    dock.session = "s1"
    dock._set_busy(True)
    for text in ("one", "two", "three"):
        dock.input.setPlainText(text)
        dock._send()
    dock._unqueue(dock.queue_list.item(1))  # double-click "two": back into the box to edit
    assert dock.queue == ["one", "three"] and dock.input.toPlainText() == "two"
    dock._stop()
    assert dock.queue == [] and dock.input.toPlainText() == "two\none\nthree"  # nothing goes out by itself
    pump(app, 0.5)
    assert not [x for x in sent if x[1] == "/chat"]


def test_enter_sends_shift_enter_adds_a_line_and_up_recalls(chat):
    app, c, dock, sent = chat
    if os.environ.get("KRITA_TEST_QT") == "5":
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest
    else:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
    keys = getattr(Qt, "Key", Qt)
    mods = getattr(Qt, "KeyboardModifier", Qt)
    dock.input.setPlainText("hello")
    dock.input.moveCursor(c._END)
    QTest.keyClick(dock.input, keys.Key_Return, mods.ShiftModifier)
    assert dock.input.blockCount() == 2  # Shift+Enter: a new line, nothing sent
    QTest.keyClick(dock.input, keys.Key_Return)
    pump(app, 0.3)
    assert [x[2]["text"] for x in sent if x[1] == "/chat"] == ["hello"] and dock.input.toPlainText() == ""
    QTest.keyClick(dock.input, keys.Key_Up)
    assert dock.input.toPlainText() == "hello"  # Up in an empty box: the last message
