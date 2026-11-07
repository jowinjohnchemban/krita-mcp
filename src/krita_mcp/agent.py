"""In-Krita assistant: chat with Gemini, Claude, GPT (or any OpenAI-compatible / local model) from Krita's
"AI Chat" docker. This module runs the agent loop - the model gets the same tools the MCP server exposes
and its tool calls are executed against Krita - and serves it to the docker over loopback HTTP.

    krita-mcp --agent            # started automatically by the docker; 127.0.0.1:5680, bridge-token auth

Sign-in: API keys entered in the docker are stored in KRITA_MCP_HOME/agent_keys.json (user-only file);
KRITA_MCP_<PROVIDER>_API_KEY environment variables work too.

Spending: every step resends the conversation, so it is kept lean - only the newest pictures stay in it
(KRITA_MCP_CHAT_KEEP_IMAGES, default 2), earlier messages' long tool results are shortened, snapshots are
sent at KRITA_MCP_CHAT_IMAGE_SIZE (768 px) unless the model asks for more, Claude's prompt cache is used,
and a turn stops when the same error keeps repeating. Each answer reports the tokens it used.
"""
import asyncio
import base64
import hmac
import inspect
import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import oauth, skills, tracelog
from .providers import catalog, compat, free, gemini, http, keys
from .providers.http import ProviderError, env, secret

DEFAULT_PORT = 5680
MAX_RESULT_CHARS = 12000
OLD_RESULT_CHARS = 600  # tool results of earlier messages are cut to this when resent
DROPPED = "[earlier picture removed to save tokens - take a new canvas_snapshot if you need to see it]"


def max_steps():
    return int(env("KRITA_MCP_CHAT_MAX_STEPS", "40"))


def keep_images():
    return max(0, int(env("KRITA_MCP_CHAT_KEEP_IMAGES", "2")))


def chat_image_size():
    return int(env("KRITA_MCP_CHAT_IMAGE_SIZE", "768"))

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
    "pollinations": {"label": "Pollinations (free, no sign-in)", "model": "",
                     "key_url": "https://enter.pollinations.ai/keys", "env": ("KRITA_MCP_POLLINATIONS_API_KEY",)},
}

for _name, (_label, _base, _env, _url, _use, _images) in compat.COMPAT.items():
    PROVIDERS[_name] = {"label": _label, "model": "", "key_url": _url, "env": (_env,)}

