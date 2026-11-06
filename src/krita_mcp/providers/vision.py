"""A second pair of eyes: send the canvas to a vision model (Claude, GPT, Gemini, or any OpenAI-compatible
endpoint such as Ollama / LM Studio / OpenRouter) and get text back - critique, caption, a prompt for an
image model, an art-direction checklist."""
import base64

from . import catalog, gemini, http
from .http import ProviderError, env, secret
from .images import GEMINI_BASE

SYSTEM = ("You are an experienced art director and retoucher reviewing work in progress in Krita. "
          "Be concrete: name regions by position (top-left, centre, ...) and give actionable fixes.")


def ask_anthropic(png, question, model=None, max_tokens=4000):
    key = secret("KRITA_MCP_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
    if not key:
        raise ProviderError("Claude is not configured: set KRITA_MCP_ANTHROPIC_API_KEY")
    try:
        import anthropic
    except ImportError:
        raise ProviderError("install the Anthropic SDK: pip install 'krita-mcp[anthropic]'") from None
    client = anthropic.Anthropic(api_key=key)
    model = model or env("KRITA_MCP_ANTHROPIC_MODEL") or catalog.first(catalog.anthropic_models(key), "Claude model")
    kwargs = dict(model=model, max_tokens=max_tokens, system=SYSTEM, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": base64.standard_b64encode(png).decode()}},
        {"type": "text", "text": question}]}])
    try:
        try:  # server-side refusal fallback: a declined request is retried on a suitable model in the same call
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                               **kwargs)
        except TypeError:  # older SDK without the parameter
            resp = client.messages.create(**kwargs)
    except anthropic.AuthenticationError:
        raise ProviderError("Claude rejected the API key") from None
    except anthropic.RateLimitError:
        raise ProviderError("Claude rate limit reached; try again shortly") from None
    except anthropic.APIStatusError as e:
        raise ProviderError("Claude API error %s: %s" % (e.status_code, e.message)) from None
    except anthropic.APIConnectionError:
        raise ProviderError("cannot reach the Claude API") from None
    if resp.stop_reason == "refusal":
        raise ProviderError("Claude declined to answer this request")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return text, {"model": resp.model}


def ask_openai(png, question, model=None, max_tokens=4000):
    base = http.service_url("KRITA_MCP_OPENAI_BASE_URL", "openai", "https://api.openai.com/v1")
    key = secret("KRITA_MCP_OPENAI_API_KEY", "OPENAI_API_KEY") or ("local" if http.is_loopback_url(base) else None)
    if not key:
        raise ProviderError("OpenAI is not configured: set KRITA_MCP_OPENAI_API_KEY (or a local "
                            "KRITA_MCP_OPENAI_BASE_URL such as Ollama's http://127.0.0.1:11434/v1)")
    model = model or env("KRITA_MCP_OPENAI_VISION_MODEL") or catalog.first(
        catalog.local_models(base) if http.is_loopback_url(base) else catalog.openai_models(base, key), "chat model")
    body = {"model": model, "max_completion_tokens": max_tokens, "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [{"type": "text", "text": question},
                                     {"type": "image_url", "image_url": {"url": http.data_uri(png)}}]}]}
    r = http.post_json(base + "/chat/completions", body, {"Authorization": "Bearer " + key})
    try:
        return r["choices"][0]["message"]["content"] or "", {"model": r.get("model", model)}
    except (KeyError, IndexError):
        raise ProviderError("unexpected response: %s" % str(r)[:300]) from None


def ask_gemini(png, question, model=None, max_tokens=4000):
    key = secret("KRITA_MCP_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    if not key:
        raise ProviderError("Gemini is not configured: set KRITA_MCP_GEMINI_API_KEY")
    base = env("KRITA_MCP_GEMINI_BASE_URL", GEMINI_BASE).rstrip("/")
    model = model or env("KRITA_MCP_GEMINI_VISION_MODEL") or gemini.pick(base, key, "chat")
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"parts": [{"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode()}},
                                    {"text": question}]}],
            "generationConfig": {"maxOutputTokens": max_tokens}}
    r, model = gemini.post(base, key, model, "generateContent", body)
    parts = ((r.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts), {"model": model}


PROVIDERS = {"anthropic": ask_anthropic, "openai": ask_openai, "gemini": ask_gemini}
ALIASES = {"claude": "anthropic", "gpt": "openai", "chatgpt": "openai", "google": "gemini", "ollama": "openai"}


def configured():
    base = env("KRITA_MCP_OPENAI_BASE_URL", "")
    return {"anthropic": bool(secret("KRITA_MCP_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")),
            "openai": bool(secret("KRITA_MCP_OPENAI_API_KEY", "OPENAI_API_KEY")) or http.is_loopback_url(base or "x:"),
            "gemini": bool(secret("KRITA_MCP_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"))}


def ask(png, question, provider=None, model=None):
    provider = provider or env("KRITA_MCP_VISION_PROVIDER")
    if not provider:
        provider = next((p for p, ok in configured().items() if ok), None)
        if not provider:
            raise ProviderError("No vision model configured. Set KRITA_MCP_ANTHROPIC_API_KEY, "
                                "KRITA_MCP_OPENAI_API_KEY or KRITA_MCP_GEMINI_API_KEY (see docs/ai-backends.md).")
    key = ALIASES.get(provider.lower(), provider.lower())
    if key not in PROVIDERS:
        raise ProviderError("unknown provider %r; choose from %s" % (provider, ", ".join(PROVIDERS)))
    text, meta = PROVIDERS[key](png, question, model)
    meta["provider"] = key
    return text, meta
