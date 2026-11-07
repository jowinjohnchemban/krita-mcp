"""Popups and dialogs Krita opens while the AI is working.

A modal dialog (a question like "Missing Colour Profile", an "are you sure?", a progress window) blocks Krita
until someone answers it - and the AI cannot. While a bridge request runs, DialogGuard watches for dialogs
that were not open before, records their title, text and buttons, and closes them with Cancel (never OK: the
AI did not ask for whatever OK would do). Progress windows get time to finish on their own first.
The AI is told exactly what appeared, so it can take another way; the user's own dialogs are never touched.

Policy (AI Bridge panel setting "dialogs", or KRITA_MCP_DIALOGS): "cancel" (default) or "leave" (the user
answers them).
"""
import os
import time

try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QAbstractButton, QApplication, QDialog, QLabel, QProgressBar
except ImportError:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QAbstractButton, QApplication, QDialog, QLabel, QProgressBar

QUESTION_GRACE = 0.3  # seconds: let the dialog finish appearing before reading and closing it
PROGRESS_LIMIT = 30.0  # seconds a progress window ("Waiting for image operation...") may take
POLICY = {"mode": None}


def policy():
    mode = POLICY["mode"] or os.environ.get("KRITA_MCP_DIALOGS") or "cancel"
    return mode if mode in ("cancel", "leave") else "cancel"


def _open_dialogs():
    app = QApplication.instance()
    if app is None or not hasattr(app, "topLevelWidgets"):
        return []
    return [w for w in app.topLevelWidgets()
            if w.isVisible() and (isinstance(w, QDialog) or w.isModal())]


def describe(w):
    texts = [lab.text().strip() for lab in w.findChildren(QLabel) if lab.isVisible() and lab.text().strip()]
    for bar in w.findChildren(QProgressBar):
        if bar.isVisible() and bar.format():
            texts.append(bar.text() or bar.format())
    buttons = [b.text().replace("&", "") for b in w.findChildren(QAbstractButton) if b.isVisible() and b.text()]
    return {"title": w.windowTitle(), "text": " ".join(texts)[:600], "buttons": buttons[:8],
            "progress": any(b.isVisible() for b in w.findChildren(QProgressBar))}


def _cancel(w):
    if isinstance(w, QDialog):
        w.reject()
    else:
        w.close()


class DialogGuard:
    """Use around one bridge request on Krita's GUI thread: start(), run it, stop() -> what was handled."""

    def __init__(self):
        self.before, self.seen, self.events, self.timer = set(), {}, [], None

    def start(self):
        self.before = {id(w) for w in _open_dialogs()}
        self.seen, self.events = {}, []
        self.timer = QTimer()
        self.timer.timeout.connect(self.check)
        self.timer.start(150)  # fires inside a dialog's own event loop too
        return self

    def check(self):
        now = time.monotonic()
        for w in _open_dialogs():
            key = id(w)
            if key in self.before:
                continue  # the user's own dialog, open before the AI started
            first = self.seen.setdefault(key, now)
            info = describe(w)
            limit = PROGRESS_LIMIT if info["progress"] else QUESTION_GRACE
            if now - first < limit:
                continue
            if policy() == "leave":
                if not any(e.get("_key") == key for e in self.events):
                    self.events.append(dict(info, handled="left for the user", _key=key))
                continue
            _cancel(w)
            self.before.add(key)
            self.events.append(dict(info, handled="cancelled", _key=key))

    def stop(self):
        if self.timer is not None:
            self.timer.stop()
            self.timer.deleteLater()
            self.timer = None
        return [{k: v for k, v in e.items() if not k.startswith("_")} for e in self.events]


def summary(events):
    """One line for the AI and the activity log."""
    return "; ".join("Krita showed %s'%s'%s - %s" % ("a progress window " if e.get("progress") else "",
                                                     e.get("title") or "dialog",
                                                     (": " + e["text"][:200]) if e.get("text") else "",
                                                     e.get("handled")) for e in events)
