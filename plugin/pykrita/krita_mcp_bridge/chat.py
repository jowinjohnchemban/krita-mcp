"""The "AI Chat" docker: sign in to an AI (Gemini, Claude, GPT, or a local model) and ask it to work in Krita.

Settings > Dockers > AI Chat. The conversation runs in the KritaPilot assistant service (`kritapilot --agent`,
started automatically on 127.0.0.1:5680); its tool calls come back through this plugin's bridge. All network
calls happen in worker threads: the bridge executes the assistant's tools on Krita's GUI thread, so the GUI
thread must never wait for the assistant.
"""
import html
import json
import re
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Krita

try:
    from PyQt6.QtCore import Qt, QTimer, QUrl
    from PyQt6.QtGui import QColor, QDesktopServices, QGuiApplication, QKeySequence, QShortcut, QTextCursor
    from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                                 QGridLayout, QLayout, QLineEdit, QListWidget, QPlainTextEdit, QPushButton,
                                 QScrollArea, QTextBrowser, QVBoxLayout, QWidget)
    _PASSWORD = QLineEdit.EchoMode.Password
    _RICH = Qt.TextFormat.RichText
    _TOP = Qt.AlignmentFlag.AlignTop
    _FIXED = QLayout.SizeConstraint.SetFixedSize
    _FOREGROUND = Qt.ItemDataRole.ForegroundRole
    _KEY_RETURN, _KEY_ENTER, _KEY_UP = Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Up
    _SHIFT = Qt.KeyboardModifier.ShiftModifier
    _END = QTextCursor.MoveOperation.End
except ImportError:  # Krita 5
    from PyQt5.QtCore import Qt, QTimer, QUrl
    from PyQt5.QtGui import QColor, QDesktopServices, QGuiApplication, QKeySequence, QTextCursor
    from PyQt5.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                                 QGridLayout, QLayout, QLineEdit, QListWidget, QPlainTextEdit, QPushButton,
                                 QScrollArea, QShortcut, QTextBrowser, QVBoxLayout,
                                 QWidget)
    _PASSWORD = QLineEdit.Password
    _RICH = Qt.RichText
    _TOP = Qt.AlignTop
    _FIXED = QLayout.SetFixedSize
    _FOREGROUND = Qt.ForegroundRole
    _KEY_RETURN, _KEY_ENTER, _KEY_UP = Qt.Key_Return, Qt.Key_Enter, Qt.Key_Up
    _SHIFT = Qt.ShiftModifier
    _END = QTextCursor.End

from . import extension, logview, protocol
from .handlers import _enum
from .util import i18n

AGENT_PORT = int(os.environ.get("KRITA_MCP_AGENT_PORT") or 5680)
PROVIDERS = [("auto", "Auto - all my providers"), ("gemini", "Google Gemini"), ("anthropic", "Anthropic Claude"),
             ("openai", "OpenAI / compatible"),
             ("openrouter", "OpenRouter (browser sign-in)"), ("ollama", "Local model (Ollama, LM Studio)"),
             ("pollinations", "Pollinations (free, no sign-in)")]
SKILLS_SITE = "https://skills.sh/"
POLL_MS = 250  # how often the panel asks the assistant what happened (a local request: cheap)
STOP_WAIT_MS = 8000  # after Stop, how long the panel waits for the assistant to confirm
OAUTH_WAIT = 600  # seconds the panel waits for a browser sign-in to be approved
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
    "auto": {"label": "Auto - all my providers", "setup": "auto", "needs_key": False, "chat": True,
             "use": "uses your providers in turn and switches when one runs out of credit or fails"},
    "gemini": {"label": "Google Gemini", "key_url": "https://aistudio.google.com/apikey"},
    "anthropic": {"label": "Anthropic Claude", "key_url": "https://console.anthropic.com/settings/keys"},
    "openai": {"label": "OpenAI / compatible", "key_url": "https://platform.openai.com/api-keys", "has_url": True},
    "openrouter": {"label": "OpenRouter", "key_url": "https://openrouter.ai/settings/keys",
                   "browser_sign_in": True},
    "ollama": {"label": "Local model (Ollama, LM Studio)", "key_url": "https://ollama.com/download",
               "use": "chat with a model on your computer",
               "needs_key": False, "has_url": True},
    "stability": {"label": "Stability AI", "key_url": "https://platform.stability.ai/account/keys"},
    "replicate": {"label": "Replicate", "key_url": "https://replicate.com/account/api-tokens"},
    "fal": {"label": "fal.ai", "key_url": "https://fal.ai/dashboard/keys"},
    "heygen": {"label": "HeyGen", "key_url": "https://app.heygen.com/settings"},
    "comfyui": {"label": "ComfyUI (local)", "key_url": "https://www.comfy.org/", "needs_key": False,
                "use": "images on your GPU",
                "has_url": True},
    "a1111": {"label": "AUTOMATIC1111 / Forge (local)", "needs_key": False, "has_url": True,
              "use": "images on your GPU",
              "key_url": "https://github.com/lllyasviel/stable-diffusion-webui-forge"},
    # free: work without a key once turned on; a free key only raises the limits
    "pollinations": {"label": "Pollinations (free, no sign-in)", "key_url": "https://enter.pollinations.ai/keys",
                     "needs_key": False, "optional_key": True, "toggle": True},
    "horde": {"label": "AI Horde (free, crowdsourced)", "key_url": "https://aihorde.net/register",
              "needs_key": False, "optional_key": True, "toggle": True},
    "omniroute": {"label": "OmniRoute (free AI gateway, local)", "needs_key": False, "optional_key": True,
                  "has_url": True, "key_url": "https://github.com/diegosouzapw/OmniRoute"},
    "kilo": {"label": "Kilo Gateway (free, no sign-in)", "needs_key": False, "optional_key": True,
             "key_url": "https://app.kilo.ai"},
    "ovhcloud": {"label": "OVHcloud AI Endpoints (free, no sign-in)", "needs_key": False, "optional_key": True,
                 "key_url": "https://endpoints.ai.cloud.ovh.net"},
    "llm7": {"label": "LLM7 (free, no sign-in)", "needs_key": False, "optional_key": True,
             "key_url": "https://token.llm7.io"},
}


LOCAL_DEFAULTS = {"ollama": "http://127.0.0.1:11434/v1", "comfyui": "http://127.0.0.1:8188",
                  "a1111": "http://127.0.0.1:7860", "omniroute": "http://localhost:20128/v1"}


