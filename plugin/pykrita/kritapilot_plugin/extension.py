"""Krita Extension: owns the bridge server and pumps it on the GUI thread."""
import os
import sys
import traceback

from krita import Extension, Krita

try:
    from PyQt6.QtCore import QTimer
except ImportError:  # Krita 5
    from PyQt5.QtCore import QTimer

from . import config, handlers, tracelog, util
from .util import i18n
from .handlers import dispatch
from .protocol import DEFAULT_PORT, BridgeServer

SETTINGS = config.SETTINGS  # the Krita settings group (never renamed)
# A copy bundled with Krita itself sets both to False (scripts/krita_upstream.py): nothing listens and no
# panel pops up until the user presses Start in Settings > Dockers > AI Bridge (MCP).
AUTOSTART_DEFAULT = True
INTRODUCE_PANEL = True
UPDATER_ENABLED = True  # over-the-air updates from GitHub releases (off in a copy bundled with Krita)

INSTANCE = None  # the running extension, for the docker


def _log(msg):
    print("[KritaPilot] " + msg, file=sys.stderr)
    trace("bridge", msg)


def trace(source, message):
    """One line in ~/.kritapilot/logs/krita.log (the Logs window shows it live; keys are masked)."""
    tracelog.write("krita.log", source, message)


def bridge_port():
    """The port the bridge listens on: the running server's, else the configured one."""
    ext = INSTANCE
    server = getattr(ext, "server", None) if ext is not None else None
    if server is not None and getattr(server, "port", None):
        return int(server.port)
    if ext is not None:
        return ext.port()
    env = os.environ.get("KRITAPILOT_PORT")
    return int(env) if env else int(setting("port", DEFAULT_PORT))


COMMAND = config.COMMAND  # the server command


def server_args():
    """The server command as a list: the one set in the AI Bridge panel, else `kritapilot`."""
    import shlex
    cmd = (setting("client_command", "") or "").strip() or COMMAND
    return shlex.split(cmd, posix=os.name != "nt")


def setting(key, default):
    return Krita.instance().readSetting(SETTINGS, key, str(default))


def set_setting(key, value):
    old = Krita.instance().readSetting(SETTINGS, key, "")
    Krita.instance().writeSetting(SETTINGS, key, str(value))
    if old != str(value):
        shown = "***" if key in ("token",) or "key" in key else "%r -> %r" % (old, str(value))
        trace("setting", "%s: %s" % (key, shown))


def startup_report():
    """Who and where: versions, system and settings, at the top of each Krita session in the log."""
    import platform
    try:
        from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
    except ImportError:
        from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
    trace("session", "=" * 20 + " Krita started " + "=" * 20)
    trace("session", "Krita %s, plugin %s, Python %s, Qt %s / PyQt %s, %s (%s)" % (
        Krita.instance().version(), handlers.version_text(), platform.python_version(), QT_VERSION_STR,
        PYQT_VERSION_STR, platform.platform(), platform.machine()))
    names = ("port", "autostart", "access", "dialogs", "workspace", "client_command", "update_mode", "chat_provider")
    trace("session", "settings: " + ", ".join("%s=%s" % (n, setting(n, "")) for n in names))
    env = {k: v for k, v in os.environ.items() if k.startswith("KRITAPILOT_")}
    if env:
        trace("session", "environment: " + ", ".join("%s=%s" % (k, "***" if any(
            w in k for w in ("KEY", "TOKEN", "SECRET", "AUTH")) else v) for k, v in sorted(env.items())))


