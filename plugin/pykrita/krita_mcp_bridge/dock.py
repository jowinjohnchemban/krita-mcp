"""The "AI / MCP" docker: run and configure the bridge, connect clients, watch and steer the AI.

Settings > Dockers > AI Bridge (MCP).
"""
import json
import shlex
import time

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Krita

try:
    from PyQt6.QtCore import Qt, QTimer, QUrl
    from PyQt6.QtGui import QDesktopServices, QGuiApplication
    from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                                 QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                                 QVBoxLayout, QWidget)
    _PASSWORD, _NORMAL = QLineEdit.EchoMode.Password, QLineEdit.EchoMode.Normal
    _RICH = Qt.TextFormat.RichText
except ImportError:  # Krita 5
    from PyQt5.QtCore import Qt, QTimer, QUrl
    from PyQt5.QtGui import QDesktopServices, QGuiApplication
    from PyQt5.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                                 QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                                 QVBoxLayout, QWidget)
    _PASSWORD, _NORMAL = QLineEdit.Password, QLineEdit.Normal
    _RICH = Qt.RichText

from . import extension, handlers, history, protocol, util

ACCESS_MODES = [("full", "Full access"), ("read_only", "Read only (AI can look, not edit)"),
                ("paused", "Paused (AI blocked)")]
CLIENTS = ["Claude Code", "Claude Desktop", "Cursor / Windsurf", "VS Code (Copilot)", "Gemini CLI",
           "Codex CLI", "HTTP (ChatGPT & remote clients)"]


def _split(cmd):
    parts = shlex.split(cmd, posix=False) if cmd.strip() else ["krita-mcp"]
    return parts[0].strip('"'), [a.strip('"') for a in parts[1:]]


def client_snippet(client, cmd, port):
    exe, args = _split(cmd)
    env = {} if port == protocol.DEFAULT_PORT else {"KRITA_MCP_PORT": str(port)}
    server = {"command": exe, "args": args}
    if env:
        server["env"] = env
    if client == "Claude Code":
        envs = "".join(" -e %s=%s" % kv for kv in env.items())
        return "claude mcp add krita%s -- %s" % (envs, cmd.strip() or "krita-mcp")
    if client == "VS Code (Copilot)":
        return "// .vscode/mcp.json\n" + json.dumps({"servers": {"krita": dict(server, type="stdio")}}, indent=2)
    if client == "Codex CLI":
        lines = ["# ~/.codex/config.toml", "[mcp_servers.krita]", "command = %s" % json.dumps(exe),
                 "args = %s" % json.dumps(args)]
        if env:
            lines.append("env = { %s }" % ", ".join("%s = %s" % (k, json.dumps(v)) for k, v in env.items()))
        return "\n".join(lines)
    if client.startswith("HTTP"):
        return ("# run in a terminal, then give the client the URL and the bearer token\n"
                "KRITA_MCP_HTTP_TOKEN=<long-random-secret> %s --transport http --port 8765\n"
                "# URL: http://127.0.0.1:8765/mcp   header: Authorization: Bearer <secret>\n"
                "# remote clients need an HTTPS tunnel in front; anyone with the URL+token can edit your art"
                % (cmd.strip() or "krita-mcp"))
    where = {"Claude Desktop": "claude_desktop_config.json", "Cursor / Windsurf": "~/.cursor/mcp.json",
             "Gemini CLI": "~/.gemini/settings.json"}.get(client, "MCP config")
    return "// %s\n%s" % (where, json.dumps({"mcpServers": {"krita": server}}, indent=2))


class McpDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Bridge (MCP)")
        root = QWidget(self)
        lay = QVBoxLayout(root)

        # --- status
        row = QHBoxLayout()
        self.status = QLabel()
        self.status.setTextFormat(_RICH)
        self.toggle_btn = QPushButton()
        self.toggle_btn.clicked.connect(self._toggle)
        row.addWidget(self.status, 1)
        row.addWidget(self.toggle_btn)
        lay.addLayout(row)

        self.access = QComboBox()
        for key, label in ACCESS_MODES:
            self.access.addItem(label, key)
        self.access.currentIndexChanged.connect(self._access_changed)
        form = QFormLayout()
        form.addRow("AI access", self.access)
        lay.addLayout(form)

        # --- settings
        box = QGroupBox("Settings")
        f = QFormLayout(box)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.editingFinished.connect(self._port_changed)
        f.addRow("Port", self.port)
        self.autostart = QCheckBox("Start with Krita")
        self.autostart.toggled.connect(lambda v: extension.set_setting("autostart", "true" if v else "false"))
        f.addRow("", self.autostart)
        ws_row = QHBoxLayout()
        self.workspace = QLineEdit()
        self.workspace.setReadOnly(True)
        browse = QPushButton("…")
        browse.setToolTip("Choose the folder the AI may read and write")
        browse.clicked.connect(self._choose_workspace)
        open_ws = QPushButton("Open")
        open_ws.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(util.workspace_root()))))
        ws_row.addWidget(self.workspace, 1)
        ws_row.addWidget(browse)
        ws_row.addWidget(open_ws)
        f.addRow("Workspace", ws_row)
        tok_row = QHBoxLayout()
        tok_row.setSpacing(2)
        self.token = QLineEdit()
        self.token.setReadOnly(True)
        self.token.setEchoMode(_PASSWORD)
        self.token.setMinimumWidth(80)
        show = QPushButton("Show")
        show.setCheckable(True)
        show.toggled.connect(lambda v: self.token.setEchoMode(_NORMAL if v else _PASSWORD))
        copy_tok = QPushButton("Copy")
        copy_tok.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.token.text()))
        regen = QPushButton("New")
        regen.setToolTip("Generate a new token (connected clients must restart)")
        regen.clicked.connect(self._regenerate)
        for w in (self.token, show, copy_tok, regen):
            tok_row.addWidget(w, 1 if w is self.token else 0)
        f.addRow("Token", tok_row)
        lay.addWidget(box)

        # --- connect a client
        box = QGroupBox("Connect an AI client")
        v = QVBoxLayout(box)
        self.client = QComboBox()
        self.client.addItems(CLIENTS)
        self.client.currentIndexChanged.connect(self._update_snippet)
        self.command = QLineEdit()
        self.command.setPlaceholderText("krita-mcp  (or: uvx krita-mcp, or a full path)")
        self.command.editingFinished.connect(self._command_changed)
        self.snippet = QPlainTextEdit()
        self.snippet.setReadOnly(True)
        self.snippet.setMaximumHeight(130)
        copy_snip = QPushButton("Copy")
        copy_snip.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.snippet.toPlainText()))
        v.addWidget(self.client)
        cf = QFormLayout()
        cf.addRow("Server command", self.command)
        v.addLayout(cf)
        v.addWidget(self.snippet)
        v.addWidget(copy_snip)
        lay.addWidget(box)

        # --- AI edits
        box = QGroupBox("AI edits in this document")
        v = QVBoxLayout(box)
        self.edits = QLabel("-")
        self.edits.setWordWrap(True)
        row = QHBoxLayout()
        undo = QPushButton("Undo AI edit")
        undo.clicked.connect(lambda: self._history("history.undo"))
        redo = QPushButton("Redo")
        redo.clicked.connect(lambda: self._history("history.redo"))
        row.addWidget(undo)
        row.addWidget(redo)
        v.addWidget(self.edits)
        v.addLayout(row)
        lay.addWidget(box)

        # --- activity
        box = QGroupBox("Activity")
        v = QVBoxLayout(box)
        self.log = QListWidget()
        self.log.setMinimumHeight(120)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_log)
        v.addWidget(self.log)
        v.addWidget(clear)
        lay.addWidget(box, 1)

        self.message = QLabel()
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(root)
        self.setWidget(scroll)

        self._shown = 0
        self._load()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    # Krita calls this when the active canvas changes
    def canvasChanged(self, canvas):
        self.refresh()

    # ----------------------------------------------------------- state
    def _ext(self):
        return extension.INSTANCE

    def _load(self):
        ext = self._ext()
        self.port.setValue(ext.port() if ext else protocol.DEFAULT_PORT)
        self.autostart.setChecked(extension.setting("autostart", "true") == "true")
        self.command.setText(extension.setting("client_command", "krita-mcp"))
        idx = [k for k, _ in ACCESS_MODES].index(handlers.ACCESS["mode"])
        self.access.setCurrentIndex(idx)
        self._update_snippet()

    def refresh(self):
        ext = self._ext()
        running = bool(ext and ext.running)
        if running:
            self.status.setText("<b style='color:#3c3'>●</b> Running on %s:%d" % (ext.server.host, ext.server.port))
            self.toggle_btn.setText("Stop")
            self.token.setText(ext.server.token)
        else:
            err = (" - " + ext.last_error) if ext and ext.last_error else ""
            self.status.setText("<b style='color:#c33'>●</b> Stopped%s" % err)
            self.toggle_btn.setText("Start")
            try:
                self.token.setText(protocol.load_or_create_token())
            except OSError:
                pass
        self.workspace.setText(str(util.workspace_root()))
        doc = Krita.instance().activeDocument()
        if doc is not None:
            h = history.listing(doc, 5)
            last = ", ".join(e["command"] for e in h["ai_edits"][-3:]) or "none yet"
            self.edits.setText("Undoable: %d (last: %s) · redoable: %d" % (h["undo_count"], last, h["redo_count"]))
        else:
            self.edits.setText("No document open")
        self._append_activity()

    def _append_activity(self):
        items = list(handlers.ACTIVITY)
        if len(items) < self._shown:
            self._shown = 0
        for t, method, ok, ms, detail in items[self._shown:]:
            line = "%s  %s %s  %dms" % (time.strftime("%H:%M:%S", time.localtime(t)), "✓" if ok else "✗", method, ms)
            if detail:
                line += "  - " + detail
            self.log.insertItem(0, line)
        self._shown = len(items)
        while self.log.count() > 300:
            self.log.takeItem(self.log.count() - 1)

    # ----------------------------------------------------------- actions
    def _say(self, text):
        self.message.setText(text)

    def _toggle(self):
        ext = self._ext()
        if ext is None:
            return self._say("The bridge extension is not loaded.")
        if ext.running:
            ext.stop()
            self._say("Stopped. AI clients cannot reach Krita now.")
        elif ext.start():
            self._say("Started.")
        else:
            self._say("Could not start: %s" % ext.last_error)
        self.refresh()

    def _port_changed(self):
        ext = self._ext()
        if ext is None or self.port.value() == ext.port():
            return
        extension.set_setting("port", self.port.value())
        if ext.running:
            ok = ext.restart()
            self._say("Restarted on port %d." % self.port.value() if ok else "Could not start: %s" % ext.last_error)
        self._update_snippet()
        self.refresh()

    def _access_changed(self, idx):
        key = self.access.itemData(idx)
        handlers.ACCESS["mode"] = key
        extension.set_setting("access", key)
        self._say(dict(ACCESS_MODES)[key])

    def _choose_workspace(self):
        d = QFileDialog.getExistingDirectory(self, "Folder the AI may read and write", str(util.workspace_root()))
        if d:
            util.WORKSPACE_OVERRIDE = d
            extension.set_setting("workspace", d)
            self.refresh()
            self._say("Workspace set. (KRITA_MCP_WORKSPACE, if set, still takes priority.)")

    def _regenerate(self):
        tok = protocol.write_new_token()
        ext = self._ext()
        if ext and ext.server:
            ext.server.token = tok
        self.refresh()
        self._say("New token written to ~/.krita-mcp/token. Restart connected AI clients.")

    def _command_changed(self):
        extension.set_setting("client_command", self.command.text().strip() or "krita-mcp")
        self._update_snippet()

    def _update_snippet(self):
        self.snippet.setPlainText(client_snippet(self.client.currentText(), self.command.text() or "krita-mcp",
                                                 self.port.value()))

    def _history(self, method):
        try:
            r = handlers.HANDLERS[method]({})
            self._say("%s: %s" % ("Undone" if method.endswith("undo") else "Redone",
                                  ", ".join(e["command"] for e in r.get("undone", r.get("redone", [])))))
        except Exception as e:  # noqa: BLE001
            self._say(str(e))
        self.refresh()

    def _clear_log(self):
        self.log.clear()


def register_docker():
    Krita.instance().addDockWidgetFactory(
        DockWidgetFactory("krita_mcp_docker", DockWidgetFactoryBase.DockRight, McpDocker))
