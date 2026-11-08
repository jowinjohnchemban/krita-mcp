"""Free services (Pollinations, AI Horde, OmniRoute), skills, and keeping the assistant's spending down."""
import base64
import json
import time

import pytest

from kritapilot import agent, skills
from kritapilot.providers import ProviderError, compat, free, images, keys
from test_providers import B64, Mock

PNG = base64.b64decode(B64)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRITAPILOT_TOKEN", "test-token")
    agent.SESSIONS.clear()
    return tmp_path


def _wait(sess, timeout=20):
    end = time.time() + timeout
    while sess.busy and time.time() < end:
        time.sleep(0.05)
    return [e for e in sess.events if e["type"] != "done"]


# ---------------------------------------------------------------- spending
def test_history_keeps_only_the_newest_pictures_and_short_old_results(monkeypatch):
    monkeypatch.setenv("KRITAPILOT_CHAT_KEEP_IMAGES", "1")
    m = agent.OpenAICompat.__new__(agent.OpenAICompat)
    pic = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + B64}}
    history = [{"role": "system", "content": "s"}, {"role": "user", "content": "first"},
               {"role": "tool", "tool_call_id": "a", "content": "x" * 5000},
               {"role": "user", "content": [{"type": "text", "text": "Images"}, pic]},
               {"role": "user", "content": "second"},
               {"role": "tool", "tool_call_id": "b", "content": "y" * 5000},
               {"role": "user", "content": [{"type": "text", "text": "Images"}, dict(pic)]}]
    m.compact(history, turn_start=4)
    assert history[3]["content"][1] == {"type": "text", "text": agent.DROPPED}  # older picture dropped
    assert history[6]["content"][1]["type"] == "image_url"  # the newest stays
    assert len(history[2]["content"]) < 700  # an earlier turn's long result is shortened ...
    assert history[5]["content"] == "y" * 5000  # ... the current turn's is not
    before = json.dumps(history)
    m.compact(history, turn_start=4)
    assert json.dumps(history) == before  # idempotent: resending stays cache-friendly


def test_gemini_and_claude_histories_are_compacted_too(monkeypatch):
    monkeypatch.setenv("KRITAPILOT_CHAT_KEEP_IMAGES", "0")
    g = agent.Gemini.__new__(agent.Gemini)
    gh = [{"role": "user", "parts": [{"functionResponse": {"name": "x", "response": {"result": "r" * 3000}}},
                                     {"inline_data": {"mime_type": "image/png", "data": B64}}]}]
    g.compact(gh, turn_start=1)
    assert gh[0]["parts"][1] == {"text": agent.DROPPED} and len(gh[0]["parts"][0]["functionResponse"]
                                                               ["response"]["result"]) < 700
    c = agent.Claude.__new__(agent.Claude)
    ch = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": [
        {"type": "text", "text": "z" * 3000},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": B64}}]}]}]
    c.compact(ch, turn_start=1)
    blocks = ch[0]["content"][0]["content"]
    assert blocks[1] == {"type": "text", "text": agent.DROPPED} and len(blocks[0]["text"]) < 700


def test_snapshots_for_the_chat_are_modest_unless_asked(bridge):
    import asyncio
    calls, _ = bridge
    asyncio.run(agent.run_tool("canvas_snapshot", {}))
    asyncio.run(agent.run_tool("canvas_snapshot", {"max_size": 1600}))
    sizes = [p["max_size"] for meth, p in calls if meth == "canvas.snapshot"]
    assert sizes == [768, 1600]


def test_the_same_error_three_times_stops_the_turn(home, bridge, monkeypatch):
    m = Mock()
    bad = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "no_such_tool", "arguments": "{}"}}]}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 800}}}
    try:
        m.json("POST", "/v1/chat/completions", lambda h, b: bad)
        agent.save_key("ollama", "", base_url=m.url + "/v1")
        events = _wait(agent.chat(None, "ollama", "llava", "do it"))
    finally:
        m.srv.shutdown()
    posts = [c for c in m.calls if c[0] == "POST"]
    assert len(posts) == 3  # not 40
    said = [e["text"] for e in events if e["type"] == "assistant"]
    assert said[-1].startswith("(stopped: the same error kept repeating")
    assert events[-1]["text"] == ("Used 3.0k tokens in, 60 out over 3 requests "
                                  "(2.4k of the input from cache, billed at a fraction)")


