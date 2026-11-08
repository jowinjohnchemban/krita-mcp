"""End-to-end test: real bridge protocol + real MCP server over stdio, fake Krita backend."""
import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "pykrita"))

from krita_mcp_bridge.protocol import BridgeServer  # noqa: E402
from krita_mcp_bridge.util import BridgeError, parse_color, safe_path  # noqa: E402

PNG_1X1 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
CALLS = []


def fake_dispatch(method, params):
    CALLS.append((method, params))
    if method == "app.info":
        return {"krita_version": "fake", "workspace": "/tmp/ws", "documents": []}
    if method == "canvas.snapshot":
        return {"png_base64": PNG_1X1, "width": 1, "height": 1, "region": [0, 0, 1, 1]}
    if method == "draw.shapes":
        return {"drawn": len(params["shapes"])}
    if method == "boom":
        raise BridgeError("nope")
    raise BridgeError("Unknown method %r" % method)


@pytest.fixture()
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("KRITA_MCP_HOME", str(tmp_path / "home"))
    srv = BridgeServer(fake_dispatch, port=0, request_timeout=5)
    port = srv.start()
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            srv.pump()
            time.sleep(0.005)

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    yield srv, port, tmp_path / "home"
    stop.set()
    srv.stop()


def raw_call(port, obj):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall((json.dumps(obj) + "\n").encode())
        return json.loads(s.makefile("rb").readline())


# --------------------------------------------------------------- security
def test_token_file_is_private(bridge):
    srv, _, home = bridge
    tok = home / "token"
    assert tok.read_text().strip() == srv.token
    if os.name == "posix":
        assert oct(tok.stat().st_mode & 0o777) == "0o600"


def test_rejects_bad_and_missing_token(bridge):
    srv, port, _ = bridge
    assert raw_call(port, {"id": 1, "token": "wrong", "method": "app.info"})["error"]["type"] == "AuthError"
    assert raw_call(port, {"id": 2, "method": "app.info"})["error"]["type"] == "AuthError"
    assert raw_call(port, {"id": 3, "token": srv.token, "method": "app.info"})["ok"] is True


def test_garbage_input_does_not_kill_server(bridge):
    srv, port, _ = bridge
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert json.loads(s.makefile("rb").readline())["ok"] is False
    assert raw_call(port, {"id": 1, "token": srv.token, "method": "app.info"})["ok"] is True


def test_refuses_non_loopback_bind():
    with pytest.raises(ValueError):
        BridgeServer(fake_dispatch, host="0.0.0.0", token="x" * 40)


def test_bridge_error_is_reported(bridge):
    srv, port, _ = bridge
    r = raw_call(port, {"id": 1, "token": srv.token, "method": "boom"})
    assert r["error"] == {"type": "BridgeError", "message": "nope"}


def test_workspace_sandbox(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    assert safe_path("a/b.png", {".png"}, root) == (root / "a" / "b.png").resolve()
    for bad in ("../x.png", "/etc/passwd", str(tmp_path / "outside.png"), "a\x00.png", ""):
        with pytest.raises(BridgeError):
            safe_path(bad, {".png"}, root)
    with pytest.raises(BridgeError):
        safe_path("x.exe", {".png"}, root)
    (tmp_path / "secret.png").write_bytes(b"x")
    link = root / "link.png"
    try:
        link.symlink_to(tmp_path / "secret.png")
    except OSError:
        return
    with pytest.raises(BridgeError):  # symlink escape
        safe_path("link.png", {".png"}, root)


def test_parse_color():
    assert parse_color("#ff0000") == (255, 0, 0, 255)
    assert parse_color("#f00") == (255, 0, 0, 255)
    assert parse_color("#11223380") == (0x11, 0x22, 0x33, 0x80)  # alpha LAST
    assert parse_color("Red") == (255, 0, 0, 255)
    assert parse_color(None, (1, 2, 3, 4)) == (1, 2, 3, 4)
    with pytest.raises(BridgeError):
        parse_color("#12345")


# ------------------------------------------------------------ MCP over stdio
def is_err(res):
    return bool(getattr(res, "is_error", None) if hasattr(res, "is_error") else res.isError)


def run_mcp(env, coro_fn):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def main():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "kritapilot"],
            env={**os.environ, **env, "PYTHONPATH": str(ROOT / "src")},
        )
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                return await coro_fn(session)

    return asyncio.run(asyncio.wait_for(main(), 60))


