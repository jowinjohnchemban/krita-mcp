"""Generative-AI providers against a local mock of each service's HTTP API (no network, no keys)."""
import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kritapilot.providers import ProviderError, http  # noqa: E402
from kritapilot.providers import images, video, vision  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
B64 = base64.b64encode(PNG).decode()
MP4 = b"\x00\x00\x00\x18ftypmp42fake-video"
# model lists as the providers return them: the newest must be found, retired / special models skipped
GEMINI_MODELS = {"models": [
    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
    {"name": "models/gemini-3.8-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.1-flash-image", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/veo-3.0-generate-001", "supportedGenerationMethods": ["predictLongRunning"]},
    {"name": "models/veo-4.1-generate-preview", "supportedGenerationMethods": ["predictLongRunning"]},
    {"name": "models/text-embedding-005", "supportedGenerationMethods": ["embedContent"]}]}
OPENAI_MODELS = {"object": "list", "data": [
    {"id": "gpt-5", "created": 400}, {"id": "gpt-6", "created": 500}, {"id": "gpt-6-mini", "created": 510},
    {"id": "gpt-image-2", "created": 505}, {"id": "sora-3", "created": 506},
    {"id": "text-embedding-4", "created": 520}]}
ANTHROPIC_MODELS = {"data": [
    {"type": "model", "id": "claude-sonnet-6", "display_name": "Claude Sonnet 6", "created_at": "2026-10-01T00:00:00Z"},
    {"type": "model", "id": "claude-opus-6", "display_name": "Claude Opus 6", "created_at": "2026-09-01T00:00:00Z"},
    {"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5",
     "created_at": "2026-06-01T00:00:00Z"}],
    "has_more": False, "first_id": "claude-sonnet-6", "last_id": "claude-opus-5-5"}


class Mock:
    """Route table: (METHOD, path) -> fn(handler, body) -> (status, content_type, bytes|obj)."""

    def __init__(self):
        self.routes, self.calls = {}, []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                path = urlsplit(self.path).path
                mock.calls.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                fn = mock.routes.get((self.command, path))
                if fn is None:
                    fn = next((f for (m, p), f in mock.routes.items()
                               if m == self.command and p.endswith("*") and path.startswith(p[:-1])), None)
                if fn is None:
                    self.send_response(404); self.end_headers(); self.wfile.write(b"no route"); return
                status, ctype, out = fn(self, body)
                if not isinstance(out, bytes):
                    out = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(out)))
                if ctype.startswith("image/"):
                    self.send_header("seed", "42")
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = _do

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.serve_model_lists()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def serve_model_lists(self):
        """What real providers answer when asked which models exist (tests may override any route)."""
        self.json("GET", "/models", GEMINI_MODELS)
        self.routes[("GET", "/v1/models")] = lambda h, b: (200, "application/json", json.dumps(
            ANTHROPIC_MODELS if h.headers.get("anthropic-version") else OPENAI_MODELS).encode())
        self.json("GET", "/sdapi/v1/upscalers", [{"name": "None"}, {"name": "Lanczos"}, {"name": "R-ESRGAN 4x+"}])
        self.json("GET", "/object_info/KSampler", {"KSampler": {"input": {"required": {
            "sampler_name": [["euler", "dpmpp_2m"]], "scheduler": [["normal", "karras"]]}}}})

    def json(self, method, path, obj, status=200):
        self.routes[(method, path)] = lambda h, b: (status, "application/json", obj(h, b) if callable(obj) else obj)

    def raw(self, method, path, data, ctype):
        self.routes[(method, path)] = lambda h, b: (200, ctype, data)


@pytest.fixture()
def mock(monkeypatch):
    m = Mock()
    monkeypatch.setattr(http, "_public", lambda host: None)  # results come from 127.0.0.1 in tests
    for k in list(__import__("os").environ):
        if k == "KRITAPILOT_HOME":  # keep the isolated key folder from conftest - never the user's real keys
            continue
        if k.startswith("KRITAPILOT_") or k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(k, raising=False)
    yield m
    m.srv.shutdown()


