"""Server-side glue of the generative tools, with a fake Krita bridge and a mock image backend."""
import asyncio
import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_providers import B64, Mock  # noqa: E402

import krita_mcp.server as S  # noqa: E402
from krita_mcp import gen_tools as G  # noqa: E402


@pytest.fixture()
def bridge(monkeypatch, tmp_path):
    calls = []
    state = {"has_selection": True}

    def fake(method, params):
        calls.append((method, params))
        if method == "app.info":
            return {"workspace": str(tmp_path), "documents": []}
        if method == "gen.context":
            out = {"region": [10, 20, 100, 50], "size": [512, 256], "has_selection": state["has_selection"],
                   "canvas": [400, 300]}
            if params.get("need_image", True):
                out["image_png"] = B64
            if params.get("need_mask"):
                out["mask_png"] = B64
            return out
        if method == "gen.place":
            n = len(params["images"])
            out = {"layers": [{"id": "L%d" % i, "name": "v%d" % i} for i in range(n)], "region": params["region"],
                   "sheet_png": B64}
            if n > 1:
                out["group"] = {"id": "G", "name": params["name"]}
            return out
        return {"ok": method}

    monkeypatch.setattr(S._client, "call", fake)
    return calls, state


def test_tool_parameter_named_method_reaches_the_bridge(bridge):
    calls, _ = bridge
    asyncio.run(S.scale_image(width=10, height=20, method="NearestNeighbor"))
    assert calls[-1] == ("document.scale", {"width": 10, "height": 20, "method": "NearestNeighbor"})


def test_generate_inpaints_selection_with_variations(bridge, monkeypatch):
    calls, state = bridge
    m = Mock()
    try:
        m.json("POST", "/sdapi/v1/img2img", lambda h, b: {"images": [B64] * json.loads(b)["batch_size"],
                                                           "info": json.dumps({"all_seeds": [4, 5]})})
        monkeypatch.setenv("KRITA_MCP_A1111_URL", m.url)
        out = asyncio.run(G.generate_image("a red door", count=2, backend="a1111", size=512, seed=4))
    finally:
        m.srv.shutdown()
    summary, sheet = out
    assert summary["mode"] == "inpaint" and summary["placed"]["group"]["id"] == "G"
    assert isinstance(sheet, S.Image)
    ctx = dict(calls)["gen.context"]
    assert ctx["target"] == 512 and ctx["multiple"] == 8 and ctx["need_mask"] is True
    place = dict(calls)["gen.place"]
    assert place["region"] == [10, 20, 100, 50] and place["mask"] is True and place["labels"] == ["seed 4", "seed 5"]
    sent = json.loads(m.calls[-1][3])
    assert (sent["width"], sent["height"], sent["batch_size"]) == (512, 256, 2) and sent["mask"] == B64


def test_inpaint_without_selection_is_refused(bridge, monkeypatch):
    _, state = bridge
    state["has_selection"] = False
    monkeypatch.setenv("KRITA_MCP_A1111_URL", "http://127.0.0.1:9")
    with pytest.raises(S.ToolError, match="selection"):
        asyncio.run(G.generate_image("x", mode="inpaint", backend="a1111"))
    with pytest.raises(S.ToolError, match="mode must be"):
        asyncio.run(G.generate_image("x", mode="paint"))


def test_workflow_files_stay_inside_the_workspace(bridge, tmp_path):
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "flux.json").write_text(json.dumps({"1": {"class_type": "X", "inputs": {}}}))
    assert asyncio.run(G._load_workflow("flux"))["1"]["class_type"] == "X"
    with pytest.raises(S.ToolError):
        asyncio.run(G._load_workflow("../../secrets.json"))
    with pytest.raises(S.ToolError):
        asyncio.run(G._workspace_file("generated/x.exe", (".mp4",)))


def test_remove_background_auto_falls_back_to_color(bridge, monkeypatch):
    calls, _ = bridge
    monkeypatch.setenv("KRITA_MCP_IMAGE_BACKEND", "comfyui")  # ComfyUI has no background removal
    out = asyncio.run(G.remove_background(layer="Photo"))
    assert out["method"] == "color" and calls[-1][0] == "image.remove_background"
    with pytest.raises(S.ToolError):
        asyncio.run(G.remove_background(layer="Photo", method="ai"))


def test_prompts_are_registered():
    names = [p.name for p in asyncio.run(S.mcp.list_prompts())]
    assert {"generative_inpaint", "concept_variations", "retouch_photo", "product_shot"} <= set(names)
    assert base64.b64decode(B64)
