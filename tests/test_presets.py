"""Looks, camera moves and the paint-vs-generate decision."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kritapilot import presets  # noqa: E402
from test_providers import B64, Mock  # noqa: E402

import kritapilot.server as S  # noqa: E402
from kritapilot import gen_tools as G  # noqa: E402


def test_style_and_camera_recipes():
    p, n = presets.apply_style("a lighthouse at dusk.", None, "Photo")
    assert p.startswith("a lighthouse at dusk, photorealistic") and "plastic skin" in n
    assert presets.apply_style("x", "my neg", "anime")[1] == "my neg"
    assert presets.apply_camera("waves", "dolly-in") == "waves. Camera: smooth slow dolly-in toward the subject."
    with pytest.raises(ValueError):
        presets.apply_style("x", None, "vaporwave-ish")
    with pytest.raises(ValueError):
        presets.apply_camera("x", "barrel_roll")


def test_guess_style_and_approach():
    assert presets.guess_style("realistic portrait of an old fisherman") == "portrait"
    assert presets.guess_style("cinematic shot of a rainy street") == "cinematic"
    assert presets.guess_style("a cute dragon") is None
    assert presets.choose_approach("a photo of a cat", "photo", True)[0] == "generate"
    assert presets.choose_approach("paint it by hand: a cat", None, True)[0] == "paint"
    assert presets.choose_approach("a pixel art cat", None, True)[0] == "paint"
    assert presets.choose_approach("a cat", None, False)[0] == "paint"
    assert presets.choose_approach("a cat", None, True)[0] == "generate"


def test_create_artwork_paints_without_a_generator(bridge, monkeypatch):
    calls, _ = bridge
    monkeypatch.setenv("KRITA_MCP_COMFYUI_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("KRITA_MCP_A1111_URL", "http://127.0.0.1:9")
    for k in ("KRITA_MCP_IMAGE_BACKEND", "KRITA_MCP_OPENAI_API_KEY", "KRITA_MCP_GEMINI_API_KEY",
              "KRITA_MCP_STABILITY_API_KEY", "KRITA_MCP_REPLICATE_API_TOKEN", "KRITA_MCP_FAL_KEY",
              "KRITA_MCP_OPENAI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    out = asyncio.run(G.create_artwork("a watercolor fox in snow", aspect="16:9", style="watercolor"))[0]
    assert out["approach"] == "paint" and out["style"] == "watercolor"
    assert ("document.new", {"width": 1344, "height": 768, "name": "a watercolor fox in snow",
                             "background": "white"}) in calls
    assert [p["name"] for m, p in calls if m == "layer.create"] == G.PAINT_LAYERS
    assert out["plan"] and "watercolour" in out["look"]


def test_create_artwork_generates_realistic_photo(bridge, monkeypatch):
    calls, state = bridge
    state["has_selection"] = False
    m = Mock()
    try:
        m.json("GET", "/sdapi/v1/sd-models", [{"title": "real"}])
        m.json("POST", "/sdapi/v1/txt2img", lambda h, b: {"images": [B64] * json.loads(b)["batch_size"],
                                                           "info": "{}"})
        monkeypatch.setenv("KRITA_MCP_A1111_URL", m.url)
        monkeypatch.setenv("KRITA_MCP_COMFYUI_URL", "http://127.0.0.1:9")
        monkeypatch.delenv("KRITA_MCP_IMAGE_BACKEND", raising=False)
        out = asyncio.run(G.create_artwork("realistic portrait of an old fisherman", count=2))
    finally:
        m.srv.shutdown()
    head = out[0]
    assert head["approach"] == "generate" and head["style"] == "portrait" and head["backend"] == "a1111"
    sent = json.loads(m.calls[-1][3])
    assert "85mm" in sent["prompt"] and "plastic skin" in sent["negative_prompt"] and sent["batch_size"] == 2
    assert isinstance(out[1], S.Image)
    with pytest.raises(S.ToolError):
        asyncio.run(G.create_artwork("x", approach="dream"))