SYSTEM = """You are an AI art assistant working inside the user's Krita, controlling it through tools.
Do what the user asks directly in their open document (or create one). Work in steps: plan, act with tools,
then LOOK at the result with canvas_snapshot (and analyze_image when colour or light matters) and improve it
before you answer. Keep every element on its own layer. Never delete or overwrite the user's own work unless
asked; prefer new layers. For pictures from a prompt, start with create_artwork. For a bigger task, the
message may name skills that fit: load one with use_skill only if it helps (find_skills searches more).
Be economical - the user pays for every step: plan before acting, put many strokes or edits in one batch
call, look with canvas_snapshot after a pass of work rather than after every stroke, generate one image
unless the user wants options, and never repeat a call that failed the same way. Reply briefly and say what
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
    if provider in compat.COMPAT:
        return compat.base_url(provider)
    return None


def status():
    """Every service (chat models and generative services), with the chat default models."""
    out = keys.status()
    for name, info in PROVIDERS.items():
        out[name]["default_model"] = info["model"] or "newest available"
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


def trace(source, message):
    """One line in ~/.krita-mcp/logs/assistant.log (shown by the Logs window in Krita; keys are masked)."""
    tracelog.write("assistant.log", source, message)


async def run_tool(name, args):
    """Execute one tool like the MCP server would. Returns (text, [png bytes])."""
    t0 = time.monotonic()
    text, images = await _run_tool(name, args)
    trace("tool", "%s %s %.1fs%s -> %s" % ("✓" if not text.startswith("error:") else "✗", name,
                                           time.monotonic() - t0, " +%d image(s)" % len(images) if images else "",
                                           text[:400]))
    return text, images


async def _run_tool(name, args):
    from .server import TOOLS, Image, ToolError
    fn = TOOLS.get(name)
    if fn is None:
        return "error: unknown tool %r" % name, []
    try:  # pictures for the model: a size it can judge, not the largest (every pixel is paid for)
        if "max_size" in inspect.signature(fn).parameters and args.get("max_size") is None:
            args["max_size"] = chat_image_size()
    except (TypeError, ValueError):
        pass
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
class _Adapter:
    usage = None
    note = None

    def reset_usage(self):
        self.usage = {"input": 0, "cached": 0, "output": 0, "requests": 0, "cost": 0.0}

    def add_usage(self, inp=0, out=0, cached=0, cost=None):
        if self.usage is None:
            self.reset_usage()
        u = self.usage
        u["input"] += int(inp or 0)
        u["output"] += int(out or 0)
        u["cached"] += int(cached or 0)
        u["requests"] += 1
        if cost:
            u["cost"] += float(cost)


def _k(n):
    return "%.1fk" % (n / 1000.0) if n >= 1000 else str(n)


def usage_text(u):
    if not u or not (u.get("input") or u.get("output")):  # the provider reported nothing
        return None
    t = "Used %s tokens in, %s out over %d request%s" % (_k(u["input"]), _k(u["output"]), u["requests"],
                                                          "" if u["requests"] == 1 else "s")
    if u.get("cached"):
        t += " (%s of the input from cache, billed at a fraction)" % _k(u["cached"])
    if u.get("cost"):
        t += ", cost %.4f credits" % u["cost"]
    return t


def _compact(history, turn_start, images_of, drop_image, results_of):
    """Keep resending cheap: only the newest pictures stay; long tool results of earlier turns are cut."""
    keep, seen = keep_images(), 0
    for msg in reversed(history):
        for holder, i in reversed(images_of(msg)):
            seen += 1
            if seen > keep:
                drop_image(holder, i)
    for msg in history[:turn_start]:
        for holder, key in results_of(msg):
            text = holder.get(key)
            if isinstance(text, str) and len(text) > OLD_RESULT_CHARS + 40:
                holder[key] = text[:OLD_RESULT_CHARS] + "... (shortened; ask again if needed)"


class Gemini(_Adapter):
    def __init__(self, model):
        self.key = key_for("gemini")
        if not self.key:
            raise ProviderError("Sign in to Gemini first (an API key from https://aistudio.google.com/apikey)")
        self.chosen, self.model, self.base = model, None, base_url_for("gemini")
        self.json_schema = True
        self.thinking = True  # off for the session if the model refuses the setting
        self.note = None

    def thinking_config(self):
        """Gemini 3 thinks at length by default (a minute per step is common); a painting assistant mostly
        needs quick tool calls. KRITA_MCP_GEMINI_THINKING: low (default), medium, high, or off (Google's default)."""
        level = env("KRITA_MCP_GEMINI_THINKING", "low").strip().lower()
        m = re.match(r"gemini-(\d+)", self.model or "")
        if not self.thinking or level in ("off", "default", "") or not m or int(m.group(1)) < 3:
            return None
        return {"thinkingConfig": {"thinkingLevel": level}}

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
        if self.thinking_config():
            body["generationConfig"] = self.thinking_config()
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
            if "generationConfig" in body and "HTTP 400" in str(e) and "thinking" in str(e).lower():
                self.thinking = False  # this model takes no thinking level: Google's default then
                return self.step(history)
            raise
        um = r.get("usageMetadata") or {}
        self.add_usage(um.get("promptTokenCount"), (um.get("candidatesTokenCount") or 0)
                       + (um.get("thoughtsTokenCount") or 0), um.get("cachedContentTokenCount"))
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

    def compact(self, history, turn_start):
        def images_of(msg):
            return [(msg["parts"], i) for i, p in enumerate(msg.get("parts") or []) if "inline_data" in p]

        def drop(parts, i):
            parts[i] = {"text": DROPPED}

        def results_of(msg):
            return [(p["functionResponse"]["response"], "result") for p in msg.get("parts") or []
                    if isinstance(p.get("functionResponse"), dict)
                    and isinstance(p["functionResponse"].get("response"), dict)]
        _compact(history, turn_start, images_of, drop, results_of)


class OpenAICompat(_Adapter):
    def __init__(self, model, provider="openai"):
        self.provider, self.chosen, self.model, self.note = provider, model, None, None
        if provider == "pollinations":  # free: works without a key (a free key gets the newer API)
            self.key = key_for(provider)
            self.url, self.headers = free.pollinations_chat(self.key)
            return
        self.base = base_url_for(provider).rstrip("/")
        self.url = self.base + "/chat/completions"
        self.key = key_for(provider)
        if not self.key and provider in compat.KEYLESS:  # free, anonymous
            self.headers = {}
            return
        self.key = self.key or ("local" if http.is_loopback_url(self.base) else None)
        if not self.key:
            raise ProviderError("Sign in to %s first (Services... > Sign in)" % PROVIDERS[provider]["label"])
        self.headers = {"Authorization": "Bearer " + self.key}
        if provider == "openrouter":  # app attribution, as OpenRouter suggests
            self.headers.update({"HTTP-Referer": "https://github.com/jowinjohnchemban/krita-mcp",
                                 "X-OpenRouter-Title": "Krita MCP"})

    def prepare(self):
        """The user's model, else the newest suitable one listed now (OpenRouter: the account's default)."""
        if self.chosen:
            self.model = self.chosen
        elif self.provider == "pollinations":
            self.model = catalog.first(free.pollinations_models(self.key), "free Pollinations model with tools")
        elif self.provider == "openrouter":  # an account need not have a default model: choose from its list
            self.model = catalog.first(catalog.openrouter_models(self.base, self.key), "OpenRouter model with tools")
        elif self.provider in compat.COMPAT:
            self.model = catalog.first(compat.chat_models(self.provider), "%s chat model" % self.provider)
        elif self.provider == "ollama" or http.is_loopback_url(self.base):
            self.model = catalog.first(catalog.local_models(self.base), "local model (install one first)")
        else:
            self.model = catalog.first(catalog.openai_models(self.base, self.key), "OpenAI chat model")
        if self.provider == "pollinations":  # free models that cannot see must not be sent pictures
            self.blind = self.model not in free.pollinations_models(self.key, seeing=True)
        return self.model

    def start(self, specs):
        self.tools = [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                                        "parameters": s["schema"]}} for s in specs]
        return [{"role": "system", "content": SYSTEM}]

    def user(self, history, text):
        history.append({"role": "user", "content": text})

    def step(self, history):
        messages = history
        if self.provider == "openrouter" and (self.model or "").startswith("anthropic/") and history \
                and history[0].get("role") == "system" and isinstance(history[0].get("content"), str):
            # Claude through OpenRouter caches only what is marked: the system prompt (tools come before it)
            messages = [{"role": "system", "content": [{"type": "text", "text": history[0]["content"],
                                                        "cache_control": {"type": "ephemeral"}}]}] + history[1:]
        body = {"messages": messages, "tools": self.tools}
        if self.model:
            body["model"] = self.model
        if self.provider == "openrouter":
            body["usage"] = {"include": True}  # report the cost of each request
        limit_key = "max_completion_tokens" if self.provider == "openai" else "max_tokens"
        body[limit_key] = int(env("KRITA_MCP_CHAT_MAX_TOKENS", "4096"))
        try:
            r = http.post_json(self.url, body, self.headers)
        except ProviderError as e:
            m = re.search(r"can only afford (\d+)", str(e))
            if not m or int(m.group(1)) < 600:
                if "HTTP 402" in str(e):
                    raise ProviderError("%s: not enough credits for this request. Add credits on the "
                                        "provider's site, or choose a cheaper or free model in the model "
                                        "box." % PROVIDERS[self.provider]["label"]) from None
                raise
            body[limit_key] = int(m.group(1)) - 256  # what the remaining balance can pay for
            r = http.post_json(self.url, body, self.headers)
        u = r.get("usage") or {}
        self.add_usage(u.get("prompt_tokens"), u.get("completion_tokens"),
                       (u.get("prompt_tokens_details") or {}).get("cached_tokens"), u.get("cost"))
        msg = (r.get("choices") or [{}])[0].get("message") or {}
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc.get("function"), dict):
                tc["function"]["name"] = clean_tool_name(tc["function"].get("name"))
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

    blind = False  # True: the model takes no pictures (sending them would only cost tokens or fail)

    def results(self, history, results):
        images = []
        for cid, name, text, imgs in results:
            if imgs and self.blind:
                text += ("\n[this model cannot see pictures: check the canvas with analyze_image, read_pixels "
                         "or find_color instead]")
                imgs = []
            history.append({"role": "tool", "tool_call_id": cid, "content": text})
            images += imgs
        if images:  # tool messages cannot carry images: show them in a user turn
            history.append({"role": "user", "content": [{"type": "text", "text": "Images returned by the tools:"}] + [
                {"type": "image_url", "image_url": {"url": http.data_uri(p)}} for p in images]})

    def compact(self, history, turn_start):
        def images_of(msg):
            c = msg.get("content")
            return [(c, i) for i, b in enumerate(c) if isinstance(b, dict) and b.get("type") == "image_url"] \
                if isinstance(c, list) else []

        def drop(content, i):
            content[i] = {"type": "text", "text": DROPPED}

        def results_of(msg):
            return [(msg, "content")] if msg.get("role") == "tool" else []
        _compact(history, turn_start, images_of, drop, results_of)


