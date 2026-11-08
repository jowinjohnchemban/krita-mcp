"""The History window: every picture an image model made (~/.kritapilot/history), newest first.

Double-click or Place on canvas adds one as a new layer (one undo step); Use prompt puts its prompt in the AI Chat
box to run it again or change it. The files are written by the server (kritapilot/gen_history.py).
"""
import base64
import json
import os

try:
    from PyQt6.QtCore import QSize, Qt, QUrl
    from PyQt6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPixmap
    from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                                 QVBoxLayout)
except ImportError:
    from PyQt5.QtCore import QSize, Qt, QUrl
    from PyQt5.QtGui import QDesktopServices, QGuiApplication, QIcon, QPixmap
    from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                                 QVBoxLayout)

from . import config
from .util import i18n

THUMB = 128
_ICON_MODE = getattr(getattr(QListWidget, "ViewMode", QListWidget), "IconMode")
_ADJUST = getattr(getattr(QListWidget, "ResizeMode", QListWidget), "Adjust")
_ROLE = getattr(getattr(Qt, "ItemDataRole", Qt), "UserRole")
_SMOOTH = getattr(getattr(Qt, "TransformationMode", Qt), "SmoothTransformation")
_KEEP = getattr(getattr(Qt, "AspectRatioMode", Qt), "KeepAspectRatio")


def folder():
    return config.home() / "history"


def entries(limit=200):
    """The newest generations first (a file the server writes; unreadable lines are skipped)."""
    try:
        lines = (folder() / "history.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[:limit]


def place(entry, index=1):
    """Add one picture of `entry` to the active document as a new layer covering the picture (one undo step)."""
    from . import handlers
    doc = handlers.active_doc()
    data = (folder() / entry["files"][index - 1]).read_bytes()
    return handlers.dispatch("gen.place", {
        "images": [base64.b64encode(data).decode()], "region": [0, 0, doc.width(), doc.height()],
        "name": ("History: %s" % (entry.get("prompt") or entry.get("id")))[:80]})


class HistoryDialog(QDialog):
    def __init__(self, parent=None, use_prompt=None):
        super().__init__(parent)
        self.use_prompt = use_prompt
        self.setWindowTitle(i18n("Generated pictures"))
        self.resize(760, 520)
        lay = QVBoxLayout(self)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.view = QListWidget()
        self.view.setViewMode(_ICON_MODE)
        self.view.setResizeMode(_ADJUST)
        self.view.setIconSize(QSize(THUMB, THUMB))
        self.view.setGridSize(QSize(THUMB + 24, THUMB + 44))
        self.view.setWordWrap(True)
        self.view.itemDoubleClicked.connect(lambda _i: self._place())
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.place_btn = QPushButton(i18n("Place on canvas"))
        self.place_btn.clicked.connect(self._place)
        self.prompt_btn = QPushButton(i18n("Use prompt"))
        self.prompt_btn.setToolTip(i18n("Put this prompt in the AI Chat box, to run it again or change it"))
        self.prompt_btn.clicked.connect(self._use_prompt)
        copy = QPushButton(i18n("Copy prompt"))
        copy.clicked.connect(self._copy)
        open_dir = QPushButton(i18n("Open folder"))
        open_dir.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder()))))
        close = QPushButton(i18n("Close"))
        close.clicked.connect(self.close)
        for b in (self.place_btn, self.prompt_btn, copy, open_dir):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.prompt_btn.setVisible(use_prompt is not None)
        self.refresh()

    def refresh(self):
        self.view.clear()
        items = entries()
        for e in items:
            for n, name in enumerate(e.get("files") or [], 1):
                path = folder() / name
                if not os.path.exists(str(path)):
                    continue
                pix = QPixmap(str(path))
                if pix.isNull():
                    continue
                prompt = e.get("prompt") or ""
                item = QListWidgetItem(QIcon(pix.scaled(THUMB, THUMB, _KEEP, _SMOOTH)),
                                       (prompt[:40] + ("…" if len(prompt) > 40 else "")))
                item.setToolTip("%s\n%s · %s%s" % (prompt, e.get("time", ""), e.get("backend", ""),
                                                  (" · " + e["model"]) if e.get("model") else ""))
                item.setData(_ROLE, (e, n))
                self.view.addItem(item)
        self.hint.setText(i18n("%d pictures. Double-click one to add it to your picture as a new layer.")
                          % self.view.count() if self.view.count() else
                          i18n('No generated pictures yet. Ask the AI Chat for one, e.g. "a lighthouse at dusk, '
                               'photo".'))
        for b in (self.place_btn, self.prompt_btn):
            b.setEnabled(self.view.count() > 0)

    def _current(self):
        item = self.view.currentItem() or (self.view.item(0) if self.view.count() else None)
        return item.data(_ROLE) if item is not None else None

    def _place(self):
        cur = self._current()
        if not cur:
            return
        try:
            place(*cur)
            self.hint.setText(i18n("Added as a new layer. Edit ▸ Undo removes it."))
        except Exception as e:  # noqa: BLE001 - shown, never lost
            self.hint.setText(i18n("Could not add it: %s") % e)

    def _copy(self):
        cur = self._current()
        if cur:
            QGuiApplication.clipboard().setText(cur[0].get("prompt") or "")

    def _use_prompt(self):
        cur = self._current()
        if cur and self.use_prompt:
            self.use_prompt(cur[0].get("prompt") or "")
            self.close()


def show(parent=None, use_prompt=None):
    dlg = HistoryDialog(parent, use_prompt)
    dlg.show()
    return dlg
