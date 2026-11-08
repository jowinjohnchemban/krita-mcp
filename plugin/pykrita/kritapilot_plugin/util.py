"""Qt-free helpers: errors, workspace sandboxing, colour parsing.

Kept free of any `krita` / PyQt import so it can be unit-tested outside Krita.
"""
import os
from pathlib import Path


class BridgeError(Exception):
    """Expected, user-facing error (bad arguments, no document, ...)."""


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
DOC_EXTS = {".kra", ".ora"}
OPEN_EXTS = IMAGE_EXTS | DOC_EXTS | {".psd"}


WORKSPACE_OVERRIDE = None  # set from the MCP docker's settings; the env var still wins


def workspace_root() -> Path:
    """Directory the bridge is allowed to read from / write to."""
    root = Path(os.environ.get("KRITAPILOT_WORKSPACE") or WORKSPACE_OVERRIDE or (Path.home() / "KritaMCP"))
    root = root.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def safe_path(value, exts=None, root=None) -> Path:
    """Resolve `value` and guarantee it lives inside the workspace.

    Relative paths are taken relative to the workspace. Symlinks and `..`
    are resolved *before* the containment check.
    """
    if not isinstance(value, str) or not value or "\x00" in value:
        raise BridgeError("path must be a non-empty string")
    base = (root or workspace_root()).resolve()
    cand = Path(value).expanduser()
    if not cand.is_absolute():
        cand = base / cand
    cand = cand.resolve()
    try:
        cand.relative_to(base)
    except ValueError:
        raise BridgeError(
            "Path is outside the allowed workspace (%s). Save/read files there "
            "or set KRITAPILOT_WORKSPACE." % base
        )
    if exts is not None and cand.suffix.lower() not in exts:
        raise BridgeError("Unsupported file type %r; allowed: %s" % (cand.suffix, ", ".join(sorted(exts))))
    return cand


_NAMED = {
    "black": (0, 0, 0, 255), "white": (255, 255, 255, 255), "red": (255, 0, 0, 255),
    "green": (0, 128, 0, 255), "blue": (0, 0, 255, 255), "yellow": (255, 255, 0, 255),
    "orange": (255, 165, 0, 255), "purple": (128, 0, 128, 255), "pink": (255, 192, 203, 255),
    "gray": (128, 128, 128, 255), "grey": (128, 128, 128, 255), "cyan": (0, 255, 255, 255),
    "magenta": (255, 0, 255, 255), "brown": (139, 69, 19, 255), "transparent": (0, 0, 0, 0),
}


def parse_color(value, default=None):
    """Return an (r, g, b, a) tuple of ints 0-255.

    Accepts '#rgb', '#rrggbb', '#rrggbbaa' (CSS order, alpha LAST), a few
    colour names, or a 3/4 element list. None -> `default`.
    """
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        if len(value) not in (3, 4):
            raise BridgeError("colour list needs 3 or 4 numbers")
        vals = [max(0, min(255, int(v))) for v in value]
        return tuple(vals + [255]) if len(vals) == 3 else tuple(vals)
    s = str(value).strip().lower()
    if s in _NAMED:
        return _NAMED[s]
    if s.startswith("#"):
        h = s[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            raise BridgeError("bad colour %r (use #rrggbb or #rrggbbaa)" % value)
        try:
            nums = [int(h[i:i + 2], 16) for i in range(0, len(h), 2)]
        except ValueError:
            raise BridgeError("bad colour %r" % value)
        return tuple(nums + [255]) if len(nums) == 3 else tuple(nums)
    raise BridgeError("bad colour %r (use #rrggbb or #rrggbbaa)" % value)


def i18n(text):
    """Krita's translation function inside Krita (it installs builtins.i18n); the text itself elsewhere.
    Format after translating: i18n("Port %d") % port."""
    import builtins
    fn = getattr(builtins, "i18n", None)
    return fn(text) if callable(fn) and fn is not i18n else text
