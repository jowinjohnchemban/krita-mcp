"""Krita Extension: owns the bridge server and pumps it on the GUI thread."""
import os
import sys
import traceback

from krita import Extension, Krita

try:
    from PyQt6.QtCore import QTimer
except ImportError:  # Krita 5
    from PyQt5.QtCore import QTimer

from . import handlers, util
from .handlers import dispatch
from .protocol import DEFAULT_PORT, BridgeServer

SETTINGS = "krita_mcp"
# A copy bundled with Krita itself sets both to False (scripts/krita_upstream.py): nothing listens and no
# panel pops up until the user presses Start in Settings > Dockers > AI Bridge (MCP).
AUTOSTART_DEFAULT = True
INTRODUCE_PANEL = True

INSTANCE = None  # the running extension, for the docker


def _log(msg):
    print("[krita-mcp] " + msg, file=sys.stderr)


def setting(key, default):
    return Krita.instance().readSetting(SETTINGS, key, str(default))


def set_setting(key, value):
    Krita.instance().writeSetting(SETTINGS, key, str(value))


class KritaMcpBridge(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        global INSTANCE
        INSTANCE = self
        self.server = None
        self.timer = None
        self.last_error = None

    def setup(self):
        ws = setting("workspace", "")
        util.WORKSPACE_OVERRIDE = ws or None
        mode = setting("access", "full")
        handlers.ACCESS["mode"] = mode if mode in ("full", "read_only", "paused") else "full"
        env = os.environ.get("KRITA_MCP_AUTOSTART")
        default = "true" if AUTOSTART_DEFAULT else "false"
        autostart = env != "0" if env is not None else setting("autostart", default) == "true"
        if autostart:
            self.start()

    def createActions(self, window):
        toggle = window.createAction("krita_mcp_toggle", "Krita MCP Bridge: start / stop", "tools/scripts")
        toggle.triggered.connect(self.toggle)
        info = window.createAction("krita_mcp_info", "Krita MCP Bridge: show status", "tools/scripts")
        info.triggered.connect(self.show_info)
        panel = window.createAction("krita_mcp_panel", "Krita MCP Bridge: show panel", "tools/scripts")
        panel.triggered.connect(lambda: self.show_panel())
        if INTRODUCE_PANEL and setting("panel_introduced", "false") != "true":  # open it once for new users
            QTimer.singleShot(3000, lambda: self.show_panel(first_time=True))

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
            dock = qwin.findChild(QDockWidget, "krita_mcp_docker") if qwin is not None else None
            if dock is None:
                return
            dock.show()
            dock.raise_()
            if first_time:
                set_setting("panel_introduced", "true")
        except Exception:  # noqa: BLE001
            _log("could not show the MCP panel:\n" + traceback.format_exc())

    def port(self):
        env = os.environ.get("KRITA_MCP_PORT")
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
            self._status("Krita MCP bridge stopped")
        else:
            self.start()
            self.show_info()

    def show_info(self):
        if self.running:
            self._status("Krita MCP bridge listening on %s:%d (token in ~/.krita-mcp/token)"
                         % (self.server.host, self.server.port))
        else:
            self._status("Krita MCP bridge is NOT running")

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
