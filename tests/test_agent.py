"""The in-Krita assistant: schemas, the agent loop for each provider, sign-in and the loopback service."""
import json
import threading
import time
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
        m.json("POST", "/models/gemini-2.5-flash:generateContent", lambda h, b: next(replies))
        monkeypatch.setenv("KRITA_MCP_GEMINI_BASE_URL", m.url)
        agent.save_key("gemini", "g-key")
        sess = agent.chat(None, "gemini", None, "make a canvas for a fox")
        events = _wait(sess)
    finally:
        m.srv.shutdown()
    kinds = [(e["type"], e.get("name")) for e in events]
    assert kinds == [("user", None), ("tool", "document_new"), ("tool_result", "document_new"),
                     ("tool", "canvas_snapshot"), ("tool_result", "canvas_snapshot"), ("assistant", None)]
    assert events[4]["images"] == 1 and events[-1]["text"] == "Made a 640x480 canvas."
    assert ("document.new", {"width": 640, "height": 480, "name": "fox", "background": "white",
                             "resolution_ppi": 72}) in calls
    first, third = (json.loads(c[3]) for c in (m.calls[0], m.calls[2]))
    assert m.calls[0][2]["x-goog-api-key"] == "g-key"
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
        m.routes[("POST", "/models/gemini-2.5-flash:generateContent")] = lambda h, b: (
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
    second = json.loads(m.calls[1][3])
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
    assert events[-1]["text"] == "There is one layer."
    second = json.loads(m.calls[1][3])
    result = second["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu1"
    assert second["model"] == "claude-opus-5-5" and len(second["tools"]) > 80


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
