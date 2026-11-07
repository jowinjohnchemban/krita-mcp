"""Agent Skills for Krita work: folders with a SKILL.md (name + description, then instructions).

Skills are found fresh on every call - nothing is cached - in, by priority:
    KRITA_MCP_SKILLS_DIRS (os.pathsep-separated; replaces the list below when set)
    ~/.krita-mcp/skills        your own, and what you add from the AI Chat > Skills panel
    the skills that ship with krita-mcp
    ~/.agents/skills, ~/.claude/skills    skills installed for other agents (e.g. `npx skills add ... -g`)

Only names and one-line descriptions are offered to the model; a skill's full text is loaded when the model
decides it fits (use_skill), so unused skills cost no tokens. Skills are read as text: krita-mcp never runs
scripts that a skill folder may contain.
"""
import os
import re
from pathlib import Path

BUNDLED = Path(__file__).with_name("bundled_skills")
MAX_SKILL_CHARS = 24000
MAX_FILE_CHARS = 60000
TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".svg", ".xml", ".html", ".css"}
_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("a an and are as at be by can do for from how i in into is it its me my of on or our please "
            "so that the their them then this to up use using we what when with you your make add".split())


def user_dir():
    from .providers import keys
    return keys.home() / "skills"


def dirs():
    custom = os.environ.get("KRITA_MCP_SKILLS_DIRS")
    if custom:
        return [Path(d).expanduser() for d in custom.split(os.pathsep) if d.strip()]
    home = Path.home()
    return [user_dir(), BUNDLED, home / ".agents" / "skills", home / ".claude" / "skills"]


def _frontmatter(text):
    """name/description from a SKILL.md's YAML front matter (simple keys, quoted or folded values)."""
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            head, body = text[3:end], text[end + 4:].lstrip("\n")
            key = None
            for line in head.splitlines():
                m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
                if m:
                    key, val = m.group(1).lower(), m.group(2).strip()
                    meta[key] = "" if val in (">", "|", ">-", "|-") else val.strip("'\"")
                elif key and line.startswith((" ", "\t")):
                    meta[key] = (meta[key] + " " + line.strip()).strip()
    return meta, body


def _source(folder):
    folder = str(folder)
    for d, label in ((BUNDLED, "built-in"), (user_dir(), "yours")):
        if folder.startswith(str(d)):
            return label
    return "installed"


def all_skills():
    """[{name, description, source, path}], first found wins for a name."""
    seen, out = set(), []
    for d in dirs():
        try:
            entries = sorted(p for p in d.iterdir() if p.is_dir())
        except OSError:
            continue
        for folder in entries:
            md = folder / "SKILL.md"
            try:
                text = md.read_text(encoding="utf-8", errors="replace")[:8000]
            except OSError:
                continue
            meta, _ = _frontmatter(text)
            name = (meta.get("name") or folder.name).strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append({"name": name, "description": (meta.get("description") or "")[:400],
                        "source": _source(folder), "path": str(folder)})
    return out


def _stem(w):
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w  # skills -> skill


def _words(text):
    return {_stem(w) for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1}


def _score(skill, words):
    """Words of the request found in the skill's name count 3, in its description 1 (prefixes too)."""
    hay = _words(skill["name"].replace("-", " ") + " " + skill["description"])
    name = _words(skill["name"].replace("-", " "))

    def hit(w, pool):
        return w in pool or (len(w) > 3 and any(h.startswith(w) for h in pool))
    return sum(3 if hit(w, name) else 1 for w in words if hit(w, hay))


def find(query="", limit=8):
    """Skills matching the query, best first (all of them, up to limit, for an empty query)."""
    skills = all_skills()
    words = _words(query)
    if not words:
        return skills[:limit]
    scored = sorted(((s, _score(s, words)) for s in skills), key=lambda t: -t[1])
    return [s for s, sc in scored if sc > 0][:limit]


def suggest(text, limit=3, min_score=3):
    """The few skills worth mentioning for a user message (cheap: names and descriptions only). A skill must
    match at least two words of the message, so one shared word ("canvas") does not drag one in."""
    words = _words(text)
    if not words:
        return []
    scored = sorted(((s, _score(s, words)) for s in all_skills()), key=lambda t: -t[1])
    return [s for s, sc in scored if sc >= min_score and _score_words(s, words) >= 2][:limit]


def _score_words(skill, words):
    """How many different words of the request the skill matches."""
    return sum(1 for w in words if _score(skill, {w}))


def _get(name):
    for s in all_skills():
        if s["name"].lower() == str(name).strip().lower() or Path(s["path"]).name.lower() == str(name).lower():
            return s
    return None


def load(name, file=None):
    """A skill's instructions (or one of its text files), plus the other files it offers."""
    s = _get(name)
    if s is None:
        near = [x["name"] for x in find(name, 5)]
        raise LookupError("no skill named %r%s" % (name, "; similar: " + ", ".join(near) if near else ""))
    root = Path(s["path"]).resolve()
    files = sorted(str(p.relative_to(root)).replace(os.sep, "/") for p in root.rglob("*")
                   if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES and p.name != "SKILL.md")[:60]
    if file:
        target = (root / file).resolve()
        if root not in target.parents or not target.is_file():
            raise LookupError("%r is not a file of skill %s; files: %s" % (file, s["name"], ", ".join(files)))
        if target.suffix.lower() not in TEXT_SUFFIXES:
            raise LookupError("only text files can be read (scripts are never run)")
        text = target.read_text(encoding="utf-8", errors="replace")
        return {"name": s["name"], "file": file, "text": text[:MAX_FILE_CHARS],
                "truncated": len(text) > MAX_FILE_CHARS}
    _, body = _frontmatter((root / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    return {"name": s["name"], "description": s["description"], "source": s["source"],
            "instructions": body[:MAX_SKILL_CHARS], "truncated": len(body) > MAX_SKILL_CHARS,
            "files": files, "note": "Read a listed file with use_skill(name, file=...). Scripts are not run."}
