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
    # ---- from OmniRoute's provider registry: services that speak the OpenAI API directly
    # free with no account (a key is optional)
    "kilo": ("Kilo Gateway (free, no sign-in)", "https://api.kilo.ai/api/gateway", "KRITA_MCP_KILO_API_KEY",
             "https://app.kilo.ai", "free models with no account; a key unlocks every model", False),
    "ovhcloud": ("OVHcloud AI Endpoints (free, no sign-in)", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
                 "KRITA_MCP_OVHCLOUD_API_KEY", "https://endpoints.ai.cloud.ovh.net",
                 "open models with no account (rate-limited); a key raises the limits", False),
    "llm7": ("LLM7 (free, no sign-in)", "https://api.llm7.io/v1", "KRITA_MCP_LLM7_API_KEY", "https://token.llm7.io",
             "free chat with no account; a free token raises the limits", False),
    # free tiers (an account and key needed)
    "sambanova": ("SambaNova", "https://api.sambanova.ai/v1", "KRITA_MCP_SAMBANOVA_API_KEY",
                  "https://cloud.sambanova.ai", "very fast open models, free tier", False),
    "cohere": ("Cohere", "https://api.cohere.com/compatibility/v1", "KRITA_MCP_COHERE_API_KEY",
               "https://dashboard.cohere.com/api-keys", "Command models, free trial key", False),
    "siliconflow": ("SiliconFlow", "https://api.siliconflow.com/v1", "KRITA_MCP_SILICONFLOW_API_KEY",
                    "https://cloud.siliconflow.com", "open models (DeepSeek, Qwen, GLM), free models", False),
    "zai": ("Z.AI GLM", "https://api.z.ai/api/paas/v4", "KRITA_MCP_ZAI_API_KEY",
            "https://z.ai/manage-apikey/apikey-list", "GLM models, free Flash models", False),
    "opencode": ("OpenCode Zen", "https://opencode.ai/zen/v1", "KRITA_MCP_OPENCODE_API_KEY", "https://opencode.ai/zen",
                 "curated models, some free", False),
    "ollamacloud": ("Ollama Cloud", "https://ollama.com/v1", "KRITA_MCP_OLLAMACLOUD_API_KEY",
                    "https://ollama.com/settings/keys", "large open models on Ollama's servers, free tier", False),
    "modelscope": ("ModelScope", "https://api-inference.modelscope.cn/v1", "KRITA_MCP_MODELSCOPE_API_KEY",
                   "https://modelscope.cn/my/myaccesstoken", "open models, free daily quota", False),
    "tencent": ("Tencent Hunyuan", "https://api.hunyuan.cloud.tencent.com/v1", "KRITA_MCP_TENCENT_API_KEY",
                "https://console.cloud.tencent.com/hunyuan", "Hunyuan models, free lite model", False),
    "qianfan": ("Baidu Qianfan (ERNIE)", "https://qianfan.baidubce.com/v2", "KRITA_MCP_QIANFAN_API_KEY",
                "https://console.bce.baidu.com/qianfan", "ERNIE models, free speed models", False),
    "scaleway": ("Scaleway Generative APIs", "https://api.scaleway.ai/v1", "KRITA_MCP_SCALEWAY_API_KEY",
                 "https://console.scaleway.com/iam/api-keys", "open models in Europe, free starter tokens", False),
    "hyperbolic": ("Hyperbolic", "https://api.hyperbolic.xyz/v1", "KRITA_MCP_HYPERBOLIC_API_KEY",
                   "https://app.hyperbolic.ai/settings", "open models, signup credit", False),
    "deepinfra": ("DeepInfra", "https://api.deepinfra.com/v1/openai", "KRITA_MCP_DEEPINFRA_API_KEY",
                  "https://deepinfra.com/dash/api_keys", "hundreds of open models, low prices", False),
    "nebius": ("Nebius Token Factory", "https://api.tokenfactory.nebius.com/v1", "KRITA_MCP_NEBIUS_API_KEY",
               "https://tokenfactory.nebius.com", "open models, signup credit", False),
    "novita": ("Novita AI", "https://api.novita.ai/openai/v1", "KRITA_MCP_NOVITA_API_KEY",
               "https://novita.ai/settings/key-management", "open models, low prices", False),
    "nscale": ("Nscale", "https://inference.api.nscale.com/v1", "KRITA_MCP_NSCALE_API_KEY", "https://console.nscale.com",
               "open models, signup credit", False),
    "featherless": ("Featherless", "https://api.featherless.ai/v1", "KRITA_MCP_FEATHERLESS_API_KEY",
                    "https://featherless.ai/account/api-keys", "thousands of open models, flat price", False),
    "inferencenet": ("Inference.net", "https://api.inference.net/v1", "KRITA_MCP_INFERENCENET_API_KEY",
                     "https://inference.net", "open models, low prices", False),
    "lambda": ("Lambda Inference", "https://api.lambda.ai/v1", "KRITA_MCP_LAMBDA_API_KEY",
               "https://cloud.lambda.ai/api-keys", "open models", False),
    "chutes": ("Chutes", "https://llm.chutes.ai/v1", "KRITA_MCP_CHUTES_API_KEY", "https://chutes.ai",
               "open models on decentralised GPUs, low prices", False),
    "requesty": ("Requesty (gateway)", "https://router.requesty.ai/v1", "KRITA_MCP_REQUESTY_API_KEY",
                 "https://app.requesty.ai/api-keys", "one key for 300+ models", False),
    "vercel": ("Vercel AI Gateway", "https://ai-gateway.vercel.sh/v1", "KRITA_MCP_VERCEL_API_KEY",
               "https://vercel.com/docs/ai-gateway", "one key for hundreds of models, monthly free credit", False),
    "venice": ("Venice AI", "https://api.venice.ai/api/v1", "KRITA_MCP_VENICE_API_KEY", "https://venice.ai/settings/api",
               "private, uncensored open models", False),
    "minimax": ("MiniMax", "https://api.minimax.io/v1", "KRITA_MCP_MINIMAX_API_KEY", "https://platform.minimax.io",
                "MiniMax models", False),
    "stepfun": ("StepFun", "https://api.stepfun.com/v1", "KRITA_MCP_STEPFUN_API_KEY", "https://platform.stepfun.com",
                "Step models, vision", False),
    "llama": ("Meta Llama API", "https://api.llama.com/compat/v1", "KRITA_MCP_LLAMA_API_KEY",
              "https://llama.developer.meta.com", "Meta's Llama models", False),
}
# work without any key (anonymous free tier); a saved key is still used when there is one
KEYLESS = {"kilo", "ovhcloud", "llm7"}


