"""Service sign-in: API keys and server URLs entered in Krita's AI Chat > Services dialog.

Stored in KRITA_MCP_HOME/agent_keys.json (a user-only file). Both the in-Krita assistant and the MCP server
read them, so signing in once inside Krita also serves Claude Desktop, Gemini CLI, Cursor, ... Saving a key
there is an explicit opt-in, like setting KRITA_MCP_<SERVICE>_API_KEY; an explicit environment variable wins.
"""
import json
import os
from pathlib import Path

# name: label, what it is for, the environment variable it stands for, where to get a key, URL field?
SERVICES = {
    "gemini": ("Google Gemini", "chat, images, video (Veo), vision", "KRITA_MCP_GEMINI_API_KEY",
               "https://aistudio.google.com/apikey", False),
    "anthropic": ("Anthropic Claude", "chat, vision", "KRITA_MCP_ANTHROPIC_API_KEY",
                  "https://console.anthropic.com/settings/keys", False),
    "openai": ("OpenAI / compatible", "chat, images, video (Sora), vision", "KRITA_MCP_OPENAI_API_KEY",
               "https://platform.openai.com/api-keys", True),
    "openrouter": ("OpenRouter (sign in with browser)", "chat with Gemini, Claude, GPT and 300+ models",
                   "KRITA_MCP_OPENROUTER_API_KEY", "https://openrouter.ai/settings/keys", False),
    "ollama": ("Local model (Ollama, LM Studio)", "chat (no key)", None, "https://ollama.com/download", True),
    "stability": ("Stability AI", "images, upscale, background removal", "KRITA_MCP_STABILITY_API_KEY",
                  "https://platform.stability.ai/account/keys", False),
    "replicate": ("Replicate", "any image / video model", "KRITA_MCP_REPLICATE_API_TOKEN",
                  "https://replicate.com/account/api-tokens", False),
    "fal": ("fal.ai", "any image / video model", "KRITA_MCP_FAL_KEY", "https://fal.ai/dashboard/keys", False),
    "heygen": ("HeyGen", "talking avatars", "KRITA_MCP_HEYGEN_API_KEY", "https://app.heygen.com/settings", False),
    "comfyui": ("ComfyUI (local)", "images on your GPU", None, "https://www.comfy.org/", True),
    "a1111": ("AUTOMATIC1111 / Forge (local)", "images on your GPU", None,
              "https://github.com/lllyasviel/stable-diffusion-webui-forge", True),
    "pollinations": ("Pollinations (free, no sign-in)", "free chat and images; a free key raises the limits",
                     "KRITA_MCP_POLLINATIONS_API_KEY", "https://enter.pollinations.ai/keys", False),
    "horde": ("AI Horde (free, crowdsourced)", "free images on volunteers' GPUs; a free key gets you ahead in "
              "the queue", "KRITA_MCP_HORDE_API_KEY", "https://aihorde.net/register", False),
}
from .compat import COMPAT  # noqa: E402

for _name, (_label, _base, _env, _url, _use, _images) in COMPAT.items():
    SERVICES[_name] = (_label, _use, _env, _url, True)
ENV_TO_SERVICE = {v[2]: k for k, v in SERVICES.items() if v[2]}
BROWSER_SIGN_IN = {"openrouter"}  # real OAuth; the others get a browser-assisted key flow
# work without a key, but are used only after the user turns them on (a key is optional, for higher limits)
OPTIONAL_KEY = {"pollinations", "horde", "omniroute"}


def home():
    return Path(os.environ.get("KRITA_MCP_HOME") or (Path.home() / ".krita-mcp")).expanduser()


def path():
    return home() / "agent_keys.json"


def load():
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def saved(service, field="key"):
    v = (load().get(service) or {}).get(field)
    return v.strip() if isinstance(v, str) and v.strip() else None


def enabled(service):
    """A free service the user turned on (Services dialog, a saved key, or KRITA_MCP_FREE_SERVICES)."""
    listed = {n.strip().lower() for n in (os.environ.get("KRITA_MCP_FREE_SERVICES") or "").split(",")}
    env_name = SERVICES.get(service, (None,) * 3)[2]
    return bool(service in listed or saved(service) or saved(service, "enabled")
                or (env_name and os.environ.get(env_name, "").strip()))


def save(service, key=None, base_url=None, remove=False, enable=None):
    if service not in SERVICES:
        raise ValueError("unknown service %r" % service)
    data = load()
    if remove:
        data.pop(service, None)
    else:
        entry = dict(data.get(service) or {})
        if key is not None:
            entry["key"] = key.strip() or None
        if base_url is not None:
            entry["base_url"] = base_url.strip() or None
        if enable is not None:
            entry["enabled"] = "on" if enable else None
        data[service] = {k: v for k, v in entry.items() if v}
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


def status():
    """Per service: label, use, signed in?, saved URL - never the key itself."""
    out = {}
    for name, (label, use, env_name, key_url, has_url) in SERVICES.items():
        key = bool(saved(name) or (env_name and os.environ.get(env_name, "").strip()))
        url = saved(name, "base_url")
        signed = key or (name in ("ollama", "comfyui", "a1111") and bool(url)) or name == "ollama"
        optional = name in OPTIONAL_KEY
        if optional:
            signed = enabled(name)
        out[name] = {"label": label, "use": use, "signed_in": bool(signed), "key_url": key_url,
                     "needs_key": bool(env_name) and not optional, "optional_key": optional,
                     "free": optional, "has_url": has_url, "base_url": url,
                     "browser_sign_in": name in BROWSER_SIGN_IN}
    return out
