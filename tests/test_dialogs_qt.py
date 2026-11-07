"""Popups Krita opens while the AI works are cancelled and reported - never left blocking, never answered OK."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.environ.get("KRITA_TEST_QT") == "5":
    sys.modules["PyQt6"] = None
    qtw = pytest.importorskip("PyQt5.QtWidgets", reason="PyQt5 not installed")
    from PyQt5.QtCore import QTimer
else:
    qtw = pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")
    from PyQt6.QtCore import QTimer

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def dialogs(monkeypatch):
    app = qtw.QApplication.instance() or qtw.QApplication([])
    if not hasattr(app, "topLevelWidgets"):  # an earlier test made a widget-less QGuiApplication
        pytest.skip("needs a QApplication; run this file on its own")
    spec = importlib.util.spec_from_file_location("bridge_dialogs", ROOT / "plugin/pykrita/krita_mcp_bridge/dialogs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # Qt only: no Krita needed
    monkeypatch.setattr(mod, "QUESTION_GRACE", 0.05)
    monkeypatch.delenv("KRITA_MCP_DIALOGS", raising=False)
    before = {id(w) for w in app.topLevelWidgets()}  # other tests' widgets: not ours to touch
    yield app, mod
    for w in app.topLevelWidgets():
        if id(w) not in before and w.isVisible():
            w.close()


def _question(title="Missing Colour Profile - Krita"):
    """A dialog like Krita's: text and OK / Cancel (a plain QDialog - QMessageBox crashes PyQt5 offscreen)."""
    box = qtw.QDialog()
    box.setWindowTitle(title)
    lay = qtw.QVBoxLayout(box)
    lay.addWidget(qtw.QLabel("The image data you are trying to paste has no colour profile information."))
    sb = getattr(qtw.QDialogButtonBox, "StandardButton", qtw.QDialogButtonBox)
    buttons = qtw.QDialogButtonBox(sb.Ok | sb.Cancel)
    buttons.accepted.connect(box.accept)
    buttons.rejected.connect(box.reject)
    lay.addWidget(buttons)
    return box


def test_a_question_is_cancelled_and_reported(dialogs):
    app, d = dialogs
    guard = d.DialogGuard().start()
    box = _question()
    QTimer.singleShot(3000, box.accept)  # safety net: the test must not hang if the guard fails
    result = box.exec()  # what Krita does: a nested event loop that blocks until someone answers
    events = guard.stop()
    assert result == 0  # rejected (Cancel), not accepted
    assert len(events) == 1 and events[0]["handled"] == "cancelled"
    assert events[0]["title"] == "Missing Colour Profile - Krita" and "colour profile" in events[0]["text"]
    assert "Cancel" in events[0]["buttons"]
    assert "Missing Colour Profile" in d.summary(events) and "cancelled" in d.summary(events)


def test_the_users_own_dialog_is_left_alone(dialogs):
    app, d = dialogs
    mine = qtw.QDialog()
    mine.setWindowTitle("Preferences")
    mine.show()
    guard = d.DialogGuard().start()
    end = __import__("time").monotonic() + 0.4
    while __import__("time").monotonic() < end:
        app.processEvents()
    assert guard.stop() == [] and mine.isVisible()


def test_progress_windows_get_time_to_finish(dialogs, monkeypatch):
    app, d = dialogs
    monkeypatch.setattr(d, "PROGRESS_LIMIT", 0.6)
    waiting = qtw.QDialog()
    waiting.setWindowTitle("Krita")
    lay = qtw.QVBoxLayout(waiting)
    lay.addWidget(qtw.QLabel("Waiting for image operation to complete..."))
    bar = qtw.QProgressBar()
    bar.setFormat("Scripted Brush Stroke")
    lay.addWidget(bar)
    guard = d.DialogGuard().start()
    QTimer.singleShot(200, waiting.accept)  # the operation finishes by itself, well before the limit
    waiting.exec()
    assert guard.stop() == []
    stuck = qtw.QDialog()
    qtw.QVBoxLayout(stuck).addWidget(qtw.QProgressBar())
    guard = d.DialogGuard().start()
    QTimer.singleShot(3000, stuck.accept)
    stuck.exec()  # never finishes: cancelled after the limit
    events = guard.stop()
    assert events and events[0]["progress"] and events[0]["handled"] == "cancelled"


def test_leave_policy_lets_the_user_answer(dialogs, monkeypatch):
    app, d = dialogs
    monkeypatch.setitem(d.POLICY, "mode", "leave")
    guard = d.DialogGuard().start()
    box = _question("Overwrite file?")
    QTimer.singleShot(500, box.accept)  # "the user" answers
    result = box.exec()
    events = guard.stop()
    assert result != 0 and events[0]["handled"] == "left for the user"
