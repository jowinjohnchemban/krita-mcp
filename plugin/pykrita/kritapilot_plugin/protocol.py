"""Loopback, token-authenticated, newline-delimited JSON bridge server.

Qt-free on purpose. Socket threads only parse/authenticate and enqueue jobs;
`pump()` must be called from Krita's GUI thread (a QTimer) because the Krita
API is not thread-safe.

Wire format (one JSON object per line, both directions):
  request : {"id": 1, "token": "...", "method": "layer.list", "params": {...}}
  response: {"id": 1, "ok": true,  "result": ...}
            {"id": 1, "ok": false, "error": {"type": "...", "message": "..."}}
"""
import hmac
import json
import os
import queue
import secrets
import socket
import threading
import time
import traceback
from pathlib import Path

from . import config
from .util import BridgeError

DEFAULT_HOST = config.BRIDGE_HOST
DEFAULT_PORT = config.BRIDGE_PORT
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def config_dir() -> Path:
    return config.home()


def write_new_token() -> str:
    """Replace the token (clients must re-read it)."""
    p = config_dir() / "token"
    p.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(32)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t)
    return t


def load_or_create_token() -> str:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    p = d / "token"
    if p.exists():
        t = p.read_text().strip()
        if len(t) >= 32:
            return t
    return write_new_token()


class _Job:
    __slots__ = ("method", "params", "event", "ok", "value", "error", "cancelled")

    def __init__(self, method, params):
        self.method, self.params = method, params
        self.event = threading.Event()
        self.ok, self.value, self.error, self.cancelled = False, None, None, False


def _watch(method):
    """Note the command in hang.log: if Krita freezes while it runs, the main-thread watchdog (watchdog.py) dumps
    every thread's stack right below this line."""
    try:
        from . import watchdog
        watchdog.note("running %s" % method)
    except Exception:  # noqa: BLE001 - diagnostics must never break a request
        pass
    return False


def _unwatch(armed):
    pass


class BridgeServer:
    def __init__(self, dispatch, host=DEFAULT_HOST, port=DEFAULT_PORT, token=None,
                 request_timeout=120.0, log=None):
        if host not in LOOPBACK:
            raise ValueError("The bridge only binds to loopback addresses (got %r)" % host)
        self._dispatch = dispatch
        self.host = "127.0.0.1" if host == "localhost" else host
        self.port = port
        self.token = token or load_or_create_token()
        self._timeout = request_timeout
        self._log = log or (lambda msg: None)
        self._jobs = queue.Queue()
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self._slots = threading.BoundedSemaphore(8)
        self._pumping = False

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> int:
        if self.running:
            return self.port
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        s = socket.socket(family, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(8)
        s.settimeout(0.5)
        self.port = s.getsockname()[1]
        self._sock = s
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="kritapilot-accept", daemon=True)
        self._thread.start()
        self._log("Krita MCP bridge listening on %s:%d" % (self.host, self.port))
        return self.port

    def stop(self):
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self._sock = None

    # -- main thread ------------------------------------------------------
    def pump(self, budget=0.05):
        # A handler may spin the Qt event loop (which fires the pump timer again); run jobs one at a time.
        if self._pumping:
            return
        self._pumping = True
        try:
            self._pump(budget)
        finally:
            self._pumping = False

    def _pump(self, budget):
        end = time.monotonic() + budget
        while time.monotonic() < end:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            if job.cancelled:
                continue
            armed = _watch(job.method)
            try:
                job.value = self._dispatch(job.method, job.params)
                job.ok = True
            except BridgeError as e:
                job.error = ("BridgeError", str(e))
            except Exception as e:  # noqa: BLE001 - report everything to the caller
                self._log(traceback.format_exc())
                job.error = (type(e).__name__, str(e))
            finally:
                _unwatch(armed)
                job.event.set()

    # -- socket threads ---------------------------------------------------
    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        try:
            conn.settimeout(300)
            with conn, conn.makefile("rwb") as f:
                while not self._stop.is_set():
                    line = f.readline(MAX_MESSAGE_BYTES + 1)
                    if not line:
                        return
                    if not line.endswith(b"\n") and len(line) > MAX_MESSAGE_BYTES:
                        self._send(f, self._err(None, "BadRequest", "message too large"))
                        return
                    self._send(f, self._handle(line))
        except (OSError, ValueError):
            pass
        finally:
            self._slots.release()

    @staticmethod
    def _err(rid, typ, msg):
        return {"id": rid, "ok": False, "error": {"type": typ, "message": msg}}

    def _handle(self, line):
        try:
            req = json.loads(line)
        except ValueError:
            return self._err(None, "BadRequest", "invalid JSON")
        if not isinstance(req, dict):
            return self._err(None, "BadRequest", "request must be an object")
        rid = req.get("id")
        tok = req.get("token")
        if not isinstance(tok, str) or not hmac.compare_digest(tok.encode(), self.token.encode()):
            return self._err(rid, "AuthError", "invalid or missing token")
        method, params = req.get("method"), req.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return self._err(rid, "BadRequest", "need string 'method' and object 'params'")
        job = _Job(method, params)
        self._jobs.put(job)
        if not job.event.wait(self._timeout):
            job.cancelled = True
            return self._err(rid, "Timeout", "Krita did not answer in %ds (is a modal dialog open?)" % self._timeout)
        if job.ok:
            return {"id": rid, "ok": True, "result": job.value}
        return self._err(rid, *job.error)

    @staticmethod
    def _send(f, obj):
        try:
            data = json.dumps(obj)
        except (TypeError, ValueError) as e:
            data = json.dumps(BridgeServer._err(obj.get("id"), "SerializationError", str(e)))
        f.write(data.encode() + b"\n")
        f.flush()
