"""Image-generation backends.

Local:  ComfyUI (any checkpoint, or your own workflow), AUTOMATIC1111 / Forge / SD.Next.
Cloud:  OpenAI (and any OpenAI-compatible images API), Google Gemini, Stability AI, Replicate, fal.ai.

Every backend takes an ImageJob and returns (list of image bytes, meta dict). Pixels go in and out as PNG;
Krita does all scaling and compositing, so backends never need an imaging library.
"""
import base64
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from . import catalog, compat, gemini, http
from .http import ProviderError, env, secret

MAX_COUNT = 8


@dataclass
class ImageJob:
    mode: str = "txt2img"  # txt2img | img2img | inpaint
    prompt: str = ""
    negative: str = ""
    width: int = 1024
    height: int = 1024
    count: int = 1
    seed: Optional[int] = None
    steps: Optional[int] = None
    cfg: Optional[float] = None
    strength: Optional[float] = None
    sampler: Optional[str] = None
    scheduler: Optional[str] = None
    model: Optional[str] = None
    image: Optional[bytes] = None  # PNG of the region, opaque
    mask: Optional[bytes] = None  # PNG, white = regenerate
    mask_alpha: Optional[bytes] = None  # RGBA PNG, transparent = regenerate (OpenAI convention)
    workflow: Optional[dict] = None  # ComfyUI API-format graph with {{placeholders}}
    extra: dict = field(default_factory=dict)  # provider-specific inputs, passed through
    # guidance images (ControlNet & co): [{"image": png, "type": "lineart", "model", "strength", "start",
    # "end", "preprocess"}]
    controls: list = field(default_factory=list)

    def strength_or_default(self):
        if self.strength is not None:
            return max(0.0, min(1.0, float(self.strength)))
        return 0.9 if self.mode == "inpaint" else 0.6 if self.mode == "img2img" else 1.0

    def seed_or_random(self):
        return int(self.seed) if self.seed is not None else random.randint(0, 2**31 - 1)


LINE_TYPES = ("lineart", "scribble", "sketch", "canny", "softedge", "mlsd", "lineart_anime")


def pick_control_model(models, ctype, given=None):
    """The ControlNet model to use: the one given, else the first whose name mentions the control type."""
    if given:
        return given
    ctype = (ctype or "").lower()
    aliases = {"sketch": ("scribble", "lineart", "sketch"), "lineart": ("lineart", "line"), "edges": ("canny",),
               "pose": ("openpose", "pose")}.get(ctype, (ctype,))
    for a in aliases:
        for m in models:
            if a and a in m.lower():
                return m
    raise ProviderError("no ControlNet model for type %r; pass control model=... (installed: %s)"
                        % (ctype, ", ".join(models[:20]) or "none"))


class Backend:
    name = ""
    native_control = False  # True: real ControlNet (wants white lines on black for line types)
    kind = "cloud"  # local | cloud
    mask_format = "gray"  # gray | alpha
    multiple = 8  # generation sizes are rounded to this
    sizes = None  # fixed size list (e.g. OpenAI) or None for any size
    default_side = 1024

    def configured(self):
        return True

    def status(self):  # cheap; local backends probe, cloud ones never spend money here
        return {"configured": self.configured()}

    def generate(self, job):
        raise NotImplementedError

    def upscale(self, png, factor, model=None):
        raise ProviderError("%s cannot upscale; use scale_image or another backend" % self.name)

    def remove_background(self, png, model=None):
        raise ProviderError("%s cannot remove backgrounds; use another backend or method='color'" % self.name)


def _b64(b):
    return base64.b64encode(b).decode()


def _guided(job, prefix):
    """A txt2img job with a guide image, turned into an edit of that image (for APIs without ControlNet)."""
    from dataclasses import replace
    return replace(job, mode="img2img", image=job.controls[0]["image"], prompt=prefix + job.prompt, controls=[])


