"""Video-generation backends: HeyGen (talking avatars / talking photos), OpenAI Sora, Google Veo,
and any video model on Replicate or fal.ai. Each returns (mp4 bytes, meta)."""
import base64
import json
import time
from dataclasses import dataclass, field
from typing import Optional

from . import http
from .http import ProviderError, env, secret
from .images import GEMINI_BASE, Fal, Replicate, collect_urls


@dataclass
class VideoJob:
    prompt: str = ""
    negative: str = ""
    image: Optional[bytes] = None  # start frame (image-to-video), HeyGen background or talking photo
    width: int = 1280
    height: int = 720
    seconds: Optional[float] = None
    model: Optional[str] = None
    script: Optional[str] = None  # HeyGen: what the avatar says
    avatar_id: Optional[str] = None
    voice_id: Optional[str] = None
    character: str = "avatar"  # HeyGen: avatar | photo (animate the canvas as a talking photo)
    extra: dict = field(default_factory=dict)


def _timeout():
    return int(env("KRITA_MCP_VIDEO_TIMEOUT", "1800"))


def _poll(fetch, done, failed, what):
    deadline = time.monotonic() + _timeout()
    while True:
        r = fetch()
        if done(r):
            return r
        err = failed(r)
        if err:
            raise ProviderError("%s failed: %s" % (what, str(err)[:500]))
        if time.monotonic() > deadline:
            raise ProviderError("%s did not finish within %d s (KRITA_MCP_VIDEO_TIMEOUT)" % (what, _timeout()))
        time.sleep(3)


class HeyGen:
    name = "heygen"
    api = "https://api.heygen.com"
    upload_api = "https://upload.heygen.com"

    def __init__(self):
        self.key = secret("KRITA_MCP_HEYGEN_API_KEY", "HEYGEN_API_KEY")
        self.api = env("KRITA_MCP_HEYGEN_BASE_URL", self.api).rstrip("/")
        self.upload_api = env("KRITA_MCP_HEYGEN_UPLOAD_URL", self.upload_api).rstrip("/")

    def configured(self):
        return bool(self.key)

    def _hdr(self):
        if not self.key:
            raise ProviderError("HeyGen is not configured: set KRITA_MCP_HEYGEN_API_KEY")
        return {"X-Api-Key": self.key, "Accept": "application/json"}

    def status(self, details=False):
        out = {"configured": self.configured()}
        if details and self.key:
            av = http.get_json(self.api + "/v2/avatars", self._hdr()).get("data") or {}
            out["avatars"] = [{"avatar_id": a.get("avatar_id"), "name": a.get("avatar_name")}
                              for a in (av.get("avatars") or [])][:60]
            vo = http.get_json(self.api + "/v2/voices", self._hdr()).get("data") or {}
            out["voices"] = [{"voice_id": v.get("voice_id"), "name": v.get("name"), "language": v.get("language")}
                             for v in (vo.get("voices") or [])][:80]
        return out

    def _upload(self, path, png):
        data, _ = http.request("POST", self.upload_api + path, png, dict(self._hdr(), **{"Content-Type": "image/png"}))
        return (json.loads(data).get("data") or {})

    def generate(self, job):
        if not (job.script or job.prompt):
            raise ProviderError("HeyGen needs `script` (what the avatar says)")
        if not job.voice_id:
            raise ProviderError("HeyGen needs voice_id (list them with video_backends(details=true))")
        if job.character == "photo":
            if job.image is None:
                raise ProviderError("character=photo animates the canvas; the canvas image is missing")
            pid = self._upload("/v1/talking_photo", job.image).get("talking_photo_id")
            if not pid:
                raise ProviderError("HeyGen did not accept the canvas as a talking photo (needs a clear face)")
            character = {"type": "talking_photo", "talking_photo_id": pid}
            background = {"type": "color", "value": "#ffffff"}
        else:
            if not job.avatar_id:
                raise ProviderError("HeyGen needs avatar_id (or character='photo' to animate the canvas)")
            character = {"type": "avatar", "avatar_id": job.avatar_id, "avatar_style": "normal"}
            background = {"type": "color", "value": "#ffffff"}
            if job.image is not None:  # the canvas becomes the scene behind the avatar
                asset = self._upload("/v1/asset", job.image)
                if asset.get("id"):
                    background = {"type": "image", "image_asset_id": asset["id"]}
        body = {"video_inputs": [{"character": character, "background": background,
                                  "voice": {"type": "text", "input_text": job.script or job.prompt,
                                            "voice_id": job.voice_id}}],
                "dimension": {"width": job.width, "height": job.height}}
        body.update(job.extra)
        r = http.post_json(self.api + "/v2/video/generate", body, self._hdr())
        vid = (r.get("data") or {}).get("video_id")
        if not vid:
            raise ProviderError("HeyGen: %s" % str(r.get("error") or r)[:500])
        st = _poll(lambda: http.get_json(self.api + "/v1/video_status.get?video_id=" + vid, self._hdr())["data"],
                   lambda d: d.get("status") == "completed", lambda d: d.get("error") if d.get("status") == "failed"
                   else None, "HeyGen video")
        return http.download(st["video_url"]), {"video_id": vid, "duration": st.get("duration")}


