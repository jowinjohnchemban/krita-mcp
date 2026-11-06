"""Shared fixtures."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_providers import B64  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_keys(monkeypatch, tmp_path_factory):
    """Never read the user's real ~/.krita-mcp (saved API keys!) from tests - and never call real services."""
    monkeypatch.setenv("KRITA_MCP_HOME", str(tmp_path_factory.mktemp("krita-mcp-home")))
    for name in ("KRITA_MCP_USE_ENV_KEYS", "KRITA_MCP_GEMINI_API_KEY", "KRITA_MCP_OPENAI_API_KEY",
                 "KRITA_MCP_ANTHROPIC_API_KEY", "KRITA_MCP_OPENROUTER_API_KEY", "KRITA_MCP_STABILITY_API_KEY",
                 "KRITA_MCP_REPLICATE_API_TOKEN", "KRITA_MCP_FAL_KEY", "KRITA_MCP_HEYGEN_API_KEY",
                 "KRITA_MCP_IMAGE_BACKEND", "KRITA_MCP_VIDEO_BACKEND", "KRITA_MCP_VISION_PROVIDER"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def bridge(monkeypatch, tmp_path):
    calls = []
    state = {"has_selection": True}

    def fake(method, params):
        calls.append((method, params))
        if method == "canvas.snapshot":
            return {"png_base64": B64, "width": 1, "height": 1, "region": [0, 0, 1, 1]}
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

    import krita_mcp.server as S

    monkeypatch.setattr(S._client, "call", fake)
    return calls, state