def test_openrouter_claude_gets_a_cached_system_prompt_and_reports_cost(home, bridge):
    m = Mock()
    try:
        m.json("POST", "/api/v1/chat/completions", {"choices": [{"message": {"content": "hi"}}],
                                                     "usage": {"prompt_tokens": 50, "completion_tokens": 5,
                                                               "cost": 0.0021}})
        keys.save("openrouter", key="sk-or-v1-x", base_url=m.url + "/api/v1")
        events = _wait(agent.chat(None, "openrouter", "anthropic/claude-sonnet-5.5", "hello"))
    finally:
        m.srv.shutdown()
    body = json.loads([c for c in m.calls if c[0] == "POST"][0][3])
    assert body["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["usage"] == {"include": True} and "cost 0.0021 credits" in events[-1]["text"]


# ---------------------------------------------------------------- free services
def _pollinations(monkeypatch, m):
    monkeypatch.setenv("KRITAPILOT_POLLINATIONS_TEXT_URL", m.url + "/text")
    monkeypatch.setenv("KRITAPILOT_POLLINATIONS_GEN_URL", m.url + "/gen")
    monkeypatch.setenv("KRITAPILOT_POLLINATIONS_IMAGE_URL", m.url + "/image")


def test_pollinations_chat_works_without_any_sign_in(home, bridge, monkeypatch):
    m = Mock()
    try:
        m.json("GET", "/text/models", [
            {"name": "paid-thing", "tools": True, "tier": "seed"},
            {"name": "openai-fast", "tools": True, "tier": "anonymous", "vision": False},
            {"name": "no-tools", "tools": False, "tier": "anonymous"},
            {"name": "sees", "tools": True, "tier": "anonymous", "vision": True}])
        m.json("POST", "/text/openai", {"choices": [{"message": {"content": "Hello from a free model."}}]})
        _pollinations(monkeypatch, m)
        assert agent.available_models("pollinations") == ["sees", "openai-fast"]
        events = _wait(agent.chat(None, "pollinations", None, "hi"))
        replies = iter([{"choices": [{"message": {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "canvas_snapshot", "arguments": "{}"}}]}}]},
                        {"choices": [{"message": {"content": "done"}}]}])
        m.json("POST", "/text/openai", lambda h, b: next(replies))
        _wait(agent.chat(None, "pollinations", "openai-fast", "look"))
    finally:
        m.srv.shutdown()
    blind = json.loads([c for c in m.calls if c[0] == "POST"][-1][3])["messages"]
    assert "image_url" not in json.dumps(blind) and "cannot see pictures" in blind[-1]["content"]
    post = [c for c in m.calls if c[0] == "POST"][0]
    assert json.loads(post[3])["model"] == "sees" and "authorization" not in post[2]
    assert [e["text"] for e in events if e["type"] == "assistant"] == ["Hello from a free model."]
    assert agent.status()["pollinations"]["chat"] is True


def test_pollinations_with_a_free_key_uses_the_newer_api(home, bridge, monkeypatch):
    m = Mock()
    try:
        m.json("GET", "/gen/v1/models", {"data": [
            {"id": "a/paid", "category": "text", "paid_only": True, "capabilities": ["tool_calling"]},
            {"id": "b/free-text", "category": "text", "capabilities": ["tool_calling"], "created": 5,
             "input_modalities": ["text"]},
            {"id": "c/free-vision", "category": "text", "capabilities": ["tool_calling"], "created": 1,
             "input_modalities": ["text", "image"]}]})
        m.json("POST", "/gen/v1/chat/completions", {"choices": [{"message": {"content": "ok"}}]})
        _pollinations(monkeypatch, m)
        keys.save("pollinations", key="sk_free")
        events = _wait(agent.chat(None, "pollinations", None, "hi"))
    finally:
        m.srv.shutdown()
    post = [c for c in m.calls if c[0] == "POST"][0]
    assert json.loads(post[3])["model"] == "c/free-vision" and post[2]["authorization"] == "Bearer sk_free"
    assert events[-1]["text"] == "ok"


