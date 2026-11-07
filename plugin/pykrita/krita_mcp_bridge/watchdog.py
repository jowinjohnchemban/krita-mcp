"""A heartbeat on Krita's main thread: if it stops (Krita frozen - by a bridge command, the AI Chat panel, or Krita
itself), every Python thread's stack is written to ~/.krita-mcp/hang.log so the freeze can be traced.

A QTimer on the main thread re-arms faulthandler's timer every second. faulthandler runs its own C thread that
needs no Python lock, so the dump is written even when the main thread is stuck. If the main thread shows no
Python frames in the dump, Krita was stuck in its own (C++) code. KRITA_MCP_HANG_SECONDS (default 60) sets how
long a freeze must last; 0 turns the watchdog off.
"""
import faulthandler
import os
import time

try:
    from PyQt6.QtCore import QTimer
except ImportError:
    from PyQt5.QtCore import QTimer

STATE = {"file": None, "timer": None, "seconds": 0.0}


def home():
    return os.environ.get("KRITA_MCP_HOME") or os.path.join(os.path.expanduser("~"), ".krita-mcp")


def _file():
    if STATE["file"] is None:
        os.makedirs(home(), exist_ok=True)
        STATE["file"] = open(os.path.join(home(), "hang.log"), "a", encoding="utf-8")
        note("Krita session started; a block of stacks below = Krita was frozen for %ds or more"
             % STATE["seconds"])
    return STATE["file"]


def note(text):
    """A dated line in hang.log (what was running), so a stack dump has its context. Never raises."""
    try:
        f = STATE["file"] if STATE["file"] is not None else _file()
        f.write("== %s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
        f.flush()
    except Exception:  # noqa: BLE001 - diagnostics must never break anything
        pass


def _beat():
    try:
        faulthandler.dump_traceback_later(STATE["seconds"], repeat=False, file=_file())
    except Exception:  # noqa: BLE001
        pass


def start():
    """Start the heartbeat (on the main thread, once)."""
    if STATE["timer"] is not None:
        return
    try:
        STATE["seconds"] = float(os.environ.get("KRITA_MCP_HANG_SECONDS") or 60)
    except ValueError:
        STATE["seconds"] = 60.0
    if STATE["seconds"] <= 0:
        return
    timer = QTimer()
    timer.timeout.connect(_beat)
    timer.start(int(max(50, min(1000, STATE["seconds"] * 250))))  # well inside the limit
    STATE["timer"] = timer
    _beat()


def stop():
    if STATE["timer"] is not None:
        STATE["timer"].stop()
        STATE["timer"] = None
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:  # noqa: BLE001
        pass
