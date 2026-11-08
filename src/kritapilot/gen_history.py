"""Every picture an image model made, kept in ~/.kritapilot/history: browse it, place a result again, reuse a prompt.

    history/history.jsonl   one line per generation, oldest first
    history/<id>-<n>.png    its pictures

The newest MAX_ENTRIES generations are kept; older ones (and their pictures) are removed. The Krita plugin's
History window reads the same folder.
"""
import json
import time
import uuid

from . import config

MAX_ENTRIES = 200


def folder():
    return config.home() / "history"


def _index():
    return folder() / "history.jsonl"


def _read():
    try:
        lines = _index().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def record(prompt, images, backend, mode, model=None, style=None, size=None, seeds=None, kind="generate"):
    """Keep the pictures of one generation; returns its id. Never raises (history must not break a generation)."""
    try:
        f = folder()
        f.mkdir(parents=True, exist_ok=True)
        gid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        files = []
        for n, png in enumerate(images, 1):
            name = "%s-%d.png" % (gid, n)
            (f / name).write_bytes(png)
            files.append(name)
        entry = {"id": gid, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "prompt": prompt,
                 "backend": backend, "model": model, "mode": mode, "style": style, "size": size,
                 "seeds": seeds or [], "files": files}
        with open(_index(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        _prune()
        return gid
    except OSError:
        return None


def _prune():
    items = _read()
    if len(items) <= MAX_ENTRIES:
        return
    drop, keep = items[:-MAX_ENTRIES], items[-MAX_ENTRIES:]
    for e in drop:
        for name in e.get("files") or []:
            try:
                (folder() / name).unlink()
            except OSError:
                pass
    _index().write_text("".join(json.dumps(e) + "\n" for e in keep), encoding="utf-8")


def entries(limit=20):
    """The newest generations first."""
    return list(reversed(_read()))[:max(1, int(limit))]


def get(gid):
    for e in _read():
        if e.get("id") == gid:
            return e
    return None


def image(gid, index=1):
    e = get(gid)
    if e is None:
        raise KeyError("no generation %r in the history" % gid)
    files = e.get("files") or []
    if not 1 <= index <= len(files):
        raise KeyError("generation %r has %d picture(s)" % (gid, len(files)))
    return (folder() / files[index - 1]).read_bytes()