def service_info(name, reported=None):
    info = {"label": name, "needs_key": True}
    info.update(SERVICE_INFO.get(name, {}))
    info.update({k: v for k, v in (reported or {}).items() if v is not None})
    info.setdefault("setup", setup_kind(name, info))
    if info["setup"] == "local":
        info.setdefault("default_url", LOCAL_DEFAULTS.get(name))
    return info


def setup_kind(name, info):
    """key | browser | local | free | toggle - how this service is set up (the service reports it; this is
    the same rule for a service that has not reported yet)."""
    if info.get("browser_sign_in"):
        return "browser"
    if info.get("toggle"):
        return "toggle"
    if name in LOCAL_DEFAULTS:
        return "local"
    if info.get("optional_key"):
        return "free"
    return "key"


def is_ready(info):
    """Usable right now: signed in, set up, turned on - or free without an account."""
    if "ready" in info:
        return bool(info["ready"])
    return info.get("setup") == "free" or bool(info.get("signed_in"))


def state_text(info):
    """A few words on where this service stands, for the Services list and the chat panel."""
    kind, signed = info.get("setup"), info.get("signed_in")
    if kind == "local":
        return (i18n("set up: %s") % (info.get("base_url") or info.get("default_url") or "")) if signed \
            else i18n("not set up")
    if kind == "free":
        return i18n("ready, no account") + (" · " + i18n("your key") if info.get("has_key") or signed else "")
    if kind == "toggle":
        return (i18n("on") + (" · " + i18n("your key") if info.get("has_key") else "")) if signed else i18n("off")
    return i18n("signed in") if signed else i18n("not signed in")


SHORT = {"auto": "AI", "gemini": "Gemini", "anthropic": "Claude", "openai": "GPT", "openrouter": "AI", "ollama": "AI"}
_SERVICE = {"proc": None}


QUIET_PATHS = ("/health", "/events", "/oauth/status")  # polled every second or so: not worth a log line


def trace(source, message):
    extension.trace(source, message)


def _request(method, path, body=None, timeout=10):
    """Call the assistant service (worker threads only)."""
    loud = not path.startswith(QUIET_PATHS)
    if loud:
        safe = {k: v for k, v in (body or {}).items() if k not in ("key", "text")}
        if body and "text" in body:
            safe["text"] = str(body["text"])[:200]
        trace("service", "%s %s %s" % (method, path, json.dumps(safe) if safe else ""))
    try:
        return _send_request(method, path, body, timeout)
    except Exception as e:
        if loud:
            trace("service", "✗ %s %s: %s" % (method, path, e))
        raise


def _send_request(method, path, body, timeout):
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
    """Make sure the current `kritapilot --agent` is running; start it with the panel's server command if needed.
    A running service of another version (left over from before an update) is asked to stop and replaced."""
    try:
        health = _request("GET", "/health", timeout=2)
        if _SERVICE.get("checked") or health.get("version") == _plugin_version():
            return health
        _SERVICE["checked"] = True  # replace it once; if the installed server is itself older, keep going
        trace("service", "replacing assistant service %s (plugin is %s)" % (health.get("version"), _plugin_version()))
        try:
            _request("POST", "/shutdown", {}, timeout=3)
        except (OSError, RuntimeError):
            pass
        for _ in range(20):
            time.sleep(0.25)
            try:
                _request("GET", "/health", timeout=1)
            except (OSError, RuntimeError):
                break
    except (OSError, RuntimeError):
        pass
    proc = _SERVICE["proc"]
    if proc is None or proc.poll() is not None:
        args = extension.server_args() + ["--agent", "--agent-port", str(AGENT_PORT)]
        cmd = args[0]
        env = dict(os.environ, KRITA_MCP_PORT=str(extension.bridge_port()), KRITA_MCP_HOME=str(protocol.config_dir()))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        trace("service", "starting the assistant service: %s" % " ".join(args))
        try:
            _SERVICE["proc"] = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError as e:
            raise RuntimeError("could not start %r (%s). Install the server with: pip install kritapilot "
                               "- or set the server command in the AI Bridge (MCP) panel." % (cmd, e)) from None
    for _ in range(40):
        time.sleep(0.25)
        try:
            return _request("GET", "/health", timeout=2)
        except (OSError, RuntimeError):
            if _SERVICE["proc"].poll() is not None:
                break
    trace("service", "✗ the assistant service did not start (exit code %s)" % _SERVICE["proc"].poll())
    raise RuntimeError("the assistant service did not start (run `kritapilot --agent` in a terminal to see why)")


def _plugin_version():
    from .handlers import BRIDGE_VERSION
    return BRIDGE_VERSION


