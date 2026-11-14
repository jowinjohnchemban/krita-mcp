"""The kritapilot command line for people and scripts."""
import json

import pytest

from kritapilot import server


def run(argv, capsys):
    server.main(argv)
    return capsys.readouterr().out


def test_status_lists_krita_and_documents(bridge, capsys, monkeypatch):
    calls, _ = bridge
    out = run(["status"], capsys)
    assert any(m == "app.info" for m, _ in calls)
    assert "no document open" in out


def test_tools_lists_and_filters(capsys):
    out = run(["tools", "describing"], capsys)
    assert "select_by_description" in out and "brush_stroke" not in out
    assert len(run(["tools"], capsys).splitlines()) >= 90


def test_call_runs_any_tool_with_json_arguments(bridge, capsys):
    calls, _ = bridge
    out = run(["call", "modify_selection", '{"operation": "grow", "amount": 3}'], capsys)
    assert ("selection.modify", {"operation": "grow", "amount": 3}) in calls and json.loads(out)
    with pytest.raises(SystemExit, match="must be JSON"):
        server.main(["call", "modify_selection", "{oops"])
    with pytest.raises(SystemExit, match="no tool"):
        server.main(["call", "no_such_tool"])



def test_call_passes_a_name_argument_to_the_tool(bridge, capsys):
    # document_new, create_layer, ... take `name`; it must reach the tool, not clash with the tool's own name
    calls, _ = bridge
    run(["call", "document_new", '{"width": 64, "height": 32, "name": "Page 1"}'], capsys)
    method, params = calls[-1]
    assert method == "document.new" and params["name"] == "Page 1" and params["width"] == 64
    with pytest.raises(SystemExit, match="JSON object"):
        server.main(["call", "document_new", "[1]"])

def test_snapshot_saves_a_png(bridge, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = run(["snapshot", "canvas.png"], capsys)
    assert (tmp_path / "canvas.png").read_bytes().startswith(b"\x89PNG") and "saved canvas.png" in out


def test_chat_prints_the_assistants_steps(capsys, monkeypatch):
    class Sess:
        import threading
        lock = threading.Lock()
        events = [{"n": 0, "type": "note", "text": "Model: free"},
                  {"n": 1, "type": "tool", "name": "create_artwork", "args": '{"prompt": "a sunset"}'},
                  {"n": 2, "type": "assistant", "text": "Painted the sunset."}, {"n": 3, "type": "done"}]
    seen = {}
    from kritapilot import agent
    monkeypatch.setattr(agent, "chat", lambda sid, provider, model, text: seen.update(p=provider, t=text) or Sess())
    out = run(["chat", "paint a sunset"], capsys)
    assert seen == {"p": "auto", "t": "paint a sunset"}
    assert "> create_artwork" in out and "Painted the sunset." in out and "Model: free" in out


def test_without_a_command_it_is_still_the_mcp_server(monkeypatch):
    ran = []
    monkeypatch.setattr(server.mcp, "run", lambda transport: ran.append(transport))
    server.main([])
    assert ran == ["stdio"]