class Claude(_Adapter):
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

    @staticmethod
    def _cached(history):
        """Prompt caching: tools + system are one cached prefix, and the conversation so far another (moved to
        the newest message every step), so each step pays full price only for what is new."""
        if not history:
            return history
        last = dict(history[-1])
        content = last.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content or [])
        if blocks and isinstance(blocks[-1], dict):
            blocks[-1] = dict(blocks[-1], cache_control={"type": "ephemeral"})
            last["content"] = blocks
        return history[:-1] + [last]

    def step(self, history):
        system = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
        kw = dict(model=self.model, max_tokens=int(env("KRITA_MCP_CHAT_MAX_TOKENS", "16000")), system=system,
                  tools=self.tools, messages=self._cached(history))
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
        u = getattr(resp, "usage", None)
        if u is not None:
            read = getattr(u, "cache_read_input_tokens", 0) or 0
            self.add_usage((getattr(u, "input_tokens", 0) or 0) + read
                           + (getattr(u, "cache_creation_input_tokens", 0) or 0), getattr(u, "output_tokens", 0), read)
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

    def compact(self, history, turn_start):
        def tool_blocks(msg):
            c = msg.get("content") if msg.get("role") == "user" else None
            return [b for b in c if isinstance(b, dict) and b.get("type") == "tool_result"] \
                if isinstance(c, list) else []

        def images_of(msg):
            return [(b["content"], i) for b in tool_blocks(msg) if isinstance(b.get("content"), list)
                    for i, x in enumerate(b["content"]) if isinstance(x, dict) and x.get("type") == "image"]

        def drop(content, i):
            content[i] = {"type": "text", "text": DROPPED}

        def results_of(msg):
            return [(x, "text") for b in tool_blocks(msg) if isinstance(b.get("content"), list)
                    for x in b["content"] if isinstance(x, dict) and x.get("type") == "text"]
        _compact(history, turn_start, images_of, drop, results_of)


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
    if provider == "pollinations":
        return free.pollinations_models(key_for("pollinations"))
    if provider in compat.COMPAT:
        return compat.chat_models(provider)
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
        if kind == "tool":
            what = "%s %s" % (data.get("name"), data.get("args", ""))
        elif kind != "tool_result":  # results are traced by run_tool, with their duration
            what = data.get("text", "")
        else:
            return
        trace("chat", "[%s] %s%s" % (self.id, kind, (": " + what) if what else ""))