class SignInDialog(QDialog):
    """Set up one service the way that service works: an API key (with a button to the key page), a browser
    sign-in, a local server's address (with a connection test, no key), or nothing at all for the free ones
    (only an optional key)."""

    def __init__(self, parent, service, info, docker=None):
        super().__init__(parent)
        info = service_info(service, info)  # fills in the setup kind and usual address if not reported
        self.service, self.info, self.browser_requested = service, info, False
        self.docker = docker or getattr(parent, "docker", None) or parent
        kind = self.kind = info.get("setup") or setup_kind(service, info)
        label = info.get("label", service)
        self.setWindowTitle({"local": i18n("Set up %s"), "free": i18n("%s: key (optional)"),
                             "toggle": i18n("%s: key (optional)")}.get(kind, i18n("Sign in to %s")) % label)
        self.setMinimumWidth(460)
        outer = QVBoxLayout(self)
        outer.setSpacing(10)
        intro = self.intro = QLabel(self._intro(kind, info))
        intro.setTextFormat(_RICH)
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        intro.setMinimumWidth(440)  # with a fixed-size dialog, this sets its width
        outer.addWidget(intro)
        form = self.form = QFormLayout()
        form.setHorizontalSpacing(10)
        outer.addLayout(form)
        # every row label and action button is kept here (Python references, and what tests check: asking Qt for
        # a dialog's children is unreliable under PyQt5 on Windows)
        self.row_labels, self.actions = [], []
        self.key = QLineEdit()
        self.key.setEchoMode(_PASSWORD)
        self.base = QLineEdit(info.get("base_url") or "")
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setTextFormat(_RICH)
        self.test_btn = None
        if kind == "browser":  # real OAuth: approve in the browser, nothing to copy
            btn = self._action(i18n("Sign in with browser"), self._browser)
            btn.setDefault(True)
            outer.insertWidget(1, btn)
            self.key.setPlaceholderText(i18n("or paste a key here"))
            self._row(i18n("API key"), self.key)
        elif kind == "local":
            self.base.setText(info.get("base_url") or info.get("default_url") or "")
            self.base.setPlaceholderText(info.get("default_url") or "")
            row = self.address_row = QHBoxLayout()
            row.addWidget(self.base, 1)
            self.test_btn = self._action(i18n("Test connection"), self._test)
            row.addWidget(self.test_btn)
            self._row(i18n("Address"), row)
            if service == "ollama":
                self.tip = QLabel(i18n("LM Studio: http://127.0.0.1:1234/v1"))
                self.tip.setStyleSheet("color:#999")
                form.addRow("", self.tip)
            if info.get("optional_key"):  # a gateway (OmniRoute) may be protected by a key of its own
                self.key.setPlaceholderText(i18n("only if your gateway asks for one"))
                self._row(i18n("Key"), self.key)
        else:
            if kind in ("free", "toggle"):
                self.key.setPlaceholderText(i18n("optional"))
            self._row(i18n("API key"), self.key)
            if info.get("key_url"):
                btn = self._action(i18n("Get a free key in the browser") if kind in ("free", "toggle")
                                   else i18n("Get a key in the browser"), self._open_key_page)
                btn.setToolTip(i18n("Opens the provider's key page; copy the key there and it appears here"))
                self.key_page_row = _left(btn)
                form.addRow("", self.key_page_row)
            if service == "openai":  # any OpenAI-compatible server
                self.base.setPlaceholderText(i18n("optional: another OpenAI-compatible address"))
                self._row(i18n("Address"), self.base)
        self.hint.hide()  # shown when there is something to say (key found, test result)
        outer.addWidget(self.hint)
        self.clip_timer = QTimer(self)
        self.clip_timer.timeout.connect(self._check_clipboard)
        self.clip_start = None
        # our own buttons (not Qt's standard OK, whose text and visibility Qt manages per platform)
        buttons = self.button_box = QDialogButtonBox()
        self.save_btn = QPushButton(i18n("Save") if kind == "local" else i18n("Save key"))
        self.save_btn.setDefault(kind != "browser")
        self.cancel_btn = QPushButton(i18n("Cancel"))
        buttons.addButton(self.save_btn, _enum(QDialogButtonBox, "ButtonRole", "AcceptRole"))
        buttons.addButton(self.cancel_btn, _enum(QDialogButtonBox, "ButtonRole", "RejectRole"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        outer.setSizeConstraint(_FIXED)  # exactly as tall as its content: no gap above the buttons

    @staticmethod
    def _intro(kind, info):
        use = html.escape(info.get("use", "") or info.get("label", ""))
        where = i18n("Stored only on this computer (~/.krita-mcp); AI apps connected through MCP use it too.")
        link = ('<a href="%s">%s</a>' % (html.escape(info["key_url"]), i18n("Download")) if info.get("key_url")
                else "")
        return {
            "key": "%s<br><span style='color:#999'>%s</span>" % (
                i18n("Used for: %s. Get an API key on the provider's site and paste it below.") % use, where),
            "browser": "%s<br><span style='color:#999'>%s</span>" % (
                i18n("Used for: %s. Sign in with your browser - nothing to copy.") % use, where),
            "local": "%s<br><span style='color:#999'>%s</span> %s" % (
                i18n("Used for: %s.") % use, i18n("It runs on this computer: no account, no key. Start it, check "
                                                  "the address and click Test connection."), link),
            "free": "<b>%s</b> %s<br><span style='color:#999'>%s</span>" % (
                i18n("Ready to use - no account needed."), i18n("A key is optional."),
                use[:1].upper() + use[1:] + "."),
            "toggle": "<b>%s</b> %s<br><span style='color:#999'>%s</span>" % (
                i18n("Free, no account."), i18n("A key is optional."), use[:1].upper() + use[1:] + "."),
        }[kind]

    def _test(self):
        url = self.base.text().strip()
        self.test_btn.setEnabled(False)
        self._hint(i18n("Testing…"))

        def done(r):
            self.test_btn.setEnabled(True)
            ok = r.get("ok")
            self._hint("<span style='color:%s'>%s %s</span>" % (
                "#4a4" if ok else "#e66", "✓" if ok else "✗", html.escape(r.get("detail") or "")))
        self.docker._background(lambda: _ensure_service() and _request(
            "POST", "/check", {"provider": self.service, "base_url": url}, timeout=20), done)

    def _row(self, text, field):
        label = QLabel(text)
        self.row_labels.append(label)
        self.form.addRow(label, field)

    def _action(self, text, slot):
        btn = QPushButton(text)
        btn.clicked.connect(lambda: slot())  # no checked flag passed on
        self.actions.append(btn)
        return btn

    def _hint(self, text):
        self.hint.setText(text)
        self.hint.setVisible(bool(text))

    def _browser(self):
        self.browser_requested = True
        self.accept()

    def _open_key_page(self):
        self.clip_start = QGuiApplication.clipboard().text()
        QDesktopServices.openUrl(QUrl(self.info["key_url"]))
        self._hint(i18n("Create or copy a key in your browser - it will appear here automatically."))
        self.clip_timer.start(600)

    def _check_clipboard(self):
        text = (QGuiApplication.clipboard().text() or "").strip()
        if not text or text == self.clip_start or len(text) > 300:
            return
        found = find_key(self.service, text)
        if found:
            self.key.setText(found)
            self._hint("✓ " + i18n("Key found in the clipboard - click Save key to check and save it."))
            self.clip_timer.stop()


def _left(widget):
    """A button at its natural width, at the start of its form row (not stretched across the dialog)."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(widget)
    row.addStretch(1)
    return box


def find_key(service, text):
    """The API key in `text` if it looks like one of `service`'s keys."""
    pattern = KEY_PATTERNS.get(service)
    if not pattern:
        return None
    m = re.search(r"(?<![0-9A-Za-z_\-])(%s)(?![0-9A-Za-z_\-])" % pattern, text)
    return m.group(1) if m else None


class ServicesDialog(QDialog):
    """Every AI service in one place: chat models, image / video generators, local servers. The list scrolls
    and can be searched; each service has Sign in, or Change and Sign out once signed in."""

    GROUPS = [("chat", "Chat models"), ("media", "Images, video and more"), ("local", "On this computer")]

    def __init__(self, docker):
        super().__init__(docker)
        self.docker = docker
        self.setWindowTitle(i18n("AI services"))
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        note = QLabel(i18n("Sign in once here: the AI Chat panel and AI apps connected through MCP (Claude Desktop, "
                           "Gemini CLI, Cursor, ...) both use these services."))
        note.setWordWrap(True)
        lay.addWidget(note)
        self.search = QLineEdit()
        self.search.setPlaceholderText(i18n("Search services… (e.g. free, images, video, local)"))
        self.search.textChanged.connect(lambda _t: self.refresh())
        lay.addWidget(self.search)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        lay.addWidget(self.scroll, 1)
        self.summary = QLabel()
        close = QPushButton(i18n("Close"))
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(close)
        lay.addLayout(bottom)
        screen = QGuiApplication.primaryScreen()
        height = int(screen.availableGeometry().height() * 0.75) if screen is not None else 640
        self.resize(600, max(420, min(760, height)))
        self.refresh()

    @staticmethod
    def group(info):
        if info.get("setup") == "local":
            return "local"
        return "chat" if info.get("chat") else "media"

    @staticmethod
    def buttons(info):
        """(label, action) pairs: what can be done with this service now - following how it is set up."""
        kind, signed, has_key = info.get("setup"), info.get("signed_in"), info.get("has_key")
        if kind == "toggle":  # free image services: off until the user turns them on
            if not signed:
                return [(i18n("Turn on"), "on")]
            return [(i18n("Change key") if has_key else i18n("Add key"), "signin"), (i18n("Turn off"), "signout")]
        if kind == "free":  # works without anything; a key only raises the limits
            return ([(i18n("Change key"), "signin"), (i18n("Remove key"), "signout")] if signed or has_key
                    else [(i18n("Add key"), "signin")])
        if kind == "local":
            return [(i18n("Change"), "signin"), (i18n("Forget"), "signout")] if signed \
                else [(i18n("Set up"), "signin")]
        if kind == "browser":
            return [(i18n("Sign out"), "signout")] if signed else [(i18n("Sign in"), "signin")]
        return [(i18n("Change key"), "signin"), (i18n("Sign out"), "signout")] if signed \
            else [(i18n("Sign in"), "signin")]

    def refresh(self):
        bar = self.scroll.verticalScrollBar()
        keep = bar.value()
        want = self.search.text().strip().lower()
        rows = QWidget()
        grid = QGridLayout(rows)
        grid.setColumnStretch(0, 1)  # names and descriptions get the room; buttons stay their own size
        grid.setVerticalSpacing(6)
        line = 0
        shown = 0
        items = sorted(((n, service_info(n, i)) for n, i in self.docker.providers.items() if n != "auto"),
                       key=lambda kv: (not is_ready(kv[1]), str(kv[1].get("label", kv[0])).lower()))
        for key, title in self.GROUPS:
            members = []
            for name, info in items:
                words = "%s %s %s %s %s" % (name, info.get("label", ""), info.get("use", ""),
                                            "free" if info.get("free") or info.get("optional_key") else "",
                                            "local" if info.get("setup") == "local" else "")
                if self.group(info) == key and (not want or want in words.lower()):
                    members.append((name, info))
            if not members:
                continue
            head = QLabel("<b>%s</b>" % html.escape(i18n(title)))
            head.setTextFormat(_RICH)
            if line:
                head.setContentsMargins(0, 10, 0, 0)
            grid.addWidget(head, line, 0, 1, 2)
            line += 1
            for name, info in members:
                shown += 1
                ready = is_ready(info)
                mark = "<span style='color:#4a4'>✓</span>" if ready else "<span style='color:#999'>○</span>"
                label = QLabel("%s <b>%s</b> <span style='color:%s'>· %s</span><br>"
                               "<span style='color:#999'>%s</span>" % (
                                   mark, html.escape(info.get("label", name)), "#4a4" if ready else "#999",
                                   html.escape(state_text(info)), html.escape(info.get("use", ""))))
                label.setTextFormat(_RICH)
                label.setWordWrap(True)
                cell = QWidget()
                row = QHBoxLayout(cell)
                row.setContentsMargins(0, 0, 0, 0)
                row.addStretch(1)
                for text, action in self.buttons(info):
                    btn = QPushButton(text)
                    btn.setObjectName("%s:%s" % (name, action))
                    btn.clicked.connect(lambda _=False, n=name, a=action: self.docker.sign_in_service(n, self, a))
                    row.addWidget(btn)
                grid.addWidget(label, line, 0)
                grid.addWidget(cell, line, 1, _TOP)
                line += 1
        if not shown:
            grid.addWidget(QLabel(i18n("No service matches.")), 0, 0)
        grid.setRowStretch(line + 1, 1)  # a short (searched) list stays at the top
        old = self.scroll.takeWidget()
        self.scroll.setWidget(rows)
        if old is not None:
            old.deleteLater()
        bar.setValue(keep)
        real = {n: i for n, i in self.docker.providers.items() if n != "auto"}
        ready = sum(1 for n, i in real.items() if is_ready(service_info(n, i)))
        self.summary.setText(i18n("%d services · %d ready to use") % (len(real), ready))


class SkillsDialog(QDialog):
    """The skills the assistant can use. It finds them itself (fresh for every message) and loads one only when
    a request needs it, so having many costs nothing."""

    def __init__(self, docker, listing):
        super().__init__(docker)
        self.setWindowTitle(i18n("AI skills"))
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        intro = QLabel(i18n("Skills are step-by-step know-how (a folder with a SKILL.md). The assistant picks the "
                            "ones that fit your request by itself and reads them only then - and AI apps connected "
                            "through MCP can use them too (find_skills, use_skill)."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        found = listing.get("skills") or []
        text = "".join("<p><b>%s</b> <span style='color:#999'>(%s)</span><br>%s</p>" % (
            html.escape(sk.get("name", "")), html.escape(sk.get("source", "")),
            html.escape(sk.get("description", ""))) for sk in found) or i18n("No skills found.")
        box = QTextBrowser()
        box.setHtml(text)
        lay.addWidget(box, 1)
        how = QLabel(i18n("Add skills: copy a skill folder (one with a SKILL.md) into your skills folder - it "
                          "appears here at once. Skills made for coding assistants (~/.claude/skills) are not used "
                          "here."))
        how.setTextFormat(_RICH)
        how.setWordWrap(True)
        lay.addWidget(how)
        row = QHBoxLayout()
        self.user_dir = listing.get("user_dir")
        open_btn = QPushButton(i18n("Open my skills folder"))
        open_btn.clicked.connect(self._open_folder)
        online = QPushButton(i18n("Find skills online"))
        online.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SKILLS_SITE)))
        close = QPushButton(i18n("Close"))
        close.clicked.connect(self.accept)
        row.addWidget(open_btn)
        row.addWidget(online)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)

    def _open_folder(self):
        if not self.user_dir:
            return
        try:
            os.makedirs(self.user_dir, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.user_dir))


