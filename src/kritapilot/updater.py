"""Update the KritaPilot server from GitHub releases.

    kritapilot --check-update     # JSON: current, latest, newer
    kritapilot --update           # download + verify the newest wheel, then upgrade this installation

On Windows a running program's files are locked, so the upgrade itself is done by a short-lived helper
process that starts after `kritapilot` has exited; its output goes to ~/.kritapilot/update.log.
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

from . import __commit__, __version__, config

REPO = config.REPO
MAX_WHEEL = 50 * 1024 * 1024
TRUSTED_HOSTS = config.UPDATE_HOSTS
UA = {"User-Agent": "kritapilot-updater", "Accept": "application/vnd.github+json"}


class UpdateError(RuntimeError):
    pass


# Runs in its own process after the server has exited. On Windows every running server (AI apps connected
# through MCP, the AI Chat assistant service) locks its files, and pip would then remove the old version and
# fail to write the new one - so it waits for them, and leaves the installation untouched if they stay.
HELPER = r'''
import subprocess, sys, time
import json
log, version, cmds = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])


NAMES = ("kritapilot.exe",)


def running():
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/NH"], capture_output=True, text=True).stdout
    except OSError:
        return False
    return any(n in (out or "").lower() for n in NAMES)


with open(log, "w", encoding="utf-8") as f:
    f.write("updating KritaPilot to %s\n" % version)
    f.flush()
    time.sleep(3)  # let the server that started this exit
    waited = 0
    while running() and waited < 180:
        if waited == 0:
            f.write("waiting for running KritaPilot servers to exit (close AI apps connected to Krita)...\n")
            f.flush()
        time.sleep(5)
        waited += 5
    if running():
        f.write("result: skipped - the KritaPilot server is still running; close the AI apps using it and "
                "update again. "
                "The installed version was left untouched.\n")
        sys.exit(2)
    code = 0
    for cmd in cmds:
        code = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT)
        if code != 0:
            break
    f.write("\nresult: %s\n" % ("updated" if code == 0 else "failed (%d)" % code))
'''


def api_url():
    return os.environ.get("KRITAPILOT_UPDATE_API") or "https://api.github.com/repos/%s/releases/latest" % REPO


def parse_version(v):
    nums = re.findall(r"\d+", str(v or ""))[:4]
    return tuple(int(n) for n in nums) + (0,) * (4 - len(nums))


def update_available(latest, current, latest_commit="", current_commit=""):
    """A newer version - or the same version built from another commit (a release built again, or a copy run
    from source), so an update never depends on the version number alone."""
    if parse_version(latest) != parse_version(current):
        return parse_version(latest) > parse_version(current)
    latest_commit, current_commit = (latest_commit or "").lower(), (current_commit or "").lower()
    if not latest_commit:
        return False  # nothing to compare: the same version is the same release
    return not current_commit or not latest_commit.startswith(current_commit)


def _trusted(url):
    u = urllib.parse.urlsplit(url)
    override = os.environ.get("KRITAPILOT_UPDATE_API")
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
    m = re.match(r"kritapilot-([^-]+)-", wheel["name"])
    latest = m.group(1) if m else (rel.get("tag_name") or "").lstrip("v")
    commit = rel.get("target_commitish") or ""
    return {"current": __version__, "current_commit": __commit__, "latest": latest, "latest_commit": commit[:7],
            "newer": update_available(latest, __version__, commit, __commit__), "url": rel.get("html_url"),
            "asset": wheel}


def _log_path():
    home = config.home()
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
    folder = Path(tempfile.mkdtemp(prefix="kritapilot-update-"))
    wheel = folder / asset["name"]
    wheel.write_bytes(data)
    extra = "[anthropic]" if importlib.util.find_spec("anthropic") else ""
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade",
           str(wheel) + extra]
    cmds = [pip]
    log = _log_path()
    helper = HELPER
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([sys.executable, "-c", helper, str(log), info["latest"], json.dumps(cmds)], creationflags=flags,
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
