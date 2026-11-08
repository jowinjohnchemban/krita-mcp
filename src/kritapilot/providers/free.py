"""Free AI services: no account needed, or a free one for higher limits.

Pollinations  chat with tools and text-to-image; works without any key (anonymous tier), a free key from
              https://enter.pollinations.ai/keys moves it to the newer API with more models.
AI Horde      text-to-image on volunteers' GPUs (https://aihorde.net); anonymous works, slower; a free key
              gets you ahead in the queue.
OmniRoute     (in compat.py) a local gateway in front of 150+ providers, many of them free.

Nothing here runs by surprise: the image services are used only after you turn them on in Krita's
AI Chat > Services (or KRITAPILOT_FREE_SERVICES=pollinations,horde), and the Pollinations chat only when you
choose it in the AI Chat panel. Models are listed live, never hard-coded.
"""
import time
import urllib.parse

from . import http, keys
from .http import ProviderError, env, secret

ANON_HORDE_KEY = "0000000000"


def _client_agent():
    from .. import __version__
    return "kritapilot:%s:github.com/jowinjohnchemban/kritapilot" % __version__


# ====================================================================== Pollinations
def pollinations_key():
    return secret("KRITAPILOT_POLLINATIONS_API_KEY")


def _text_url():
    return env("KRITAPILOT_POLLINATIONS_TEXT_URL", "https://text.pollinations.ai").rstrip("/")


def _gen_url():
    return env("KRITAPILOT_POLLINATIONS_GEN_URL", "https://gen.pollinations.ai").rstrip("/")


def _image_url():
    return env("KRITAPILOT_POLLINATIONS_IMAGE_URL", "https://image.pollinations.ai").rstrip("/")


def pollinations_chat(key=None):
    """(chat completions URL, headers) - the newer API with a key, the anonymous tier without."""
    if key:
        return _gen_url() + "/v1/chat/completions", {"Authorization": "Bearer " + key}
    return _text_url() + "/openai", {}


def pollinations_models(key=None, seeing=False):
    """Chat models that can use tools and cost nothing on this tier; those that also see images first.
    seeing=True: only the ones that accept pictures."""
    if key:
        r = http.get_json(_gen_url() + "/v1/models", {"Authorization": "Bearer " + key}, timeout=20)
        items = [m for m in (r.get("data") if isinstance(r, dict) else r) or []
                 if m.get("id") and (m.get("category") or "text") == "text" and not m.get("paid_only")
                 and "tool_calling" in (m.get("capabilities") or [])]
        items.sort(key=lambda m: m.get("created") or 0, reverse=True)
        sees = [m["id"] for m in items if "image" in (m.get("input_modalities") or [])]
        return sees if seeing else sees + [m["id"] for m in items if m["id"] not in sees]
    r = http.get_json(_text_url() + "/models", timeout=20)
    items = [m for m in r or [] if isinstance(m, dict) and m.get("name") and m.get("tools")
             and (m.get("tier") or "anonymous") == "anonymous"]
    sees = [m["name"] for m in items if m.get("vision")]
    return sees if seeing else sees + [m["name"] for m in items if not m.get("vision")]


def pollinations_image_models(key=None):
    if key:
        r = http.get_json(_gen_url() + "/image/models", {"Authorization": "Bearer " + key}, timeout=20)
        return [m["name"] for m in r or [] if isinstance(m, dict) and m.get("name") and not m.get("paid_only")
                and (m.get("category") or "image") == "image"]
    r = http.get_json(_image_url() + "/models", timeout=20)
    return [m if isinstance(m, str) else m.get("name") for m in r or [] if m]


class PollinationsImages:
    """Text-to-image from Pollinations: free, no key needed."""
    name = "pollinations"
    kind = "cloud"
    native_control = False
    mask_format = "gray"
    multiple = 16
    sizes = None
    default_side = 1024

    def __init__(self):
        self.key = pollinations_key()
        self.model = env("KRITAPILOT_POLLINATIONS_IMAGE_MODEL")

    def configured(self):
        return keys.enabled("pollinations")

    def status(self):
        return {"configured": self.configured(), "free": True, "key": bool(self.key),
                "model": self.model or "auto (listed live)"}

    def generate(self, job):
        if not self.configured():
            raise ProviderError("Pollinations is off: turn it on in Krita's AI Chat > Services (free, no key)")
        if job.mode != "txt2img" or job.controls:
            raise ProviderError("Pollinations offers text-to-image only; use another backend to edit or inpaint")
        models = [job.model or self.model] if (job.model or self.model) else pollinations_image_models(self.key)
        if not models:
            raise ProviderError("Pollinations lists no free image model right now; try again later")
        model, seed = models[0], job.seed_or_random()
        prompt = job.prompt + ("\nAvoid: " + job.negative if job.negative else "")
        imgs, seeds = [], []
        for i in range(job.count):
            q = {"model": model, "width": job.width, "height": job.height, "seed": seed + i, "nologo": "true"}
            q.update({k: v for k, v in job.extra.items() if isinstance(v, (str, int, float, bool))})
            path = urllib.parse.quote(prompt[:1800], safe="")
            if self.key:
                url = "%s/image/%s?%s" % (_gen_url(), path, urllib.parse.urlencode(q))
                data, hdrs = http.request("GET", url, headers={"Authorization": "Bearer " + self.key}, timeout=300)
            else:
                url = "%s/prompt/%s?%s" % (_image_url(), path, urllib.parse.urlencode(q))
                data, hdrs = http.request("GET", url, timeout=300)
            if not (hdrs.get("Content-Type") or "").startswith("image/"):
                raise ProviderError("Pollinations returned no image: %s" % data[:200].decode("utf-8", "replace"))
            imgs.append(data)
            seeds.append(seed + i)
        return imgs, {"model": model, "seeds": seeds, "cost": "free"}

    def upscale(self, png, factor, model=None):
        raise ProviderError("Pollinations cannot upscale; use scale_image or another backend")

    def remove_background(self, png, model=None):
        raise ProviderError("Pollinations cannot remove backgrounds; use method='color' or another backend")