# ====================================================================== AUTOMATIC1111 / Forge / SD.Next
class A1111(Backend):
    name, kind = "a1111", "local"
    native_control = True

    def __init__(self):
        self.url = http.service_url("KRITA_MCP_A1111_URL", "a1111", "http://127.0.0.1:7860")
        auth = env("KRITA_MCP_A1111_AUTH")  # user:password when the WebUI runs with --api-auth
        self.headers = {"Authorization": "Basic " + _b64(auth.encode())} if auth else {}

    def _get(self, path, timeout=60):
        return http.get_json(self.url + path, self.headers, timeout)

    def _post(self, path, body):
        return http.post_json(self.url + path, body, self.headers, timeout=int(env("KRITA_MCP_GEN_TIMEOUT", "900")))

    def status(self):
        try:
            models = [m.get("title") for m in self._get("/sdapi/v1/sd-models", 3)]
        except ProviderError as e:
            return {"configured": True, "reachable": False, "url": self.url, "error": str(e)}
        out = {"configured": True, "reachable": True, "url": self.url, "models": models[:100]}
        for key, path in (("samplers", "/sdapi/v1/samplers"), ("upscalers", "/sdapi/v1/upscalers")):
            try:
                out[key] = [s.get("name") for s in self._get(path, 5)][:100]
            except ProviderError:
                pass
        try:  # sd-webui-controlnet extension
            out["controlnet_models"] = self._get("/controlnet/model_list", 5).get("model_list", [])[:100]
        except ProviderError:
            pass
        return out

    def _controlnet(self, job):
        models = None
        units = []
        for c in job.controls:
            model = c.get("model")
            if not model:
                if models is None:
                    models = self._get("/controlnet/model_list", 10).get("model_list", [])
                model = pick_control_model(models, c.get("type"))
            units.append({"enabled": True, "image": _b64(c["image"]), "module": c.get("preprocess") or "none",
                          "model": model, "weight": float(c.get("strength", 1.0)),
                          "guidance_start": float(c.get("start", 0.0)), "guidance_end": float(c.get("end", 1.0)),
                          "pixel_perfect": True, "resize_mode": "Crop and Resize"})
        return {"controlnet": {"args": units}}

    def generate(self, job):
        body = {"prompt": job.prompt, "negative_prompt": job.negative, "width": job.width, "height": job.height,
                "steps": job.steps or 25, "cfg_scale": job.cfg or 7, "seed": job.seed_or_random(),
                "batch_size": job.count, "n_iter": 1, "send_images": True, "save_images": False}
        if job.sampler:
            body["sampler_name"] = job.sampler
        if job.scheduler:
            body["scheduler"] = job.scheduler
        if job.model:
            body["override_settings"] = {"sd_model_checkpoint": job.model}
            body["override_settings_restore_afterwards"] = True
        if job.controls:
            body["alwayson_scripts"] = self._controlnet(job)
        if job.mode == "txt2img":
            body.update(job.extra)
            r = self._post("/sdapi/v1/txt2img", body)
        else:
            body.update(init_images=[_b64(job.image)], denoising_strength=job.strength_or_default(), resize_mode=0)
            if job.mode == "inpaint":
                body.update(mask=_b64(job.mask), mask_blur=4, inpainting_fill=1, inpaint_full_res=False,
                            inpainting_mask_invert=0)
            body.update(job.extra)
            r = self._post("/sdapi/v1/img2img", body)
        imgs = [base64.b64decode(i.split(",", 1)[-1]) for i in (r.get("images") or [])][:job.count]
        if not imgs:
            raise ProviderError("A1111 returned no images")
        meta = {"seed": body["seed"]}
        try:
            info = json.loads(r.get("info") or "{}")
            meta["seeds"] = info.get("all_seeds")
            meta["model"] = info.get("sd_model_name")
        except ValueError:
            pass
        return imgs, meta

    def upscale(self, png, factor, model=None):
        if not model:  # the first real upscaler this WebUI offers
            names = [u.get("name") for u in self._get("/sdapi/v1/upscalers", 10)]
            model = catalog.first([n for n in names if n and n not in ("None", "Lanczos", "Nearest")],
                                  "AUTOMATIC1111 upscaler")
        r = self._post("/sdapi/v1/extra-single-image", {"image": _b64(png), "upscaling_resize": factor,
                                                         "upscaler_1": model})
        if not r.get("image"):
            raise ProviderError("A1111 returned no image")
        return base64.b64decode(r["image"])

    def remove_background(self, png, model=None):  # needs the sd-webui-rembg extension
        body = {"input_image": _b64(png), "return_mask": False}
        if model:
            body["model"] = model  # otherwise the extension's own default
        r = self._post("/rembg", body)
        return base64.b64decode(r["image"])


