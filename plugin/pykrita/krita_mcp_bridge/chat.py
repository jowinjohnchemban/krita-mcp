"""The "AI Chat" docker: sign in to an AI (Gemini, Claude, GPT, or a local model) and ask it to work in Krita.

Settings > Dockers > AI Chat. The conversation runs in the krita-mcp assistant service (`krita-mcp --agent`,
started automatically on 127.0.0.1:5680); its tool calls come back through this plugin's bridge. All network
calls happen in worker threads: the bridge executes the assistant's tools on Krita's GUI thread, so the GUI
thread must never wait for the assistant.
"""
import html
import json
import re
import os
import queue
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Krita

try:
    from PyQt6.QtCore import Qt, QTimer, QUrl
    from PyQt6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
    from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                                 QLineEdit, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget)
    _PASSWORD = QLineEdit.EchoMode.Password
    _RICH = Qt.TextFormat.RichText
except ImportError:  # Krita 5
    from PyQt5.QtCore import Qt, QTimer, QUrl
    from PyQt5.QtGui import QDesktopServices, QGuiApplication, QKeySequence
    from PyQt5.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                                 QLineEdit, QPlainTextEdit, QPushButton, QShortcut, QTextBrowser, QVBoxLayout,
                                 QWidget)
    _PASSWORD = QLineEdit.Password
    _RICH = Qt.RichText

from . import extension, protocol
from .handlers import _enum
from .util import i18n

AGENT_PORT = int(os.environ.get("KRITA_MCP_AGENT_PORT") or 5680)
PROVIDERS = [("gemini", "Google Gemini"), ("anthropic", "Anthropic Claude"), ("openai", "OpenAI / compatible"),
             ("openrouter", "OpenRouter (browser sign-in)"), ("ollama", "Local model (Ollama, LM Studio)")]
# what each service's keys look like, to pick them up from the clipboard while the key page is open
KEY_PATTERNS = {
    "gemini": r"AIza[0-9A-Za-z_\-]{30,}", "anthropic": r"sk-ant-[0-9A-Za-z_\-]{20,}",
    "openai": r"sk-(?!ant-|or-)[0-9A-Za-z_\-]{20,}", "openrouter": r"sk-or-[0-9A-Za-z_\-]{20,}",
    "replicate": r"r8_[0-9A-Za-z]{20,}", "stability": r"sk-[0-9A-Za-z]{20,}",
    "fal": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{20,}",
    "heygen": r"[0-9A-Za-z_\-=]{24,}"}
# Built-in facts about each service, so sign-in works before the assistant service is running
# (anything the service reports overrides these).
SERVICE_INFO = {
    "gemini": {"label": "Google Gemini", "key_url": "https://aistudio.google.com/apikey"},
    "anthropic": {"label": "Anthropic Claude", "key_url": "https://console.anthropic.com/settings/keys"},
    "openai": {"label": "OpenAI / compatible", "key_url": "https://platform.openai.com/api-keys", "has_url": True},
    "openrouter": {"label": "OpenRouter", "key_url": "https://openrouter.ai/settings/keys",
                   "browser_sign_in": True},
    "ollama": {"label": "Local model (Ollama, LM Studio)", "key_url": "https://ollama.com/download",
               "needs_key": False, "has_url": True},
    "stability": {"label": "Stability AI", "key_url": "https://platform.stability.ai/account/keys"},
    "replicate": {"label": "Replicate", "key_url": "https://replicate.com/account/api-tokens"},
    "fal": {"label": "fal.ai", "key_url": "https://fal.ai/dashboard/keys"},
    "heygen": {"label": "HeyGen", "key_url": "https://app.heygen.com/settings"},
    "comfyui": {"label": "ComfyUI (local)", "key_url": "https://www.comfy.org/", "needs_key": False,
                "has_url": True},
    "a1111": {"label": "AUTOMATIC1111 / Forge (local)", "needs_key": False, "has_url": True,
              "key_url": "https://github.com/lllyasviel/stable-diffusion-webui-forge"},
}