def _body(call):
    return json.loads(call[3])


# ---------------------------------------------------------------- helpers
def test_secret_requires_opt_in_for_generic_keys(monkeypatch):
    monkeypatch.delenv("KRITAPILOT_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("KRITAPILOT_USE_ENV_KEYS", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-generic")
    assert http.secret("KRITAPILOT_OPENAI_API_KEY", "OPENAI_API_KEY") is None
    monkeypatch.setenv("KRITAPILOT_USE_ENV_KEYS", "1")
    assert http.secret("KRITAPILOT_OPENAI_API_KEY", "OPENAI_API_KEY") == "sk-generic"
    monkeypatch.setenv("KRITAPILOT_OPENAI_API_KEY", "sk-own")
    assert http.secret("KRITAPILOT_OPENAI_API_KEY", "OPENAI_API_KEY") == "sk-own"


def test_sizes_aspects_and_workflow_placeholders():
    assert http.nearest_aspect(1920, 1080) == "16:9" and http.nearest_aspect(800, 1000) == "4:5"
    assert http.nearest_size(300, 200, ["1024x1024", "1536x1024", "1024x1536"]) == "1536x1024"
    wf = {"1": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "text": "a {{prompt}}!", "n": 3}}}
    g = images.fill_workflow(wf, {"seed": 7, "prompt": "cat"})
    assert g["1"]["inputs"] == {"seed": 7, "text": "a cat!", "n": 3}
    with pytest.raises(ProviderError):
        images.fill_workflow({"nodes": [], "links": []}, {})  # UI-format export, not API format
    assert images.collect_urls({"images": [{"url": "https://x/1.png"}], "logs": "https://no"}) == ["https://x/1.png"]


def test_download_refuses_private_hosts():
    with pytest.raises(ProviderError):
        http.download("http://127.0.0.1:9/x.png")
    assert http.download("data:image/png;base64," + B64) == PNG