# ====================================================================== ComfyUI
class ComfyUI(Backend):
    name, kind = "comfyui", "local"
    native_control = True

    def __init__(self):
        self.url = http.service_url("KRITA_MCP_COMFYUI_URL", "comfyui", "http://127.0.0.1:8188")
        self.timeout = int(env("KRITA_MCP_GEN_TIMEOUT", "900"))

    def _choices(self, node, inp, timeout=10):
        info = http.get_json("%s/object_info/%s" % (self.url, node), timeout=timeout)
        spec = info[node]["input"]["required"][inp]
        vals = spec[0]
        if isinstance(vals, str) and len(spec) > 1 and isinstance(spec[1], dict):  # newer "COMBO" format
            vals = spec[1].get("options") or []
        return list(vals) if isinstance(vals, list) else []

    def status(self):
        try:
            ckpts = self._choices("CheckpointLoaderSimple", "ckpt_name", 3)
        except (ProviderError, KeyError, IndexError, TypeError) as e:
            return {"configured": True, "reachable": False, "url": self.url, "error": str(e)}
        out = {"configured": True, "reachable": True, "url": self.url, "models": ckpts[:200]}
        for key, node, inp in (("samplers", "KSampler", "sampler_name"), ("schedulers", "KSampler", "scheduler"),
                               ("upscalers", "UpscaleModelLoader", "model_name"),
                               ("controlnet_models", "ControlNetLoader", "control_net_name")):
            try:
                out[key] = self._choices(node, inp)[:200]
            except (ProviderError, KeyError, IndexError, TypeError):
                pass
        return out

    def upload(self, png):
        body, ctype = http.multipart([("type", "input"), ("overwrite", "true")],
                                     [("image", "krita-mcp-%s.png" % uuid.uuid4().hex[:12], png, "image/png")])
        data, _ = http.request("POST", self.url + "/upload/image", body, {"Content-Type": ctype}, timeout=120)
        r = json.loads(data)
        return (r["subfolder"] + "/" + r["name"]) if r.get("subfolder") else r["name"]

    def run(self, graph):
        r = http.post_json(self.url + "/prompt", {"prompt": graph, "client_id": uuid.uuid4().hex}, timeout=60)
        if r.get("node_errors"):
            raise ProviderError("ComfyUI rejected the workflow: %s" % json.dumps(r["node_errors"])[:800])
        pid = r["prompt_id"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            h = http.get_json("%s/history/%s" % (self.url, pid), timeout=30).get(pid)
            if h:
                st = h.get("status") or {}
                if st.get("status_str") == "error":
                    msgs = [m for m in st.get("messages") or [] if m and m[0] == "execution_error"]
                    raise ProviderError("ComfyUI failed: %s" % json.dumps(msgs)[:800])
                if st.get("completed", True):
                    return self._collect(h.get("outputs") or {})
            time.sleep(0.5)
        raise ProviderError("ComfyUI did not finish within %d s (KRITA_MCP_GEN_TIMEOUT)" % self.timeout)

    def _collect(self, outputs):
        imgs = []
        for node_out in outputs.values():
            for im in node_out.get("images") or []:
                q = "filename=%s&subfolder=%s&type=%s" % tuple(
                    http.urllib.parse.quote(str(im.get(k, ""))) for k in ("filename", "subfolder", "type"))
                data, _ = http.request("GET", "%s/view?%s" % (self.url, q), timeout=120)
                imgs.append(data)
        if not imgs:
            raise ProviderError("ComfyUI finished but produced no images (does the workflow have a SaveImage node?)")
        return imgs

    def graph(self, job, ckpt, image_name=None, mask_name=None, controls=()):
        """controls: [(uploaded image name, control model, settings dict)]"""
        seed = job.seed_or_random()
        g = {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": job.prompt, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": job.negative or "", "clip": ["4", 1]}},
        }
        pos, neg = ["6", 0], ["7", 0]
        for i, (name, model, c) in enumerate(controls):
            a, b, k = str(20 + 3 * i), str(21 + 3 * i), str(22 + 3 * i)
            g[a] = {"class_type": "LoadImage", "inputs": {"image": name}}
            g[b] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": model}}
            g[k] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
                "positive": pos, "negative": neg, "control_net": [b, 0], "image": [a, 0],
                "strength": float(c.get("strength", 1.0)), "start_percent": float(c.get("start", 0.0)),
                "end_percent": float(c.get("end", 1.0))}}
            pos, neg = [k, 0], [k, 1]
        if job.mode == "txt2img":
            g["5"] = {"class_type": "EmptyLatentImage",
                      "inputs": {"width": job.width, "height": job.height, "batch_size": job.count}}
            latent = ["5", 0]
        else:
            g["10"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
            g["11"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["4", 2]}}
            latent = ["11", 0]
            if job.mode == "inpaint":
                g["12"] = {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}}
                g["13"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": latent, "mask": ["12", 0]}}
                latent = ["13", 0]
            if job.count > 1:
                g["14"] = {"class_type": "RepeatLatentBatch", "inputs": {"samples": latent, "amount": job.count}}
                latent = ["14", 0]
        g["3"] = {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": job.steps or 25, "cfg": job.cfg or 7.0, "sampler_name": job.sampler,
            "scheduler": job.scheduler, "denoise": job.strength_or_default(),
            "model": ["4", 0], "positive": pos, "negative": neg, "latent_image": latent}}
        g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}}
        g["9"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "krita-mcp", "images": ["8", 0]}}
        return g, seed

    def generate(self, job):
        image_name = self.upload(job.image) if job.image is not None and job.mode != "txt2img" else None
        mask_name = self.upload(job.mask) if job.mask is not None and job.mode == "inpaint" else None
        control_names = [self.upload(c["image"]) for c in job.controls]
        if job.workflow:
            seed = job.seed_or_random()
            values = {
                "prompt": job.prompt, "negative": job.negative or "", "seed": seed, "width": job.width,
                "height": job.height, "count": job.count, "steps": job.steps or 25, "cfg": job.cfg or 7.0,
                "strength": job.strength_or_default(), "denoise": job.strength_or_default(),
                "image": image_name or "", "mask": mask_name or "", "model": job.model or "",
                "control": control_names[0] if control_names else ""}
            for i, name in enumerate(control_names, 1):
                values["control_%d" % i] = name
            graph = fill_workflow(job.workflow, values)
        else:
            ckpt = job.model
            if not ckpt:
                ckpts = self._choices("CheckpointLoaderSimple", "ckpt_name")
                if not ckpts:
                    raise ProviderError("ComfyUI has no checkpoints in models/checkpoints")
                ckpt = ckpts[0]
            if not job.sampler:
                job.sampler = catalog.first(self._choices("KSampler", "sampler_name"), "ComfyUI sampler")
            if not job.scheduler:
                job.scheduler = catalog.first(self._choices("KSampler", "scheduler"), "ComfyUI scheduler")
            controls = []
            if job.controls:
                models = self._choices("ControlNetLoader", "control_net_name")
                controls = [(name, pick_control_model(models, c.get("type"), c.get("model")), c)
                            for name, c in zip(control_names, job.controls)]
            graph, seed = self.graph(job, ckpt, image_name, mask_name, controls)
        imgs = self.run(graph)
        return imgs[:max(job.count, 1)] if not job.workflow else imgs, {"seed": seed,
                                                                         "model": job.model or "workflow"}

    def upscale(self, png, factor, model=None):
        model = model or next(iter(self._choices("UpscaleModelLoader", "model_name")), None)
        if not model:
            raise ProviderError("ComfyUI has no upscale models in models/upscale_models")
        g = {"1": {"class_type": "LoadImage", "inputs": {"image": self.upload(png)}},
             "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
             "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
             "4": {"class_type": "SaveImage", "inputs": {"filename_prefix": "krita-mcp-up", "images": ["3", 0]}}}
        return self.run(g)[0]