def base_url(name):
    return http.service_url("KRITA_MCP_%s_BASE_URL" % name.upper(), name, COMPAT[name][1])


def api_key(name):
    """The saved key; a server on this computer (OmniRoute, ...) may need none."""
    return secret(COMPAT[name][2]) or ("local" if http.is_loopback_url(base_url(name)) else None)


def need_key(name):
    """The key to send ("" = none: a free service used anonymously)."""
    key = api_key(name)
    if not key and name in KEYLESS:
        return ""
    if not key:
        raise ProviderError("Sign in to %s first (AI Chat > Services..., or %s)" % (COMPAT[name][0], COMPAT[name][2]))
    return key


def chat_models(name):
    key = need_key(name)
    anonymous = key in ("local", "")
    ids = catalog.openai_models(base_url(name), None if anonymous else key, "chat")
    if name == "omniroute":  # its routing models first: "auto" picks a working (often free) provider itself
        ids = sorted(ids, key=lambda i: (i != "auto", not i.startswith("auto")))
    elif name == "kilo":  # anonymous: only the free models answer; "kilo-auto/free" picks a working one
        if anonymous:
            ids = [i for i in ids if i == "kilo-auto/free" or i.endswith(":free")]
        ids = sorted(ids, key=lambda i: (i != "kilo-auto/free" if anonymous else not i.startswith("kilo-auto/")))
    elif name == "llm7" and anonymous:  # "default" routes to whatever the free tier offers right now
        ids = ["default"] + [i for i in ids if i != "default"]
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
