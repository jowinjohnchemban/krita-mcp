"""Over-the-air updates from GitHub releases (no Qt here: called from worker threads).

The plugin checks github.com/<REPO>/releases/latest, and when a newer release exists can download its plugin
zip, verify it (SHA-256 published by GitHub, safe paths only) and swap it in atomically. The new version is
loaded when Krita restarts. The server is updated with `kritapilot --update`.
"""
import hashlib
import io
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

try:
    from . import config
except ImportError:  # loaded as a single file (tests, install scripts): read config.py beside it
    import importlib.util as _iu
    import os as _os
    _spec = _iu.spec_from_file_location("kritapilot_plugin_config",
                                        _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "config.py"))
    config = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(config)

REPO = config.REPO
MAX_ZIP = 50 * 1024 * 1024
TRUSTED_HOSTS = config.UPDATE_HOSTS
PACKAGE = config.PACKAGE_DIR
UA = {"User-Agent": "kritapilot-updater", "Accept": "application/vnd.github+json"}


class UpdateError(RuntimeError):
    pass


def api_url():
    return os.environ.get("KRITAPILOT_UPDATE_API") or "https://api.github.com/repos/%s/releases/latest" % REPO


def parse_version(v):
    """'2026.11.7', 'v1.0.4', '2026.11.07' -> comparable tuples."""
    nums = re.findall(r"\d+", str(v or ""))[:4]
    return tuple(int(n) for n in nums) + (0,) * (4 - len(nums))


def is_newer(candidate, current):
    return parse_version(candidate) > parse_version(current)


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
    host = urllib.parse.urlsplit(url).hostname or ""
    override = os.environ.get("KRITAPILOT_UPDATE_API")
    if override and host == urllib.parse.urlsplit(override).hostname:
        return True  # tests / a mirror explicitly configured by the user
    return urllib.parse.urlsplit(url).scheme == "https" and host in TRUSTED_HOSTS


def _get(url, limit=MAX_ZIP, headers=None):
    if not _trusted(url):
        raise UpdateError("refusing to download from %s" % urllib.parse.urlsplit(url).hostname)
    req = urllib.request.Request(url, headers=dict(UA, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            if not _trusted(r.geturl()):  # after redirects
                raise UpdateError("download was redirected to an untrusted host")
            data = r.read(limit + 1)
    except UpdateError:
        raise
    except Exception as e:  # noqa: BLE001 - network problems are reported, never crash Krita
        raise UpdateError("cannot reach GitHub: %s" % e) from None
    if len(data) > limit:
        raise UpdateError("download larger than expected")
    return data


def latest():
    """The newest release: version, tag, page, notes, and its plugin zip / server wheel assets."""
    try:
        rel = json.loads(_get(api_url(), limit=2 * 1024 * 1024))
    except ValueError:
        raise UpdateError("GitHub returned an unexpected answer") from None
    assets = {}
    version = None
    for a in rel.get("assets") or []:
        name = a.get("name") or ""
        m = re.fullmatch(r"kritapilot-plugin-(.+)\.zip", name)
        if m:
            version = m.group(1)
            assets["plugin"] = a
        elif name.endswith(".whl") and name.startswith("kritapilot-"):
            assets["server"] = a
    if "plugin" not in assets:
        raise UpdateError("the latest release has no plugin zip")
    return {"version": version or (rel.get("tag_name") or "").lstrip("v"), "tag": rel.get("tag_name"),
            "commit": rel.get("target_commitish") or "",
            "url": rel.get("html_url"), "notes": (rel.get("body") or "")[:4000], "assets": assets}


def download(asset):
    data = _get(asset["browser_download_url"], headers={"Accept": "application/octet-stream"})
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        if hashlib.sha256(data).hexdigest() != digest.split(":", 1)[1].lower():
            raise UpdateError("the download does not match the checksum GitHub published; not installed")
    elif asset.get("size") and asset["size"] != len(data):
        raise UpdateError("the download is incomplete; not installed")
    return data


def _check_zip(zf):
    names = zf.namelist()
    for n in names:
        p = Path(n)
        if p.is_absolute() or ".." in p.parts or n.startswith(("/", "\\")):
            raise UpdateError("unsafe path in the update: %s" % n)
        if not (n == PACKAGE + ".desktop" or n.startswith(PACKAGE + "/")):
            raise UpdateError("unexpected file in the update: %s" % n)
    if PACKAGE + "/__init__.py" not in names or PACKAGE + ".desktop" not in names:
        raise UpdateError("the update is not a KritaPilot plugin")


def install_plugin(data, pykrita=None):
    """Swap the plugin folder for the one in `data` (a release zip). Atomic with rollback; active after a
    Krita restart. Returns the installed folder."""
    pykrita = Path(pykrita) if pykrita else Path(__file__).resolve().parent.parent
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError("the download is not a valid zip") from None
    _check_zip(zf)
    staging = pykrita / (".%s.new" % PACKAGE)
    backup = pykrita / (".%s.old" % PACKAGE)
    target = pykrita / PACKAGE
    for d in (staging, backup):
        shutil.rmtree(d, ignore_errors=True)
    staging.mkdir(parents=True)
    zf.extractall(staging)
    new_pkg = staging / PACKAGE
    if not (new_pkg / "__init__.py").is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("the update is incomplete")
    try:
        if target.exists():
            target.rename(backup)
        new_pkg.rename(target)
        shutil.copy2(staging / (PACKAGE + ".desktop"), pykrita / (PACKAGE + ".desktop"))
    except OSError as e:
        if backup.exists() and not target.exists():
            backup.rename(target)  # roll back
        raise UpdateError("could not replace the plugin files: %s" % e) from None
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    return target