class ChatInput(QPlainTextEdit):
    """The message box: Enter sends (or queues), Shift+Enter adds a line, Up in an empty box recalls the last
    message - as in a coding assistant's chat."""

    def __init__(self, docker):
        super().__init__()
        self.docker = docker

    def keyPressEvent(self, e):
        key, mods = e.key(), e.modifiers()
        if key in (_KEY_RETURN, _KEY_ENTER):
            if mods & _SHIFT:  # a real line break (Qt's own would be a soft U+2028 separator)
                self.insertPlainText("\n")
            else:
                self.docker._send()
            return
        if key == _KEY_UP and not self.toPlainText():
            self.docker.recall()
            return
        super().keyPressEvent(e)


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
        if extension.setting("auto_default", "") != "1":  # once: everyone starts on Auto (then their choice stays)
            extension.set_setting("auto_default", "1")
            extension.set_setting("chat_provider", "auto")
        idx = [k for k, _ in PROVIDERS].index(extension.setting("chat_provider", "auto")) \
            if extension.setting("chat_provider", "auto") in dict(PROVIDERS) else 0
        saved = extension.setting("chat_provider", "auto")
        if saved not in dict(PROVIDERS):  # e.g. Kilo: kept until the service sends the full list
            self.provider.addItem(service_info(saved).get("label", saved), saved)
            idx = self.provider.count() - 1
        self.provider.setCurrentIndex(idx)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.signin = QPushButton(i18n("Sign in"))
        self.signin.clicked.connect(lambda: self.sign_in_service(self._key()))
        services = QPushButton(i18n("Services…"))
        services.setToolTip(i18n("Sign in to image, video and chat services, or turn on the free ones"))
        services.clicked.connect(self._services)
        skills_btn = QPushButton(i18n("Skills…"))
        skills_btn.setToolTip(i18n("Know-how the assistant loads when a request needs it"))
        skills_btn.clicked.connect(self._skills)
        logs_btn = QPushButton(i18n("Logs…"))
        logs_btn.setToolTip(i18n("Follow what the assistant and Krita are doing, live, to find out what went wrong"))
        logs_btn.clicked.connect(lambda: logview.show(self, "logs/assistant.log"))
        top.addWidget(self.provider, 1)
        top.addWidget(self.signin)
        top.addWidget(services)
        top.addWidget(skills_btn)
        top.addWidget(logs_btn)
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
        self.refresh_models.setFixedWidth(self.refresh_models.sizeHint().height() + 6)
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

        self.status = QLabel()  # "Working · 0:12 · create_artwork" while the AI works
        self.status.setStyleSheet("color:#999")
        lay.addWidget(self.status)

        # messages typed while the AI works wait here and go out in order, like a coding assistant's chat
        self.queue = []
        self.queue_box = QWidget()
        qlay = QVBoxLayout(self.queue_box)
        qlay.setContentsMargins(0, 0, 0, 0)
        qlay.setSpacing(2)
        qhead = QHBoxLayout()
        self.queue_label = QLabel()
        self.queue_label.setStyleSheet("color:#999")
        clear_queue = QPushButton(i18n("Clear"))
        clear_queue.setToolTip(i18n("Drop the queued messages"))
        clear_queue.clicked.connect(self._clear_queue)
        qhead.addWidget(self.queue_label, 1)
        qhead.addWidget(clear_queue)
        qlay.addLayout(qhead)
        self.queue_list = QListWidget()
        self.queue_list.setToolTip(i18n("Double-click a queued message to take it back and edit it"))
        self.queue_list.itemDoubleClicked.connect(self._unqueue)
        qlay.addWidget(self.queue_list)
        lay.addWidget(self.queue_box)
        self.queue_box.hide()

        self.input = ChatInput(self)
        self.input.setPlaceholderText(i18n("Message the AI - Enter to send, Shift+Enter for a new line"))
        self.input.setMaximumHeight(90)
        lay.addWidget(self.input)
        QShortcut(QKeySequence("Ctrl+Return"), self.input, activated=lambda: self._send())
        self.sent_texts = []  # Up in an empty box brings back the last ones

        buttons = QHBoxLayout()
        self.send_btn = QPushButton(i18n("Send"))
        self.send_btn.clicked.connect(lambda: self._send())  # not _send(checked): clicked passes a bool
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
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._show_status)
        self._set_busy(False)
        # look for a running service now; once the panel is on screen, start it if needed, so the provider list
        # shows what is set up (a hidden panel starts nothing)
        self._background(lambda: _request("GET", "/health", timeout=2), self._got_health, quiet=True)
        self._started_service = False
        self.visibilityChanged.connect(self._shown)

    def _shown(self, visible):
        if visible and not self._started_service:
            self._started_service = True
            self._background(_ensure_service, self._got_health, quiet=True)

    # ---------------------------------------------------------------- plumbing
    def canvasChanged(self, canvas):
        pass

    def _key(self):
        key = self.provider.currentData()
        if key is None or key == self.MANAGE or str(key).startswith(self.SETUP):
            return getattr(self, "_last_key", None) or "auto"
        return key

    def _background(self, fn, done, *args, quiet=False):
        def work():
            try:
                self.inbox.put((done, fn(*args)))
            except Exception as e:  # noqa: BLE001 - shown in the chat
                if not quiet:
                    self.inbox.put((self._error, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _drain(self):
        """Hand finished background work to its callback. Each callback runs on its own (not inside this timer):
        a callback that opens a dialog must not block the ones after it - Qt does not fire a timer again while
        its handler is still running, so everything else would wait until the dialog closed."""
        while True:
            try:
                cb, value = self.inbox.get_nowait()
            except queue.Empty:
                return
            QTimer.singleShot(0, lambda cb=cb, value=value: self._run(cb, value))

    def _run(self, cb, value):
        try:
            cb(value)
        except Exception:  # noqa: BLE001 - logged and shown, never lost
            import traceback
            trace("panel", "✗ panel error: " + traceback.format_exc().replace("\n", " | "))
            self._error(i18n("Something went wrong in the panel; see Logs… for details."))

    def _append(self, html_text):
        self.log.append(html_text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _error(self, msg):
        trace("panel", "✗ shown to the user: %s" % msg)
        self._append("<p style='color:#e66'>%s</p>" % html.escape(msg))
        self._set_busy(False)

    def _set_busy(self, busy):
        was = getattr(self, "busy", False)
        self.busy = busy
        self.send_btn.setEnabled(True)  # while busy, Send queues the message
        self.stop_btn.setEnabled(busy)
        self.send_btn.setText(i18n("Queue") if busy else i18n("Send"))
        self.send_btn.setToolTip(i18n("The AI is working: your message waits and is sent when it is done")
                                 if busy else "")
        if busy and not was:
            self._busy_since, self._doing = time.monotonic(), ""
            self.status_timer.start(1000)
        elif not busy:
            self.status_timer.stop()
            self.status.setText("")
        self._show_status()
        if was and not busy and self.queue and not getattr(self, "_hold_queue", False):
            QTimer.singleShot(0, self._send_next)  # the next queued message, now that the AI is free

    def _show_status(self):
        if not self.busy:
            return
        secs = int(time.monotonic() - getattr(self, "_busy_since", time.monotonic()))
        self.status.setText("● %s · %d:%02d%s" % (i18n("Working"), secs // 60, secs % 60,
                                                  (" · " + self._doing) if getattr(self, "_doing", "") else ""))

    # ---------------------------------------------------------------- the queue
    def _sync_queue(self):
        self.queue_list.clear()
        for text in self.queue:
            self.queue_list.addItem(text.replace("\n", " ")[:200])
        self.queue_label.setText(i18n("Queued (%d) - sent when the AI is done") % len(self.queue))
        self.queue_box.setVisible(bool(self.queue))
        if self.queue:  # as tall as its rows (up to 4), no empty space
            rows = min(len(self.queue), 4)
            self.queue_list.setFixedHeight(rows * max(self.queue_list.sizeHintForRow(0), 16)
                                           + 2 * self.queue_list.frameWidth() + 2)

    def _send_next(self):
        if self.queue and not self.busy:
            self._send(self.queue.pop(0))
            self._sync_queue()

    def _unqueue(self, item):
        """Take a queued message back into the box to edit it."""
        i = self.queue_list.row(item)
        if 0 <= i < len(self.queue):
            text = self.queue.pop(i)
            current = self.input.toPlainText().strip()
            self.input.setPlainText((current + "\n" + text) if current else text)
            self._sync_queue()

    def _clear_queue(self):
        trace("panel", "queue cleared (%d)" % len(self.queue))
        self.queue = []
        self._sync_queue()

    def recall(self):
        """Up in an empty box: the last message sent."""
        if self.sent_texts and not self.input.toPlainText():
            self.input.setPlainText(self.sent_texts[-1])
            self.input.moveCursor(_END)

    # ---------------------------------------------------------------- sign in
    def _got_health(self, health):
        self.providers = (health or {}).get("providers", {})
        self._sync_providers()
        self._show_account()
        self._refresh_models()

    MANAGE = "__manage__"
    SETUP = "__setup__:"

    def _sync_providers(self):
        """The provider menu, from the assistant service (new providers appear without a plugin update):
        Auto first, then the providers you can use now (signed in, set up, free) - always shown - then every
        other one, marked: picking one of those opens its setup and selects it once it is ready."""
        if not self.providers:
            return
        current = self._key()
        chats = [(k, service_info(k, v)) for k, v in self.providers.items() if v.get("chat")]

        def usable(i):
            return is_ready(i) or i.get("setup") == "toggle"  # toggle (Pollinations): its chat is free

        def order(kv):  # yours first, then the free ones, by name
            return (kv[1].get("setup") in ("free", "toggle"), str(kv[1].get("label", kv[0])).lower())
        auto = [(k, i) for k, i in chats if k == "auto"]
        ready = sorted(((k, i) for k, i in chats if k != "auto" and usable(i)), key=order)
        others = sorted(((k, i) for k, i in chats if k != "auto" and not usable(i)),
                        key=lambda kv: str(kv[1].get("label", kv[0])).lower())
        groups = [[(k, i.get("label", k)) for k, i in auto + ready],
                  [(self.SETUP + k, i18n("%s - set up…") % i.get("label", k)) for k, i in others],
                  [(self.MANAGE, i18n("Manage services…"))]]
        wanted = [item for g in groups for item in g]
        if [(self.provider.itemData(i), self.provider.itemText(i)) for i in range(self.provider.count())
                if self.provider.itemData(i) is not None] == wanted:
            return
        self.provider.blockSignals(True)
        try:
            self.provider.clear()
            for n, group in enumerate(g for g in groups if g):
                if n:
                    self.provider.insertSeparator(self.provider.count())
                for key, label in group:
                    self.provider.addItem(label, key)
                    if key == self.MANAGE or key.startswith(self.SETUP):  # not usable yet: shown dimmed
                        self.provider.setItemData(self.provider.count() - 1, QColor("#888"), _FOREGROUND)
            idx = self.provider.findData(current)
            if idx < 0:
                idx = self.provider.findData(self.SETUP + current)
            if idx < 0 or str(self.provider.itemData(idx)).startswith(self.SETUP):
                idx = 0  # the old choice cannot be used now: Auto (or the first usable one)
            self.provider.setCurrentIndex(idx)
        finally:
            self.provider.blockSignals(False)
        self._last_key = self._key()
        if self._key() != current:
            self._provider_changed()
        else:
            self._show_account()

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
        signed, kind = info.get("signed_in"), info.get("setup")
        self.account.setText(("✓ " if is_ready(info) else "") + state_text(info))
        if kind == "auto":
            order = [service_info(n, self.providers.get(n)).get("label", n) for n in info.get("order") or []]
            self.account.setText(i18n("%d providers") % len(order) if order else i18n("nothing set up"))
            self.account.setToolTip(i18n("In turn, switching when one runs out of credit or fails:\n") +
                                    "\n".join("%d. %s" % (n + 1, x) for n, x in enumerate(order)))
        self.signin.setVisible(kind != "auto")  # Auto has nothing to sign in to (Services... is right beside it)
        if kind == "local":
            self.signin.setText(i18n("Change") if signed else i18n("Set up"))
        elif kind in ("free", "toggle"):
            self.signin.setText(i18n("Change key") if info.get("has_key") else i18n("Add key"))
        elif kind != "auto":
            self.signin.setText(i18n("Sign out") if signed else i18n("Sign in"))
        if info.get("default_model"):
            self.model.lineEdit().setPlaceholderText(i18n("Auto: %s") % info["default_model"])

    def _provider_changed(self):
        data = self.provider.currentData()
        if data == self.MANAGE or str(data).startswith(self.SETUP):
            # not usable yet: go back to the last choice, then open Services or that provider's setup
            idx = self.provider.findData(getattr(self, "_last_key", None))
            self.provider.blockSignals(True)
            self.provider.setCurrentIndex(max(idx, 0))
            self.provider.blockSignals(False)
            if data == self.MANAGE:
                QTimer.singleShot(0, self._services)
            else:
                name = data[len(self.SETUP):]
                self._select_when_ready = name  # chosen as soon as it is signed in / set up
                QTimer.singleShot(0, lambda: self.sign_in_service(name, action="signin"))
            return
        self._last_key = self._key()
        trace("panel", "chat provider: %s" % self._key())
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

    def _skills(self):
        def fetch():
            _ensure_service()
            return _request("GET", "/skills", timeout=20)
        self._background(fetch, self._show_skills)

    def _show_skills(self, r):
        dlg = SkillsDialog(self, r)
        dlg.exec()

    def sign_in_service(self, name, parent=None, action=None):
        if name == "auto":  # Auto uses whatever is set up: manage that in Services
            return self._services()
        """action: "signin" (set up / sign in, also to change a key or address), "signout" (sign out, remove the
        key, forget the address, turn off), "on" (turn a free service on), or None: whichever fits now (the
        panel's own button)."""
        info = service_info(name, self.providers.get(name))
        if action is None:
            removable = info.get("signed_in") and info.get("setup") in ("key", "browser")
            action = "signout" if removable else "signin"
        trace("panel", "%s: %s" % (name, action))
        if action == "signout":
            self._background(lambda: _ensure_service() and _request("POST", "/signout", {"provider": name}),
                             lambda r: self._signed(r, name))
            return
        if action == "on":  # nothing to ask: free and anonymous
            body = {"provider": name, "key": "", "verify": False}
            self._background(lambda: _ensure_service() and _request("POST", "/signin", body),
                             lambda r: self._signed(r, name))
            return
        dlg = SignInDialog(parent or self, name, info, docker=self)
        if not dlg.exec():
            return
        if dlg.browser_requested:
            return self.browser_sign_in(name)
        body = {"provider": name, "key": dlg.key.text().strip(), "base_url": dlg.base.text().strip() or None}
        kind = info.get("setup")
        if kind in ("key", "browser") and not body["key"]:
            return self._error(i18n("No key entered."))
        if kind == "local" and not body["base_url"]:
            return self._error(i18n("No address entered."))
        if kind in ("free", "toggle") and not body["key"]:
            if kind == "free" or info.get("signed_in"):
                return  # nothing to save: it already works without a key
        self._background(lambda: _ensure_service() and _request("POST", "/signin", body),
                         lambda r: self._signed(r, name))

    def browser_sign_in(self, name):
        """OAuth in the user's browser; the assistant service receives the redirect and saves the key."""
        trace("panel", "browser sign-in started: %s" % name)
        def opened(flow):
            QDesktopServices.openUrl(QUrl(flow["url"]))
            self._append("<p style='color:#8a8'>%s</p>" % html.escape(
                i18n("Your browser opened: approve Krita there. This panel updates when you are signed in.")))
            # one sign-in is watched at a time: a new one replaces the old (clicking again must not
            # leave a second timer reporting "signed in" every second)
            self.oauth_id, self.oauth_name, self._oauth_polling = flow["id"], name, False
            self._oauth_until = time.monotonic() + OAUTH_WAIT
            if getattr(self, "oauth_timer", None) is None:
                self.oauth_timer = QTimer(self)
                self.oauth_timer.timeout.connect(self._oauth_poll)
            self.oauth_timer.start(1000)
        self._background(lambda: _ensure_service() and _request("POST", "/oauth/start", {"provider": name}), opened)

    def _oauth_stop(self):
        self.oauth_id = None
        if getattr(self, "oauth_timer", None) is not None:
            self.oauth_timer.stop()

    def _oauth_poll(self):
        fid, name = getattr(self, "oauth_id", None), getattr(self, "oauth_name", None)
        if not fid:
            return self._oauth_stop()
        if time.monotonic() > getattr(self, "_oauth_until", 0):
            self._oauth_stop()
            return self._error(i18n("Browser sign-in timed out. Try again."))
        if getattr(self, "_oauth_polling", False):
            return
        self._oauth_polling = True

        def ask():
            try:
                return _request("GET", "/oauth/status?id=%s" % fid, timeout=10)
            except Exception:  # noqa: BLE001 - the service may be restarting: ask again next tick
                return {"status": "pending"}

        def got(r):
            self._oauth_polling = False
            if fid != self.oauth_id or r.get("status") == "pending":
                return  # an old sign-in, or not approved yet
            self._oauth_stop()
            if r.get("status") == "done":
                self._background(lambda: _request("GET", "/health"), lambda h: self._signed(h, name))
            else:
                self._error(i18n("Browser sign-in failed: %s") % (r.get("error") or "?"))
        self._background(ask, got)

    def _signed(self, result, name=None):
        self.providers = (result or {}).get("providers", self.providers)
        self._sync_providers()
        wanted = getattr(self, "_select_when_ready", None)
        if wanted and name == wanted:
            self._select_when_ready = None
            idx = self.provider.findData(wanted)
            if idx >= 0 and idx != self.provider.currentIndex():
                self.provider.setCurrentIndex(idx)  # -> _provider_changed
        self._show_account()
        self._refresh_models()
        if getattr(self, "services_dialog", None) is not None:
            self.services_dialog.refresh()
        if name:  # name the service that changed, not the selected chat provider
            info = service_info(name, self.providers.get(name))
            state = "✓ " + i18n("signed in") if info.get("signed_in") else i18n("not signed in")
            text = "%s: %s" % (info.get("label") or name, state)
        else:
            text = "%s: %s" % (self.provider.currentText(), self.account.text())
        trace("panel", text)
        self._append("<p style='color:#8a8'>%s</p>" % html.escape(text))

    # ---------------------------------------------------------------- chat
    def _send(self, text=None):
        """Send the box's text - or `text`, a queued message. Anything else (a signal's checked flag) is
        ignored."""
        if not isinstance(text, str):
            text = None
        queued = text is not None
        text = (text if queued else self.input.toPlainText()).strip()
        if not text:
            return
        if not queued:
            self.input.clear()
            self.sent_texts = (self.sent_texts + [text])[-50:]
        if self.busy:  # the AI is working: the message waits its turn
            self.queue.append(text)
            trace("panel", "queued (%d waiting): %s" % (len(self.queue), text[:120]))
            self._sync_queue()
            return
        self._hold_queue = False
        self._set_busy(True)
        self._you(text)  # shown at once, not when the service first answers
        self._echoed = getattr(self, "_echoed", []) + [text]
        self._doing = i18n("sending")
        self._show_status()
        body = {"session": self.session, "provider": self._key(), "model": self._model_text() or None,
                "text": text}
        self._background(lambda: _ensure_service() and _request("POST", "/chat", body), self._started)

    def _started(self, result):
        if result.get("session") != self.session:
            self.session, self.seen = result["session"], 0
        self._poll_failures = 0
        self._doing = i18n("thinking")
        self._show_status()
        self.poll_timer.start(POLL_MS)
        self._poll()  # right away, not after the first interval
        if getattr(self, "_stop_pending", False):  # Stop was pressed before the service answered
            self._stop_pending = False
            self._stop()

    def _poll(self):
        if getattr(self, "_polling", False) or not self.session:
            return
        self._polling = True
        sid, since = self.session, self.seen

        def ask():
            try:
                return _request("GET", "/events?session=%s&since=%d" % (sid, since))
            except Exception as e:  # noqa: BLE001 - handled in done(), never leaves the panel stuck
                return {"_failed": str(e)}

        def done(r):
            self._polling = False
            if sid != self.session:
                return  # a new chat started meanwhile
            if "_failed" in r:
                return self._poll_failed(r["_failed"])
            self._poll_failures = 0
            self._events(r)
        self._background(ask, done)

    def _poll_failed(self, why):
        """The service did not answer: a few misses are normal while it is busy; then stop waiting."""
        self._poll_failures = getattr(self, "_poll_failures", 0) + 1
        lost = "no such session" in why
        if lost or self._poll_failures >= 8:
            self.poll_timer.stop()
            self._error(i18n("The assistant service restarted and this reply was lost. Send your message again.")
                        if lost else i18n("Lost contact with the assistant service (%s). Send again to restart it.")
                        % why)

    def _events(self, r):
        for ev in r.get("events", []):
            self.seen = max(self.seen, ev["n"] + 1)
            kind = ev["type"]
            if kind == "user":
                echoed = getattr(self, "_echoed", [])
                if echoed and ev["text"] == echoed[0][:len(ev["text"])]:
                    echoed.pop(0)  # already shown when it was sent
                else:
                    self._you(ev["text"])
            elif kind == "assistant":
                self._append("<p><b>%s:</b> %s</p>" % (SHORT.get(self._key(), "AI"),
                                                       html.escape(ev["text"]).replace("\n", "<br>")))
            elif kind == "note":
                self._append("<p style='color:#999; font-size:small'>%s</p>" % html.escape(ev.get("text", "")))
            elif kind == "tool":
                self._doing = ev["name"]
                self._show_status()
                self._append("<p style='color:#999; font-size:small'>🔧 %s %s</p>" % (
                    html.escape(ev["name"]), html.escape(ev.get("args", ""))))
            elif kind == "tool_result":
                self._doing = i18n("thinking")
                self._show_status()
                if ev.get("ok"):
                    continue
                self._append("<p style='color:#c96; font-size:small'>⚠ %s</p>" % html.escape(ev.get("text", "")))
            elif kind == "error":
                self._error(ev["text"])
            elif kind == "done":
                self.poll_timer.stop()
                self._set_busy(False)

    def _you(self, text):
        self._append("<p><b>%s</b> %s</p>" % (i18n("You:"), html.escape(text).replace("\n", "<br>")))

    def _stop(self):
        sid = self.session
        trace("panel", "Stop pressed (chat %s)" % sid)
        if self.queue:  # stopped: what was queued comes back to the box, nothing goes out on its own
            current = self.input.toPlainText().strip()
            self.input.setPlainText("\n".join(([current] if current else []) + self.queue))
            self.queue = []
            self._sync_queue()
        self._hold_queue = True
        if not self.busy:
            return
        self.stop_btn.setEnabled(False)
        self.status.setText("● " + i18n("Stopping…"))
        if not sid:  # the message is still on its way: stop as soon as the service has it
            self._stop_pending = True
        else:
            self._background(lambda: _request("POST", "/stop", {"session": sid}), lambda _: None, quiet=True)

        def give_up():  # the service answers "(stopped)" within a second; never leave the panel stuck
            if self.busy and self.session == sid:
                self.poll_timer.stop()
                self._stop_pending = False
                self._append("<p style='color:#999; font-size:small'>%s</p>" % html.escape(
                    i18n("(stopped waiting for the assistant)")))
                self._set_busy(False)
        QTimer.singleShot(STOP_WAIT_MS, give_up)

    def _new_chat(self):
        sid = self.session
        trace("panel", "New chat (was %s)" % sid)
        self._stop_pending = False
        self._echoed = []
        self.queue = []
        self._sync_queue()
        self.session, self.seen = None, 0
        self.poll_timer.stop()
        self.log.clear()
        self._set_busy(False)
        if sid:
            self._background(lambda: _request("POST", "/reset", {"session": sid}), lambda _: None, quiet=True)


def register_docker():
    Krita.instance().addDockWidgetFactory(
        DockWidgetFactory("krita_mcp_chat", DockWidgetFactoryBase.DockRight, ChatDocker))