def _short(args):
    s = json.dumps(args, default=str)
    return s if len(s) < 160 else s[:157] + "..."


class Stopped(Exception):
    """The user pressed Stop (or started a new chat)."""


async def until_stopped(sess, aw):
    """Await `aw`, but give up at once when the user presses Stop: a model can take a minute to answer, and
    Stop must not wait for it. (A request already sent finishes in the background; its answer is dropped.)"""
    task = asyncio.ensure_future(aw)
    while True:
        done, _ = await asyncio.wait({task}, timeout=0.2)
        if done:
            return task.result()
        if sess.stop:
            task.cancel()
            raise Stopped()


RETRY_WAITS = (3, 8)  # seconds before the 2nd and 3rd try when the model service is briefly unavailable


def transient(error):
    """Worth trying again: the service is overloaded or the connection dropped (not: a bad key, no credit)."""
    text = str(error)
    return (any("HTTP %d" % c in text for c in (500, 502, 503, 504)) or "cannot reach" in text
            or "timed out" in text.lower())


def _status_of(error):
    m = re.search(r"HTTP (\d{3})", str(error))
    return "HTTP " + m.group(1) if m else "connection problem"


def clean_tool_name(name):
    """Some models leak chat-template tokens into a tool name ('ai_backends<|channel|>commentary')."""
    name = re.split(r"<\||\s", str(name or "").strip(), maxsplit=1)[0]
    name = re.sub(r"^(?:functions|tools?)\.", "", name)  # 'functions.list_layers' from some templates
    return re.sub(r"[^A-Za-z0-9_\-]", "", name)