def test_free_image_services_need_turning_on(home, monkeypatch):
    m = Mock()
    try:
        m.json("GET", "/image/models", ["flux", "turbo"])
        m.raw("GET", "/image/prompt/*", PNG, "image/jpeg")
        _pollinations(monkeypatch, m)
        monkeypatch.setenv("KRITAPILOT_COMFYUI_URL", "http://127.0.0.1:9")  # no local GPU server here
        monkeypatch.setenv("KRITAPILOT_A1111_URL", "http://127.0.0.1:9")
        with pytest.raises(ProviderError, match="No image-generation backend"):
            images.pick()  # nothing is sent anywhere until the user says so
        assert keys.status()["pollinations"]["signed_in"] is False
        keys.save("pollinations", key="", enable=True)
        assert keys.status()["pollinations"] == dict(keys.status()["pollinations"], signed_in=True,
                                                     needs_key=False, optional_key=True, free=True)
        b = images.pick()
        assert b.name == "pollinations"
        imgs, meta = b.generate(images.ImageJob(prompt="red apple", width=512, height=512, count=2, seed=7))
    finally:
        m.srv.shutdown()
    assert imgs == [PNG, PNG] and meta == {"model": "flux", "seeds": [7, 8], "cost": "free"}
    got = [c[1] for c in m.calls if c[1].startswith("/image/prompt/")]
    assert got[0].startswith("/image/prompt/red%20apple?") and "width=512" in got[0] and "seed=7" in got[0]
    keys.save("pollinations", remove=True)
    assert images.PollinationsImages().configured() is False
    monkeypatch.setenv("KRITAPILOT_FREE_SERVICES", "horde,pollinations")
    assert images.PollinationsImages().configured() and images.HordeImages().configured()


def test_ai_horde_queue_round_trip(home, monkeypatch):
    m = Mock()
    checks = iter([{"done": False, "queue_position": 3, "is_possible": True}, {"done": True}])
    try:
        m.json("GET", "/horde/status/models", [{"name": "few", "count": 1}, {"name": "popular", "count": 9},
                                               {"name": "Some-NSFW-XL", "count": 30}, {"name": "offline", "count": 0}])
        m.json("POST", "/horde/generate/async", {"id": "job1"})
        m.json("GET", "/horde/generate/check/job1", lambda h, b: next(checks))
        m.json("GET", "/horde/generate/status/job1", {"generations": [{"img": B64, "seed": "5"}]})
        monkeypatch.setenv("KRITAPILOT_HORDE_URL", m.url + "/horde")
        monkeypatch.setenv("KRITAPILOT_HORDE_POLL", "0")
        monkeypatch.setenv("KRITAPILOT_FREE_SERVICES", "horde")
        b = images.get("aihorde")
        assert b.default_side == 576  # anonymous requests stay small
        imgs, meta = b.generate(images.ImageJob(prompt="a castle", negative="blurry", width=576, height=576))
    finally:
        m.srv.shutdown()
    sent = json.loads([c for c in m.calls if c[0] == "POST"][0][3])
    headers = [c for c in m.calls if c[0] == "POST"][0][2]
    assert sent["models"] == ["popular"] and sent["prompt"] == "a castle ### blurry"
    assert headers["apikey"] == free.ANON_HORDE_KEY and headers["client-agent"].startswith("kritapilot:")
    assert imgs == [PNG] and meta["model"] == "popular"


