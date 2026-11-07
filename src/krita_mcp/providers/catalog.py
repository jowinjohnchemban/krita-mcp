"""Live model discovery for every provider. Nothing is hard-coded and nothing is cached: when no model was
chosen, the provider's current model list is fetched and the newest suitable model is used.

    newest Gemini of a family     - Google ListModels (versions in the names)
    newest OpenAI model by kind   - GET /models, ranked by `created`
    newest Claude (Opus first)    - Anthropic Models API via the official SDK
    newest local model            - Ollama / LM Studio GET /models
    OpenRouter                    - no model sent: the account's default (the panel lists all live)
"""
import re

from . import gemini, http
from .http import ProviderError

# not chat models. "turbo-instruct" only: open models named "...-Instruct" ARE chat models
NOT_CHAT = re.compile(r"(audio|realtime|transcri|tts|speech|search|embed|moderation|turbo-instruct|image|dall-e|"
                      r"sora|whisper|davinci|babbage|computer-use|codex|omni-moderation|guard|bge-|rerank|"
                      r"stable-diffusion|flux|voxtral|ocr)", re.I)
SMALL = re.compile(r"(mini|nano|lite|small)", re.I)


def _openai_list(base, key, extra_headers=None):
    headers = {"Authorization": "Bearer " + key} if key else {}
    headers.update(extra_headers or {})
    r = http.get_json(base.rstrip("/") + "/models", headers, timeout=20)
    items = r.get("data") if isinstance(r, dict) else r
    out = [{"id": m.get("id"), "created": m.get("created") or 0, "type": (m.get("type") or "").lower()}
           for m in items or [] if m.get("id")]
    return sorted(out, key=lambda m: m["created"], reverse=True)


def openai_models(base, key, purpose="chat"):
    """Model ids for a purpose (chat | image | video), newest first; full-size chat models before small ones."""
    models = _openai_list(base, key)
    if purpose == "image":
        ids = [m["id"] for m in models if m["type"] == "image" or re.search(r"(gpt-image|dall-e|-image\b|image-)",
                                                                              m["id"])]
    elif purpose == "video":
        ids = [m["id"] for m in models if m["type"] == "video" or "sora" in m["id"]]
    else:
        chat = [m for m in models if not NOT_CHAT.search(m["id"])
                and m["type"] in ("", "chat", "language", "code", "model", "vlm", "llm")]
        ids = [m["id"] for m in chat if not SMALL.search(m["id"])] + [m["id"] for m in chat if SMALL.search(m["id"])]
    return ids


def local_models(base):
    return [m["id"] for m in _openai_list(base, None) if not re.search(r"(embed|whisper|tts|rerank)", m["id"], re.I)]


def openrouter_models(base, key):
    """OpenRouter models that can work in Krita (tool use), newest first; those that also see images first."""
    headers = {"Authorization": "Bearer " + key} if key else {}
    r = http.get_json(base.rstrip("/") + "/models", headers, timeout=30)
    usable = []
    for m in r.get("data") or []:
        params = m.get("supported_parameters") or []
        if params and "tools" not in params:
            continue  # cannot call tools: it could only talk, not work in Krita
        sees = "image" in ((m.get("architecture") or {}).get("input_modalities") or [])
        usable.append((sees, m.get("created") or 0, m.get("id")))
    usable.sort(reverse=True)
    return [i for _, _, i in usable if i]


def anthropic_models(key):
    try:
        import anthropic
    except ImportError:
        raise ProviderError("install the Anthropic SDK: pip install 'krita-mcp[anthropic]'") from None
    try:
        page = anthropic.Anthropic(api_key=key).models.list(limit=100)
    except anthropic.AuthenticationError:
        raise ProviderError("Claude rejected the API key; sign in again") from None
    except anthropic.APIError as e:
        raise ProviderError("could not list Claude models: %s" % e) from None
    models = sorted(page.data, key=lambda m: getattr(m, "created_at", None) or 0, reverse=True)
    ids = [m.id for m in models]
    return [i for i in ids if "opus" in i] + [i for i in ids if "opus" not in i]  # most capable first


def gemini_models(base, key, family="chat"):
    """Gemini model ids of a family, newest first."""
    pattern, method = gemini.FAMILIES[family]
    found = []
    for m in gemini.list_models(base, key):
        name = (m.get("name") or "").split("/", 1)[-1]
        mt = re.fullmatch(pattern, name)
        if mt and method in (m.get("supportedGenerationMethods") or [method]):
            found.append((tuple(int(p) for p in mt.group(1).split(".")), name))
    return [n for _, n in sorted(found, reverse=True)]


def first(ids, what):
    if not ids:
        raise ProviderError("no %s is available to this account; choose a model by name" % what)
    return ids[0]
