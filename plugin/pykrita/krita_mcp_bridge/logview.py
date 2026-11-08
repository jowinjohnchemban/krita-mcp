"""The Logs window: follows KritaPilot's log files live (like `tail -f`), to trace what happened and why.

It stays open beside Krita (not modal), shows the end of the chosen log and every new line as it is
written, and can filter, copy and open the folder. The files are written by tracelog.py; keys are masked
before they are written, so what is shown here can be shared in a bug report.
"""
import os

try:
    from PyQt6.QtCore import QTimer, QUrl
    from PyQt6.QtGui import QDesktopServices, QFont, QGuiApplication
    from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                                 QPushButton, QVBoxLayout)
except ImportError:
    from PyQt5.QtCore import QTimer, QUrl
    from PyQt5.QtGui import QDesktopServices, QFont, QGuiApplication
    from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                                 QPushButton, QVBoxLayout)

from . import tracelog
from .util import i18n

# (file under ~/.krita-mcp, what it holds)
LOGS = [("logs/krita.log", "Krita: commands, panel actions, settings"),
        ("logs/assistant.log", "AI Chat assistant: messages, models, tools"),
        ("update.log", "Updates"),
        ("hang.log", "Freezes (where Krita was stuck)")]
FIRST_BYTES = 200_000  # how much of the end of a file to show when it is opened
MAX_LINES = 5000
_OPEN = {"dialog": None}


def show(parent=None, which=None):
    """Open (or raise) the one Logs window."""
    dlg = _OPEN["dialog"]
    if dlg is None:
        dlg = _OPEN["dialog"] = LogsDialog(parent)
    if which is not None:
        dlg.choose(which)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


class LogsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n("KritaPilot logs"))
        self.resize(900, 520)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.pick = QComboBox()
        for rel, label in LOGS:
            self.pick.addItem(i18n(label), rel)
        self.pick.currentIndexChanged.connect(self._reload)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(i18n("Show only lines containing… (e.g. ✗, error, layer.create)"))
        self.filter.textChanged.connect(self._reload)
        self.follow = QCheckBox(i18n("Follow"))
        self.follow.setChecked(True)
        self.follow.setToolTip(i18n("Keep showing the newest lines as they are written"))
        top.addWidget(self.pick)
        top.addWidget(self.filter, 1)
        top.addWidget(self.follow)
        lay.addLayout(top)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap if hasattr(QPlainTextEdit, "LineWrapMode")
                                  else QPlainTextEdit.NoWrap)
        font = QFont("Consolas" if os.name == "nt" else "Monospace")
        font.setStyleHint(getattr(getattr(QFont, "StyleHint", QFont), "Monospace"))
        self.view.setFont(font)
        lay.addWidget(self.view, 1)
        bottom = QHBoxLayout()
        self.where = QLabel()
        self.where.setWordWrap(True)
        copy = QPushButton(i18n("Copy"))
        copy.setToolTip(i18n("Copy what is shown (keys are already masked) for a bug report"))
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.view.toPlainText()))
        clear = QPushButton(i18n("Clear view"))
        clear.setToolTip(i18n("Empty this window; the file is kept"))
        clear.clicked.connect(self.view.clear)
        folder = QPushButton(i18n("Open folder"))
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.path()))))
        close = QPushButton(i18n("Close"))
        close.clicked.connect(self.close)
        bottom.addWidget(self.where, 1)
        for b in (copy, clear, folder, close):
            bottom.addWidget(b)
        lay.addLayout(bottom)
        self.pos, self.partial = 0, ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tail)
        self._reload()

    def choose(self, rel):
        i = self.pick.findData(rel)
        if i >= 0:
            self.pick.setCurrentIndex(i)

    def path(self):
        return os.path.join(tracelog.home(), *self.pick.currentData().split("/"))

    def _reload(self):
        """Start over on the chosen file: its last FIRST_BYTES, then follow."""
        self.view.clear()
        self.pos, self.partial = 0, ""
        p = self.path()
        self.where.setText(p)
        try:
            size = os.path.getsize(p)
            self.pos = max(0, size - FIRST_BYTES)
        except OSError:
            self.view.setPlaceholderText(i18n("Nothing logged here yet."))
        self.tail(skip_partial=self.pos > 0)
        self.timer.start(500)

    def tail(self, skip_partial=False):
        """Show the lines written since last time (the file is opened and closed each time, so it can rotate)."""
        p = self.path()
        try:
            size = os.path.getsize(p)
        except OSError:
            return
        if size < self.pos:  # rotated or emptied: start from the top of the new file
            self.pos, self.partial = 0, ""
        if size == self.pos:
            return
        try:
            with open(p, "rb") as f:
                f.seek(self.pos)
                chunk = f.read(size - self.pos)
        except OSError:
            return
        self.pos += len(chunk)
        text = self.partial + chunk.decode("utf-8", "replace")
        lines = text.split("\n")
        self.partial = lines.pop()  # an unfinished last line waits for the rest
        if skip_partial and lines:
            lines = lines[1:]  # started mid-file: the first line is cut
        want = self.filter.text().strip().lower()
        if want:
            lines = [ln for ln in lines if want in ln.lower()]
        if not lines:
            return
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        self.view.appendPlainText("\n".join(lines))
        if self.follow.isChecked() and (at_end or skip_partial):  # not while the user reads further up
            bar.setValue(bar.maximum())

    def closeEvent(self, e):
        self.timer.stop()
        _OPEN["dialog"] = None
        super().closeEvent(e)

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        self.timer.start(500)
        super().showEvent(e)