# ====================================================================== AI Horde
def _horde_url():
    return env("KRITAPILOT_HORDE_URL", "https://aihorde.net/api/v2").rstrip("/")


class HordeImages:
    """Text-to-image and img2img on AI Horde, the free crowdsourced cluster. Slower: requests wait in a queue."""
    name = "horde"
    kind = "cloud"
    native_control = False
    mask_format = "gray"
    multiple = 64
    sizes = None

    def __init__(self):
        self.key = secret("KRITAPILOT_HORDE_API_KEY")
        self.model = env("KRITAPILOT_HORDE_MODEL")
        # anonymous requests are limited in size; a (free) account allows more
        self.default_side = 1024 if self.key else 576

    def configured(self):
        return keys.enabled("horde")

    def status(self):
        return {"configured": self.configured(), "free": True, "key": bool(self.key),
                "model": self.model or "auto (most workers)"}

    def _headers(self):
        return {"apikey": self.key or ANON_HORDE_KEY, "Client-Agent": _client_agent()}

    def models(self):
        r = http.get_json(_horde_url() + "/status/models?type=image", self._headers(), timeout=30)
        items = [m for m in r or [] if m.get("name") and (m.get("count") or 0) > 0
                 and "nsfw" not in m["name"].lower()]  # never chosen by itself; pass model= to use one
        items.sort(key=lambda m: (m.get("count") or 0), reverse=True)
        return [m["name"] for m in items]

    def generate(self, job):
        import base64
        if not self.configured():
            raise ProviderError("AI Horde is off: turn it on in Krita's AI Chat > Services (free, no key)")
        if job.mode == "inpaint" or job.controls:
            raise ProviderError("AI Horde here does text-to-image and img2img; use another backend to inpaint")
        online = self.models()
        model = job.model or self.model
        if model and online and model not in online:  # not served now (or a name meant for another service)
            model = None
        model = model or (online or [None])[0]
        if not model:
            raise ProviderError("no AI Horde worker is online right now; try again later")
        params = {"width": job.width, "height": job.height, "n": job.count, "steps": int(job.steps or 25),
                  "cfg_scale": float(job.cfg or 7), "seed": str(job.seed_or_random())}
        if job.sampler:
            params["sampler_name"] = job.sampler
        if job.count > 1:  # each picture gets the next seed; with one seed for all, the variations were identical
            params["seed_variation"] = 1
        body = {"prompt": job.prompt + (" ### " + job.negative if job.negative else ""), "params": params,
                "models": [model], "r2": True, "nsfw": False, "censor_nsfw": True}
        if job.mode == "img2img" and job.image:
            body["source_image"] = base64.b64encode(job.image).decode()
            body["source_processing"] = "img2img"
            params["denoising_strength"] = job.strength_or_default()
        body.update(job.extra)
        rid = http.post_json(_horde_url() + "/generate/async", body, self._headers(), timeout=60).get("id")
        if not rid:
            raise ProviderError("AI Horde did not accept the request")
        poll = float(env("KRITAPILOT_HORDE_POLL", "4"))
        deadline = time.time() + float(env("KRITAPILOT_HORDE_TIMEOUT", "600"))
        while True:
            st = http.get_json("%s/generate/check/%s" % (_horde_url(), rid), self._headers(), timeout=30)
            if st.get("done"):
                break
            if st.get("faulted") or st.get("is_possible") is False or time.time() > deadline:
                try:  # give the queue slot back
                    http.request("DELETE", "%s/generate/status/%s" % (_horde_url(), rid), headers=self._headers(),
                                 timeout=30)
                except ProviderError:
                    pass
                why = ("no worker can serve this model and size - try a smaller image or another model"
                       if st.get("is_possible") is False else "the request failed" if st.get("faulted")
                       else "it waited too long in the queue (position %s)" % st.get("queue_position"))
                raise ProviderError("AI Horde: %s" % why)
            time.sleep(poll)
        out = http.get_json("%s/generate/status/%s" % (_horde_url(), rid), self._headers(), timeout=60)
        imgs, seeds = [], []
        for g in out.get("generations") or []:
            img = g.get("img") or ""
            imgs.append(http.download(img) if img.startswith("http") else base64.b64decode(img))
            seeds.append(g.get("seed"))
        if not imgs:
            raise ProviderError("AI Horde returned no images")
        return imgs, {"model": model, "seeds": seeds, "cost": "free"}

    def upscale(self, png, factor, model=None):
        raise ProviderError("AI Horde cannot upscale here; use scale_image or another backend")

    def remove_background(self, png, model=None):
        raise ProviderError("AI Horde cannot remove backgrounds here; use method='color' or another backend")
