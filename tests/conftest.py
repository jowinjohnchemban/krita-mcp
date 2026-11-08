"""Shared fixtures."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Before anything is imported or collected: modules that write logs at import time (the plugin's) must land in
# a throw-away folder, never in the user's real ~/.kritapilot. Each test then gets its own (isolated_keys).
os.environ["KRITAPILOT_HOME"] = tempfile.mkdtemp(prefix="kritapilot-tests-")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_providers import B64  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_keys(monkeypatch, tmp_path_factory):
    """Never read the user's real ~/.kritapilot (saved API keys!) from tests - and never call real services."""
    monkeypatch.setenv("KRITAPILOT_HOME", str(tmp_path_factory.mktemp("kritapilot-home")))
    for name in ("KRITAPILOT_USE_ENV_KEYS", "KRITAPILOT_GEMINI_API_KEY", "KRITAPILOT_OPENAI_API_KEY",
                 "KRITAPILOT_ANTHROPIC_API_KEY", "KRITAPILOT_OPENROUTER_API_KEY", "KRITAPILOT_STABILITY_API_KEY",
                 "KRITAPILOT_REPLICATE_API_TOKEN", "KRITAPILOT_FAL_KEY", "KRITAPILOT_HEYGEN_API_KEY",
                 "KRITAPILOT_IMAGE_BACKEND", "KRITAPILOT_VIDEO_BACKEND", "KRITAPILOT_VISION_PROVIDER",
                 "KRITAPILOT_POLLINATIONS_API_KEY", "KRITAPILOT_HORDE_API_KEY", "KRITAPILOT_OMNIROUTE_API_KEY",
                 "KRITAPILOT_FREE_SERVICES", "KRITAPILOT_CHAT_KEEP_IMAGES", "KRITAPILOT_CHAT_IMAGE_SIZE",
                 "KRITAPILOT_CHAT_MAX_STEPS"):
        monkeypatch.delenv(name, raising=False)
    # skills: only the built-in ones (never whatever is installed in the user's home)
    from kritapilot import skills
    monkeypatch.setenv("KRITAPILOT_SKILLS_DIRS", str(skills.BUNDLED))


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

    import kritapilot.server as S

    monkeypatch.setattr(S._client, "call", fake)
    return calls, state

