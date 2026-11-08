"""A plain-text trace of what happened, one line per action, for the Logs window in Krita.

Files live in KRITA_MCP_HOME/logs (default ~/.krita-mcp/logs):
  krita.log      - the Krita plugin: every bridge command the AI ran, panel actions, sign-ins, errors
  assistant.log  - the AI Chat assistant service: messages, models, tool calls and results, errors
Each file is kept under MAX_BYTES (the older half moves to NAME.1). Keys never reach a log: anything that
looks like a key, token or password is masked before writing, and long data (images) is cut.

The same file is shipped in the server package (kritapilot/tracelog.py) and the plugin
(krita_mcp_bridge/tracelog.py); a test keeps them identical.
"""
import datetime
import os
import re
import threading

MAX_BYTES = 2_000_000
LINE_CHARS = 1500
_LOCK = threading.Lock()

_SECRETS = [
    # "api_key": "...", token=..., Authorization: Bearer ...
    (re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|token|secret|password|authorization|x-goog-api-key|key)"
                r"[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?)[^\s\"',;}&]{4,}"), r"\1***"),
    (re.compile(r"(?i)\bbearer\s+[^\s\"',;}]{8,}"), "Bearer ***"),
    (re.compile(r"\b(?:sk|pk|rk|gsk|hf|r8|xai|fw|nvapi|csk|tgp_v1)[-_][A-Za-z0-9_\-]{12,}"), "***"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "***"),
    (re.compile(r"[A-Za-z0-9+/=_]{48,}"), "…"),  # long tokens and base64 data
]


def home():
    return os.environ.get("KRITA_MCP_HOME") or os.path.join(os.path.expanduser("~"), ".krita-mcp")


def path(name):
    return os.path.join(home(), "logs", name)


def redact(text):
    text = str(text)
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


def write(name, source, message):
    """Append one line; never raises (a log must not break what it records)."""
    msg = " ".join(redact(message).split())
    if len(msg) > LINE_CHARS:
        msg = msg[:LINE_CHARS - 1] + "…"
    now = datetime.datetime.now()
    line = "%s.%03d %-9s %s\n" % (now.strftime("%Y-%m-%d %H:%M:%S"), now.microsecond // 1000, source, msg)
    with _LOCK:
        try:
            p = path(name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            try:
                if os.path.getsize(p) > MAX_BYTES:
                    os.replace(p, p + ".1")
            except OSError:
                pass  # not there yet, or held open elsewhere: rotate next time
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
