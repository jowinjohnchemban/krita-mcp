"""In-Krita assistant: chat with Gemini, Claude, GPT (or any OpenAI-compatible / local model) from Krita's
"AI Chat" docker. This module runs the agent loop - the model gets the same tools the MCP server exposes
and its tool calls are executed against Krita - and serves it to the docker over loopback HTTP.

    krita-mcp --agent            # started automatically by the docker; 127.0.0.1:5680, bridge-token auth

Sign-in: API keys entered in the docker are stored in KRITA_MCP_HOME/agent_keys.json (user-only file);
KRITA_MCP_<PROVIDER>_API_KEY environment variables work too.
"""
import asyncio
import base64
import hmac
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import oauth
from .providers import catalog, gemini, http, keys
from .providers.http import ProviderError, env, secret

DEFAULT_PORT = 5680
MAX_STEPS = 40
MAX_RESULT_CHARS = 12000

PROVIDERS = {
    "gemini": {"label": "Google Gemini", "model": "", "key_url": "https://aistudio.google.com/apikey",
               "env": ("KRITA_MCP_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")},
    "anthropic": {"label": "Anthropic Claude", "model": "",
                  "key_url": "https://console.anthropic.com/settings/keys",
                  "env": ("KRITA_MCP_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")},
    "openai": {"label": "OpenAI (or compatible)", "model": "", "key_url": "https://platform.openai.com/api-keys",
               "env": ("KRITA_MCP_OPENAI_API_KEY", "OPENAI_API_KEY")},
    "openrouter": {"label": "OpenRouter", "model": "", "key_url": "https://openrouter.ai/settings/keys",
                   "env": ("KRITA_MCP_OPENROUTER_API_KEY",)},
    "ollama": {"label": "Local model (Ollama / LM Studio)", "model": "", "key_url": "",
               "env": ()},
}

SYSTEM = """You are an AI art assistant working inside the user's Krita, controlling it through tools.
Do what the user asks directly in their open document (or create one). Work in steps: plan, act with tools,
then LOOK at the result with canvas_snapshot (and analyze_image when colour or light matters) and improve it
before you answer. Keep every element on its own layer. Never delete or overwrite the user's own work unless
asked; prefer new layers. For pictures from a prompt, start with create_artwork. Reply briefly and say what
you changed (layer names), and what the user could ask next."""


# ------------------------------------------------------------------ keys ("sign in", shared with the MCP server)
def save_key(provider, key, base_url=None):
    if key is None:
        keys.save(provider, remove=True)
    else:
        keys.save(provider, key=key, base_url=base_url)


def key_for(provider):
    names = PROVIDERS[provider]["env"]
    return secret(names[0], *names[1:]) if names else None