def test_mcp_tools_roundtrip(bridge):
    srv, port, home = bridge
    env = {"KRITA_MCP_HOME": str(home), "KRITA_MCP_PORT": str(port)}

    async def go(session):
        tools = {t.name for t in (await session.list_tools()).tools}
        status = await session.call_tool("krita_status", {})
        snap = await session.call_tool("canvas_snapshot", {"max_size": 256})
        draw = await session.call_tool("draw_shapes", {"shapes": [{"type": "line", "x1": 0, "y1": 0, "x2": 5, "y2": 5}]})
        bad = await session.call_tool("document_open", {"path": "x.kra"})
        return tools, status, snap, draw, bad

    tools, status, snap, draw, bad = run_mcp(env, go)
    assert {"krita_status", "document_new", "canvas_snapshot", "draw_shapes", "import_image",
            "apply_filter", "set_selection", "export_image", "undo"} <= tools
    assert "fake" in status.content[0].text
    assert snap.content[0].type == "image" and (getattr(snap.content[0], "mime_type", None) or snap.content[0].mimeType) == "image/png"
    assert not is_err(draw)
    assert is_err(bad)  # fake backend: unknown method -> clean tool error
    assert ("canvas.snapshot", {"max_size": 256}) in CALLS


def test_mcp_wrong_token_gives_clear_error(bridge, tmp_path):
    _, port, _ = bridge
    other = tmp_path / "other"
    other.mkdir()
    (other / "token").write_text("z" * 40)
    env = {"KRITA_MCP_HOME": str(other), "KRITA_MCP_PORT": str(port)}

    async def go(session):
        return await session.call_tool("krita_status", {})

    res = run_mcp(env, go)
    assert is_err(res) and "token" in res.content[0].text.lower()


def test_mcp_krita_not_running_gives_clear_error(tmp_path):
    (tmp_path / "token").write_text("z" * 40)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]; s.close()
    env = {"KRITA_MCP_HOME": str(tmp_path), "KRITA_MCP_PORT": str(free)}

    async def go(session):
        return await session.call_tool("krita_status", {})

    res = run_mcp(env, go)
    assert is_err(res) and "Cannot reach Krita" in res.content[0].text


def test_mcp_batch(bridge):
    _, port, home = bridge
    env = {"KRITA_MCP_HOME": str(home), "KRITA_MCP_PORT": str(port)}
    line = {"type": "line", "x1": 0, "y1": 0, "x2": 5, "y2": 5}

    async def go(session):
        ok = await session.call_tool("batch", {"operations": [
            {"tool": "draw_shapes", "args": {"shapes": [line]}},
            {"tool": "canvas_snapshot", "args": {"max_size": 64}},
            {"tool": "krita_status"}]})
        stop = await session.call_tool("batch", {"operations": [
            {"tool": "document_open", "args": {"path": "x.kra"}},  # fails on the fake backend
            {"tool": "krita_status"}]})
        bad = await session.call_tool("batch", {"operations": [
            {"tool": "nope"}, {"tool": "draw_shapes", "args": {"wrong": 1}}], "stop_on_error": False})
        return ok, stop, bad

    ok, stop, bad = run_mcp(env, go)
    summary = json.loads(ok.content[0].text)
    assert summary["completed"] == 3 and summary["results"][1]["result"] == {"image": 1}
    assert ok.content[1].type == "image"
    s2 = json.loads(stop.content[0].text)
    assert s2["completed"] == 0 and len(s2["results"]) == 1  # stopped at the first failure
    s3 = json.loads(bad.content[0].text)
    assert [r["ok"] for r in s3["results"]] == [False, False] and "bad arguments" in s3["results"][1]["error"]
