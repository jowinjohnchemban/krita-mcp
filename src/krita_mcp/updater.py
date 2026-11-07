"""Update the krita-mcp server from GitHub releases.

    krita-mcp --check-update     # JSON: current, latest, newer
    krita-mcp --update           # download + verify the newest wheel, then upgrade this installation

On Windows a running program's files are locked, so the upgrade itself is done by a short-lived helper
process that starts after `krita-mcp` has exited; its output goes to ~/.krita-mcp/update.log.
"""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__

REPO = "jowinjohnchemban/krita-mcp"
MAX_WHEEL = 50 * 1024 * 1024
TRUSTED_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com", "github-releases.githubusercontent.com")
UA = {"User-Agent": "krita-mcp-updater", "Accept": "application/vnd.github+json"}


class UpdateError(RuntimeError):
    pass


def api_url():
    return os.environ.get("KRITA_MCP_UPDATE_API") or "https://api.github.com/repos/%s/releases/latest" % REPO


def parse_version(v):
    nums = re.findall(r"\d+", str(v or ""))[:4]
    return tuple(int(n) for n in nums) + (0,) * (4 - len(nums))


def _trusted(url):
    u = urllib.parse.urlsplit(url)
    override = os.environ.get("KRITA_MCP_UPDATE_API")
    if override and u.hostname == urllib.parse.urlsplit(override).hostname:
        return True
    return u.scheme == "https" and (u.hostname or "") in TRUSTED_HOSTS


def _get(url, limit, headers=None):
    if not _trusted(url):
        raise UpdateError("refusing to download from %s" % urllib.parse.urlsplit(url).hostname)
    req = urllib.request.Request(url, headers=dict(UA, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            if not _trusted(r.geturl()):
                raise UpdateError("download was redirected to an untrusted host")
            data = r.read(limit + 1)
    except UpdateError:
        raise
    except Exception as e:  # noqa: BLE001
        raise UpdateError("cannot reach GitHub: %s" % e) from None
    if len(data) > limit:
        raise UpdateError("download larger than expected")
    return data


def check():
    try:
        rel = json.loads(_get(api_url(), 2 * 1024 * 1024))
    except ValueError:
        raise UpdateError("GitHub returned an unexpected answer") from None
    wheel = next((a for a in rel.get("assets") or [] if (a.get("name") or "").endswith(".whl")), None)
    if wheel is None:
        raise UpdateError("the latest release has no server wheel")
    m = re.match(r"krita_mcp-([^-]+)-", wheel["name"])
    latest = m.group(1) if m else rel.get("tag_name")
    return {"current": __version__, "latest": latest, "newer": parse_version(latest) > parse_version(__version__),
            "url": rel.get("html_url"), "asset": wheel}


def _log_path():
    home = Path(os.environ.get("KRITA_MCP_HOME") or (Path.home() / ".krita-mcp")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    return home / "update.log"


def update(force=False):
    """Download and verify the newest wheel, then upgrade this installation from a helper process."""
    info = check()
    if not info["newer"] and not force:
        return dict(info, started=False, message="already up to date")
    asset = info["asset"]
    data = _get(asset["browser_download_url"], MAX_WHEEL, {"Accept": "application/octet-stream"})
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:") and hashlib.sha256(data).hexdigest() != digest.split(":", 1)[1].lower():
        raise UpdateError("the download does not match the checksum GitHub published; not installed")
    folder = Path(tempfile.mkdtemp(prefix="krita-mcp-update-"))
    wheel = folder / asset["name"]
    wheel.write_bytes(data)
    extra = "[anthropic]" if importlib.util.find_spec("anthropic") else ""
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade",
           str(wheel) + extra]
    log = _log_path()
    helper = ("import subprocess, sys, time\n"
              "time.sleep(3)  # let krita-mcp exit so Windows releases its files\n"
              "with open(sys.argv[1], 'w', encoding='utf-8') as f:\n"
              "    f.write('updating krita-mcp to %s\\n' % sys.argv[2]); f.flush()\n"
              "    code = subprocess.call(sys.argv[3:], stdout=f, stderr=subprocess.STDOUT)\n"
              "    f.write('\\nresult: %s\\n' % ('updated' if code == 0 else 'failed (%d)' % code))\n")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([sys.executable, "-c", helper, str(log), info["latest"]] + pip, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)
    return dict(info, started=True, log=str(log),
                message="installing %s in the background; see %s" % (info["latest"], log))


def main(action):
    try:
        out = check() if action == "check" else update()
        out.pop("asset", None)
        print(json.dumps(out))
        return 0
    except UpdateError as e:
        print(json.dumps({"error": str(e), "current": __version__}))
        return 1
