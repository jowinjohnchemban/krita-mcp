"""The in-Krita assistant: schemas, the agent loop for each provider, sign-in and the loopback service."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from krita_mcp import agent
from test_providers import B64, Mock


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRITA_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("KRITA_MCP_TOKEN", "test-token")
    for k in ("KRITA_MCP_GEMINI_API_KEY", "KRITA_MCP_OPENAI_API_KEY", "KRITA_MCP_ANTHROPIC_API_KEY",
              "KRITA_MCP_USE_ENV_KEYS", "KRITA_MCP_OLLAMA_BASE_URL", "KRITA_MCP_GEMINI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    agent.SESSIONS.clear()
    return tmp_path


def test_schema_cleanup():
    s = {"title": "x", "type": "object", "properties": {
        "layer": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Layer"},
        "settings": {"anyOf": [{"additionalProperties": True, "type": "object"}, {"type": "null"}]},
        "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}}}, "required": []}
    c = agent.clean_schema(s)
    assert c["properties"]["layer"] == {"type": "string"} and "title" not in c
    g = agent.gemini_schema(s)
    assert g["properties"]["settings"]["type"] == "string"  # free-form objects become JSON strings for Gemini
    assert g["properties"]["points"]["items"]["items"] == {"type": "number"}


def test_sign_in_is_stored_privately(home):
    assert agent.status()["gemini"]["signed_in"] is False
    agent.save_key("gemini", "g-123")
    assert agent.key_for("gemini") == "g-123" and agent.status()["gemini"]["signed_in"] is True
    assert "g-123" not in json.dumps(agent.status())  # status never reveals keys
    agent.save_key("gemini", None)
    assert agent.key_for("gemini") is None
    with pytest.raises(ValueError):
        agent.save_key("nope", "x")


def _wait(sess, timeout=20):
    end = time.time() + timeout
    while sess.busy and time.time() < end:
        time.sleep(0.05)
    return [e for e in sess.events if e["type"] != "done"]


def test_gemini_agent_loop_calls_tools_and_answers(home, bridge, monkeypatch):
    calls, _ = bridge
    m = Mock()
    replies = iter([
        {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "document_new", "args": {"width": 640, "height": 480, "name": "fox"}},
             "thoughtSignature": "sig-1"}]}}]},
        {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "canvas_snapshot", "args": {"max_size": 256}}}]}}]},
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Made a 640x480 canvas."}]},
                         "finishReason": "STOP"}]},
    ])
    try:
        m.json("POST", "/models/gemini-3.8-flash:generateContent", lambda h, b: next(replies))
        monkeypatch.setenv("KRITA_MCP_GEMINI_BASE_URL", m.url)
        agent.save_key("gemini", "g-key")
        sess = agent.chat(None, "gemini", None, "make a canvas for a fox")
        events = _wait(sess)
    finally:
        m.srv.shutdown()
    kinds = [(e["type"], e.get("name")) for e in events]
    assert kinds == [("user", None), ("note", None), ("tool", "document_new"), ("tool_result", "document_new"),
                     ("tool", "canvas_snapshot"), ("tool_result", "canvas_snapshot"), ("assistant", None)]
    assert events[1]["text"] == "Model: gemini-3.8-flash"  # newest Flash listed now; 2.5 is skipped
    assert events[5]["images"] == 1 and events[-1]["text"] == "Made a 640x480 canvas."
    assert ("document.new", {"width": 640, "height": 480, "name": "fox", "background": "white",
                             "resolution_ppi": 72}) in calls
    posts = [c for c in m.calls if c[0] == "POST"]
    first, third = (json.loads(c[3]) for c in (posts[0], posts[2]))
    assert posts[0][2]["x-goog-api-key"] == "g-key" and m.calls[0][1].startswith("/models?")
    decl = first["tools"][0]["functionDeclarations"]
    assert len(decl) > 80 and "parametersJsonSchema" in decl[0]
    turns = third["contents"]
    assert turns[1]["parts"][0]["thoughtSignature"] == "sig-1"  # model turns are sent back verbatim
    assert turns[2]["parts"][0]["functionResponse"]["name"] == "document_new"
    assert any("inline_data" in p for p in turns[4]["parts"])  # the snapshot is shown to the model


def test_gemini_falls_back_to_openapi_schemas(home, bridge, monkeypatch):
    m = Mock()
    state = {"n": 0}

    def reply(h, b):
        state["n"] += 1
        body = json.loads(b)
        if "parametersJsonSchema" in body["tools"][0]["functionDeclarations"][0]:
            return {"error": {"message": "Unknown name parametersJsonSchema"}}
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]}
    try:
        m.routes[("POST", "/models/gemini-3.8-flash:generateContent")] = lambda h, b: (
            400 if "parametersJsonSchema" in b.decode() else 200, "application/json",
            json.dumps(reply(h, b)).encode())
        monkeypatch.setenv("KRITA_MCP_GEMINI_BASE_URL", m.url)
        agent.save_key("gemini", "k")
        events = _wait(agent.chat(None, "gemini", None, "hi"))
    finally:
        m.srv.shutdown()
    assert events[-1] == dict(events[-1], type="assistant", text="ok")
    assert "parameters" in json.loads(m.calls[-1][3])["tools"][0]["functionDeclarations"][0]


def test_openai_compatible_local_model(home, bridge, monkeypatch):
    m = Mock()
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "canvas_snapshot", "arguments": "{}"}}]}}]},
        {"choices": [{"message": {"content": "Looks good."}}]},
    ])
    try:
        m.json("POST", "/v1/chat/completions", lambda h, b: next(replies))
        agent.save_key("ollama", "", base_url=m.url + "/v1")
        events = _wait(agent.chat(None, "ollama", "llava", "how does it look?"))
    finally:
        m.srv.shutdown()
    assert events[-1]["text"] == "Looks good."
    second = json.loads([c for c in m.calls if c[0] == "POST"][1][3])
    roles = [msg["role"] for msg in second["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"]  # image follows as a user turn
    assert second["model"] == "llava" and second["messages"][4]["content"][1]["type"] == "image_url"


def test_claude_agent_loop(home, bridge, monkeypatch):
    pytest.importorskip("anthropic")
    m = Mock()
    replies = iter([
        {"id": "m1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "stop_reason": "tool_use",
         "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
         "content": [{"type": "tool_use", "id": "tu1", "name": "list_layers", "input": {}}]},
        {"id": "m2", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "stop_reason": "end_turn",
         "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
         "content": [{"type": "text", "text": "There is one layer."}]},
    ])
    try:
        m.json("POST", "/v1/messages", lambda h, b: next(replies))
        monkeypatch.setenv("ANTHROPIC_BASE_URL", m.url)
        agent.save_key("anthropic", "sk-ant-x")
        events = _wait(agent.chat(None, "anthropic", None, "what layers are there?"))
    finally:
        m.srv.shutdown()
    said = [e["text"] for e in events if e["type"] == "assistant"]
    assert said[-1] == "There is one layer."
    assert events[-1]["type"] == "note" and events[-1]["text"].startswith("Used 2 tokens in, 2 out")
    second = json.loads([c for c in m.calls if c[0] == "POST"][1][3])
    result = second["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu1"
    # prompt caching: tools + system cached once, the conversation so far at its newest block
    assert second["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert result["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in json.dumps(second["messages"][:2])
    assert second["model"] == "claude-opus-6" and len(second["tools"]) > 80  # newest Opus listed


def test_service_requires_the_token_and_runs_a_chat(home, bridge, monkeypatch):
    srv_box = {}
    t = threading.Thread(target=agent.serve, kwargs={"port": 0, "ready": lambda s: srv_box.setdefault("s", s)},
                         daemon=True)
    t.start()
    for _ in range(100):
        if "s" in srv_box:
            break
        time.sleep(0.02)
    port = srv_box["s"].server_address[1]
    m = Mock()
    m.json("POST", "/v1/chat/completions", {"choices": [{"message": {"content": "Hello from a local model."}}]})

    def req(method, path, body=None, token="test-token"):
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method=method,
                                   data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        assert req("GET", "/health", token="wrong")[0] == 401
        code, h = req("GET", "/health")
        assert code == 200 and {"gemini", "anthropic", "openai", "ollama", "comfyui", "fal", "heygen"} <= set(
            h["providers"]) and h["providers"]["gemini"]["chat"] and "chat" not in h["providers"]["fal"]
        code, r = req("POST", "/signin", {"provider": "ollama", "key": "", "base_url": m.url + "/v1"})
        assert code == 200 and r["providers"]["ollama"]["signed_in"]
        assert req("POST", "/chat", {"provider": "ollama", "text": ""})[0] == 400
        code, r = req("POST", "/chat", {"provider": "ollama", "text": "hello"})
        sid = r["session"]
        for _ in range(100):
            code, ev = req("GET", "/events?session=%s&since=0" % sid)
            if not ev["busy"]:
                break
            time.sleep(0.05)
        texts = [e.get("text") for e in ev["events"]]
        assert "Hello from a local model." in texts and ev["events"][-1]["type"] == "done"
        assert req("POST", "/signout", {"provider": "gemini"})[0] == 200
    finally:
        m.srv.shutdown()
        srv_box["s"].shutdown()
    assert B64



def test_keys_signed_in_from_krita_serve_the_generative_tools(home, monkeypatch):
    from krita_mcp.providers import http, images, keys
    monkeypatch.delenv("KRITA_MCP_FAL_KEY", raising=False)
    monkeypatch.delenv("KRITA_MCP_COMFYUI_URL", raising=False)
    assert images.get("fal").configured() is False
    keys.save("fal", key="fal-from-krita")
    keys.save("comfyui", base_url="http://127.0.0.1:9999/")
    assert images.get("fal").key == "fal-from-krita" and images.get("comfyui").url == "http://127.0.0.1:9999"
    monkeypatch.setenv("KRITA_MCP_FAL_KEY", "from-env")  # an explicit environment variable wins
    assert http.secret("KRITA_MCP_FAL_KEY") == "from-env"
    st = keys.status()
    assert st["fal"]["signed_in"] and "fal-from-krita" not in json.dumps(st)


# ---------------------------------------------------------------- browser sign-in (OAuth PKCE) and key checks
def test_pkce_pair_is_s256_base64url():
    import base64
    import hashlib
    from krita_mcp import oauth
    verifier, challenge = oauth.pkce_pair()
    assert 43 <= len(verifier) <= 128 and "=" not in challenge
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _browser_hits(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_openrouter_browser_sign_in(home, monkeypatch):
    import urllib.parse
    from krita_mcp import oauth
    from krita_mcp.providers import keys
    m = Mock()
    try:
        m.json("POST", "/api/v1/auth/keys", lambda h, b: {"key": "sk-or-v1-" + "a" * 64}
               if json.loads(b) == {"code": "good-code", "code_verifier": oauth.FLOWS[fid].verifier,
                                    "code_challenge_method": "S256"} else {"error": "bad"})
        monkeypatch.setenv("KRITA_MCP_OPENROUTER_BASE_URL", m.url + "/api/v1")
        flow = oauth.start("openrouter")
        fid = flow["id"]
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(flow["url"]).query))
        assert flow["url"].startswith("https://openrouter.ai/auth?") and q["code_challenge_method"] == "S256"
        assert q["callback_url"].startswith("http://localhost:") and q["key_label"] == "Krita MCP"
        cb = q["callback_url"].replace("localhost", "127.0.0.1")
        code, page = _browser_hits(cb + "?code=good-code&state=forged")  # CSRF: wrong state is refused
        assert code == 400 and oauth.poll(fid)["status"] == "pending"
        code, page = _browser_hits(cb + "?code=good-code&state=" + q["state"])
        assert code == 200 and "Signed in" in page and oauth.poll(fid) == {"status": "done", "error": None}
        assert keys.saved("openrouter").startswith("sk-or-v1-") and agent.status()["openrouter"]["signed_in"]
        cancelled = oauth.start("openrouter")
        q2 = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(cancelled["url"]).query))
        _browser_hits(q2["callback_url"].replace("localhost", "127.0.0.1") + "?state=" + q2["state"])
        assert oauth.poll(cancelled["id"])["status"] == "error"
    finally:
        m.srv.shutdown()
    with pytest.raises(ValueError, match="no browser sign-in"):
        oauth.start("gemini")


def test_keys_are_checked_before_saving(home, monkeypatch):
    from krita_mcp import oauth
    m = Mock()
    try:
        m.routes[("GET", "/models")] = lambda h, b: (
            200 if h.headers.get("x-goog-api-key") == "AIza-good" else 400, "application/json", b'{"models": []}')
        monkeypatch.setenv("KRITA_MCP_GEMINI_BASE_URL", m.url)
        assert oauth.verify("gemini", "AIza-good") == (True, "key works")
        assert oauth.verify("gemini", "AIza-bad")[0] is False
        assert oauth.verify("fal", "x")[0] is None and oauth.verify("gemini", "")[0] is False
        srv = {}
        threading.Thread(target=agent.serve, kwargs={"port": 0, "ready": lambda s: srv.setdefault("s", s)},
                         daemon=True).start()
        while "s" not in srv:
            time.sleep(0.02)
        port = srv["s"].server_address[1]

        def post(path, body):
            r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method="POST",
                                       data=json.dumps(body).encode(), headers={"Authorization": "Bearer test-token"})
            try:
                with urllib.request.urlopen(r, timeout=10) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        code, r = post("/signin", {"provider": "gemini", "key": "AIza-bad"})
        assert code == 400 and "not saved" in r["error"] and agent.key_for("gemini") is None
        code, r = post("/signin", {"provider": "gemini", "key": "AIza-good"})
        assert code == 200 and r["providers"]["gemini"]["signed_in"]
        assert post("/verify", {"provider": "gemini", "key": "AIza-bad"})[1]["ok"] is False
        srv["s"].shutdown()
    finally:
        m.srv.shutdown()


def test_openrouter_picks_a_tool_using_model_from_the_live_list(home, bridge, monkeypatch):
    """An OpenRouter account need not have a default model ("No models provided"): choose from its list."""
    m = Mock()
    try:
        m.json("GET", "/api/v1/models", {"data": [
            {"id": "acme/chat-only", "created": 900, "supported_parameters": ["temperature"],
             "architecture": {"input_modalities": ["text", "image"]}},  # cannot call tools: skipped
            {"id": "acme/text-tools", "created": 800, "supported_parameters": ["tools"],
             "architecture": {"input_modalities": ["text"]}},
            {"id": "acme/vision-tools", "created": 700, "supported_parameters": ["tools", "tool_choice"],
             "architecture": {"input_modalities": ["text", "image"]}}]})
        m.json("POST", "/api/v1/chat/completions", {"choices": [{"message": {"content": "hi"}}]})
        monkeypatch.setenv("KRITA_MCP_OPENROUTER_BASE_URL", m.url + "/api/v1")
        agent.save_key("openrouter", "sk-or-v1-x")
        events = _wait(agent.chat(None, "openrouter", None, "hello"))
        body, headers = json.loads(m.calls[-1][3]), m.calls[-1][2]
        listed = agent.available_models("openrouter")
    finally:
        m.srv.shutdown()
    assert events[-1]["text"] == "hi" and body["model"] == "acme/vision-tools"  # tools + sees the canvas
    assert listed[:2] == ["acme/vision-tools", "acme/text-tools"]
    assert headers["x-openrouter-title"] == "Krita MCP" and headers["authorization"] == "Bearer sk-or-v1-x"


def test_chat_with_a_compatible_service(home, bridge, monkeypatch):
    m = Mock()
    try:
        m.json("GET", "/mistral/models", {"data": [{"id": "mistral-embed", "created": 50},
                                                   {"id": "mistral-large-2611", "created": 40},
                                                   {"id": "ministral-8b", "created": 45}]})
        m.json("POST", "/mistral/chat/completions", {"choices": [{"message": {"content": "Bonjour."}}]})
        monkeypatch.setenv("KRITA_MCP_MISTRAL_BASE_URL", m.url + "/mistral")
        agent.save_key("mistral", "m-key")
        assert agent.available_models("mistral") == ["mistral-large-2611", "ministral-8b"]
        events = _wait(agent.chat(None, "mistral", None, "hello"))
    finally:
        m.srv.shutdown()
    assert [e["text"] for e in events if e["type"] == "note"] == ["Model: mistral-large-2611"]
    assert events[-1]["text"] == "Bonjour." and agent.status()["mistral"]["chat"] is True
    from krita_mcp import oauth
    m2 = Mock()
    try:
        m2.routes[("GET", "/mistral/models")] = lambda h, b: (
            200 if h.headers.get("Authorization") == "Bearer good" else 401, "application/json", b'{"data": []}')
        monkeypatch.setenv("KRITA_MCP_MISTRAL_BASE_URL", m2.url + "/mistral")
        assert oauth.verify("mistral", "good")[0] is True and oauth.verify("mistral", "bad")[0] is False
    finally:
        m2.srv.shutdown()


def test_service_reports_its_version_and_can_be_stopped(home):
    from krita_mcp import __version__
    srv = {}
    t = threading.Thread(target=agent.serve, kwargs={"port": 0, "ready": lambda s: srv.setdefault("s", s)},
                         daemon=True)
    t.start()
    while "s" not in srv:
        time.sleep(0.02)
    port = srv["s"].server_address[1]

    def call(method, path):
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method=method,
                                   data=b"{}" if method == "POST" else None,
                                   headers={"Authorization": "Bearer test-token"})
        with urllib.request.urlopen(r, timeout=5) as resp:
            return json.loads(resp.read())
    assert call("GET", "/health")["version"] == __version__
    assert call("POST", "/shutdown") == {"ok": True}
    t.join(5)
    assert not t.is_alive()  # the service really stopped (the panel then starts the current one)


def test_reply_length_is_capped_and_low_credit_is_handled(home, bridge, monkeypatch):
    """OpenRouter reserves credit for the longest possible reply: cap it, and fit what the balance allows."""
    m = Mock()
    calls = {"n": 0}
    afford = ('{"error":{"message":"This request requires more credits, or fewer max_tokens. You requested '
              'up to 4096 tokens, but can only afford 1893.","code":402}}')

    def chat(h, b):
        calls["n"] += 1
        if calls["n"] == 1:
            return 402, "application/json", afford.encode()
        return 200, "application/json", json.dumps({"choices": [{"message": {"content": "painted"}}]}).encode()
    try:
        m.routes[("POST", "/api/v1/chat/completions")] = chat
        m.json("GET", "/api/v1/models", {"data": [{"id": "acme/vision-tools", "created": 1,
                                                    "supported_parameters": ["tools"],
                                                    "architecture": {"input_modalities": ["text", "image"]}}]})
        monkeypatch.setenv("KRITA_MCP_OPENROUTER_BASE_URL", m.url + "/api/v1")
        agent.save_key("openrouter", "sk-or-v1-x")
        events = _wait(agent.chat(None, "openrouter", None, "paint"))
        posts = [json.loads(c[3]) for c in m.calls if c[0] == "POST"]
    finally:
        m.srv.shutdown()
    assert events[-1]["text"] == "painted"
    assert posts[0]["max_tokens"] == 4096 and posts[1]["max_tokens"] == 1893 - 256  # fitted to the balance


def test_no_credit_at_all_and_rejected_keys_explain_themselves(home, bridge, monkeypatch):
    m = Mock()
    try:
        m.routes[("POST", "/api/v1/chat/completions")] = lambda h, b: (
            402, "application/json", b'{"error":{"message":"Insufficient credits","code":402}}')
        m.json("GET", "/api/v1/models", {"data": [{"id": "acme/m", "supported_parameters": ["tools"]}]})
        m.routes[("GET", "/models")] = lambda h, b: (
            401, "application/json", b'{"error":{"code":401,"message":"API key not valid."}}')
        monkeypatch.setenv("KRITA_MCP_OPENROUTER_BASE_URL", m.url + "/api/v1")
        monkeypatch.setenv("KRITA_MCP_GEMINI_BASE_URL", m.url)
        agent.save_key("openrouter", "sk-or-v1-x")
        agent.save_key("gemini", "bad-key")
        no_credit = [e for e in _wait(agent.chat(None, "openrouter", None, "paint")) if e["type"] == "error"]
        bad_key = [e for e in _wait(agent.chat(None, "gemini", None, "paint")) if e["type"] == "error"]
    finally:
        m.srv.shutdown()
    assert "not enough credits" in no_credit[0]["text"] and "cheaper or free model" in no_credit[0]["text"]
    assert "rejected the Gemini API key" in bad_key[0]["text"] and "sign in" in bad_key[0]["text"]


def test_openai_uses_max_completion_tokens(home, bridge, monkeypatch):
    m = Mock()
    try:
        m.json("POST", "/v1/chat/completions", {"choices": [{"message": {"content": "ok"}}]})
        monkeypatch.setenv("KRITA_MCP_OPENAI_BASE_URL", m.url + "/v1")
        agent.save_key("openai", "sk-x")
        _wait(agent.chat(None, "openai", "gpt-x", "hi"))
        body = json.loads([c for c in m.calls if c[0] == "POST"][-1][3])
    finally:
        m.srv.shutdown()
    assert body["max_completion_tokens"] == 4096 and "max_tokens" not in body  # newer OpenAI models need this


class SlowModel(agent._Adapter):
    """A model that takes its time, like a big model thinking: Stop must not wait for it."""

    def __init__(self, replies, delay=5.0):
        self.replies, self.delay, self.note, self.model = list(replies), delay, None, "slow"
        self.reset_usage()

    def prepare(self):
        return "slow"

    def start(self, specs):
        return [{"role": "system", "content": "s"}]

    def user(self, history, text):
        history.append({"role": "user", "content": text})

    def step(self, history):
        time.sleep(self.delay)
        reply, calls = self.replies.pop(0)
        history.append({"role": "assistant", "content": reply, "calls": [c[1] for c in calls]})
        return reply, calls

    def results(self, history, results):
        history.extend({"role": "tool", "id": cid, "name": n, "content": out} for cid, n, out, _ in results)

    def compact(self, history, turn_start):
        pass


def test_stop_interrupts_a_slow_model_at_once(home, monkeypatch):
    model = SlowModel([("thinking...", [])], delay=5.0)
    monkeypatch.setattr(agent, "make_model", lambda p, m=None: model)
    sess = agent.chat(None, "gemini", None, "paint a sunrise")
    time.sleep(0.5)
    t0 = time.monotonic()
    sess.stop = True
    events = _wait(sess, timeout=4)
    assert not sess.busy and time.monotonic() - t0 < 2.0  # not the 5 s the model takes
    assert events[-1]["type"] == "assistant" and events[-1]["text"] == "(stopped)"
    time.sleep(5.2)  # the abandoned request finishes later: its answer must not enter the conversation
    assert [m["role"] for m in sess.history] == ["system", "user"]


def test_stop_during_tools_answers_every_call(home, bridge, monkeypatch):
    calls = [("c1", "list_layers", {}), ("c2", "list_layers", {})]
    model = SlowModel([("", calls), ("never", [])], delay=0.0)
    monkeypatch.setattr(agent, "make_model", lambda p, m=None: model)
    real = agent.run_tool

    async def slow_tool(name, args):
        import asyncio
        await asyncio.sleep(3)
        return await real(name, args)
    monkeypatch.setattr(agent, "run_tool", slow_tool)
    sess = agent.chat(None, "gemini", None, "go")
    time.sleep(0.5)
    sess.stop = True
    events = _wait(sess, timeout=3)
    assert events[-1]["text"] == "(stopped)"
    answered = [m for m in sess.history if m["role"] == "tool"]
    assert [m["id"] for m in answered] == ["c1", "c2"]  # the next message would be refused otherwise
    assert all(m["content"] == "error: stopped by the user" for m in answered)


def test_new_chat_stops_the_old_one(home, monkeypatch):
    model = SlowModel([("x", [])], delay=5.0)
    monkeypatch.setattr(agent, "make_model", lambda p, m=None: model)
    srv, port = _serve()
    try:
        sess = agent.chat(None, "gemini", None, "paint")
        time.sleep(0.3)
        _post(port, "/reset", {"session": sess.id})
        _wait(sess, timeout=3)
        assert not sess.busy and sess.id not in agent.SESSIONS
    finally:
        srv.shutdown()


def test_leaked_template_tokens_are_cleaned_from_tool_names():
    assert agent.clean_tool_name("ai_backends<|channel|>commentary") == "ai_backends"
    assert agent.clean_tool_name(" canvas_snapshot ") == "canvas_snapshot"
    assert agent.clean_tool_name("functions.list_layers") == "list_layers"


def _serve():
    ready = threading.Event()
    box = {}

    def go():
        agent.serve(port=0, ready=lambda s: (box.update(srv=s), ready.set()))
    threading.Thread(target=go, daemon=True).start()
    ready.wait(5)
    return box["srv"], box["srv"].server_address[1]


def _post(port, path, body):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=json.dumps(body).encode(),
                                 method="POST", headers={"Authorization": "Bearer test-token",
                                                         "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_gemini_3_thinks_briefly_unless_told_otherwise(home, monkeypatch):
    agent.save_key("gemini", "k")
    g = agent.Gemini(None)
    g.model = "gemini-3.8-flash"
    assert g.thinking_config() == {"thinkingConfig": {"thinkingLevel": "low"}}
    monkeypatch.setenv("KRITA_MCP_GEMINI_THINKING", "off")
    assert g.thinking_config() is None
    monkeypatch.delenv("KRITA_MCP_GEMINI_THINKING")
    g.model = "gemini-2.5-flash"
    assert g.thinking_config() is None  # older models have no thinking level


def test_a_model_without_thinking_levels_still_answers(home, monkeypatch):
    m = Mock()
    seen = []

    def reply(h, b):
        body = json.loads(b)
        seen.append("generationConfig" in body)
        if "generationConfig" in body:
            return 400, "application/json", json.dumps(
                {"error": {"message": "Thinking level is not supported for this model."}}).encode()
        return 200, "application/json", json.dumps(
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]}).encode()
    try:
        m.routes[("POST", "/models/gemini-3.8-flash:generateContent")] = reply
        monkeypatch.setenv("KRITA_MCP_GEMINI_BASE_URL", m.url)
        agent.save_key("gemini", "k")
        events = _wait(agent.chat(None, "gemini", "gemini-3.8-flash", "hi"))
    finally:
        m.srv.shutdown()
    assert events[-1]["text"] == "ok" and seen == [True, False]


def test_a_busy_model_service_is_tried_again(home, monkeypatch):
    monkeypatch.setattr(agent, "RETRY_WAITS", (0.1, 0.1))
    model = SlowModel([("done", [])], delay=0.0)
    fails = ["HTTP 503: The model is overloaded. Please try again later."]
    real_step = model.step

    def step(history):
        if fails:
            raise agent.ProviderError("POST https://x/generateContent -> " + fails.pop())
        return real_step(history)
    model.step = step
    monkeypatch.setattr(agent, "make_model", lambda p, m=None: model)
    events = _wait(agent.chat(None, "gemini", None, "hi"), timeout=5)
    assert events[-1]["text"] == "done"
    assert any("busy (HTTP 503)" in e.get("text", "") for e in events)


def test_a_bad_key_is_not_retried(home, monkeypatch):
    monkeypatch.setattr(agent, "RETRY_WAITS", (0.1, 0.1))
    model = SlowModel([], delay=0.0)
    tries = []

    def step(history):
        tries.append(1)
        raise agent.ProviderError("HTTP 401: API key not valid")
    model.step = step
    monkeypatch.setattr(agent, "make_model", lambda p, m=None: model)
    events = _wait(agent.chat(None, "gemini", None, "hi"), timeout=5)
    assert len(tries) == 1 and events[-1]["type"] == "error"


def test_each_service_says_how_it_is_set_up(home):
    st = agent.status()
    kinds = {n: st[n]["setup"] for n in ("gemini", "mistral", "openrouter", "ollama", "comfyui", "a1111",
                                         "omniroute", "kilo", "llm7", "pollinations", "horde")}
    assert kinds == {"gemini": "key", "mistral": "key", "openrouter": "browser", "ollama": "local",
                     "comfyui": "local", "a1111": "local", "omniroute": "local", "kilo": "free", "llm7": "free",
                     "pollinations": "toggle", "horde": "toggle"}
    assert not st["mistral"]["has_url"] and st["openai"]["has_url"]  # cloud services ask for no address
    assert st["kilo"]["ready"] and not st["gemini"]["ready"] and not st["horde"]["ready"]
    assert not st["ollama"]["ready"] and st["ollama"]["default_url"] == "http://127.0.0.1:11434/v1"
    agent.keys.save("ollama", key="", base_url="http://127.0.0.1:1234/v1")
    agent.keys.save("horde", key="", enable=True)
    st = agent.status()
    assert st["ollama"]["ready"] and st["horde"]["ready"] and st["horde"]["signed_in"]
    agent.keys.save("horde", remove=True)
    assert not agent.status()["horde"]["ready"]  # Turn off


def test_the_connection_test_says_whether_a_local_server_answers(home):
    m = Mock()
    try:
        m.json("GET", "/v1/models", {"data": [{"id": "llama3.2"}]})
        ok, detail = agent.check_server("ollama", m.url + "/v1")
    finally:
        m.srv.shutdown()
    assert ok and "llama3.2" in detail
    ok, detail = agent.check_server("ollama", "http://127.0.0.1:9/v1")
    assert not ok and "nothing answers" in detail


class Broke(SlowModel):
    """A provider whose credit ran out."""

    def step(self, history):
        raise agent.ProviderError("POST https://x/chat -> HTTP 402: insufficient credits")


def _auto(monkeypatch, models):
    real = agent.make_model
    monkeypatch.setattr(agent, "auto_order", lambda st=None: list(models))
    monkeypatch.setattr(agent, "make_model", lambda p, m=None: real(p) if p == "auto" else models[p])


def test_auto_switches_to_the_next_provider_when_credit_runs_out(home, monkeypatch):
    first = Broke([])
    second = SlowModel([("Painted the sunrise.", [])], delay=0.0)
    _auto(monkeypatch, {"openrouter": first, "kilo": second})
    events = _wait(agent.chat(None, "auto", None, "paint a sunrise"), timeout=5)
    assert events[-1]["text"] == "Painted the sunrise."
    notes = " ".join(e.get("text", "") for e in events if e["type"] == "note")
    assert "switched to" in notes and "HTTP 402" in notes
    sess = list(agent.SESSIONS.values())[-1]
    recap = [m["content"] for m in sess.history if m["role"] == "user"][0]
    assert "continues on another AI service" in recap and "paint a sunrise" in recap  # the task carried over


def test_auto_keeps_going_across_messages_and_reports_when_all_fail(home, monkeypatch):
    _auto(monkeypatch, {"a": Broke([]), "b": Broke([])})
    events = _wait(agent.chat(None, "auto", None, "hi"), timeout=5)
    assert events[-1]["type"] == "error" and "tried every provider" in events[-1]["text"]


def test_auto_does_not_switch_on_a_real_mistake(home, monkeypatch):
    class Bad(SlowModel):
        def step(self, history):
            raise agent.ProviderError("HTTP 400: the request is malformed")
    second = SlowModel([("x", [])], delay=0.0)
    _auto(monkeypatch, {"a": Bad([]), "b": second})
    events = _wait(agent.chat(None, "auto", None, "hi"), timeout=5)
    assert events[-1]["type"] == "error" and "malformed" in events[-1]["text"]  # not hidden by switching


def test_auto_order_is_yours_first_then_free(home, monkeypatch):
    assert agent.auto_order()[:4] == ["pollinations", "kilo", "ovhcloud", "llm7"]
    agent.save_key("mistral", "m" * 20)
    agent.save_key("anthropic", "a" * 20)
    order = agent.auto_order()
    assert order[:2] == ["anthropic", "mistral"] and order[2] == "pollinations"
    monkeypatch.setenv("KRITA_MCP_AUTO_ORDER", "kilo, mistral")
    assert agent.auto_order() == ["kilo", "mistral"]
    st = agent.status()
    assert st["auto"]["chat"] and st["auto"]["setup"] == "auto" and st["auto"]["order"] == ["kilo", "mistral"]
