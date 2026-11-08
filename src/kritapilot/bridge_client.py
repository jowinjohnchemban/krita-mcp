"""Tiny synchronous client for the Krita-side bridge (newline-delimited JSON over loopback TCP)."""
import itertools
import json
import os
import socket

from . import config  # noqa: E402

DEFAULT_PORT = config.BRIDGE_PORT


class KritaError(RuntimeError):
    """Raised for any bridge problem; the message is safe to show to an LLM."""


def _token() -> str:
    t = os.environ.get("KRITAPILOT_TOKEN")
    if t:
        return t.strip()
    home = config.home()
    p = home / "token"
    try:
        return p.read_text().strip()
    except OSError:
        raise KritaError(
            "No bridge token found at %s. Start Krita with the 'KritaPilot' plugin enabled "
            "(it creates the token), or set KRITAPILOT_TOKEN." % p
        )


class BridgeClient:
    def __init__(self, host=None, port=None, timeout=180.0):
        self.host = host or os.environ.get("KRITAPILOT_HOST", "127.0.0.1")
        self.port = int(port or os.environ.get("KRITAPILOT_PORT", DEFAULT_PORT))
        self.timeout = timeout
        self._ids = itertools.count(1)

    def call(self, method: str, params: dict | None = None):
        req = {"id": next(self._ids), "token": _token(), "method": method, "params": params or {}}
        try:
            with socket.create_connection((self.host, self.port), timeout=5) as s:
                s.settimeout(self.timeout)
                s.sendall(json.dumps(req).encode() + b"\n")
                with s.makefile("rb") as f:
                    line = f.readline()
        except (ConnectionRefusedError, socket.timeout, OSError) as e:
            raise KritaError(
                "Cannot reach Krita on %s:%d (%s). Make sure Krita is running and the "
                "'KritaPilot' plugin is enabled (Settings > Configure Krita > Python Plugin Manager)."
                % (self.host, self.port, e)
            )
        if not line:
            raise KritaError("Krita closed the connection without answering")
        resp = json.loads(line)
        if not resp.get("ok"):
            err = resp.get("error") or {}
            raise KritaError("%s: %s" % (err.get("type", "Error"), err.get("message", "unknown error")))
        return resp.get("result")