def test_pick_reports_how_to_configure(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_COMFYUI_URL", mock.url + "/nothing")
    monkeypatch.setenv("KRITAPILOT_A1111_URL", mock.url + "/nothing")
    with pytest.raises(ProviderError, match="No image-generation backend"):
        images.pick()


# ---------------------------------------------------------------- image backends
def test_a1111_txt2img_inpaint_and_upscale(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_A1111_URL", mock.url)
    mock.json("GET", "/sdapi/v1/sd-models", [{"title": "sdxl.safetensors"}])
    mock.json("GET", "/sdapi/v1/samplers", [{"name": "Euler a"}])
    mock.json("POST", "/sdapi/v1/txt2img", {"images": [B64, B64], "info": json.dumps({"all_seeds": [5, 6]})})
    mock.json("POST", "/sdapi/v1/img2img", {"images": [B64], "info": "{}"})
    mock.json("POST", "/sdapi/v1/extra-single-image", {"image": B64})
    b = images.pick()  # auto-detected because it answers
    assert b.name == "a1111" and b.status()["models"] == ["sdxl.safetensors"]
    imgs, meta = b.generate(images.ImageJob(prompt="cat", count=2, width=512, height=768, seed=5, model="sdxl"))
    assert imgs == [PNG, PNG] and meta["seeds"] == [5, 6]
    body = _body(mock.calls[-1])
    assert body["width"] == 512 and body["batch_size"] == 2 and body["override_settings"] == {"sd_model_checkpoint": "sdxl"}
    b.generate(images.ImageJob(mode="inpaint", prompt="dog", image=PNG, mask=PNG, strength=0.5))
    body = _body(mock.calls[-1])
    assert body["denoising_strength"] == 0.5 and body["mask"] == B64 and body["init_images"] == [B64]
    assert b.upscale(PNG, 2) == PNG


def test_comfyui_builtin_graph_and_custom_workflow(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_COMFYUI_URL", mock.url)
    ckpt = {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["model-a.safetensors"]]}}}}
    mock.json("GET", "/object_info/CheckpointLoaderSimple", ckpt)
    mock.json("POST", "/upload/image", lambda h, b: {"name": "up-%d.png" % len(mock.calls), "subfolder": ""})
    mock.json("POST", "/prompt", {"prompt_id": "p1", "node_errors": {}})
    hist = {"p1": {"status": {"status_str": "success", "completed": True},
                   "outputs": {"9": {"images": [{"filename": "o.png", "subfolder": "", "type": "output"}]}}}}
    mock.json("GET", "/history/p1", hist)
    mock.raw("GET", "/view", PNG, "image/png")
    b = images.get("comfy")
    imgs, meta = b.generate(images.ImageJob(mode="inpaint", prompt="moon", image=PNG, mask=PNG, seed=3))
    assert imgs == [PNG] and meta["seed"] == 3
    graph = _body([c for c in mock.calls if c[1] == "/prompt"][-1])["prompt"]
    assert graph["4"]["inputs"]["ckpt_name"] == "model-a.safetensors"
    assert graph["13"]["class_type"] == "SetLatentNoiseMask" and graph["3"]["inputs"]["denoise"] == 0.9
    assert graph["12"]["inputs"]["image"].startswith("up-")
    wf = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}"}},
          "2": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}"}}}
    b.generate(images.ImageJob(prompt="sun", seed=11, workflow=wf))
    graph = _body([c for c in mock.calls if c[1] == "/prompt"][-1])["prompt"]
    assert graph["1"]["inputs"]["text"] == "sun" and graph["2"]["inputs"]["seed"] == 11
    mock.json("POST", "/prompt", {"error": "bad"}, status=400)
    with pytest.raises(ProviderError, match="HTTP 400"):
        b.generate(images.ImageJob(prompt="x"))