async def run_turn(sess, text):
    m = None
    try:
        if sess.model is None:
            sess.model = make_model(sess.provider, sess.model_name)
            sess.history = sess.model.start(await tool_specs())
        m = sess.model
        used = await until_stopped(sess, asyncio.to_thread(m.prepare))  # models are listed fresh every message
        sess.emit("note", text="Model: %s" % used)
        m.reset_usage()
        await _turn(sess, m, text)
    except Stopped:
        trace("chat", "[%s] stopped by the user" % sess.id)
        sess.emit("assistant", text="(stopped)")
    finally:
        spent = usage_text(m.usage) if m is not None else ""
        if spent:
            sess.emit("note", text=spent)


def skill_hint(text):
    """Name the few skills that fit this message (names and descriptions only - their text costs nothing
    until the model loads one)."""
    try:
        found = skills.suggest(text)
    except OSError:
        return "", []
    if not found:
        return "", []
    return ("\n\n(Skills that may fit - load one with use_skill only if it helps: %s)" % "; ".join(
        "%s: %s" % (s["name"], s["description"][:140]) for s in found)), [s["name"] for s in found]


async def _turn(sess, m, text):
    hint, names = skill_hint(text)
    if names:
        sess.emit("note", text="Skills that may help: %s" % ", ".join(names))
    turn_start = len(sess.history)
    m.user(sess.history, text + hint)
    m.compact(sess.history, turn_start)
    last_errors, repeats = None, 0
    for step in range(max_steps()):
        if sess.stop:
            raise Stopped()
        t0 = time.monotonic()
        for attempt, wait in enumerate(RETRY_WAITS + (None,)):
            work = list(sess.history)  # an abandoned (stopped) request must not write into the conversation
            try:
                reply, calls = await until_stopped(sess, asyncio.to_thread(m.step, work))
                break
            except ProviderError as e:
                if wait is None or not transient(e):
                    raise
                trace("model", "[%s] busy, retry %d in %ds: %s" % (sess.id, attempt + 1, wait, str(e)[:200]))
                sess.emit("note", text="The model service is busy (%s); trying again in %d s…" % (
                    _status_of(e), wait))
                await until_stopped(sess, asyncio.sleep(wait))
        sess.history[:] = work
        calls = [(cid, clean_tool_name(name), args) for cid, name, args in calls]
        trace("model", "[%s] step %d: %.1fs, %d tool call(s)%s" % (
            sess.id, step + 1, time.monotonic() - t0, len(calls),
            (" " + ", ".join(c[1] for c in calls)) if calls else ""))
        if getattr(m, "note", None):
            sess.emit("note", text=m.note)
            m.note = None
        if reply.strip():
            sess.emit("assistant", text=reply.strip())
        if not calls:
            return
        results = []
        try:
            for cid, name, args in calls:
                if sess.stop:
                    raise Stopped()
                sess.emit("tool", name=name, args=_short(args))
                out, images = await until_stopped(sess, run_tool(name, dict(args)))
                sess.emit("tool_result", name=name, ok=not out.startswith("error:"), text=out[:300],
                          images=len(images))
                results.append((cid, name, out, images))
        except Stopped:  # every call the model made still needs an answer, or the next message is refused
            results += [(cid, name, "error: stopped by the user", []) for cid, name, _ in calls[len(results):]]
            m.results(sess.history, results)
            raise
        m.results(sess.history, results)
        m.compact(sess.history, turn_start)
        errors = sorted(out[:200] for _, _, out, _ in results if out.startswith("error:"))
        if errors and len(errors) == len(results) and errors == last_errors:
            repeats += 1
            if repeats >= 2:  # the same failure three times in a row: stop instead of paying for more
                sess.emit("assistant", text="(stopped: the same error kept repeating - %s)" % errors[0][7:200])
                return
        else:
            repeats = 0
        last_errors = errors
    sess.emit("assistant", text="(stopped after %d steps - say 'continue' to go on)" % max_steps())


