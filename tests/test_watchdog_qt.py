"""A frozen Krita leaves its stacks in hang.log - whatever froze it, not only a bridge command."""
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.environ.get("KRITA_TEST_QT") == "5":
    sys.modules["PyQt6"] = None
    qtc = pytest.importorskip("PyQt5.QtCore", reason="PyQt5 not installed")
else:
    qtc = pytest.importorskip("PyQt6.QtCore", reason="PyQt6 not installed")

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("bridge_watchdog", ROOT / "plugin/pykrita/krita_mcp_bridge/watchdog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # Qt only: no Krita needed
    return mod


def a_frozen_main_thread(seconds):
    time.sleep(seconds)  # what a stuck Krita looks like to the watchdog: no events processed


def test_a_freeze_writes_every_threads_stack(tmp_path, monkeypatch):
    app = qtc.QCoreApplication.instance() or qtc.QCoreApplication([])
    monkeypatch.setenv("KRITA_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("KRITA_MCP_HANG_SECONDS", "0.5")
    w = _load()
    try:
        w.start()
        w.note("running layer.create")
        a_frozen_main_thread(1.5)
        app.processEvents()
    finally:
        w.stop()
        if w.STATE["file"]:
            w.STATE["file"].close()
    log = (tmp_path / "hang.log").read_text(encoding="utf-8")
    assert "running layer.create" in log and "a_frozen_main_thread" in log  # what ran, and where it was stuck


def test_no_freeze_no_dump(tmp_path, monkeypatch):
    app = qtc.QCoreApplication.instance() or qtc.QCoreApplication([])
    monkeypatch.setenv("KRITA_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("KRITA_MCP_HANG_SECONDS", "1")
    w = _load()
    try:
        w.start()
        end = time.monotonic() + 2.0
        while time.monotonic() < end:  # a responsive main thread keeps re-arming the watchdog
            app.processEvents()
            time.sleep(0.05)
    finally:
        w.stop()
        if w.STATE["file"]:
            w.STATE["file"].close()
    assert "most recent call first" not in (tmp_path / "hang.log").read_text(encoding="utf-8")