def test_openai_generations_and_masked_edit(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_OPENAI_BASE_URL", mock.url + "/v1")  # loopback: no key needed
    mock.json("POST", "/v1/images/generations", {"data": [{"b64_json": B64}]})
    mock.json("POST", "/v1/images/edits", {"data": [{"b64_json": B64}]})
    b = images.get("openai")
    assert b.configured() and b.sizes[0] == "1024x1024"
    imgs, _ = b.generate(images.ImageJob(prompt="fox", width=1536, height=1024, negative="blur"))
    body = _body(mock.calls[-1])
    assert imgs == [PNG] and body["size"] == "1536x1024" and "Avoid: blur" in body["prompt"]
    b.generate(images.ImageJob(mode="inpaint", prompt="hat", image=PNG, mask_alpha=PNG))
    raw = mock.calls[-1][3]
    assert b'name="mask"; filename="mask.png"' in raw and b'name="image"; filename="image.png"' in raw


def test_gemini_image(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("KRITAPILOT_GEMINI_BASE_URL", mock.url)
    mock.json("POST", "/models/gemini-3.1-flash-image:generateContent",  # the newest image model listed
              {"candidates": [{"content": {"parts": [{"text": "here"}, {"inlineData": {"data": B64}}]}}]})
    imgs, meta = images.get("google").generate(images.ImageJob(mode="inpaint", prompt="add a bird", image=PNG,
                                                                mask=PNG, width=1600, height=900))
    call = mock.calls[-1]
    body = _body(call)
    assert imgs == [PNG] and meta["notes"] == "here" and call[2]["x-goog-api-key"] == "g-key"
    assert len(body["contents"][0]["parts"]) == 3 and body["generationConfig"]["imageConfig"]["aspectRatio"] == "16:9"


def test_stability_routes(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_STABILITY_API_KEY", "s-key")
    monkeypatch.setenv("KRITAPILOT_STABILITY_BASE_URL", mock.url)
    for path in ("/v2beta/stable-image/generate/core", "/v2beta/stable-image/edit/inpaint",
                 "/v2beta/stable-image/upscale/fast", "/v2beta/stable-image/edit/remove-background"):
        mock.raw("POST", path, PNG, "image/png")
    b = images.get("stability")
    imgs, meta = b.generate(images.ImageJob(prompt="x", count=2))
    assert imgs == [PNG, PNG] and meta["seeds"] == ["42", "42"]
    assert b"aspect_ratio" in mock.calls[-1][3] and mock.calls[-1][2]["authorization"] == "Bearer s-key"
    b.generate(images.ImageJob(mode="inpaint", prompt="y", image=PNG, mask=PNG))
    assert mock.calls[-1][1] == "/v2beta/stable-image/edit/inpaint"
    assert b.upscale(PNG, 4) == PNG and b.remove_background(PNG) == PNG


def test_replicate_maps_inputs_from_the_model_schema(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_REPLICATE_API_TOKEN", "r-key")
    monkeypatch.setenv("KRITAPILOT_REPLICATE_BASE_URL", mock.url + "/v1")
    schema = {"latest_version": {"id": "v1", "openapi_schema": {"components": {"schemas": {"Input": {"properties": {
        "prompt": {}, "num_outputs": {}, "aspect_ratio": {"enum": ["1:1", "16:9"]}, "seed": {},
        "output_format": {}}}}}}}}
    mock.json("GET", "/v1/models/acme/painter", schema)
    mock.raw("GET", "/files/out.png", PNG, "image/png")
    mock.json("POST", "/v1/models/acme/painter/predictions",
              lambda h, b: {"id": "x", "status": "processing", "urls": {"get": mock.url + "/v1/predictions/x"}})
    mock.json("GET", "/v1/predictions/x", {"id": "x", "status": "succeeded", "output": [mock.url + "/files/out.png"]})
    imgs, meta = images.get("replicate").generate(images.ImageJob(prompt="p", model="acme/painter", count=2,
                                                                   width=1600, height=900, seed=1))
    sent = _body([c for c in mock.calls if c[1].endswith("/predictions")][-1])["input"]
    assert imgs == [PNG] and sent == {"prompt": "p", "seed": 1, "aspect_ratio": "16:9", "output_format": "png",
                                      "num_outputs": 2}


def test_fal_queue(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_FAL_KEY", "f-key")
    monkeypatch.setenv("KRITAPILOT_FAL_BASE_URL", mock.url + "/q")
    mock.json("POST", "/q/fal-ai/flux/dev", {"request_id": "r", "status_url": mock.url + "/q/s",
                                             "response_url": mock.url + "/q/r"})
    mock.json("GET", "/q/s", {"status": "COMPLETED"})
    mock.json("GET", "/q/r", {"images": [{"url": "data:image/png;base64," + B64}], "seed": 9})
    with pytest.raises(ProviderError, match="no model-listing API"):  # fal: never a guessed model name
        images.get("fal").generate(images.ImageJob(prompt="p"))
    imgs, meta = images.get("fal").generate(images.ImageJob(prompt="p", width=512, height=512,
                                                            model="fal-ai/flux/dev"))
    assert imgs == [PNG] and meta["seed"] == 9 and mock.calls[0][2]["authorization"] == "Key f-key"


# ---------------------------------------------------------------- video
def test_sora_video(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_OPENAI_API_KEY", "o-key")
    monkeypatch.setenv("KRITAPILOT_OPENAI_BASE_URL", mock.url + "/v1")
    mock.json("POST", "/v1/videos", {"id": "vid", "status": "queued"})
    mock.json("GET", "/v1/videos/vid", {"id": "vid", "status": "completed"})
    mock.raw("GET", "/v1/videos/vid/content", MP4, "video/mp4")
    data, meta = video.get("sora").generate(video.VideoJob(prompt="waves", image=PNG, seconds=7, width=720, height=1280))
    assert data == MP4 and meta["size"] == "720x1280" and meta["seconds"] == 8
    assert b'name="input_reference"' in [c for c in mock.calls if c[1] == "/v1/videos"][0][3]


def test_heygen_talking_avatar(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_HEYGEN_API_KEY", "h-key")
    monkeypatch.setenv("KRITAPILOT_HEYGEN_BASE_URL", mock.url)
    monkeypatch.setenv("KRITAPILOT_HEYGEN_UPLOAD_URL", mock.url + "/up")
    mock.json("POST", "/up/v1/asset", {"data": {"id": "asset1"}})
    mock.json("POST", "/v2/video/generate", {"error": None, "data": {"video_id": "v9"}})
    mock.json("GET", "/v1/video_status.get", {"data": {"status": "completed", "video_url": mock.url + "/out.mp4"}})
    mock.raw("GET", "/out.mp4", MP4, "video/mp4")
    mock.json("GET", "/v2/avatars", {"data": {"avatars": [{"avatar_id": "av-first", "avatar_name": "Ana"}]}})
    mock.json("GET", "/v2/voices", {"data": {"voices": [{"voice_id": "vo-first", "name": "Sam"}]}})
    b = video.get("heygen")
    b.generate(video.VideoJob(script="hi"))  # nothing chosen: the account's own first avatar and voice
    first = _body([c for c in mock.calls if c[1] == "/v2/video/generate"][0])["video_inputs"][0]
    assert first["character"]["avatar_id"] == "av-first" and first["voice"]["voice_id"] == "vo-first"
    data, meta = b.generate(video.VideoJob(script="Hello!", avatar_id="a1", voice_id="v1", image=PNG))
    gen = _body([c for c in mock.calls if c[1] == "/v2/video/generate"][-1])["video_inputs"][0]
    assert data == MP4 and gen["background"] == {"type": "image", "image_asset_id": "asset1"}
    assert gen["voice"]["input_text"] == "Hello!" and mock.calls[-2][2]["x-api-key"] == "h-key"


def test_veo_video(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_GEMINI_API_KEY", "g")
    monkeypatch.setenv("KRITAPILOT_GEMINI_BASE_URL", mock.url)
    mock.json("POST", "/models/veo-4.1-generate-preview:predictLongRunning", {"name": "operations/op1"})
    mock.json("GET", "/operations/op1", {"done": True, "response": {"generateVideoResponse": {
        "generatedSamples": [{"video": {"uri": mock.url + "/v.mp4"}}]}}})
    mock.raw("GET", "/v.mp4", MP4, "video/mp4")
    data, _ = video.get("veo").generate(video.VideoJob(prompt="p", image=PNG))
    assert data == MP4


# ---------------------------------------------------------------- vision
def test_vision_openai_compatible_and_gemini(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_OPENAI_BASE_URL", mock.url + "/v1")  # e.g. a local Ollama
    mock.json("POST", "/v1/chat/completions", {"model": "llava", "choices": [{"message": {"content": "nice"}}]})
    text, meta = vision.ask(PNG, "critique", provider="ollama", model="llava")
    body = _body(mock.calls[-1])
    assert text == "nice" and meta["provider"] == "openai"
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    monkeypatch.setenv("KRITAPILOT_GEMINI_API_KEY", "g")
    monkeypatch.setenv("KRITAPILOT_GEMINI_BASE_URL", mock.url)
    mock.json("POST", "/models/gemini-3.8-flash:generateContent",
              {"candidates": [{"content": {"parts": [{"text": "warm "}, {"text": "palette"}]}}]})
    assert vision.ask(PNG, "q", provider="gemini")[0] == "warm palette"


def test_vision_claude_needs_explicit_key(monkeypatch):
    monkeypatch.delenv("KRITAPILOT_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("KRITAPILOT_USE_ENV_KEYS", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-generic")
    with pytest.raises(ProviderError, match="KRITAPILOT_ANTHROPIC_API_KEY"):
        vision.ask(PNG, "q", provider="claude")


def test_vision_claude_through_the_sdk(mock, monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("KRITAPILOT_ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", mock.url)
    reply = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
             "content": [{"type": "text", "text": "Strong focal point; "}, {"type": "text", "text": "lift the shadows."}],
             "stop_reason": "end_turn", "stop_sequence": None,
             "usage": {"input_tokens": 10, "output_tokens": 5}}
    mock.json("POST", "/v1/messages", reply)
    text, meta = vision.ask(PNG, "critique this", provider="claude")
    call = mock.calls[-1]
    body = json.loads(call[3])
    assert text == "Strong focal point; lift the shadows." and meta == {"model": "claude-opus-5-5", "provider": "anthropic"}
    assert "sk-ant-test" in (call[2].get("x-api-key", "") + call[2].get("authorization", ""))
    assert body["model"] == "claude-opus-6"  # the newest Opus listed, not a built-in name
    assert body["messages"][0]["content"][0]["source"]["data"] == B64
    assert body.get("fallbacks") == "default" and "server-side-fallback" in call[2].get("anthropic-beta", "")
    reply["stop_reason"] = "refusal"
    mock.json("POST", "/v1/messages", reply)
    with pytest.raises(ProviderError, match="declined"):
        vision.ask(PNG, "q", provider="claude")


# ---------------------------------------------------------------- control (sketch / ControlNet)
def test_control_model_picking():
    models = ["control_v11p_sd15_canny", "control_v11p_sd15_lineart", "t2i-depth"]
    assert images.pick_control_model(models, "lineart") == "control_v11p_sd15_lineart"
    assert images.pick_control_model(models, "sketch") == "control_v11p_sd15_lineart"
    assert images.pick_control_model(models, "depth") == "t2i-depth"
    assert images.pick_control_model(models, "pose", "mine") == "mine"
    with pytest.raises(ProviderError, match="no ControlNet model"):
        images.pick_control_model(models, "openpose")


def test_a1111_controlnet_units(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_A1111_URL", mock.url)
    mock.json("GET", "/controlnet/model_list", {"model_list": ["control_v11p_sd15_lineart [43d4be0d]"]})
    mock.json("POST", "/sdapi/v1/txt2img", {"images": [B64], "info": "{}"})
    images.get("a1111").generate(images.ImageJob(prompt="cat", controls=[
        {"image": PNG, "type": "lineart", "strength": 0.7, "end": 0.8}]))
    unit = _body(mock.calls[-1])["alwayson_scripts"]["controlnet"]["args"][0]
    assert unit["model"].startswith("control_v11p_sd15_lineart") and unit["image"] == B64
    assert unit["weight"] == 0.7 and unit["guidance_end"] == 0.8 and unit["module"] == "none"


def test_comfyui_controlnet_chain(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_COMFYUI_URL", mock.url)
    mock.json("GET", "/object_info/ControlNetLoader", {"ControlNetLoader": {"input": {"required": {
        "control_net_name": [["sdxl-canny.safetensors", "sdxl-depth.safetensors"]]}}}})
    mock.json("POST", "/upload/image", lambda h, b: {"name": "c%d.png" % len(mock.calls), "subfolder": ""})
    mock.json("POST", "/prompt", {"prompt_id": "p", "node_errors": {}})
    mock.json("GET", "/history/p", {"p": {"status": {"status_str": "success", "completed": True},
                                          "outputs": {"9": {"images": [{"filename": "o.png"}]}}}})
    mock.raw("GET", "/view", PNG, "image/png")
    images.get("comfyui").generate(images.ImageJob(prompt="x", model="ck.safetensors", controls=[
        {"image": PNG, "type": "canny"}, {"image": PNG, "type": "depth", "strength": 0.5}]))
    g = _body([c for c in mock.calls if c[1] == "/prompt"][-1])["prompt"]
    assert g["21"]["inputs"]["control_net_name"] == "sdxl-canny.safetensors"
    assert g["24"]["inputs"]["control_net_name"] == "sdxl-depth.safetensors"
    assert g["25"]["inputs"]["positive"] == ["22", 0] and g["25"]["inputs"]["strength"] == 0.5
    assert g["3"]["inputs"]["positive"] == ["25", 0] and g["3"]["inputs"]["negative"] == ["25", 1]


def test_cloud_backends_use_the_guide_image(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_STABILITY_API_KEY", "s")
    monkeypatch.setenv("KRITAPILOT_STABILITY_BASE_URL", mock.url)
    mock.raw("POST", "/v2beta/stable-image/control/sketch", PNG, "image/png")
    images.get("stability").generate(images.ImageJob(prompt="x", controls=[{"image": PNG, "type": "scribble",
                                                                             "strength": 0.6}]))
    assert mock.calls[-1][1] == "/v2beta/stable-image/control/sketch" and b"control_strength" in mock.calls[-1][3]
    monkeypatch.setenv("KRITAPILOT_OPENAI_BASE_URL", mock.url + "/v1")
    mock.json("POST", "/v1/images/edits", {"data": [{"b64_json": B64}]})
    images.get("openai").generate(images.ImageJob(prompt="castle", controls=[{"image": PNG}]))
    raw = mock.calls[-1][3]
    assert mock.calls[-1][1] == "/v1/images/edits" and b"layout and line guide" in raw and b'name="mask"' not in raw
    monkeypatch.setenv("KRITAPILOT_REPLICATE_API_TOKEN", "r")
    monkeypatch.setenv("KRITAPILOT_REPLICATE_BASE_URL", mock.url + "/v1")
    rep = images.get("replicate")
    inp = rep.inputs_for(images.ImageJob(prompt="p", controls=[{"image": PNG, "strength": 0.4}]),
                         {"prompt": {}, "control_image": {}, "control_strength": {}})
    assert inp["control_image"].startswith("data:image/png") and inp["control_strength"] == 0.4
    with pytest.raises(ProviderError, match="no control-image input"):
        rep.inputs_for(images.ImageJob(prompt="p", controls=[{"image": PNG}]), {"prompt": {}})



# ---------------------------------------------------------------- asset libraries
def test_polyhaven_search_and_fetch(mock, monkeypatch):
    from kritapilot.providers import assets
    monkeypatch.setenv("KRITAPILOT_POLYHAVEN_URL", mock.url)
    mock.json("GET", "/assets", {
        "rock_wall": {"name": "Rock Wall", "tags": ["stone", "cliff"], "categories": ["rock"], "authors": {"A": "All"}},
        "oak_bark": {"name": "Oak Bark", "tags": ["tree", "wood"], "categories": ["wood"]},
        "grey_rock": {"name": "Grey Rock", "tags": ["rock"], "categories": ["rock", "natural"]}})
    found = assets.search("rock", "textures", 5)
    assert [r["id"] for r in found["results"]] == ["grey_rock", "rock_wall"] and "CC0" in found["licence"]
    assert mock.calls[-1][2]["user-agent"].startswith("kritapilot")
    mock.json("GET", "/files/rock_wall", {"Diffuse": {"1k": {"jpg": {"url": mock.url + "/t/rock_1k.jpg"}},
                                                      "2k": {"jpg": {"url": mock.url + "/t/rock_2k.jpg"}}}})
    mock.json("GET", "/info/rock_wall", {"name": "Rock Wall"})
    mock.raw("GET", "/t/rock_2k.jpg", PNG, "image/jpeg")
    data, name, credit = assets.fetch("rock_wall", "textures", "2k")
    assert data == PNG and name == "Rock Wall" and "CC0" in credit
    with pytest.raises(ProviderError, match="no normal map"):
        assets.fetch("rock_wall", "textures", "2k", "normal")


def test_openverse_photos_carry_attribution(mock, monkeypatch):
    from kritapilot.providers import assets
    monkeypatch.setenv("KRITAPILOT_OPENVERSE_URL", mock.url + "/v1")
    mock.json("GET", "/v1/images/", {"results": [{"id": "abc", "title": "Granite", "creator": "Ana",
                                                  "license": "by", "license_version": "2.0", "width": 800,
                                                  "height": 600, "attribution": "Granite by Ana is CC BY 2.0"}]})
    r = assets.search("granite", "photos", 3, licence="by")
    assert r["results"][0]["licence"] == "BY 2.0" and "Ana" in r["results"][0]["attribution"]
    assert "mature=false" in mock.calls[-1][1] and "license=by" in mock.calls[-1][1]
    mock.json("GET", "/v1/images/abc/", {"url": mock.url + "/img/abc.jpg", "title": "Granite",
                                         "attribution": "Granite by Ana is CC BY 2.0", "mature": False})
    mock.raw("GET", "/img/abc.jpg", PNG, "image/jpeg")
    assert assets.fetch("abc", "photos")[2] == "Granite by Ana is CC BY 2.0"


# ---------------------------------------------------------------- OpenAI-compatible services
def test_compatible_services_list_models_live(mock, monkeypatch):
    from kritapilot.providers import compat
    monkeypatch.setenv("KRITAPILOT_TOGETHER_API_KEY", "t-key")
    monkeypatch.setenv("KRITAPILOT_TOGETHER_BASE_URL", mock.url + "/tg")
    mock.json("GET", "/tg/models", [  # Together answers with a bare list and labels each model's type
        {"id": "meta-llama/Llama-5-70B", "type": "chat", "created": 300},
        {"id": "black-forest-labs/FLUX.3-pro", "type": "image", "created": 310},
        {"id": "black-forest-labs/FLUX.2-dev", "type": "image", "created": 200},
        {"id": "togethercomputer/m2-bert", "type": "embedding", "created": 400}])
    assert compat.chat_models("together") == ["meta-llama/Llama-5-70B"]
    mock.json("POST", "/tg/images/generations", {"data": [{"b64_json": B64}]})
    imgs, meta = images.get("together").generate(images.ImageJob(prompt="a fox", width=768, height=512))
    sent = _body(mock.calls[-1])
    assert imgs == [PNG] and meta["model"] == "black-forest-labs/FLUX.3-pro"
    assert (sent["width"], sent["height"], sent["response_format"]) == (768, 512, "b64_json")
    with pytest.raises(ProviderError, match="text-to-image only"):
        images.get("together").generate(images.ImageJob(mode="inpaint", prompt="x", image=PNG, mask=PNG))
    assert "together" in images.all_status() and images.all_status()["xai"]["configured"] is False


def test_vision_through_a_compatible_service(mock, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_GROQ_API_KEY", "g-key")
    monkeypatch.setenv("KRITAPILOT_GROQ_BASE_URL", mock.url + "/groq")
    mock.json("GET", "/groq/models", {"data": [{"id": "whisper-large-v4", "created": 9},
                                               {"id": "llama-5-vision", "created": 8}]})
    mock.json("POST", "/groq/chat/completions", {"choices": [{"message": {"content": "balanced"}}]})
    text, meta = vision.ask(PNG, "how is the composition?", provider="groq")
    body = _body(mock.calls[-1])
    assert text == "balanced" and body["model"] == "llama-5-vision" and mock.calls[-1][2]["authorization"] == "Bearer g-key"


def test_request_only_opens_web_addresses():
    from kritapilot.providers import http
    for bad in ("file:///etc/passwd", "ftp://example.com/x", "data:text/plain,hi", "http://", "//example.com",
                "https://user:pw@example.com/v1", 42):
        with pytest.raises(http.ProviderError):
            http.request("GET", bad)
    assert http.check_url(" https://example.com/v1 ") == "https://example.com/v1"
    assert http.check_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"
