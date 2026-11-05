"""Krita Extension: owns the bridge server and pumps it on the GUI thread."""
import os
import sys
import traceback

from krita import Extension, Krita

try:
    from PyQt6.QtCore import QTimer
except ImportError:  # Krita 5
    from PyQt5.QtCore import QTimer

from .handlers import dispatch
from .protocol import DEFAULT_PORT, BridgeServer


def _log(msg):
    print("[krita-mcp] " + msg, file=sys.stderr)


class KritaMcpBridge(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.server = None
        self.timer = None

    def setup(self):
        if os.environ.get("KRITA_MCP_AUTOSTART", "1") != "0":
            self.start()

    def createActions(self, window):
        toggle = window.createAction("krita_mcp_toggle", "Krita MCP Bridge: start / stop", "tools/scripts")
        toggle.triggered.connect(self.toggle)
        info = window.createAction("krita_mcp_info", "Krita MCP Bridge: show status", "tools/scripts")
        info.triggered.connect(self.show_info)

    def start(self):
        if self.server and self.server.running:
            return
        try:
            port = int(os.environ.get("KRITA_MCP_PORT", DEFAULT_PORT))
            self.server = BridgeServer(dispatch, port=port, log=_log)
            self.server.start()
        except Exception:  # noqa: BLE001
            _log("failed to start:\n" + traceback.format_exc())
            self.server = None
            return
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.server and self.server.pump())
        self.timer.start(15)

    def stop(self):
        if self.timer:
            self.timer.stop()
            self.timer = None
        if self.server:
            self.server.stop()
            self.server = None

    def toggle(self):
        if self.server and self.server.running:
            self.stop()
            self._status("Krita MCP bridge stopped")
        else:
            self.start()
            self.show_info()

    def show_info(self):
        if self.server and self.server.running:
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
