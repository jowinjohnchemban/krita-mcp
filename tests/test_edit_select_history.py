"""Edit by instruction, select by description, and the history of generated pictures."""
import asyncio
import base64
import json

import pytest

from kritapilot import gen_history, gen_tools
from kritapilot.providers import ProviderError
from kritapilot.providers import segment
from test_providers import B64, Mock

PNG = base64.b64decode(B64)


# ---------------------------------------------------------------- finding something in a picture
def test_model_answers_are_read_even_with_words_and_fences_around_the_json():
    assert segment._json('Here you go:\n```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert segment._json('Sure! {"polygons": [[[0, 0], [1, 0], [1, 1]]]} Hope it helps.')["polygons"]
    with pytest.raises(ProviderError):
        segment._json("I can't see that")


def test_gemini_masks_are_placed_in_their_boxes(monkeypatch):
    monkeypatch.setenv("KRITAPILOT_GEMINI_API_KEY", "g")
    m = Mock()
    answer = json.dumps([{"box_2d": [100, 200, 500, 800], "mask": "data:image/png;base64," + B64, "label": "sky"},
                         {"box_2d": [0, 0, 10, 10], "label": "no mask"}])
    try:
        m.json("POST", "/models/gemini-x:generateContent",
               {"candidates": [{"content": {"parts": [{"text": "```json\n" + answer + "\n```"}]}}]})
        monkeypatch.setenv("KRITAPILOT_GEMINI_BASE_URL", m.url)
        res = segment.with_gemini(PNG, "the sky", model="gemini-x")
    finally:
        m.srv.shutdown()
    assert res["method"] == "gemini" and len(res["masks"]) == 2
    first = res["masks"][0]
    assert first["box"] == [0.2, 0.1, 0.8, 0.5] and first["png"] == PNG  # Gemini's [y0, x0, y1, x1] of 1000
    assert res["masks"][1]["png"] is None  # a box without a mask is still a box


def test_vision_outlines_in_pixels_are_turned_into_fractions(monkeypatch):
    monkeypatch.setattr(segment.vision, "ask", lambda png, q, provider=None, model=None: (
        '{"polygons": [[[100, 50], [200, 50], [200, 100]]]}', {"provider": "pollinations", "model": "free"}))
    res = segment.with_vision(PNG, "the house", 400, 200)
    assert res["polygons"] == [[[0.25, 0.25], [0.5, 0.25], [0.5, 0.5]]] and res["method"] == "vision:pollinations"


def test_auto_finds_with_what_is_set_up_and_falls_back_to_vision(monkeypatch):
    calls = []
    monkeypatch.setattr(segment, "with_gemini", lambda *a: calls.append("gemini") or {"masks": [], "polygons": [],
                                                                                      "method": "gemini"})
    monkeypatch.setattr(segment, "with_vision", lambda *a: calls.append("vision") or {
        "masks": [], "polygons": [[[0, 0], [1, 0], [1, 1]]], "method": "vision:x"})
    res = segment.segment(PNG, "a cat", 10, 10)
    assert calls == ["vision"] and res["method"] == "vision:x"  # no Gemini or fal key: straight to vision
    monkeypatch.setenv("KRITAPILOT_GEMINI_API_KEY", "g")
    calls.clear()
    res = segment.segment(PNG, "a cat", 10, 10)
    assert calls == ["gemini", "vision"] and "gemini: found nothing" in res["tried"]


# ---------------------------------------------------------------- the tools
def test_select_by_description_makes_a_selection(bridge, monkeypatch):
    calls, _ = bridge
    monkeypatch.setattr(gen_tools.gen_segment, "segment", lambda *a: {
        "masks": [{"box": [0, 0, 0.5, 0.5], "png": PNG, "use_brightness": True},
                  {"box": [0.6, 0.6, 0.9, 0.9], "png": None}],
        "polygons": [[[0, 0], [1, 0], [1, 1]]], "method": "vision:pollinations", "model": "m", "tried": []})
    out = asyncio.run(gen_tools.select_by_description("the sky", mode="add", feather=3))
    sent = [p for m, p in calls if m == "selection.from_masks"][0]
    assert sent["mode"] == "add" and sent["feather"] == 3 and sent["region"] == [0, 0, 1, 1]
    assert len(sent["masks"]) == 1 and sent["masks"][0]["data_base64"] == B64
    assert len(sent["polygons"]) == 2  # the outline, and the box that had no mask
    assert out["found"] == 3 and "approximate" in out["note"]


def _fake_backends(monkeypatch, *spec):
    """(name, works) fake image services, in the order given."""
    made, used = {}, []
    for name, works in spec:
        class Be:
            kind, native_control, mask_format, multiple, sizes, default_side = "cloud", False, "gray", 8, None, 512
            ok = works

            def configured(self):
                return True

            def generate(self, job):
                used.append((self.name, job.mode, job.prompt, job.model))
                if not self.ok:
                    raise ProviderError("HTTP 402: insufficient credits")
                return [PNG], {}
        Be.name = name
        made[name] = Be
    monkeypatch.setattr(gen_tools.gen_images, "candidates", lambda name=None: [c() for c in made.values()])

    def pick(name=None):
        if name is not None and name not in made:
            raise ProviderError("unknown image backend %r" % name)
        return made[name or next(iter(made))]()
    monkeypatch.setattr(gen_tools.gen_images, "pick", pick)
    return used


def test_edit_image_prefers_models_that_edit_by_instruction(bridge, monkeypatch):
    used = _fake_backends(monkeypatch, ("pollinations", True), ("fal", False), ("gemini", True))
    out = asyncio.run(gen_tools.edit_image("make it night"))
    names = [u[0] for u in used]
    assert names == ["gemini"]  # an edit model first, even though it is listed last
    assert used[0][1] == "img2img" and "make it night" in used[0][2] and "Keep everything else" in used[0][2]
    assert out[0]["instruction"] == "make it night" and out[0]["backend"] == "gemini"


def test_edit_image_moves_on_when_a_service_has_no_credit(bridge, monkeypatch):
    used = _fake_backends(monkeypatch, ("fal", False), ("pollinations", True))
    out = asyncio.run(gen_tools.edit_image("add snow"))
    assert [u[0] for u in used] == ["fal", "pollinations"] and out[0]["backend"] == "pollinations"
    assert used[0][3] == "fal-ai/flux-pro/kontext"  # fal edits with Flux Kontext unless told otherwise
    assert "fal" in out[0]["skipped"][0]


# ---------------------------------------------------------------- history
def test_every_generation_is_kept_and_can_be_placed_again(bridge, monkeypatch):
    calls, _ = bridge
    _fake_backends(monkeypatch, ("pollinations", True))
    out = asyncio.run(gen_tools.generate_image("a lighthouse at dusk", region="canvas"))
    gid = out[0]["history_id"]
    listing = asyncio.run(gen_tools.generation_history())
    assert listing["generations"][0]["id"] == gid and listing["generations"][0]["prompt"] == "a lighthouse at dusk"
    assert listing["generations"][0]["pictures"] == 1
    calls.clear()
    asyncio.run(gen_tools.place_generation(gid))
    placed = [p for m, p in calls if m == "gen.place"][0]
    assert placed["images"] == [B64] and "lighthouse" in placed["name"]
    with pytest.raises(gen_tools.ToolError, match="no generation"):
        asyncio.run(gen_tools.place_generation("nope"))


def test_history_keeps_only_the_newest(monkeypatch):
    monkeypatch.setattr(gen_history, "MAX_ENTRIES", 3)
    ids = [gen_history.record("p%d" % i, [PNG, PNG], "x", "txt2img") for i in range(5)]
    left = gen_history.entries(10)
    assert [e["id"] for e in left] == ids[:1:-1]  # the 3 newest, newest first
    assert sorted(p.name for p in gen_history.folder().glob("*.png")) == sorted(
        f for e in left for f in e["files"])  # pictures of dropped generations are deleted too