def base_url_for(provider):
    if provider == "ollama":
        return http.service_url("KRITA_MCP_OLLAMA_BASE_URL", "ollama", "http://127.0.0.1:11434/v1")
    if provider == "openai":
        return http.service_url("KRITA_MCP_OPENAI_BASE_URL", "openai", "https://api.openai.com/v1")
    if provider == "openrouter":
        return http.service_url("KRITA_MCP_OPENROUTER_BASE_URL", "openrouter", "https://openrouter.ai/api/v1")
    if provider == "gemini":
        return env("KRITA_MCP_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    return None


def status():
    """Every service (chat models and generative services), with the chat default models."""
    out = keys.status()
    for name, info in PROVIDERS.items():
        out[name]["default_model"] = info["model"] or ("your OpenRouter default" if name == "openrouter"
                                                         else "newest available")
        out[name]["chat"] = True
    return out


# ------------------------------------------------------------------ tools
def clean_schema(s):
    """Pydantic JSON schema -> compact schema: no titles/defaults, Optional[X] collapsed to X."""
    if isinstance(s, list):
        return [clean_schema(x) for x in s]
    if not isinstance(s, dict):
        return s
    if "anyOf" in s:
        opts = [o for o in s["anyOf"] if o.get("type") != "null"]
        if len(opts) == 1:
            merged = dict(opts[0])
            if s.get("description"):
                merged["description"] = s["description"]
            return clean_schema(merged)
    return {k: clean_schema(v) for k, v in s.items() if k not in ("title", "default")}


def gemini_schema(s):
    """OpenAPI-subset schema for Gemini's `parameters` (fallback when parametersJsonSchema is refused)."""
    s = clean_schema(s)
    if not isinstance(s, dict):
        return s
    out = {}
    for k, v in s.items():
        if k == "properties":
            out[k] = {n: gemini_schema(p) for n, p in v.items()}
        elif k == "items":
            out[k] = gemini_schema(v)
        elif k in ("type", "description", "enum", "required", "format", "nullable"):
            out[k] = v
    if out.get("type") == "object" and not out.get("properties"):
        out = {"type": "string", "description": (out.get("description", "") + " (a JSON object)").strip()}
    return out


async def tool_specs():
    from .server import mcp
    specs = []
    for t in await mcp.list_tools():
        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {"type": "object"}
        specs.append({"name": t.name, "description": (t.description or "")[:1800], "schema": clean_schema(schema)})
    return specs


async def run_tool(name, args):
    """Execute one tool like the MCP server would. Returns (text, [png bytes])."""
    from .server import TOOLS, Image, ToolError
    fn = TOOLS.get(name)
    if fn is None:
        return "error: unknown tool %r" % name, []
    for k, v in list(args.items()):  # Gemini fallback schema sends free-form objects as JSON strings
        if isinstance(v, str) and v[:1] in "{[":
            try:
                args[k] = json.loads(v)
            except ValueError:
                pass
    try:
        out = await fn(**args)
    except ToolError as e:
        return "error: %s" % e, []
    except TypeError as e:
        return "error: bad arguments: %s" % e, []
    except Exception as e:  # noqa: BLE001 - report, keep the conversation going
        return "error: %s: %s" % (type(e).__name__, e), []
    images, parts = [], out if isinstance(out, list) else [out]
    texts = []
    for p in parts:
        if isinstance(p, Image):
            images.append(p.data if isinstance(p.data, bytes) else base64.b64decode(p.data))
            texts.append("[image %d attached]" % len(images))
        else:
            texts.append(json.dumps(p, default=str))
    text = "\n".join(texts)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "... (truncated)"
    return text, images


# ------------------------------------------------------------------ model adapters
class Gemini:
    def __init__(self, model):
        self.key = key_for("gemini")
        if not self.key:
            raise ProviderError("Sign in to Gemini first (an API key from https://aistudio.google.com/apikey)")
        self.chosen, self.model, self.base = model, None, base_url_for("gemini")
        self.json_schema = True
        self.note = None

    def prepare(self):
        """Pick the model for this turn: the user's choice, else the newest Gemini Flash available now."""
        self.model = self.chosen or gemini.pick(self.base, self.key, "chat")
        return self.model

    def start(self, specs):
        self.specs = specs
        return []

    def _tools(self):
        if self.json_schema:
            decls = [{"name": s["name"], "description": s["description"], "parametersJsonSchema": s["schema"]}
                     for s in self.specs]
        else:
            decls = [{"name": s["name"], "description": s["description"], "parameters": gemini_schema(s["schema"])}
                     for s in self.specs]
        return [{"functionDeclarations": decls}]

    def user(self, history, text):
        history.append({"role": "user", "parts": [{"text": text}]})

    def step(self, history):
        body = {"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": history, "tools": self._tools()}
        try:
            r, used = gemini.post(self.base, self.key, self.model, "generateContent", body)
            if used != self.model:  # Google retired the model and named its replacement
                self.note = "Gemini model %s is no longer available; switched to %s." % (self.model, used)
                self.model = used
                if self.chosen:
                    self.chosen = used
        except ProviderError as e:
            if self.json_schema and "parametersJsonSchema" in str(e):
                self.json_schema = False
                return self.step(history)
            raise
        cand = (r.get("candidates") or [{}])[0]
        content = cand.get("content") or {"role": "model", "parts": []}
        content.setdefault("role", "model")
        history.append(content)  # verbatim: keeps thought signatures for the next turn
        text = "".join(p.get("text", "") for p in content.get("parts", []) if not p.get("thought"))
        calls = [(None, p["functionCall"]["name"], p["functionCall"].get("args") or {})
                 for p in content.get("parts", []) if p.get("functionCall")]
        if not calls and not text and cand.get("finishReason") not in (None, "STOP"):
            text = "(Gemini stopped: %s)" % cand.get("finishReason")
        return text, calls

    def results(self, history, results):
        parts = [{"functionResponse": {"name": name, "response": {"result": text}}} for _, name, text, _ in results]
        for _, name, _, images in results:
            for png in images:
                parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode()}})
        history.append({"role": "user", "parts": parts})