def fill_workflow(workflow, values):
    """Replace "{{name}}" placeholders anywhere in a ComfyUI API-format graph. A string that is exactly one
    placeholder takes the value's type (numbers stay numbers); placeholders inside text are substituted."""
    def sub(v):
        if isinstance(v, str):
            for k, val in values.items():
                token = "{{%s}}" % k
                if v == token:
                    return val
                if token in v:
                    v = v.replace(token, str(val))
            return v
        if isinstance(v, list):
            return [sub(x) for x in v]
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        return v
    g = sub(workflow)
    if not isinstance(g, dict) or not all(isinstance(n, dict) and "class_type" in n for n in g.values()):
        raise ProviderError("workflow must be a ComfyUI *API format* graph (Save (API Format) in ComfyUI)")
    return g


# ====================================================================== OpenAI-compatible images API
class OpenAIImages(Backend):
    name = "openai"
    mask_format = "alpha"

    def __init__(self):
        self.base = http.service_url("KRITA_MCP_OPENAI_BASE_URL", "openai", "https://api.openai.com/v1")
        self.key = secret("KRITA_MCP_OPENAI_API_KEY", "OPENAI_API_KEY")
        self.model = env("KRITA_MCP_OPENAI_IMAGE_MODEL")  # None: the newest image model this key can use
        self.sizes = env("KRITA_MCP_OPENAI_SIZES", "1024x1024,1536x1024,1024x1536").split(",")
        if self.key is None and http.is_loopback_url(self.base):
            self.key = "local"

    def configured(self):
        return bool(self.key)

    def status(self):
        return {"configured": self.configured(), "base_url": self.base,
                "model": self.model or "auto (newest image model)", "sizes": self.sizes}

    def _hdr(self):
        return {"Authorization": "Bearer " + self.key}

    def generate(self, job):
        if not self.key:
            raise ProviderError("OpenAI is not configured: set KRITA_MCP_OPENAI_API_KEY")
        if job.mode == "txt2img" and job.controls:  # no ControlNet: give the sketch as the image to work from
            job = _guided(job, "Use the provided image as the layout and line guide (keep its composition). ")
        model = job.model or self.model or catalog.first(catalog.openai_models(self.base, self.key, "image"),
                                                         "OpenAI image model")
        size = "%dx%d" % (job.width, job.height)
        prompt = job.prompt + ("\nAvoid: " + job.negative if job.negative else "")
        legacy = model.startswith("dall-e")
        if job.mode == "txt2img":
            body = {"model": model, "prompt": prompt, "n": job.count, "size": size}
            if legacy:
                body["response_format"] = "b64_json"
            body.update(job.extra)
            r = http.post_json(self.base + "/images/generations", body, self._hdr())
        else:
            fields = [("model", model), ("prompt", prompt), ("n", job.count), ("size", size)]
            if legacy:
                fields.append(("response_format", "b64_json"))
            fields += [(k, v if isinstance(v, str) else json.dumps(v)) for k, v in job.extra.items()]
            files = [("image", "image.png", job.image, "image/png")]
            if job.mode == "inpaint":
                files.append(("mask", "mask.png", job.mask_alpha, "image/png"))
            body, ctype = http.multipart(fields, files)
            data, _ = http.request("POST", self.base + "/images/edits", body,
                                   dict(self._hdr(), **{"Content-Type": ctype}))
            r = json.loads(data)
        imgs = []
        for d in r.get("data") or []:
            if d.get("b64_json"):
                imgs.append(base64.b64decode(d["b64_json"]))
            elif d.get("url"):
                imgs.append(http.download(d["url"]))
        if not imgs:
            raise ProviderError("OpenAI returned no images")
        return imgs, {"model": model, "size": size, "revised_prompt": (r.get("data") or [{}])[0].get("revised_prompt")}