def _worker(sess, text):
    loop = asyncio.new_event_loop()  # not asyncio.run: that would wait for a stopped request to finish
    try:
        loop.run_until_complete(run_turn(sess, text))
    except ProviderError as e:
        sess.emit("error", text=str(e))
    except Exception as e:  # noqa: BLE001
        import traceback
        trace("crash", traceback.format_exc().replace("\n", " | "))
        sess.emit("error", text="%s: %s" % (type(e).__name__, e))
    finally:
        loop.close()
        sess.busy = False
        sess.emit("done")


SESSIONS = {}


def chat(session_id, provider, model, text):
    sess = SESSIONS.get(session_id) if session_id else None
    if sess is None or sess.provider != provider or (model and sess.model_name != model):
        sess = Session(provider, model)
        SESSIONS[sess.id] = sess
        trace("chat", "[%s] new chat with %s, model %s" % (sess.id, provider, model or "auto"))
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
            from . import __version__
            return self._send(200, {"ok": True, "version": __version__, "providers": status()})
        if path == "/models":
            prov = q.get("provider", "gemini")
            try:
                found = available_models(prov)
                trace("models", "%s: %d available" % (prov, len(found)))
                return self._send(200, {"models": found})
            except ProviderError as e:
                trace("models", "%s: ✗ %s" % (prov, e))
                return self._send(200, {"models": [], "error": str(e)})
        if path == "/oauth/status":
            r = oauth.poll(q.get("id", ""))
            if r.get("status") != "pending":
                trace("signin", "browser sign-in %s%s" % (r.get("status"), (": %s" % r["error"]) if r.get("error") else ""))
            return self._send(200, r)
        if path == "/skills":
            found = skills.all_skills()
            return self._send(200, {"skills": found, "dirs": [str(d) for d in skills.dirs()],
                                    "user_dir": str(skills.user_dir())})
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
            trace("http", "✗ POST %s refused: wrong token" % self.path)
            return self._send(401, {"error": "unauthorized"})
        try:
            b = self._body()
            if self.path != "/chat":  # chats are traced as they run
                trace("http", "POST %s %s" % (self.path, " ".join(
                    "%s=%s" % (k, v) for k, v in b.items() if k in ("provider", "session", "base_url", "verify"))))
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
            if self.path == "/reset":  # New chat: the old one stops too, it must not keep painting
                old = SESSIONS.pop(b.get("session", ""), None)
                if old:
                    old.stop = True
                return self._send(200, {"ok": True})
            if self.path == "/shutdown":  # the AI Chat panel replaces an outdated service
                self._send(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
            if self.path == "/signin":
                key = b.get("key") or ""
                if key and b.get("verify", True):
                    ok, detail = oauth.verify(b["provider"], key)
                    if ok is False:
                        trace("signin", "✗ %s: key refused (%s), not saved" % (b["provider"], detail))
                        return self._send(400, {"error": "%s - not saved" % detail})
                keys.save(b["provider"], key=key, base_url=b.get("base_url"),
                          enable=True if b["provider"] in keys.OPTIONAL_KEY else None)
                trace("signin", "✓ %s: %s" % (b["provider"], "key saved" if key else "turned on"))
                return self._send(200, {"providers": status()})
            if self.path == "/check":  # Services > Test: does a server on this computer answer?
                ok, detail = check_server(b["provider"], b.get("base_url"))
                trace("signin", "%s %s: %s" % ("✓" if ok else "✗", b["provider"], detail))
                return self._send(200, {"ok": ok, "detail": detail})
            if self.path == "/verify":
                ok, detail = oauth.verify(b["provider"], b.get("key"))
                return self._send(200, {"ok": ok, "detail": detail})
            if self.path == "/oauth/start":
                return self._send(200, oauth.start(b["provider"]))
            if self.path == "/signout":
                keys.save(b["provider"], remove=True)
                return self._send(200, {"providers": status()})
        except (ValueError, KeyError) as e:
            trace("http", "✗ POST %s: %s" % (self.path, e))
            return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "not found"})


