"""The trace logs behind the Logs window: one line per action, dated, keys masked, size-limited."""
import asyncio
import os
from pathlib import Path

from krita_mcp import agent, tracelog

ROOT = Path(__file__).resolve().parents[1]


def test_server_and_plugin_share_one_logger():
    assert (ROOT / "src/krita_mcp/tracelog.py").read_bytes() == \
        (ROOT / "plugin/pykrita/krita_mcp_bridge/tracelog.py").read_bytes()


def test_keys_never_reach_a_log():
    for secret in ("Authorization: Bearer sk-or-v1-0123456789abcdef0123", '{"key": "AIzaSyC' + "k" * 33 + '"}',
                   "api_key=hf_abcdefghijklmnop", "token=r8_abcdefghijklmnopqrst", "gsk_abcdefghijklmnopq",
                   "password: hunter22", "data:image/png;base64," + "QUJD" * 40):
        out = tracelog.redact(secret)
        assert "***" in out or "…" in out, secret
        assert not any(part in out for part in ("0123456789abcdef", "kkkkkkkk", "abcdefghijklmnop", "hunter22",
                                                "QUJDQUJD")), out
    keep = "layer 32e58737-9086-4c5a-9d41-225920212111, hotkey: ctrl+s, keyframe=3, provider=openrouter"
    assert tracelog.redact(keep) == keep


def test_lines_are_dated_single_and_the_file_stays_small(monkeypatch):
    monkeypatch.setattr(tracelog, "MAX_BYTES", 400)
    for i in range(30):
        tracelog.write("t.log", "test", "line %d\nwith a break" % i)
    p = Path(tracelog.path("t.log"))
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines and all(" test " in ln and "with a break" in ln for ln in lines)
    assert p.stat().st_size < 600 and Path(str(p) + ".1").exists()
    assert str(p).startswith(os.environ["KRITA_MCP_HOME"])  # tests never touch the real ~/.krita-mcp


def test_the_assistant_logs_messages_and_tool_calls():
    sess = agent.Session("pollinations", None)
    sess.emit("user", text="paint a sunrise")
    out, _ = asyncio.run(agent.run_tool("no_such_tool", {}))
    text = Path(tracelog.path("assistant.log")).read_text(encoding="utf-8")
    assert "[%s] user: paint a sunrise" % sess.id in text
    assert "✗ no_such_tool" in text and "unknown tool" in text