def test_omniroute_needs_no_key_on_this_computer_and_prefers_auto(home, monkeypatch):
    m = Mock()
    try:
        m.json("GET", "/v1/models", {"data": [{"id": "glm/glm-5.2", "created": 9}, {"id": "auto", "created": 1},
                                              {"id": "auto/cheap", "created": 2}]})
        monkeypatch.setenv("KRITAPILOT_OMNIROUTE_BASE_URL", m.url + "/v1")
        assert compat.api_key("omniroute") == "local"
        assert compat.chat_models("omniroute")[:2] == ["auto", "auto/cheap"]
    finally:
        m.srv.shutdown()
    assert "authorization" not in m.calls[0][2]
    assert agent.PROVIDERS["omniroute"]["label"].startswith("OmniRoute")


# ---------------------------------------------------------------- skills
def test_built_in_skills_are_found_by_what_the_user_asks(monkeypatch):
    names = {s["name"] for s in skills.all_skills()}
    assert {"photoreal-image", "paint-by-hand", "retouch-photo", "still-to-video"} <= names
    assert skills.find("realistic photo portrait")[0]["name"] == "photoreal-image"
    assert skills.suggest("please animate this picture into a video")[0]["name"] == "still-to-video"
    assert skills.suggest("hi") == []  # nothing is offered for small talk
    hint, picked = agent.skill_hint("remove the person from this photo and fix the colours")
    assert picked[0] == "retouch-photo" and "use_skill" in hint


def test_user_skills_are_picked_up_live_and_read_safely(tmp_path, monkeypatch):
    mine = tmp_path / "mine"
    (mine / "ink-comics" / "references").mkdir(parents=True)
    (mine / "ink-comics" / "SKILL.md").write_text(
        "---\nname: ink-comics\ndescription: >\n  Ink comic pages with bold\n  lines and screentones\n---\n"
        "# Steps\nUse a G-pen preset.\n", encoding="utf-8")
    (mine / "ink-comics" / "references" / "panels.md").write_text("Six-panel grid.", encoding="utf-8")
    (mine / "ink-comics" / "run.py").write_text("print('never run')", encoding="utf-8")
    monkeypatch.setenv("KRITAPILOT_SKILLS_DIRS", str(mine) + __import__("os").pathsep + str(skills.BUNDLED))
    found = skills.find("comic lines")
    assert found[0]["name"] == "ink-comics" and found[0]["description"] == "Ink comic pages with bold lines and screentones"
    s = skills.load("ink-comics")
    assert s["instructions"].startswith("# Steps") and s["files"] == ["references/panels.md"]
    assert skills.load("ink-comics", "references/panels.md")["text"] == "Six-panel grid."
    for bad in ("../../etc/passwd", "run.py", "SKILL.md/../../x"):
        with pytest.raises(LookupError):
            skills.load("ink-comics", bad)
    with pytest.raises(LookupError, match="similar"):
        skills.load("comic")
    (mine / "watercolor").mkdir()
    (mine / "watercolor" / "SKILL.md").write_text("---\nname: watercolor\ndescription: soft washes\n---\nWet.",
                                                  encoding="utf-8")
    assert "watercolor" in {x["name"] for x in skills.all_skills()}  # no restart, no cache


def test_default_skill_folders(monkeypatch):
    monkeypatch.delenv("KRITAPILOT_SKILLS_DIRS")
    d = [str(x) for x in skills.dirs()]
    assert d[0].endswith("skills") and d[1] == str(skills.BUNDLED)
    assert len(d) == 2  # coding assistants' skills (~/.claude/skills: security-audit, ...) are not offered


def test_skill_tools_for_mcp_clients():
    import asyncio
    from kritapilot.server import TOOLS
    out = asyncio.run(TOOLS["find_skills"]("hand painted oil landscape"))
    assert out["skills"][0]["name"] == "paint-by-hand"
    got = asyncio.run(TOOLS["use_skill"]("paint-by-hand"))
    assert "batch" in got["instructions"] and "Scripts are not run" in got["note"]