class Sora:
    name = "openai"

    def __init__(self):
        self.base = http.service_url("KRITA_MCP_OPENAI_BASE_URL", "openai", "https://api.openai.com/v1")
        self.key = secret("KRITA_MCP_OPENAI_API_KEY", "OPENAI_API_KEY")
        self.model = env("KRITA_MCP_OPENAI_VIDEO_MODEL", "sora-2")

    def configured(self):
        return bool(self.key)

    def status(self, details=False):
        return {"configured": self.configured(), "model": self.model}

    def generate(self, job):
        if not self.key:
            raise ProviderError("OpenAI is not configured: set KRITA_MCP_OPENAI_API_KEY")
        hdr = {"Authorization": "Bearer " + self.key}
        size = http.nearest_size(job.width, job.height, ["1280x720", "720x1280"])
        secs = min((4, 8, 12), key=lambda s: abs(s - (job.seconds or 4)))
        fields = [("model", job.model or self.model), ("prompt", job.prompt), ("size", size), ("seconds", str(secs))]
        fields += [(k, v if isinstance(v, str) else json.dumps(v)) for k, v in job.extra.items()]
        files = [("input_reference", "frame.png", job.image, "image/png")] if job.image is not None else []
        body, ctype = http.multipart(fields, files)
        data, _ = http.request("POST", self.base + "/videos", body, dict(hdr, **{"Content-Type": ctype}))
        vid = json.loads(data)["id"]
        _poll(lambda: http.get_json("%s/videos/%s" % (self.base, vid), hdr),
              lambda r: r.get("status") == "completed",
              lambda r: r.get("error") or ("failed" if r.get("status") == "failed" else None), "Sora video")
        mp4, _ = http.request("GET", "%s/videos/%s/content" % (self.base, vid), headers=hdr)
        return mp4, {"video_id": vid, "size": size, "seconds": secs}