# ====================================================================== Google Gemini
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class Gemini(Backend):
    name = "gemini"
    multiple = 1

    def __init__(self):
        self.key = secret("KRITA_MCP_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
        self.base = env("KRITA_MCP_GEMINI_BASE_URL", GEMINI_BASE).rstrip("/")
        self.model = env("KRITA_MCP_GEMINI_IMAGE_MODEL")  # None: the newest Flash Image model the key can use

    def configured(self):
        return bool(self.key)

    def status(self):
        return {"configured": self.configured(), "model": self.model or "auto (newest Flash Image)"}

    def generate(self, job):
        if not self.key:
            raise ProviderError("Gemini is not configured: set KRITA_MCP_GEMINI_API_KEY")
        model = job.model or self.model or gemini.pick(self.base, self.key, "image")
        text = job.prompt + ("\nAvoid: " + job.negative if job.negative else "")
        parts = []
        if job.mode == "txt2img" and job.controls:
            parts.append({"inline_data": {"mime_type": "image/png", "data": _b64(job.controls[0]["image"])}})
            text = "Follow the composition and lines of this guide image exactly. Create: " + text
        if job.mode != "txt2img":
            parts.append({"inline_data": {"mime_type": "image/png", "data": _b64(job.image)}})
            if job.mode == "inpaint":
                parts.append({"inline_data": {"mime_type": "image/png", "data": _b64(job.mask)}})
                text = ("Edit the first image. The second image is a mask: change ONLY the white area, keep "
                        "everything else identical, same framing and size. Edit: " + text)
            else:
                text = "Rework this image, keeping its composition and framing: " + text
        parts.insert(0, {"text": text})
        body = {"contents": [{"parts": parts}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                     "imageConfig": {"aspectRatio": http.nearest_aspect(job.width, job.height)}}}
        body.update(job.extra)
        imgs, notes = [], []
        for _ in range(job.count):
            r, model = gemini.post(self.base, self.key, model, "generateContent", body)
            for c in r.get("candidates") or []:
                for p in (c.get("content") or {}).get("parts") or []:
                    d = p.get("inlineData") or p.get("inline_data")
                    if d and d.get("data"):
                        imgs.append(base64.b64decode(d["data"]))
                    elif p.get("text"):
                        notes.append(p["text"])
        if not imgs:
            raise ProviderError("Gemini returned no image. %s" % " ".join(notes)[:500])
        return imgs, {"model": model, "notes": " ".join(notes)[:1000] or None}


# ====================================================================== Stability AI
class Stability(Backend):
    name = "stability"
    multiple = 64

    def __init__(self):
        self.key = secret("KRITA_MCP_STABILITY_API_KEY", "STABILITY_API_KEY")
        self.base = env("KRITA_MCP_STABILITY_BASE_URL", "https://api.stability.ai").rstrip("/")

    def configured(self):
        return bool(self.key)

    def _post(self, path, fields, files=()):
        if not self.key:
            raise ProviderError("Stability is not configured: set KRITA_MCP_STABILITY_API_KEY")
        body, ctype = http.multipart(fields, list(files))
        data, hdrs = http.request("POST", self.base + path, body, {
            "Authorization": "Bearer " + self.key, "Accept": "image/*", "Content-Type": ctype})
        return data, hdrs.get("seed")

    def generate(self, job):
        common = [("prompt", job.prompt), ("negative_prompt", job.negative or None), ("output_format", "png")]
        extra = [(k, v if isinstance(v, str) else json.dumps(v)) for k, v in job.extra.items()]
        imgs, seeds = [], []
        for i in range(job.count):
            seed = None if job.seed is None else int(job.seed) + i
            fields = common + [("seed", seed)]
            if job.mode == "inpaint":
                data, s = self._post("/v2beta/stable-image/edit/inpaint", fields + extra,
                                     [("image", "image.png", job.image, "image/png"),
                                      ("mask", "mask.png", job.mask, "image/png")])
            elif job.mode == "img2img":
                data, s = self._post("/v2beta/stable-image/generate/sd3", fields + extra + [
                    ("mode", "image-to-image"), ("strength", job.strength_or_default()),
                    ("model", job.model)], [("image", "image.png", job.image, "image/png")])
            elif job.controls:
                c = job.controls[0]
                kind = "sketch" if (c.get("type") or "sketch") in LINE_TYPES else "structure"
                data, s = self._post("/v2beta/stable-image/control/" + kind, fields + extra + [
                    ("control_strength", c.get("strength", 0.7))],
                    [("image", "control.png", c["image"], "image/png")])
            else:
                aspect = ("aspect_ratio", http.nearest_aspect(job.width, job.height))
                if job.model and job.model.startswith("sd3"):
                    path, fields = "/v2beta/stable-image/generate/sd3", fields + [("model", job.model)]
                else:
                    path = "/v2beta/stable-image/generate/%s" % ("ultra" if job.model == "ultra" else "core")
                data, s = self._post(path, fields + [aspect] + extra)
            imgs.append(data)
            seeds.append(s)
        return imgs, {"seeds": seeds}

    def upscale(self, png, factor, model=None):
        return self._post("/v2beta/stable-image/upscale/fast", [("output_format", "png")],
                          [("image", "image.png", png, "image/png")])[0]

    def remove_background(self, png, model=None):
        return self._post("/v2beta/stable-image/edit/remove-background", [("output_format", "png")],
                          [("image", "image.png", png, "image/png")])[0]


# ====================================================================== Replicate (thousands of models)
REPLICATE_FIELDS = {
    "count": ("num_outputs", "num_images", "number_of_images", "batch_size"),
    "image": ("image", "input_image", "init_image", "image_url", "start_image", "first_frame_image", "img"),
    "mask": ("mask", "mask_image", "mask_url"),
    "strength": ("prompt_strength", "strength", "denoising_strength", "image_strength"),
    "steps": ("num_inference_steps", "steps"),
    "cfg": ("guidance", "guidance_scale", "cfg"),
    "negative": ("negative_prompt",),
}


def _first(props, names):
    return next((n for n in names if n in props), None)


def collect_urls(out):
    """All result URLs in a provider's output (string, list, or nested dicts like {"images": [{"url"}]})."""
    if isinstance(out, str):
        return [out] if out.startswith(("http://", "https://", "data:")) else []
    if isinstance(out, list):
        return [u for x in out for u in collect_urls(x)]
    if isinstance(out, dict):
        return [u for k, x in out.items() if k not in ("logs", "metrics") for u in collect_urls(x)]
    return []


class Replicate(Backend):
    name = "replicate"
    multiple = 16
    # Replicate's curated collections to look in, and the inputs a model must have, per task
    DISCOVER = {"txt2img": (("text-to-image",), ()),
                "img2img": (("image-editing", "text-to-image"), ("image",)),
                "inpaint": (("inpainting", "image-editing", "text-to-image"), ("image", "mask")),
                "upscale": (("super-resolution",), ("image",)),
                "rembg": (("remove-backgrounds",), ("image",)),
                "video": (("text-to-video", "image-to-video"), ())}

    def __init__(self):
        self.key = secret("KRITA_MCP_REPLICATE_API_TOKEN", "REPLICATE_API_TOKEN")
        self.base = env("KRITA_MCP_REPLICATE_BASE_URL", "https://api.replicate.com/v1").rstrip("/")
        self.timeout = int(env("KRITA_MCP_GEN_TIMEOUT", "900"))

    def configured(self):
        return bool(self.key)

    def _hdr(self):
        if not self.key:
            raise ProviderError("Replicate is not configured: set KRITA_MCP_REPLICATE_API_TOKEN")
        return {"Authorization": "Bearer " + self.key}

    def schema(self, model):
        try:
            m = http.get_json("%s/models/%s" % (self.base, model.split(":")[0]), self._hdr())
        except ProviderError:
            return {}, None
        lv = m.get("latest_version") or {}
        props = (((lv.get("openapi_schema") or {}).get("components") or {}).get("schemas") or {}).get("Input", {})
        return props.get("properties") or {}, lv.get("id")

    def predict(self, model, inputs):
        """Run any Replicate model ("owner/name" or "owner/name:version") and return its output."""
        hdr = dict(self._hdr(), Prefer="wait=60")
        if ":" in model:
            r = http.post_json(self.base + "/predictions", {"version": model.split(":", 1)[1], "input": inputs}, hdr)
        else:
            try:
                r = http.post_json("%s/models/%s/predictions" % (self.base, model), {"input": inputs}, hdr)
            except ProviderError as e:
                if "HTTP 404" not in str(e):
                    raise
                _, version = self.schema(model)
                if not version:
                    raise
                r = http.post_json(self.base + "/predictions", {"version": version, "input": inputs}, hdr)
        deadline = time.monotonic() + self.timeout
        while r.get("status") not in ("succeeded", "failed", "canceled"):
            if time.monotonic() > deadline:
                raise ProviderError("Replicate prediction did not finish in time")
            time.sleep(1.5)
            r = http.get_json((r.get("urls") or {}).get("get") or "%s/predictions/%s" % (self.base, r["id"]),
                              self._hdr())
        if r["status"] != "succeeded":
            raise ProviderError("Replicate prediction %s: %s" % (r["status"], str(r.get("error"))[:500]))
        return r.get("output")

    def inputs_for(self, job, props):
        """Map a job onto whatever input names this model's schema uses."""
        inp = {"prompt": job.prompt}
        if not props:  # no schema: send the common names
            props = {"prompt": {}, "image": {}, "mask": {}, "num_outputs": {}, "seed": {}, "width": {}, "height": {}}
        if "seed" in props and job.seed is not None:
            inp["seed"] = int(job.seed)
        if "width" in props and "height" in props:
            inp["width"], inp["height"] = job.width, job.height
        elif "aspect_ratio" in props:
            enum = props["aspect_ratio"].get("enum")
            inp["aspect_ratio"] = http.nearest_aspect(job.width, job.height, [e for e in enum or http.ASPECTS
                                                                              if ":" in str(e)] or http.ASPECTS)
        if "output_format" in props:
            inp["output_format"] = "png"
        for key, value in (("count", job.count if job.count > 1 else None), ("negative", job.negative or None),
                           ("steps", job.steps), ("cfg", job.cfg)):
            name = _first(props, REPLICATE_FIELDS[key])
            if name and value is not None:
                inp[name] = value
        if job.mode != "txt2img" and job.image is not None:
            inp[_first(props, REPLICATE_FIELDS["image"]) or "image"] = http.data_uri(job.image)
            name = _first(props, REPLICATE_FIELDS["strength"])
            if name and job.mode == "img2img":
                inp[name] = job.strength_or_default()
        if job.mode == "inpaint" and job.mask is not None:
            inp[_first(props, REPLICATE_FIELDS["mask"]) or "mask"] = http.data_uri(job.mask)
        if job.controls:
            name = _first(props, ("control_image", "control", "structure_image", "sketch_image", "conditioning_image"))
            if not name:
                raise ProviderError("this Replicate model has no control-image input; choose a ControlNet model "
                                    "or pass the input name via extra")
            inp[name] = http.data_uri(job.controls[0]["image"])
            for key in ("control_strength", "controlnet_conditioning_scale", "conditioning_scale"):
                if key in props:
                    inp[key] = job.controls[0].get("strength", 1.0)
                    break
        inp.update(job.extra)
        return inp

    def discover(self, task):
        """A model for `task` from Replicate's live collections whose inputs fit the task."""
        slugs, needs = self.DISCOVER[task]
        tried = []
        for slug in slugs:
            try:
                col = http.get_json("%s/collections/%s" % (self.base, slug), self._hdr())
            except ProviderError:
                continue
            for m in (col.get("models") or [])[:12]:
                name = "%s/%s" % (m.get("owner"), m.get("name"))
                tried.append(name)
                lv = m.get("latest_version") or {}
                props = (((lv.get("openapi_schema") or {}).get("components") or {}).get("schemas")
                         or {}).get("Input", {}).get("properties")
                if props is None:
                    props, _ = self.schema(name)
                fits = all(_first(props, REPLICATE_FIELDS[n]) for n in needs)
                if fits:
                    return name
        raise ProviderError("found no Replicate model for %s (looked at: %s); choose one with model='owner/name'"
                            % (task, ", ".join(tried[:8]) or "nothing"))

    def generate(self, job):
        model = job.model or self.discover(job.mode)
        props, _ = self.schema(model)
        out = self.predict(model, self.inputs_for(job, props))
        urls = collect_urls(out)
        if not urls:
            raise ProviderError("Replicate model returned no files: %s" % str(out)[:300])
        return [http.download(u) for u in urls], {"model": model}

    def upscale(self, png, factor, model=None):
        model = model or self.discover("upscale")
        out = self.predict(model, {"image": http.data_uri(png), "scale": factor})
        return http.download(collect_urls(out)[0])

    def remove_background(self, png, model=None):
        out = self.predict(model or self.discover("rembg"), {"image": http.data_uri(png)})
        return http.download(collect_urls(out)[0])


# ====================================================================== fal.ai (queue API)
class Fal(Backend):
    name = "fal"
    multiple = 16

    def __init__(self):
        self.key = secret("KRITA_MCP_FAL_KEY", "FAL_KEY")
        self.timeout = int(env("KRITA_MCP_GEN_TIMEOUT", "900"))

    def configured(self):
        return bool(self.key)

    def run(self, model, inputs):
        if not self.key:
            raise ProviderError("fal is not configured: set KRITA_MCP_FAL_KEY")
        hdr = {"Authorization": "Key " + self.key}
        r = http.post_json(env("KRITA_MCP_FAL_BASE_URL", "https://queue.fal.run").rstrip("/") + "/" + model, inputs, hdr)
        status_url, response_url = r.get("status_url"), r.get("response_url")
        if not status_url:  # some deployments answer synchronously
            return r
        deadline = time.monotonic() + self.timeout
        while True:
            s = http.get_json(status_url, hdr)
            if s.get("status") == "COMPLETED":
                break
            if s.get("status") in ("FAILED", "ERROR") or s.get("error"):
                raise ProviderError("fal job failed: %s" % str(s.get("error") or s)[:500])
            if time.monotonic() > deadline:
                raise ProviderError("fal job did not finish in time")
            time.sleep(1.5)
        return http.get_json(response_url, hdr)

    def need_model(self, model, task):
        if not model:
            raise ProviderError("fal.ai has no model-listing API: choose an endpoint for %s with model='fal-ai/...' "
                                "(browse https://fal.ai/models)" % task)
        return model

    def generate(self, job):
        model = self.need_model(job.model, job.mode)
        if job.controls and not job.extra:
            raise ProviderError("fal: pass the control model's own input names via extra (e.g. a ControlNet "
                                "endpoint's control image field), or use comfyui / a1111 / stability for control")
        inp = {"prompt": job.prompt, "num_images": job.count, "output_format": "png"}
        if job.mode == "txt2img":
            inp["image_size"] = {"width": job.width, "height": job.height}
        else:
            inp["image_url"] = http.data_uri(job.image)
            if job.mode == "img2img":
                inp["strength"] = job.strength_or_default()
            else:
                inp["mask_url"] = http.data_uri(job.mask)
        if job.negative:
            inp["negative_prompt"] = job.negative
        if job.seed is not None:
            inp["seed"] = int(job.seed)
        if job.steps:
            inp["num_inference_steps"] = job.steps
        if job.cfg:
            inp["guidance_scale"] = job.cfg
        inp.update(job.extra)
        out = self.run(model, inp)
        urls = collect_urls(out.get("images") or out.get("image") or out)
        if not urls:
            raise ProviderError("fal returned no images: %s" % str(out)[:300])
        return [http.download(u) for u in urls], {"model": model, "seed": out.get("seed")}

    def upscale(self, png, factor, model=None):
        out = self.run(self.need_model(model, "upscaling"), {"image_url": http.data_uri(png),
                                                             "upscale_factor": factor})
        return http.download(collect_urls(out.get("image") or out)[0])

    def remove_background(self, png, model=None):
        out = self.run(self.need_model(model, "background removal"), {"image_url": http.data_uri(png)})
        return http.download(collect_urls(out.get("image") or out)[0])


# ====================================================================== registry
BACKENDS = {"comfyui": ComfyUI, "a1111": A1111, "openai": OpenAIImages, "gemini": Gemini,
            "stability": Stability, "replicate": Replicate, "fal": Fal}
for _name, _info in compat.COMPAT.items():
    if _info[5]:  # services with an images API
        BACKENDS[_name] = type("%sImages" % _name.title(), (compat.CompatImages,),
                               {"__init__": (lambda n: lambda self: compat.CompatImages.__init__(self, n))(_name)})
ALIASES = {"forge": "a1111", "sdnext": "a1111", "automatic1111": "a1111", "comfy": "comfyui", "google": "gemini",
           "dalle": "openai", "gpt-image": "openai", "stabilityai": "stability"}


def get(name):
    key = ALIASES.get(str(name).lower(), str(name).lower())
    if key not in BACKENDS:
        raise ProviderError("unknown image backend %r; choose from %s" % (name, ", ".join(BACKENDS)))
    return BACKENDS[key]()


def pick(name=None):
    """The requested backend, else KRITA_MCP_IMAGE_BACKEND, else the first local one that answers, else the
    first configured cloud one."""
    name = name or env("KRITA_MCP_IMAGE_BACKEND")
    if name:
        return get(name)
    for cls in (ComfyUI, A1111):
        b = cls()
        if b.status().get("reachable"):
            return b
    for cls in [OpenAIImages, Gemini, Stability, Replicate, Fal] + [
            BACKENDS[n] for n, i in compat.COMPAT.items() if i[5]]:
        b = cls()
        if b.configured():
            return b
    raise ProviderError(
        "No image-generation backend found. Start ComfyUI (127.0.0.1:8188) or AUTOMATIC1111/Forge with --api "
        "(127.0.0.1:7860), or set one of KRITA_MCP_OPENAI_API_KEY, KRITA_MCP_GEMINI_API_KEY, "
        "KRITA_MCP_STABILITY_API_KEY, KRITA_MCP_REPLICATE_API_TOKEN, KRITA_MCP_FAL_KEY for the MCP server. "
        "See docs/ai-backends.md.")


def all_status():
    out = {}
    for name, cls in BACKENDS.items():
        try:
            out[name] = cls().status()
        except Exception as e:  # noqa: BLE001 - status must never fail the whole listing
            out[name] = {"error": str(e)}
        out[name]["kind"] = cls.kind
    return out