# ---------------------------------------------------------------- variations cost money in the cloud
def test_create_artwork_makes_one_cloud_image_but_four_local(bridge, monkeypatch):
    import asyncio
    from kritapilot import gen_tools

    class Be:
        name, kind, native_control, mask_format, multiple, sizes, default_side = "x", "cloud", False, "gray", 8, None, 512

        def generate(self, job):
            seen.append(job.count)
            return [PNG] * job.count, {}
    seen = []
    monkeypatch.setattr(gen_tools.gen_images, "pick", lambda name=None: Be())
    asyncio.run(gen_tools.create_artwork("a photo of a lake", approach="generate"))
    Be.kind = "local"
    asyncio.run(gen_tools.create_artwork("a photo of a lake", approach="generate"))
    asyncio.run(gen_tools.create_artwork("a photo of a lake", approach="generate", count=2))
    assert seen == [1, 4, 2]


def _backends(monkeypatch, *names_ok):
    """Fake image services in order; (name, True) works, (name, False) rejects the saved key."""
    from kritapilot import gen_tools
    made, used = {}, []
    for n, ok in names_ok:
        class Be:
            name, kind, native_control, mask_format, multiple, sizes, default_side = n, "cloud", False, "gray", 8, None, 512
            works = ok

            def configured(self):
                return True

            def generate(self, job):
                used.append(self.name)
                if not self.works:
                    raise gen_tools.ProviderError("Google rejected the Gemini API key saved for Krita.")
                return [PNG], {}
        made[n] = Be
    monkeypatch.setattr(gen_tools.gen_images, "candidates", lambda name=None: [c() for c in made.values()])

    def pick(name=None):
        if name is not None and name not in made:
            raise gen_tools.ProviderError("unknown image backend %r" % name)
        return made[name or next(iter(made))]()
    monkeypatch.setattr(gen_tools.gen_images, "pick", pick)

    def get(name):
        if name in made:
            return made[name]()
        raise gen_tools.ProviderError("unknown image backend %r" % name)
    monkeypatch.setattr(gen_tools.gen_images, "get", get)
    return used


def test_a_rejected_key_falls_through_to_the_next_image_service(bridge, monkeypatch):
    import asyncio
    from kritapilot import gen_tools
    used = _backends(monkeypatch, ("gemini", False), ("pollinations", True))
    out = asyncio.run(gen_tools.create_artwork("sunrise over green mountains", approach="generate"))
    assert used == ["gemini", "pollinations"]
    assert out[0]["backend"] == "pollinations" and "gemini" in out[0]["note"] and out[0]["skipped"]


def test_a_named_image_service_is_never_swapped(bridge, monkeypatch):
    import asyncio
    from kritapilot import gen_tools
    used = _backends(monkeypatch, ("gemini", False), ("pollinations", True))
    with pytest.raises(gen_tools.ToolError, match="rejected"):
        asyncio.run(gen_tools.generate_image("a lake", backend="gemini"))
    assert used == ["gemini"]


def test_auto_artwork_paints_by_hand_when_no_service_accepts_the_account(bridge, monkeypatch):
    import asyncio
    from kritapilot import gen_tools
    _backends(monkeypatch, ("gemini", False))
    out = asyncio.run(gen_tools.create_artwork("a photo of sunrise over mountains"))
    assert out[0]["approach"] == "paint" and "Gemini" in out[0]["why"] and out[0]["layers"]
    with pytest.raises(gen_tools.ToolError, match="No connected image service"):  # asked to generate: say so
        asyncio.run(gen_tools.create_artwork("a photo of sunrise", approach="generate"))


def test_account_problems_are_told_apart_from_bad_requests():
    from kritapilot.providers import images
    for e in ("HTTP 401 unauthorized", "Google rejected the Gemini API key", "HTTP 402: insufficient credits",
              "HTTP 429 rate limit"):
        assert images.unusable(e), e
    assert not images.unusable("HTTP 400: prompt too long") and not images.unusable("inpaint needs a selection")


