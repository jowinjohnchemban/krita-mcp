"""Browser sign-in.

OpenRouter: real OAuth (PKCE, S256) with no app registration - Krita opens the browser, the user approves,
OpenRouter redirects to a one-shot listener on localhost, and the code is exchanged for an API key that is
saved like any other key. One OpenRouter account reaches Gemini, Claude, GPT and hundreds of other models.

Other services have no third-party OAuth; the docker offers a browser-assisted flow instead (opens the
provider's key page and picks the key up from the clipboard), and verify() checks a key before it is saved.
"""
import base64
import hashlib
import html
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .providers import compat, http, keys
from .providers.http import ProviderError, env

TIMEOUT = 600  # OpenRouter codes expire after 10 minutes
FLOWS = {}


def pkce_pair():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _page(title, text):
    return ("<!doctype html><meta charset=utf-8><title>%s</title><body style='font-family:sans-serif;"
            "background:#1e1e24;color:#eee;display:grid;place-items:center;height:90vh'><div><h2>%s</h2><p>%s</p>"
            "</div>" % (html.escape(title), html.escape(title), html.escape(text))).encode()


class Flow:
    def __init__(self, provider):
        self.id = secrets.token_urlsafe(9)
        self.provider = provider
        self.state = secrets.token_urlsafe(24)
        self.verifier, self.challenge = pkce_pair()
        self.status, self.error = "pending", None
        self.started = time.time()
        flow = self

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                if u.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                q = dict(urllib.parse.parse_qsl(u.query))
                ok, title, text = flow.complete(q)
                body = _page(title, text)
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if flow.status != "pending":
                    threading.Thread(target=flow.server.shutdown, daemon=True).start()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
        self.port = self.server.server_address[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        timer = threading.Timer(TIMEOUT, self._expire)
        timer.daemon = True
        timer.start()
        self.server.serve_forever()
        timer.cancel()
        self.server.server_close()

    def _expire(self):
        if self.status == "pending":
            self.status, self.error = "error", "sign-in timed out; try again"
        self.server.shutdown()

    @property
    def callback_url(self):
        return "http://localhost:%d/callback" % self.port

    def auth_url(self):
        base = env("KRITAPILOT_OPENROUTER_AUTH_URL", "https://openrouter.ai/auth")
        return base + "?" + urllib.parse.urlencode({
            "callback_url": self.callback_url, "code_challenge": self.challenge, "code_challenge_method": "S256",
            "state": self.state, "key_label": "Krita MCP"})

    def complete(self, q):
        if self.status != "pending":
            return False, "Already finished", "You can close this tab."
        if not q.get("code"):
            self.status, self.error = "error", "sign-in was cancelled"
            return False, "Sign-in cancelled", "Nothing was changed. You can close this tab."
        if not secrets.compare_digest(q.get("state", ""), self.state):
            return False, "Sign-in rejected", "This sign-in link does not belong to Krita's current request."
        try:
            api = env("KRITAPILOT_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
            r = http.post_json(api + "/auth/keys", {"code": q["code"], "code_verifier": self.verifier,
                                                    "code_challenge_method": "S256"}, timeout=60)
            key = r.get("key")
            if not key:
                raise ProviderError("OpenRouter did not return a key")
            keys.save(self.provider, key=key)
        except ProviderError as e:
            self.status, self.error = "error", str(e)
            return False, "Sign-in failed", str(e)
        self.status = "done"
        return True, "Signed in to OpenRouter", "Krita is connected. You can close this tab and go back to Krita."


def start(provider):
    if provider != "openrouter":
        raise ValueError("%s has no browser sign-in; use 'Get a key in the browser' instead" % provider)
    for fid in [f for f, fl in FLOWS.items() if time.time() - fl.started > TIMEOUT + 60]:
        FLOWS.pop(fid, None)
    flow = Flow(provider)
    FLOWS[flow.id] = flow
    return {"id": flow.id, "url": flow.auth_url(), "callback": flow.callback_url}


def poll(flow_id):
    flow = FLOWS.get(flow_id)
    if flow is None:
        return {"status": "error", "error": "unknown sign-in"}
    return {"status": flow.status, "error": flow.error}


# ------------------------------------------------------------------ key checks
def verify(provider, key):
    """True / False if the service says so, None when it cannot be checked cheaply."""
    key = (key or "").strip()
    if not key:
        return False, "empty key"
    try:
        if provider == "gemini":
            base = env("KRITAPILOT_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
            http.get_json(base + "/models?pageSize=1", {"x-goog-api-key": key}, timeout=20)
        elif provider in ("openai", "openrouter"):
            base = http.service_url("KRITAPILOT_%s_BASE_URL" % provider.upper(), provider,
                                    "https://api.openai.com/v1" if provider == "openai"
                                    else "https://openrouter.ai/api/v1")
            http.get_json(base + "/models", {"Authorization": "Bearer " + key}, timeout=20)
        elif provider in compat.COMPAT:
            http.get_json(compat.base_url(provider) + "/models", {"Authorization": "Bearer " + key}, timeout=20)
        elif provider == "anthropic":
            try:
                import anthropic
            except ImportError:
                return None, "pip install anthropic to check Claude keys"
            try:
                anthropic.Anthropic(api_key=key).models.list(limit=1)
            except anthropic.AuthenticationError:
                return False, "Claude rejected the key"
            except anthropic.APIError as e:
                return None, "could not check: %s" % e
        elif provider == "replicate":
            http.get_json("https://api.replicate.com/v1/account", {"Authorization": "Bearer " + key}, timeout=20)
        elif provider == "stability":
            http.get_json("https://api.stability.ai/v1/user/account", {"Authorization": "Bearer " + key}, timeout=20)
        else:
            return None, "this service's keys cannot be checked in advance"
    except ProviderError as e:
        msg = str(e)
        if any(c in msg for c in ("HTTP 400", "HTTP 401", "HTTP 403")):
            return False, "the service rejected the key"
        return None, "could not check the key (%s)" % msg[:160]
    return True, "key works"