def check_server(provider, base_url=None):
    """(answers?, what it said) for a local server at base_url (default: the saved or usual address)."""
    url = (base_url or keys.saved(provider, "base_url") or keys.LOCAL.get(provider) or "").strip().rstrip("/")
    if not url:
        return False, "no address"
    try:
        if provider in ("comfyui", "a1111"):
            from .providers import images
            be = images.get(provider)
            be.url = url
            st = be.status()
            if not st.get("reachable"):
                return False, "%s does not answer at %s (%s)" % (keys.SERVICES[provider][0], url,
                                                                 str(st.get("error", ""))[:160])
            return True, "%s answers at %s: %d model(s)" % (keys.SERVICES[provider][0], url,
                                                             len(st.get("models") or []))
        headers = {"Authorization": "Bearer " + (keys.saved(provider) or "local")}
        listing = http.get_json(url + "/models", headers, timeout=6)
        names = [m.get("id") for m in listing.get("data") or [] if isinstance(m, dict)]
        if provider == "ollama" and not names:
            return True, "the server answers at %s but has no models yet (ollama pull <model>)" % url
        return True, "answers at %s: %d model(s)%s" % (url, len(names), (", e.g. " + names[0]) if names else "")
    except ProviderError as e:
        return False, "nothing answers at %s (%s)" % (url, str(e)[:160])


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
    from . import __version__
    import os
    import platform
    import sys
    trace("service", "=" * 20 + " assistant service started " + "=" * 20)
    trace("service", "krita-mcp %s on %s:%s, Python %s, %s, %s" % (
        __version__, host, srv.server_address[1], platform.python_version(), platform.platform(), sys.executable))
    try:
        st = status()
        trace("service", "signed in / turned on: %s" % (", ".join(n for n, i in sorted(st.items())
                                                                     if i.get("signed_in")) or "nothing"))
    except Exception as e:  # noqa: BLE001 - the report is a help only
        trace("service", "could not read sign-ins: %s" % e)
    env_vars = {k: v for k, v in os.environ.items() if k.startswith("KRITA_MCP_")}
    trace("service", "settings: max_steps=%s keep_images=%s chat_image_size=%s %s" % (
        max_steps(), keep_images(), chat_image_size(), " ".join(
            "%s=%s" % (k, "***" if any(w in k for w in ("KEY", "TOKEN", "SECRET", "AUTH")) else v)
            for k, v in sorted(env_vars.items()))))
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
        trace("service", "stopped")