class OpenAICompat:
    def __init__(self, model, provider="openai"):
        self.base = base_url_for(provider).rstrip("/")
        self.key = key_for(provider) or ("local" if http.is_loopback_url(self.base) else None)
        if not self.key:
            raise ProviderError("Sign in to %s first (Services... > Sign in)" % PROVIDERS[provider]["label"])
        self.provider, self.chosen, self.model, self.note = provider, model, None, None
        self.headers = {"Authorization": "Bearer " + self.key}
        if provider == "openrouter":  # app attribution, as OpenRouter suggests
            self.headers.update({"HTTP-Referer": "https://github.com/jowinjohnchemban/krita-mcp",
                                 "X-OpenRouter-Title": "Krita MCP"})

    def prepare(self):
        """The user's model, else the newest suitable one listed now (OpenRouter: the account's default)."""
        if self.chosen or self.provider == "openrouter":
            self.model = self.chosen or None
        elif self.provider == "ollama" or http.is_loopback_url(self.base):
            self.model = catalog.first(catalog.local_models(self.base), "local model (install one first)")
        else:
            self.model = catalog.first(catalog.openai_models(self.base, self.key), "OpenAI chat model")
        return self.model or "your OpenRouter default model"

    def start(self, specs):
        self.tools = [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                                        "parameters": s["schema"]}} for s in specs]
        return [{"role": "system", "content": SYSTEM}]

    def user(self, history, text):
        history.append({"role": "user", "content": text})

    def step(self, history):
        body = {"messages": history, "tools": self.tools}
        if self.model:  # OpenRouter: no model = the account's default model
            body["model"] = self.model
        r = http.post_json(self.base + "/chat/completions", body, self.headers)
        msg = (r.get("choices") or [{}])[0].get("message") or {}
        entry = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            entry["tool_calls"] = msg["tool_calls"]
        history.append(entry)
        calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append((tc["id"], tc["function"]["name"], args))
        return msg.get("content") or "", calls

    def results(self, history, results):
        images = []
        for cid, name, text, imgs in results:
            history.append({"role": "tool", "tool_call_id": cid, "content": text})
            images += imgs
        if images:  # tool messages cannot carry images: show them in a user turn
            history.append({"role": "user", "content": [{"type": "text", "text": "Images returned by the tools:"}] + [
                {"type": "image_url", "image_url": {"url": http.data_uri(p)}} for p in images]})


class Claude:
    def __init__(self, model):
        key = key_for("anthropic")
        if not key:
            raise ProviderError("Sign in to Claude first (an API key from https://console.anthropic.com)")
        try:
            import anthropic
        except ImportError:
            raise ProviderError("install the Anthropic SDK: pip install 'krita-mcp[anthropic]'") from None
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=key)
        self.key, self.chosen, self.model, self.note = key, model, None, None

    def prepare(self):
        """The user's model, else the newest Claude (Opus first) listed now."""
        self.model = self.chosen or catalog.first(catalog.anthropic_models(self.key), "Claude model")
        return self.model

    def start(self, specs):
        self.tools = [{"name": s["name"], "description": s["description"], "input_schema": s["schema"]}
                      for s in specs]
        return []

    def user(self, history, text):
        history.append({"role": "user", "content": text})

    def step(self, history):
        kw = dict(model=self.model, max_tokens=16000, system=SYSTEM, tools=self.tools, messages=history)
        a = self.anthropic
        try:
            try:  # server-side refusal fallback, as recommended for current Claude models
                resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                        fallbacks="default", **kw)
            except TypeError:
                resp = self.client.messages.create(**kw)
        except a.AuthenticationError:
            raise ProviderError("Claude rejected the API key; sign in again") from None
        except a.RateLimitError:
            raise ProviderError("Claude rate limit reached; try again shortly") from None
        except a.APIStatusError as e:
            raise ProviderError("Claude API error %s: %s" % (e.status_code, e.message)) from None
        except a.APIConnectionError:
            raise ProviderError("cannot reach the Claude API") from None
        history.append({"role": "assistant", "content": resp.content})  # whole content (keeps thinking blocks)
        if resp.stop_reason == "refusal":
            return "(Claude declined this request.)", []
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [(b.id, b.name, b.input or {}) for b in resp.content if b.type == "tool_use"]
        return text, calls

    def results(self, history, results):
        blocks = []
        for cid, name, text, imgs in results:
            content = [{"type": "text", "text": text}] + [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(p).decode()}} for p in imgs]
            blocks.append({"type": "tool_result", "tool_use_id": cid, "content": content,
                           "is_error": text.startswith("error:")})
        history.append({"role": "user", "content": blocks})


