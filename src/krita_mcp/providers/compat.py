"""Services that speak the OpenAI API: one connector serves them all (chat with tools, vision, and for some
image generation). Models are always listed live from each service's /models; nothing is hard-coded but the
service's address, which KRITA_MCP_<NAME>_BASE_URL (or the Services dialog) can change.
"""
from . import catalog, http
from .http import ProviderError, env, secret

# name: (label, default base URL, key environment variable, where to get a key, what it is for, images API?)
COMPAT = {
    "mistral": ("Mistral AI", "https://api.mistral.ai/v1", "KRITA_MCP_MISTRAL_API_KEY",
                "https://console.mistral.ai/api-keys", "chat, vision", False),
    "groq": ("Groq", "https://api.groq.com/openai/v1", "KRITA_MCP_GROQ_API_KEY",
             "https://console.groq.com/keys", "very fast chat", False),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/v1", "KRITA_MCP_DEEPSEEK_API_KEY",
                 "https://platform.deepseek.com/api_keys", "chat", False),
    "xai": ("xAI Grok", "https://api.x.ai/v1", "KRITA_MCP_XAI_API_KEY", "https://console.x.ai",
            "chat, vision, images", True),
    "together": ("Together AI", "https://api.together.xyz/v1", "KRITA_MCP_TOGETHER_API_KEY",
                 "https://api.together.ai/settings/api-keys", "chat, images (open models)", True),
    "fireworks": ("Fireworks AI", "https://api.fireworks.ai/inference/v1", "KRITA_MCP_FIREWORKS_API_KEY",
                  "https://fireworks.ai/account/api-keys", "chat with open models", False),
    "cerebras": ("Cerebras", "https://api.cerebras.ai/v1", "KRITA_MCP_CEREBRAS_API_KEY",
                 "https://cloud.cerebras.ai", "very fast chat", False),
    "huggingface": ("Hugging Face (Inference Providers)", "https://router.huggingface.co/v1",
                    "KRITA_MCP_HUGGINGFACE_API_KEY", "https://huggingface.co/settings/tokens",
                    "chat with thousands of open models", False),
    "nvidia": ("NVIDIA NIM", "https://integrate.api.nvidia.com/v1", "KRITA_MCP_NVIDIA_API_KEY",
               "https://build.nvidia.com", "chat", False),
    "qwen": ("Alibaba Qwen (Model Studio)", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             "KRITA_MCP_QWEN_API_KEY", "https://modelstudio.console.alibabacloud.com", "chat, vision", False),
    "moonshot": ("Moonshot Kimi", "https://api.moonshot.ai/v1", "KRITA_MCP_MOONSHOT_API_KEY",
                 "https://platform.moonshot.ai/console/api-keys", "chat", False),
    # a gateway you run yourself (npm install -g omniroute; omniroute): one endpoint for 150+ providers,
    # many free; its "auto" models route to whichever provider is available
    "omniroute": ("OmniRoute (free AI gateway, local)", "http://localhost:20128/v1", "KRITA_MCP_OMNIROUTE_API_KEY",
                  "https://github.com/diegosouzapw/OmniRoute", "chat through 150+ providers, many free", False),
}


def base_url(name):
    return http.service_url("KRITA_MCP_%s_BASE_URL" % name.upper(), name, COMPAT[name][1])


def api_key(name):
    """The saved key; a server on this computer (OmniRoute, ...) may need none."""
    return secret(COMPAT[name][2]) or ("local" if http.is_loopback_url(base_url(name)) else None)


def need_key(name):
    key = api_key(name)
    if not key:
        raise ProviderError("Sign in to %s first (AI Chat > Services..., or %s)" % (COMPAT[name][0], COMPAT[name][2]))
    return key


def chat_models(name):
    key = need_key(name)
    ids = catalog.openai_models(base_url(name), None if key == "local" else key, "chat")
    if name == "omniroute":  # its routing models first: "auto" picks a working (often free) provider itself
        ids = sorted(ids, key=lambda i: (i != "auto", not i.startswith("auto")))
    return ids


class CompatImages:
    """Text-to-image through an OpenAI-style /images/generations endpoint (xAI, Together, ...)."""
    kind = "cloud"
    mask_format = "gray"
    multiple = 16
    sizes = None
    default_side = 1024

    def __init__(self, name):
        self.name = name
        self.base, self.key = base_url(name), api_key(name)
        self.model = env("KRITA_MCP_%s_IMAGE_MODEL" % name.upper())

    def configured(self):
        return bool(self.key)

    def status(self):
        return {"configured": self.configured(), "base_url": self.base, "model": self.model or "auto (newest)"}

    def generate(self, job):
        import base64
        if not self.key:
            raise ProviderError("Sign in to %s first" % COMPAT[self.name][0])
        if job.mode != "txt2img" or job.controls:
            raise ProviderError("%s offers text-to-image only here; use another backend to edit or inpaint"
                                % COMPAT[self.name][0])
        model = job.model or self.model or catalog.first(catalog.openai_models(self.base, self.key, "image"),
                                                         "%s image model" % COMPAT[self.name][0])
        body = {"model": model, "prompt": job.prompt + ("\nAvoid: " + job.negative if job.negative else ""),
                "n": job.count, "response_format": "b64_json"}
        if self.name == "together":  # Together takes a size in pixels
            body.update(width=job.width, height=job.height)
        body.update(job.extra)
        r = http.post_json(self.base + "/images/generations", body, {"Authorization": "Bearer " + self.key})
        imgs = []
        for d in r.get("data") or []:
            if d.get("b64_json"):
                imgs.append(base64.b64decode(d["b64_json"]))
            elif d.get("url"):
                imgs.append(http.download(d["url"]))
        if not imgs:
            raise ProviderError("%s returned no images" % COMPAT[self.name][0])
        return imgs, {"model": model}

    def upscale(self, png, factor, model=None):
        raise ProviderError("%s cannot upscale; use scale_image or another backend" % self.name)

    def remove_background(self, png, model=None):
        raise ProviderError("%s cannot remove backgrounds; use method='color' or another backend" % self.name)