def test_every_built_in_skill_is_valid_and_names_only_real_tools():
    import re
    from kritapilot.server import TOOLS
    found = skills.all_skills()
    assert len(found) >= 30 and len({s["name"] for s in found}) == len(found)
    for s in found:
        assert 40 <= len(s["description"]) <= 400, s["name"]
        text = skills.load(s["name"])["instructions"]
        used = set(re.findall(r"`([a-z_]+)\(", text)) | set(re.findall(r"`([a-z_]+)`", text))
        unknown = {u for u in used if "_" in u and u not in TOOLS} - {"non_destructive", "blend_mode",
                                                                     "full_canvas", "color_balance",
                                                                     "hue_saturation", "max_size", "tile_size",
                                                                     "avatar_id", "voice_id", "lineart_anime",
                                                                     "negative_prompt"}
        assert not unknown, (s["name"], unknown)


def test_skills_are_suggested_for_typical_requests():
    cases = {"make a pixel art sprite of a knight": "pixel-art",
             "design a logo for my bakery": "vector-logo",
             "draw a 4 panel comic about a cat": "comic-storyboard",
             "restore this old damaged photo": "photo-restoration",
             "make an instagram story graphic": "social-media-graphics",
             "is there a skill for tattoo design": "find-skills",
             "keep it cheap, I have little credit left": "save-credits",
             "fix the hands in this AI image": "fix-ai-artifacts",
             "give it a cinematic teal and orange grade": "color-grade",
             "turn my sketch into a realistic render": "sketch-to-render"}
    for text, want in cases.items():
        got = [s["name"] for s in skills.suggest(text)]
        assert got and got[0] == want, (text, got)


def test_keyless_gateways_chat_without_any_key(home, bridge, monkeypatch):
    m = Mock()
    try:
        m.json("GET", "/kilo/models", {"data": [{"id": "anthropic/claude-x", "created": 9},
                                                {"id": "kilo-auto/balanced", "created": 8},
                                                {"id": "nvidia/nemotron:free", "created": 7},
                                                {"id": "kilo-auto/free", "created": 1}]})
        m.json("POST", "/kilo/chat/completions", {"choices": [{"message": {"content": "free answer"}}]})
        monkeypatch.setenv("KRITAPILOT_KILO_BASE_URL", m.url + "/kilo")
        assert agent.available_models("kilo") == ["kilo-auto/free", "nvidia/nemotron:free"]  # only free ones
        events = _wait(agent.chat(None, "kilo", None, "hi"))
        keys.save("kilo", key="kilo-key")
        assert agent.available_models("kilo")[:2] == ["kilo-auto/balanced", "kilo-auto/free"]
    finally:
        m.srv.shutdown()
    post = [c for c in m.calls if c[0] == "POST"][0]
    assert json.loads(post[3])["model"] == "kilo-auto/free" and "authorization" not in post[2]
    assert events[-1]["text"] == "free answer"
    st = keys.status()
    assert st["kilo"]["optional_key"] and not st["kilo"]["toggle"] and st["pollinations"]["toggle"]
    assert {"kilo", "ovhcloud", "llm7", "sambanova", "zai", "deepinfra", "vercel"} <= set(agent.PROVIDERS)


def test_open_instruct_models_count_as_chat_models():
    from kritapilot.providers import catalog
    ids = ["Meta-Llama-3_3-70B-Instruct", "gpt-3.5-turbo-instruct", "Qwen3Guard-Gen-8B", "bge-m3",
           "stable-diffusion-xl-base-v10", "whisper-large-v3", "gpt-oss-120b"]
    kept = [i for i in ids if not catalog.NOT_CHAT.search(i)]
    assert kept == ["Meta-Llama-3_3-70B-Instruct", "gpt-oss-120b"]


def test_no_working_image_service_points_to_the_free_ones(bridge, monkeypatch):
    import asyncio
    from kritapilot import gen_tools
    _backends(monkeypatch, ("gemini", False))
    with pytest.raises(gen_tools.ToolError, match="turn on Pollinations or AI Horde in AI Chat > Services"):
        asyncio.run(gen_tools.generate_image("a lake"))