def service_info(name, reported=None):
    info = {"label": name, "needs_key": True}
    info.update(SERVICE_INFO.get(name, {}))
    info.update({k: v for k, v in (reported or {}).items() if v is not None})
    return info


SHORT = {"gemini": "Gemini", "anthropic": "Claude", "openai": "GPT", "openrouter": "AI", "ollama": "AI"}
_SERVICE = {"proc": None}


def _request(method, path, body=None, timeout=10):
    """Call the assistant service (worker threads only)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (AGENT_PORT, path), data=data, method=method, headers={
        "Authorization": "Bearer " + protocol.load_or_create_token(), "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        raise RuntimeError(msg or "assistant service: HTTP %d" % e.code) from None


def _ensure_service():
    """Make sure `krita-mcp --agent` is running; start it with the panel's server command if needed."""
    try:
        return _request("GET", "/health", timeout=2)
    except (OSError, RuntimeError):
        pass
    proc = _SERVICE["proc"]
    if proc is None or proc.poll() is not None:
        cmd = extension.setting("client_command", "krita-mcp") or "krita-mcp"
        args = shlex.split(cmd, posix=os.name != "nt") + ["--agent", "--agent-port", str(AGENT_PORT)]
        env = dict(os.environ, KRITA_MCP_PORT=str(extension.bridge_port()), KRITA_MCP_HOME=str(protocol.config_dir()))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            _SERVICE["proc"] = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError as e:
            raise RuntimeError("could not start %r (%s). Install the server (pip install krita-mcp) or set the "
                               "server command in the AI Bridge (MCP) panel." % (cmd, e)) from None
    for _ in range(40):
        time.sleep(0.25)
        try:
            return _request("GET", "/health", timeout=2)
        except (OSError, RuntimeError):
            if _SERVICE["proc"].poll() is not None:
                break
    raise RuntimeError("the assistant service did not start (run `krita-mcp --agent` in a terminal to see why)")