class Veo:
    name = "gemini"

    def __init__(self):
        self.key = secret("KRITA_MCP_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
        self.base = env("KRITA_MCP_GEMINI_BASE_URL", GEMINI_BASE).rstrip("/")
        self.model = env("KRITA_MCP_GEMINI_VIDEO_MODEL", "veo-3.0-generate-001")

    def configured(self):
        return bool(self.key)

    def status(self, details=False):
        return {"configured": self.configured(), "model": self.model}

    def generate(self, job):
        if not self.key:
            raise ProviderError("Gemini is not configured: set KRITA_MCP_GEMINI_API_KEY")
        hdr = {"x-goog-api-key": self.key}
        inst = {"prompt": job.prompt}
        if job.image is not None:
            inst["image"] = {"bytesBase64Encoded": base64.b64encode(job.image).decode(), "mimeType": "image/png"}
        params = {"aspectRatio": "9:16" if job.height > job.width else "16:9"}
        if job.negative:
            params["negativePrompt"] = job.negative
        params.update(job.extra)
        model = job.model or self.model
        op = http.post_json("%s/models/%s:predictLongRunning" % (self.base, model),
                            {"instances": [inst], "parameters": params}, hdr)
        name = op["name"]
        op = _poll(lambda: http.get_json("%s/%s" % (self.base, name), hdr), lambda r: r.get("done"),
                   lambda r: None, "Veo video")
        if op.get("error"):
            raise ProviderError("Veo failed: %s" % str(op["error"])[:500])
        resp = op.get("response") or {}
        samples = (resp.get("generateVideoResponse") or {}).get("generatedSamples") or resp.get("generatedVideos") or []
        uri = ((samples[0] if samples else {}).get("video") or {}).get("uri")
        if not uri:
            raise ProviderError("Veo returned no video (it may have been filtered): %s" % str(resp)[:300])
        return http.download(uri, headers=hdr), {"model": model}


class ReplicateVideo(Replicate):
    def status(self, details=False):
        return {"configured": self.configured()}

    def generate(self, job):
        if not job.model:
            raise ProviderError("Replicate video needs model='owner/name' (any text/image-to-video model)")
        props, _ = self.schema(job.model)
        inp = {"prompt": job.prompt}
        if job.image is not None:
            inp[next((n for n in ("start_image", "first_frame_image", "image", "input_image", "image_url")
                      if n in props), "image")] = http.data_uri(job.image)
        if job.seconds and "duration" in props:
            inp["duration"] = job.seconds
        if "aspect_ratio" in props:
            inp["aspect_ratio"] = "9:16" if job.height > job.width else "16:9"
        if job.negative and "negative_prompt" in props:
            inp["negative_prompt"] = job.negative
        inp.update(job.extra)
        urls = collect_urls(self.predict(job.model, inp))
        if not urls:
            raise ProviderError("the model returned no video")
        return http.download(urls[0]), {"model": job.model}


class FalVideo(Fal):
    def status(self, details=False):
        return {"configured": self.configured()}

    def generate(self, job):
        if not job.model:
            raise ProviderError("fal video needs model='fal-ai/...' (any text/image-to-video endpoint)")
        inp = {"prompt": job.prompt}
        if job.image is not None:
            inp["image_url"] = http.data_uri(job.image)
        if job.seconds:
            inp["duration"] = str(int(job.seconds))
        if job.negative:
            inp["negative_prompt"] = job.negative
        inp.update(job.extra)
        out = self.run(job.model, inp)
        urls = collect_urls(out.get("video") or out)
        if not urls:
            raise ProviderError("fal returned no video: %s" % str(out)[:300])
        return http.download(urls[0]), {"model": job.model}


BACKENDS = {"heygen": HeyGen, "openai": Sora, "gemini": Veo, "replicate": ReplicateVideo, "fal": FalVideo}
ALIASES = {"sora": "openai", "veo": "gemini", "google": "gemini"}


def get(name=None):
    name = name or env("KRITA_MCP_VIDEO_BACKEND")
    if not name:
        for key, cls in BACKENDS.items():
            if cls().configured():
                return cls()
        raise ProviderError("No video backend configured. Set KRITA_MCP_HEYGEN_API_KEY, KRITA_MCP_OPENAI_API_KEY, "
                            "KRITA_MCP_GEMINI_API_KEY, KRITA_MCP_REPLICATE_API_TOKEN or KRITA_MCP_FAL_KEY.")
    key = ALIASES.get(str(name).lower(), str(name).lower())
    if key not in BACKENDS:
        raise ProviderError("unknown video backend %r; choose from %s" % (name, ", ".join(BACKENDS)))
    return BACKENDS[key]()


def all_status(details=False):
    out = {}
    for name, cls in BACKENDS.items():
        try:
            out[name] = cls().status(details) if details else cls().status()
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": str(e)}
    return out