class KritaMcpBridge(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        global INSTANCE
        INSTANCE = self
        self.server = None
        self.timer = None
        self.last_error = None

    def setup(self):
        try:  # a frozen Krita (from anything) leaves its stacks in ~/.kritapilot/hang.log
            from . import watchdog
            watchdog.start()
        except Exception:  # noqa: BLE001 - diagnostics only
            _log("watchdog unavailable:\n" + traceback.format_exc())
        try:
            startup_report()
        except Exception:  # noqa: BLE001 - the log is a help, never a reason not to start
            _log("start-up report failed:\n" + traceback.format_exc())
        ws = setting("workspace", "")
        util.WORKSPACE_OVERRIDE = ws or None
        mode = setting("access", "full")
        handlers.ACCESS["mode"] = mode if mode in ("full", "read_only", "paused") else "full"
        from . import dialogs
        dialogs.POLICY["mode"] = setting("dialogs", "cancel")
        env = os.environ.get("KRITAPILOT_AUTOSTART")
        default = "true" if AUTOSTART_DEFAULT else "false"
        autostart = env != "0" if env is not None else setting("autostart", default) == "true"
        if autostart:
            self.start()
        try:  # look for updates once Krita is up (never delays start-up)
            from . import autoupdate
            QTimer.singleShot(20000, autoupdate.startup)
        except Exception:  # noqa: BLE001 - updates are optional
            _log("updater unavailable:\n" + traceback.format_exc())

    def createActions(self, window):
        toggle = window.createAction("kritapilot_toggle", i18n("KritaPilot: start / stop the AI bridge"), "tools/scripts")
        toggle.triggered.connect(self.toggle)
        about = window.createAction("kritapilot_about", i18n("KritaPilot: About"), "tools/scripts")
        about.triggered.connect(self.show_about)
        info = window.createAction("kritapilot_info", i18n("KritaPilot: show status"), "tools/scripts")
        info.triggered.connect(self.show_info)
        panel = window.createAction("kritapilot_panel", i18n("KritaPilot: show panel"), "tools/scripts")
        panel.triggered.connect(lambda: self.show_panel())
        if INTRODUCE_PANEL and setting("panel_introduced", "false") != "true":  # open it once for new users
            QTimer.singleShot(3000, lambda: self.show_panel(first_time=True))

    def show_about(self):
        from . import about
        about.show(Krita.instance().activeWindow().qwindow() if Krita.instance().activeWindow() else None)

    def show_panel(self, first_time=False):
        # Look the window up now: the Window wrapper given to createActions does not outlive that call,
        # and an exception escaping here would pop up a modal dialog that blocks the bridge.
        try:
            from PyQt6.QtWidgets import QDockWidget
        except ImportError:
            from PyQt5.QtWidgets import QDockWidget
        try:
            win = Krita.instance().activeWindow()
            qwin = win.qwindow() if win is not None else None
            dock = qwin.findChild(QDockWidget, "kritapilot_docker") if qwin is not None else None
            if dock is None:
                return
            dock.show()
            dock.raise_()
            if first_time:
                set_setting("panel_introduced", "true")
        except Exception:  # noqa: BLE001
            _log("could not show the MCP panel:\n" + traceback.format_exc())

    def port(self):
        env = os.environ.get("KRITAPILOT_PORT")
        return int(env) if env else int(setting("port", DEFAULT_PORT))

    @property
    def running(self):
        return bool(self.server and self.server.running)

    def start(self):
        if self.running:
            return True
        try:
            self.server = BridgeServer(dispatch, port=self.port(), log=_log)
            self.server.start()
            self.last_error = None
            trace("bridge", "listening on %s:%d" % (self.server.host, self.server.port))
        except Exception as e:  # noqa: BLE001
            _log("failed to start:\n" + traceback.format_exc())
            self.server = None
            self.last_error = str(e)
            return False
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.server and self.server.pump())
        self.timer.start(15)
        return True

    def stop(self):
        if self.server:
            trace("bridge", "stopped")
        if self.timer:
            self.timer.stop()
            self.timer = None
        if self.server:
            self.server.stop()
            self.server = None

    def restart(self):
        self.stop()
        return self.start()

    def toggle(self):
        if self.running:
            self.stop()
            self._status(i18n("KritaPilot bridge stopped"))
        else:
            self.start()
            self.show_info()

    def show_info(self):
        if self.running:
            self._status(i18n("KritaPilot bridge listening on %s:%d (token in ~/.kritapilot/token)")
                         % (self.server.host, self.server.port))
        else:
            self._status(i18n("KritaPilot bridge is NOT running"))

    def _status(self, msg):
        _log(msg)
        win = Krita.instance().activeWindow()
        if win:
            try:
                win.qwindow().statusBar().showMessage(msg, 8000)
            except Exception:  # noqa: BLE001
                pass


def register():
    Krita.instance().addExtension(KritaMcpBridge(Krita.instance()))
    try:
        from .dock import register_docker
        register_docker()
    except Exception:  # noqa: BLE001 - the bridge must work even if the panel cannot load
        _log("MCP docker unavailable:\n" + traceback.format_exc())
    try:
        from .chat import register_docker as register_chat
        register_chat()
    except Exception:  # noqa: BLE001
        _log("AI Chat docker unavailable:\n" + traceback.format_exc())