def available_models(provider):
    """The chat models this account can use right now, newest first (for the panel's model list)."""
    if provider == "gemini":
        key = key_for("gemini")
        if not key:
            raise ProviderError("sign in to Gemini to list its models")
        return catalog.gemini_models(base_url_for("gemini"), key, "chat")
    if provider == "anthropic":
        key = key_for("anthropic")
        if not key:
            raise ProviderError("sign in to Claude to list its models")
        return catalog.anthropic_models(key)
    if provider == "openrouter":
        return catalog.openrouter_models(base_url_for("openrouter"), key_for("openrouter"))
    if provider == "ollama":
        return catalog.local_models(base_url_for("ollama"))
    if provider == "openai":
        base = base_url_for("openai")
        key = key_for("openai") or ("local" if http.is_loopback_url(base) else None)
        if not key:
            raise ProviderError("sign in to OpenAI to list its models")
        return catalog.openai_models(base, key)
    raise ProviderError("unknown provider %r" % provider)


def make_model(provider, model=None):
    if provider not in PROVIDERS:
        raise ProviderError("unknown provider %r; choose from %s" % (provider, ", ".join(PROVIDERS)))
    model = model or env("KRITA_MCP_%s_CHAT_MODEL" % provider.upper()) or None
    if provider == "gemini":
        return Gemini(model)
    if provider == "anthropic":
        return Claude(model)
    return OpenAICompat(model, provider)


# ------------------------------------------------------------------ sessions and the agent loop
class Session:
    def __init__(self, provider, model):
        self.id = uuid.uuid4().hex[:12]
        self.provider, self.model_name = provider, model
        self.model = None
        self.history = []
        self.events = []
        self.busy = False
        self.stop = False
        self.lock = threading.Lock()

    def emit(self, kind, **data):
        with self.lock:
            self.events.append(dict(data, type=kind, n=len(self.events), t=round(time.time(), 2)))


def _short(args):
    s = json.dumps(args, default=str)
    return s if len(s) < 160 else s[:157] + "..."


async def run_turn(sess, text):
    if sess.model is None:
        sess.model = make_model(sess.provider, sess.model_name)
        sess.history = sess.model.start(await tool_specs())
    m = sess.model
    used = await asyncio.to_thread(m.prepare)  # models are listed fresh for every message
    sess.emit("note", text="Model: %s" % used)
    m.user(sess.history, text)
    for step in range(MAX_STEPS):
        if sess.stop:
            sess.emit("assistant", text="(stopped)")
            return
        reply, calls = await asyncio.to_thread(m.step, sess.history)
        if getattr(m, "note", None):
            sess.emit("note", text=m.note)
            m.note = None
        if reply.strip():
            sess.emit("assistant", text=reply.strip())
        if not calls:
            return
        results = []
        for cid, name, args in calls:
            sess.emit("tool", name=name, args=_short(args))
            out, images = await run_tool(name, dict(args))
            sess.emit("tool_result", name=name, ok=not out.startswith("error:"), text=out[:300],
                      images=len(images))
            results.append((cid, name, out, images))
        m.results(sess.history, results)
    sess.emit("assistant", text="(stopped after %d steps - say 'continue' to go on)" % MAX_STEPS)


