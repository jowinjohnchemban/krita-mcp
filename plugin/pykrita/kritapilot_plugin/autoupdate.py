"""Over-the-air updates, as seen from Krita: checks on start-up (at most every 6 hours) and on demand,
then notifies, or installs automatically, depending on the setting in the AI Bridge (MCP) panel.

Network work runs in threads; STATE is read by the panel's timer on the GUI thread.
"""
import json
import subprocess
import threading
import time

from . import extension, updater
from .handlers import BRIDGE_COMMIT, BRIDGE_VERSION, version_text

MODES = (("notify", "Tell me when an update is available"), ("auto", "Install updates automatically"),
         ("off", "Never check"))
from . import config  # noqa: E402

CHECK_EVERY = config.UPDATE_EVERY
STATE = {"status": "idle", "latest": None, "message": "", "changed": 0.0}
_lock = threading.Lock()


def enabled():
    """The copy bundled with Krita itself is updated with Krita, not by this."""
    return getattr(extension, "UPDATER_ENABLED", True)


def mode():
    m = extension.setting("update_mode", "notify")
    return m if m in dict(MODES) else "notify"


def _set(status, message="", latest=None):
    with _lock:
        STATE.update(status=status, message=message, changed=time.time())
        if latest is not None:
            STATE["latest"] = latest


def _busy():
    return STATE["status"] in ("checking", "installing")


def check(install_if_newer=False):
    """Ask GitHub for the newest release (in a thread). install_if_newer: also install it."""
    if not enabled() or _busy():
        return
    _set("checking", "Checking GitHub for updates…")

    def work():
        try:
            info = updater.latest()
            extension.set_setting("update_last_check", str(int(time.time())))
            if not updater.update_available(info["version"], BRIDGE_VERSION, info.get("commit"), BRIDGE_COMMIT):
                _set("up_to_date", "Up to date (%s)" % version_text(), info)
                return
            _set("available", "Update available: %s (you have %s)" % (info["version"], version_text()), info)
            if install_if_newer:
                _install(info)
        except updater.UpdateError as e:
            _set("error", "Update check failed: %s" % e)
    threading.Thread(target=work, daemon=True).start()


def install():
    """Install the release found by the last check (in a thread)."""
    info = STATE.get("latest")
    if not info or _busy():
        return check(install_if_newer=True)
    threading.Thread(target=_install, args=(info,), daemon=True).start()


def _install(info):
    _set("installing", "Downloading %s…" % info["version"], info)
    try:
        data = updater.download(info["assets"]["plugin"])
        updater.install_plugin(data)
    except updater.UpdateError as e:
        _set("error", "Update failed: %s" % e)
        return
    server = _update_server()
    _set("installed", "Updated to %s. Restart Krita to use it.%s" % (info["version"], server), info)


def _update_server():
    """Upgrade the KritaPilot server too (stops the assistant service first so its files are not locked)."""
    try:
        from . import chat
        try:
            chat._request("POST", "/shutdown", {}, timeout=3)
        except Exception:  # noqa: BLE001 - not running
            pass
        args = extension.server_args() + ["--update"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(args, capture_output=True, text=True, timeout=180, creationflags=flags)
        out = json.loads((r.stdout or "{}").strip().splitlines()[-1] or "{}")
        if out.get("error"):
            return " (Server: %s)" % out["error"]
        return " The server is updating in the background." if out.get("started") else ""
    except FileNotFoundError:
        return " (The KritaPilot server is not installed here; only the plugin was updated.)"
    except Exception as e:  # noqa: BLE001
        return " (Server not updated: %s)" % e


def startup():
    """Called once Krita is up: check if due, and notify on the canvas when something happens."""
    if not enabled() or mode() == "off":
        return
    try:
        last = int(extension.setting("update_last_check", "0") or 0)
    except ValueError:
        last = 0
    if time.time() - last >= CHECK_EVERY:
        check(install_if_newer=mode() == "auto")
        _watch_and_notify()


def _watch_and_notify():
    """Show one on-canvas message when the background check finds or installs something."""
    try:
        from PyQt6.QtCore import QTimer
    except ImportError:
        from PyQt5.QtCore import QTimer
    started = time.time()
    timer = QTimer()
    STATE["_timer"] = timer  # keep a reference

    def poll():
        status = STATE["status"]
        if status in ("available", "installed") or time.time() - started > 300:
            timer.stop()
            if status in ("available", "installed"):
                _notify(STATE["message"] + ("" if status == "installed" else
                                            " Settings > Dockers > AI Bridge (MCP) to update."))
    timer.timeout.connect(poll)
    timer.start(1000)


def _notify(message):
    try:
        from krita import Krita
        try:
            from PyQt6.QtGui import QIcon
        except ImportError:
            from PyQt5.QtGui import QIcon
        win = Krita.instance().activeWindow()
        view = win.activeView() if win is not None else None
        if view is not None:
            view.showFloatingMessage("Krita MCP: " + message, QIcon(), 10000, 1)
    except Exception:  # noqa: BLE001 - notifications are best effort
        pass
