"""The `kritapilot` command line for people and scripts (Krita must be running with the KritaPilot plugin):

    kritapilot status                        Krita, the plugin and the open documents
    kritapilot tools [WORD]                  the tools, optionally only those mentioning WORD
    kritapilot call TOOL ['{"arg": 1}']      run any tool and print its result as JSON
    kritapilot snapshot [FILE.png]           save what the canvas shows (default snapshot.png)
    kritapilot chat "MESSAGE" [--provider P] [--model M]
                                             ask the AI Chat assistant; its steps are printed as it works
    kritapilot history [N]                   the N newest generated pictures
    kritapilot install [--dest PYKRITA]      put the Krita plugin in place and turn it on
    kritapilot doctor                        check every link to Krita and the AI services

Without one of these, `kritapilot` is the MCP server that AI apps start (see `kritapilot --help`).
"""
import argparse
import asyncio
import base64
import json
import sys
import time

COMMANDS = ("status", "tools", "call", "snapshot", "chat", "history", "install", "doctor")


def _run(name, /, **args):
    from .server import TOOLS, Image, ToolError
    if name not in TOOLS:
        raise SystemExit("no tool %r - see: kritapilot tools" % name)
    try:
        out = asyncio.run(TOOLS[name](**args))
    except ToolError as e:
        raise SystemExit("error: %s" % e)
    except TypeError as e:
        raise SystemExit("bad arguments for %s: %s" % (name, e))
    parts = out if isinstance(out, list) else [out]
    images = [p for p in parts if isinstance(p, Image)]
    data = [p for p in parts if not isinstance(p, Image)]
    return (data[0] if len(data) == 1 else data), images


def _png(img):
    return img.data if isinstance(img.data, bytes) else base64.b64decode(img.data)


def status(_args):
    info, _ = _run("krita_status")
    docs = info.get("documents") or []
    print("Krita %s · plugin %s · workspace %s" % (info.get("krita_version"), info.get("bridge_version"),
                                                   info.get("workspace")))
    for d in docs:
        print("  %s %s  %dx%d%s" % ("*" if d.get("active") else " ", d.get("name"), d.get("width", 0),
                                    d.get("height", 0), "  (modified)" if d.get("modified") else ""))
    if not docs:
        print("  no document open")


def tools(args):
    from .server import TOOLS
    word = (args.word or "").lower()
    for name in sorted(TOOLS):
        doc = (TOOLS[name].__doc__ or "").strip().split("\n")[0]
        if not word or word in name.lower() or word in doc.lower():
            print("%-26s %s" % (name, doc[:100]))


def call(args):
    try:
        kwargs = json.loads(args.args) if args.args else {}
    except ValueError as e:
        raise SystemExit("the arguments must be JSON, like '{\"width\": 800}': %s" % e)
    if not isinstance(kwargs, dict):
        raise SystemExit("the arguments must be a JSON object, like '{\"width\": 800}'")
    data, images = _run(args.tool, **kwargs)
    print(json.dumps(data, indent=2, default=str))
    for n, img in enumerate(images, 1):
        path = "%s-%d.png" % (args.tool, n)
        with open(path, "wb") as f:
            f.write(_png(img))
        print("picture saved: %s" % path)


def snapshot(args):
    _, images = _run("canvas_snapshot", max_size=args.max_size)
    if not images:
        raise SystemExit("no picture came back")
    with open(args.file, "wb") as f:
        f.write(_png(images[0]))
    print("saved %s" % args.file)


def chat(args):
    from . import agent
    sess = agent.chat(None, args.provider, args.model, args.message)
    seen = 0
    while True:
        with sess.lock:
            new = sess.events[seen:]
        for e in new:
            seen = e["n"] + 1
            kind = e["type"]
            if kind == "assistant":
                print("\n%s\n" % e["text"])
            elif kind == "tool":
                print("  > %s %s" % (e["name"], e.get("args", "")[:120]))
            elif kind == "tool_result" and not e.get("ok"):
                print("  ! %s" % e.get("text", "")[:200])
            elif kind == "note":
                print("  · %s" % e.get("text", ""))
            elif kind == "error":
                print("error: %s" % e["text"], file=sys.stderr)
            elif kind == "done":
                return
        time.sleep(0.2)


def history(args):
    data, _ = _run("generation_history", limit=args.n)
    for e in data.get("generations") or []:
        print("%s  %-12s %s" % (e.get("time"), e.get("backend"), (e.get("prompt") or "")[:90]))
    print("(pictures in %s)" % data.get("folder"))


def main(argv):
    ap = argparse.ArgumentParser(prog="kritapilot", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Krita, the plugin and the open documents")
    t = sub.add_parser("tools", help="list the tools")
    t.add_argument("word", nargs="?")
    c = sub.add_parser("call", help="run a tool")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", help="JSON arguments")
    s = sub.add_parser("snapshot", help="save what the canvas shows")
    s.add_argument("file", nargs="?", default="snapshot.png")
    s.add_argument("--max-size", type=int, default=2048)
    ch = sub.add_parser("chat", help="ask the AI Chat assistant")
    ch.add_argument("message")
    ch.add_argument("--provider", default="auto")
    ch.add_argument("--model", default=None)
    h = sub.add_parser("history", help="recent generated pictures")
    h.add_argument("n", nargs="?", type=int, default=10)
    i = sub.add_parser("install", help="put the Krita plugin in place and turn it on")
    i.add_argument("--dest", help="Krita's pykrita folder (found automatically)")
    i.add_argument("--kritarc", help="Krita's settings file (found automatically)")
    i.add_argument("--no-enable", action="store_true", help="only copy the files")
    sub.add_parser("doctor", help="check every link to Krita and the AI services")
    args = ap.parse_args(argv)
    from . import install as setup
    {"status": status, "tools": tools, "call": call, "snapshot": snapshot, "chat": chat,
     "history": history, "install": setup.install, "doctor": setup.doctor}[args.command](args)