def test_a_made_up_image_service_falls_back_to_the_automatic_choice(bridge, monkeypatch):
    """Models invent services ("stability" with no key): use what is set up instead of failing."""
    import asyncio
    from kritapilot import gen_tools
    used = _backends(monkeypatch, ("pollinations", True))
    out = asyncio.run(gen_tools.generate_image("a lake", backend="stability"))
    assert used == ["pollinations"] and "stability is not set up" in out[0]["note_backend"]


def test_model_calls_give_up_after_the_chat_timeout(monkeypatch):
    from kritapilot import agent
    assert agent.chat_timeout() == 180
    monkeypatch.setenv("KRITAPILOT_CHAT_TIMEOUT", "60")
    assert agent.chat_timeout() == 60
    assert agent.transient("cannot reach https://text.pollinations.ai/openai (The read operation timed out)")


def test_ai_horde_uses_a_served_model_when_asked_for_one_it_does_not_have(home, monkeypatch):
    m = Mock()
    try:
        m.json("GET", "/horde/status/models", [{"name": "popular", "count": 9}, {"name": "Deliberate", "count": 2}])
        m.json("POST", "/horde/generate/async", {"id": "job2"})
        m.json("GET", "/horde/generate/check/job2", {"done": True})
        m.json("GET", "/horde/generate/status/job2", {"generations": [{"img": B64, "seed": "1"}]})
        monkeypatch.setenv("KRITAPILOT_HORDE_URL", m.url + "/horde")
        monkeypatch.setenv("KRITAPILOT_HORDE_POLL", "0")
        monkeypatch.setenv("KRITAPILOT_FREE_SERVICES", "horde")
        b = images.get("horde")
        b.generate(images.ImageJob(prompt="hills", width=576, height=256, model="stable-diffusion-3.5"))
        b.generate(images.ImageJob(prompt="hills", width=576, height=256, model="Deliberate"))
    finally:
        m.srv.shutdown()
    posts = [json.loads(c[3]) for c in m.calls if c[0] == "POST"]
    assert [x["models"] for x in posts] == [["popular"], ["Deliberate"]]  # unknown name ignored; served one used
    assert "seed_variation" not in posts[0]["params"]  # one picture: one seed


def test_ai_horde_variations_get_different_seeds(home, monkeypatch):
    """Two variations with one seed came out identical in a live run: each must get the next seed."""
    m = Mock()
    try:
        m.json("GET", "/horde/status/models", [{"name": "popular", "count": 9}])
        m.json("POST", "/horde/generate/async", {"id": "j3"})
        m.json("GET", "/horde/generate/check/j3", {"done": True})
        m.json("GET", "/horde/generate/status/j3", {"generations": [{"img": B64, "seed": "42"},
                                                                    {"img": B64, "seed": "43"}]})
        monkeypatch.setenv("KRITAPILOT_HORDE_URL", m.url + "/horde")
        monkeypatch.setenv("KRITAPILOT_HORDE_POLL", "0")
        monkeypatch.setenv("KRITAPILOT_FREE_SERVICES", "horde")
        images.get("horde").generate(images.ImageJob(prompt="hills", width=576, height=256, count=2, seed=42))
    finally:
        m.srv.shutdown()
    params = [json.loads(c[3]) for c in m.calls if c[0] == "POST"][0]["params"]
    assert params["seed"] == "42" and params["n"] == 2 and params["seed_variation"] == 1


def test_ai_horde_makes_at_most_two_variations(bridge, monkeypatch):
    import asyncio
    from kritapilot import gen_tools
    seen = []

    class Horde:
        name, kind, native_control, mask_format, multiple, sizes, default_side = "horde", "cloud", False, "gray", 64, None, 576

        def configured(self):
            return True

        def generate(self, job):
            seen.append(job.count)
            return [PNG] * job.count, {}
    monkeypatch.setattr(gen_tools.gen_images, "pick", lambda name=None: Horde())
    out = asyncio.run(gen_tools.generate_image("hills", count=4, backend="horde"))
    assert seen == [2] and "volunteers" in out[0]["note"]