class SignInDialog(QDialog):
    def __init__(self, parent, service, info):
        super().__init__(parent)
        self.setWindowTitle(i18n("Sign in to %s") % info.get("label", service))
        form = QFormLayout(self)
        intro = QLabel(i18n("Used for: %s. Keys are stored only on this computer (~/.krita-mcp) and also serve "
                            "AI apps connected through MCP.") % info.get("use", ""))
        intro.setWordWrap(True)
        form.addRow(intro)
        self.key = QLineEdit()
        self.key.setEchoMode(_PASSWORD)
        self.base = QLineEdit(info.get("base_url") or "")
        placeholders = {"ollama": "http://127.0.0.1:11434/v1  (LM Studio: http://127.0.0.1:1234/v1)",
                        "openai": i18n("optional: another OpenAI-compatible URL"),
                        "comfyui": "http://127.0.0.1:8188", "a1111": "http://127.0.0.1:7860"}
        self.base.setPlaceholderText(placeholders.get(service, ""))
        if info.get("needs_key", True):
            form.addRow(i18n("API key"), self.key)
        if info.get("has_url"):
            form.addRow(i18n("Server URL"), self.base)
        self.service, self.info, self.browser_requested = service, info, False
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        if info.get("browser_sign_in"):  # real OAuth: approve in the browser, nothing to copy
            btn = QPushButton(i18n("Sign in with browser"))
            btn.setDefault(True)
            btn.clicked.connect(self._browser)
            form.addRow(btn)
            form.addRow(QLabel(i18n("or paste a key:")))
        elif info.get("needs_key", True) and info.get("key_url"):
            btn = QPushButton(i18n("Get a key in the browser"))
            btn.setToolTip(i18n("Opens the provider's key page; copy the key there and it appears here"))
            btn.clicked.connect(self._open_key_page)
            form.addRow(btn)
        elif info.get("key_url"):
            link = QLabel('<a href="%s">%s</a>' % (info["key_url"], i18n("Download / documentation")))
            link.setTextFormat(_RICH)
            link.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
            form.addRow(link)
        form.addRow(self.hint)
        self.clip_timer = QTimer(self)
        self.clip_timer.timeout.connect(self._check_clipboard)
        self.clip_start = None
        buttons = QDialogButtonBox(_enum(QDialogButtonBox, "StandardButton", "Ok")
                                   | _enum(QDialogButtonBox, "StandardButton", "Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browser(self):
        self.browser_requested = True
        self.accept()

    def _open_key_page(self):
        self.clip_start = QGuiApplication.clipboard().text()
        QDesktopServices.openUrl(QUrl(self.info["key_url"]))
        self.hint.setText(i18n("Create or copy a key in your browser - it will appear here automatically."))
        self.clip_timer.start(600)

    def _check_clipboard(self):
        text = (QGuiApplication.clipboard().text() or "").strip()
        if not text or text == self.clip_start or len(text) > 300:
            return
        found = find_key(self.service, text)
        if found:
            self.key.setText(found)
            self.hint.setText("✓ " + i18n("Key found in the clipboard - click OK to check and save it."))
            self.clip_timer.stop()


def find_key(service, text):
    """The API key in `text` if it looks like one of `service`'s keys."""
    pattern = KEY_PATTERNS.get(service)
    if not pattern:
        return None
    m = re.search(r"(?<![0-9A-Za-z_\-])(%s)(?![0-9A-Za-z_\-])" % pattern, text)
    return m.group(1) if m else None


class ServicesDialog(QDialog):
    """Every AI service in one place: chat models, image / video generators, local servers."""

    def __init__(self, docker):
        super().__init__(docker)
        self.docker = docker
        self.setWindowTitle(i18n("AI services"))
        self.setMinimumWidth(460)
        self.lay = QVBoxLayout(self)
        note = QLabel(i18n("Sign in once here: the AI Chat panel and AI apps connected through MCP (Claude Desktop, "
                           "Gemini CLI, Cursor, ...) both use these services."))
        note.setWordWrap(True)
        self.lay.addWidget(note)
        self.rows = QWidget()
        self.lay.addWidget(self.rows)
        close = QPushButton(i18n("Close"))
        close.clicked.connect(self.accept)
        self.lay.addWidget(close)
        self.refresh()

    def refresh(self):
        old = self.rows
        self.rows = QWidget()
        grid = QFormLayout(self.rows)
        for name, info in sorted(self.docker.providers.items(), key=lambda kv: (not kv[1].get("chat"), kv[0])):
            mark = "✓ " if info.get("signed_in") else "○ "
            label = QLabel("%s<b>%s</b> <span style='color:#999'>%s</span>" % (
                mark, html.escape(info.get("label", name)), html.escape(info.get("use", ""))))
            label.setTextFormat(_RICH)
            label.setWordWrap(True)
            signed_with_key = info.get("signed_in") and info.get("needs_key")
            btn = QPushButton(i18n("Sign out") if signed_with_key else i18n("Sign in"))
            btn.clicked.connect(lambda _=False, n=name: self.docker.sign_in_service(n, self))
            grid.addRow(label, btn)
        self.lay.replaceWidget(old, self.rows)
        old.deleteLater()


class ChatDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(i18n("AI Chat"))
        self.inbox = queue.Queue()  # (callback, value) from worker threads, handled on the GUI thread
        self.session = None
        self.seen = 0
        self.busy = False
        self.providers = {}
        root = QWidget(self)
        lay = QVBoxLayout(root)

        top = QHBoxLayout()
        self.provider = QComboBox()
        for key, label in PROVIDERS:
            self.provider.addItem(label, key)
        idx = [k for k, _ in PROVIDERS].index(extension.setting("chat_provider", "gemini")) \
            if extension.setting("chat_provider", "gemini") in dict(PROVIDERS) else 0
        self.provider.setCurrentIndex(idx)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.signin = QPushButton(i18n("Sign in"))
        self.signin.clicked.connect(lambda: self.sign_in_service(self._key()))
        services = QPushButton(i18n("Services…"))
        services.setToolTip(i18n("Sign in to image, video and chat services"))
        services.clicked.connect(self._services)
        top.addWidget(self.provider, 1)
        top.addWidget(self.signin)
        top.addWidget(services)
        lay.addLayout(top)

        row = QHBoxLayout()
        # model: Auto (the newest, chosen fresh for every message) or any model the account offers right now
        self._populating = False
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setToolTip(i18n("Auto picks the newest model your account can use, every time. The list is "
                                   "loaded live from the provider; you can also type a model name."))
        self._set_models([], saved=extension.setting("chat_model_" + self._key(), ""))
        self.model.currentTextChanged.connect(self._model_chosen)
        self.refresh_models = QPushButton("⟳")
        self.refresh_models.setToolTip(i18n("Reload the models available to this account"))
        self.refresh_models.clicked.connect(lambda: self._refresh_models(start=True))
        self.account = QLabel()
        row.addWidget(self.model, 1)
        row.addWidget(self.refresh_models)
        row.addWidget(self.account)
        lay.addLayout(row)

        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(True)
        self.log.setPlaceholderText(i18n("Ask for anything, e.g. \"paint a misty forest at dawn\", \"make this photo "
                                         "warmer\", \"animate this with a slow dolly-in\"."))
        lay.addWidget(self.log, 1)

        self.input = QPlainTextEdit()
        self.input.setPlaceholderText(i18n("Message the AI (Ctrl+Enter to send)"))
        self.input.setMaximumHeight(90)
        lay.addWidget(self.input)
        QShortcut(QKeySequence("Ctrl+Return"), self.input, activated=self._send)

        buttons = QHBoxLayout()
        self.send_btn = QPushButton(i18n("Send"))
        self.send_btn.clicked.connect(self._send)
        self.stop_btn = QPushButton(i18n("Stop"))
        self.stop_btn.clicked.connect(self._stop)
        new = QPushButton(i18n("New chat"))
        new.clicked.connect(self._new_chat)
        buttons.addWidget(self.send_btn, 1)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(new)
        lay.addLayout(buttons)
        self.setWidget(root)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain)
        self.timer.start(150)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll)
        self._set_busy(False)
        # passive: only look for a running service; it is started when the user sends or signs in
        self._background(lambda: _request("GET", "/health", timeout=2), self._got_health, quiet=True)

    # ---------------------------------------------------------------- plumbing
    def canvasChanged(self, canvas):
        pass

    def _key(self):
        return self.provider.currentData() or "gemini"

    def _background(self, fn, done, *args, quiet=False):
        def work():
            try:
                self.inbox.put((done, fn(*args)))
            except Exception as e:  # noqa: BLE001 - shown in the chat
                if not quiet:
                    self.inbox.put((self._error, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _drain(self):
        while True:
            try:
                cb, value = self.inbox.get_nowait()
            except queue.Empty:
                return
            cb(value)

    def _append(self, html_text):
        self.log.append(html_text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _error(self, msg):
        self._append("<p style='color:#e66'>%s</p>" % html.escape(msg))
        self._set_busy(False)

    def _set_busy(self, busy):
        self.busy = busy
        self.send_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        self.send_btn.setText(i18n("Working…") if busy else i18n("Send"))

    # ---------------------------------------------------------------- sign in
    def _got_health(self, health):
        self.providers = (health or {}).get("providers", {})
        self._sync_providers()
        self._show_account()
        self._refresh_models()

    def _sync_providers(self):
        """The chat providers come from the assistant service, so new ones appear without a plugin update."""
        chats = [(k, v.get("label", k)) for k, v in self.providers.items() if v.get("chat")]
        if not chats or [self.provider.itemData(i) for i in range(self.provider.count())] == [k for k, _ in chats]:
            return
        current = self._key()
        self.provider.blockSignals(True)
        try:
            self.provider.clear()
            for key, label in chats:
                self.provider.addItem(label, key)
            idx = self.provider.findData(current)
            self.provider.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self.provider.blockSignals(False)
        if self._key() != current:
            self._provider_changed()

    # ---------------------------------------------------------------- live model list
    AUTO = "Auto (newest)"

    def _model_text(self):
        t = self.model.currentText().strip()
        return "" if t in (self.AUTO, i18n(self.AUTO)) else t

    def _set_models(self, models, saved=None):
        current = self._model_text() if saved is None else saved
        self._populating = True
        try:
            self.model.clear()
            self.model.addItem(i18n(self.AUTO))
            for m in models:
                self.model.addItem(m)
            if current:
                self.model.setEditText(current)
            else:
                self.model.setCurrentIndex(0)
        finally:
            self._populating = False

    def _model_chosen(self, _text):
        if not self._populating:
            extension.set_setting("chat_model_" + self._key(), self._model_text())

    def _refresh_models(self, start=False):
        """Ask the provider which models this account can use, now (never cached)."""
        key = self._key()

        def fetch():
            if start:
                _ensure_service()
            return _request("GET", "/models?provider=%s" % key, timeout=30)

        def done(r):
            if key != self._key():
                return  # the user switched provider meanwhile
            self._set_models(r.get("models") or [])
            if start and r.get("error"):
                self._error(r["error"])
            elif start:
                self._append("<p style='color:#999; font-size:small'>%s</p>" % html.escape(
                    i18n("%d models available") % len(r.get("models") or [])))
        self._background(fetch, done, quiet=not start)

    def _show_account(self):
        info = service_info(self._key(), self.providers.get(self._key()))
        signed = info.get("signed_in")
        self.account.setText("✓ " + i18n("signed in") if signed else i18n("not signed in"))
        self.signin.setText(i18n("Sign out") if signed and info.get("needs_key") else i18n("Sign in"))
        if info.get("default_model"):
            self.model.lineEdit().setPlaceholderText(i18n("Auto: %s") % info["default_model"])

    def _provider_changed(self):
        extension.set_setting("chat_provider", self._key())
        self._set_models([], saved=extension.setting("chat_model_" + self._key(), ""))
        self._refresh_models()
        self.session = None
        self._show_account()

    def _services(self):
        if not self.providers:  # nothing known yet: start the service, then open the list
            return self._background(_ensure_service, lambda h: (self._got_health(h), self._services()))
        self.services_dialog = ServicesDialog(self)
        self.services_dialog.exec()
        self.services_dialog = None

    def sign_in_service(self, name, parent=None):
        info = service_info(name, self.providers.get(name))
        if info.get("signed_in") and info.get("needs_key"):
            self._background(lambda: _ensure_service() and _request("POST", "/signout", {"provider": name}),
                             self._signed)
            return
        dlg = SignInDialog(parent or self, name, info)
        if not dlg.exec():
            return
        if dlg.browser_requested:
            return self.browser_sign_in(name)
        body = {"provider": name, "key": dlg.key.text().strip(), "base_url": dlg.base.text().strip() or None}
        if info.get("needs_key", True) and not body["key"]:
            return self._error(i18n("No key entered."))
        self._background(lambda: _ensure_service() and _request("POST", "/signin", body), self._signed)

    def browser_sign_in(self, name):
        """OAuth in the user's browser; the assistant service receives the redirect and saves the key."""
        def opened(flow):
            QDesktopServices.openUrl(QUrl(flow["url"]))
            self._append("<p style='color:#8a8'>%s</p>" % html.escape(
                i18n("Your browser opened: approve Krita there. This panel updates when you are signed in.")))
            self.oauth_id = flow["id"]
            self.oauth_timer = QTimer(self)
            self.oauth_timer.timeout.connect(self._oauth_poll)
            self.oauth_timer.start(1000)
        self._background(lambda: _ensure_service() and _request("POST", "/oauth/start", {"provider": name}), opened)

    def _oauth_poll(self):
        if getattr(self, "_oauth_polling", False):
            return
        self._oauth_polling = True
        fid = self.oauth_id

        def got(r):
            self._oauth_polling = False
            if r.get("status") == "pending":
                return
            self.oauth_timer.stop()
            if r.get("status") == "done":
                self._background(lambda: _request("GET", "/health"), lambda h: self._signed(h))
            else:
                self._error(i18n("Browser sign-in failed: %s") % (r.get("error") or "?"))
        self._background(lambda: _request("GET", "/oauth/status?id=%s" % fid), got)

    def _signed(self, result):
        self.providers = (result or {}).get("providers", self.providers)
        self._sync_providers()
        self._show_account()
        self._refresh_models()
        if getattr(self, "services_dialog", None) is not None:
            self.services_dialog.refresh()
        self._append("<p style='color:#8a8'>%s</p>" % html.escape(
            "%s: %s" % (self.provider.currentText(), self.account.text())))

    # ---------------------------------------------------------------- chat
    def _send(self):
        text = self.input.toPlainText().strip()
        if not text or self.busy:
            return
        self.input.clear()
        self._set_busy(True)
        body = {"session": self.session, "provider": self._key(), "model": self._model_text() or None,
                "text": text}
        self._background(lambda: _ensure_service() and _request("POST", "/chat", body), self._started)

    def _started(self, result):
        if result.get("session") != self.session:
            self.session, self.seen = result["session"], 0
        self.poll_timer.start(400)

    def _poll(self):
        if getattr(self, "_polling", False) or not self.session:
            return
        self._polling = True
        sid, since = self.session, self.seen

        def done(r):
            self._polling = False
            self._events(r)
        self._background(lambda: _request("GET", "/events?session=%s&since=%d" % (sid, since)), done)

    def _events(self, r):
        for ev in r.get("events", []):
            self.seen = max(self.seen, ev["n"] + 1)
            kind = ev["type"]
            if kind == "user":
                self._append("<p><b>%s</b> %s</p>" % (i18n("You:"), html.escape(ev["text"]).replace("\n", "<br>")))
            elif kind == "assistant":
                self._append("<p><b>%s:</b> %s</p>" % (SHORT.get(self._key(), "AI"),
                                                       html.escape(ev["text"]).replace("\n", "<br>")))
            elif kind == "note":
                self._append("<p style='color:#999; font-size:small'>%s</p>" % html.escape(ev.get("text", "")))
            elif kind == "tool":
                self._append("<p style='color:#999; font-size:small'>🔧 %s %s</p>" % (
                    html.escape(ev["name"]), html.escape(ev.get("args", ""))))
            elif kind == "tool_result" and not ev.get("ok"):
                self._append("<p style='color:#c96; font-size:small'>⚠ %s</p>" % html.escape(ev.get("text", "")))
            elif kind == "error":
                self._error(ev["text"])
            elif kind == "done":
                self.poll_timer.stop()
                self._set_busy(False)

    def _stop(self):
        sid = self.session
        if sid:
            self._background(lambda: _request("POST", "/stop", {"session": sid}), lambda _: None)

    def _new_chat(self):
        sid = self.session
        self.session, self.seen = None, 0
        self.poll_timer.stop()
        self.log.clear()
        self._set_busy(False)
        if sid:
            self._background(lambda: _request("POST", "/reset", {"session": sid}), lambda _: None, quiet=True)


def register_docker():
    Krita.instance().addDockWidgetFactory(
        DockWidgetFactory("krita_mcp_chat", DockWidgetFactoryBase.DockRight, ChatDocker))