def _worker(sess, text):
    try:
        asyncio.run(run_turn(sess, text))
    except ProviderError as e:
        sess.emit("error", text=str(e))
    except Exception as e:  # noqa: BLE001
        sess.emit("error", text="%s: %s" % (type(e).__name__, e))
    finally:
        sess.busy = False
        sess.emit("done")


SESSIONS = {}


def chat(session_id, provider, model, text):
    sess = SESSIONS.get(session_id) if session_id else None
    if sess is None or sess.provider != provider or (model and sess.model_name != model):
        sess = Session(provider, model)
        SESSIONS[sess.id] = sess
    if sess.busy:
        raise ValueError("the assistant is still working on the previous message")
    sess.busy, sess.stop = True, False
    sess.emit("user", text=text)
    threading.Thread(target=_worker, args=(sess, text), daemon=True).start()
    return sess


# ------------------------------------------------------------------ loopback HTTP for the docker
def _bridge_token():
    from .bridge_client import _token
    return _token()


class Handler(BaseHTTPRequestHandler):
    server_version = "krita-mcp-agent"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        given = (self.headers.get("Authorization") or "")[7:].strip().encode()
        try:
            want = _bridge_token().encode()
        except Exception:  # noqa: BLE001
            return False
        return hmac.compare_digest(given, want)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        path, _, query = self.path.partition("?")
        q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path == "/health":
            return self._send(200, {"ok": True, "providers": status()})
        if path == "/models":
            try:
                return self._send(200, {"models": available_models(q.get("provider", "gemini"))})
            except ProviderError as e:
                return self._send(200, {"models": [], "error": str(e)})
        if path == "/oauth/status":
            return self._send(200, oauth.poll(q.get("id", "")))
        if path == "/events":
            sess = SESSIONS.get(q.get("session", ""))
            if sess is None:
                return self._send(404, {"error": "no such session"})
            since = int(q.get("since", "0") or 0)
            with sess.lock:
                ev = sess.events[since:]
            return self._send(200, {"events": ev, "busy": sess.busy})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        try:
            b = self._body()
            if self.path == "/chat":
                text = str(b.get("text") or "").strip()
                if not text:
                    raise ValueError("empty message")
                sess = chat(b.get("session"), b.get("provider") or "gemini", b.get("model") or None, text[:20000])
                return self._send(200, {"session": sess.id})
            if self.path == "/stop":
                sess = SESSIONS.get(b.get("session", ""))
                if sess:
                    sess.stop = True
                return self._send(200, {"ok": True})
            if self.path == "/reset":
                SESSIONS.pop(b.get("session", ""), None)
                return self._send(200, {"ok": True})
            if self.path == "/signin":
                key = b.get("key") or ""
                if key and b.get("verify", True):
                    ok, detail = oauth.verify(b["provider"], key)
                    if ok is False:
                        return self._send(400, {"error": "%s - not saved" % detail})
                keys.save(b["provider"], key=key, base_url=b.get("base_url"))
                return self._send(200, {"providers": status()})
            if self.path == "/verify":
                ok, detail = oauth.verify(b["provider"], b.get("key"))
                return self._send(200, {"ok": ok, "detail": detail})
            if self.path == "/oauth/start":
                return self._send(200, oauth.start(b["provider"]))
            if self.path == "/signout":
                keys.save(b["provider"], remove=True)
                return self._send(200, {"providers": status()})
        except (ValueError, KeyError) as e:
            return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "not found"})


class _ExclusiveServer(ThreadingHTTPServer):
    # Windows' SO_REUSEADDR would let a second service share the port with a running one (and requests
    # would go to either); one service per port, and a clear error if it is taken.
    allow_reuse_address = False


def serve(port=None, host="127.0.0.1", ready=None):
    if port is None:  # 0 is valid: a free port chosen by the OS (tests)
        port = int(env("KRITA_MCP_AGENT_PORT", str(DEFAULT_PORT)))
    try:
        srv = _ExclusiveServer((host, int(port)), Handler)
    except OSError as e:
        raise SystemExit("the assistant service cannot use %s:%s (%s); is it already running?" % (host, port, e))
    if ready:
        ready(srv)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()

