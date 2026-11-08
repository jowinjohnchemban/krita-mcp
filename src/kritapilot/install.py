"""`kritapilot install` puts the Krita plugin in place and turns it on; `kritapilot doctor` checks every link from
this command to Krita and the AI services, and says how to fix what is broken."""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import config

PLUGIN = "kritapilot_plugin"
ENABLE_KEY = "enable_%s=true" % PLUGIN


def plugin_source():
    """The plugin shipped inside the package (PyPI) or the repository's copy (GitHub checkout)."""
    here = Path(__file__).resolve().parent
    for d in (here / "krita_plugin", here.parents[1] / "plugin" / "pykrita"):
        if (d / PLUGIN / "__init__.py").is_file() and (d / (PLUGIN + ".desktop")).is_file():
            return d
    return None


def krita_data_dirs():
    """Where Krita keeps its resources (pykrita lives inside), most likely first."""
    home = Path.home()
    if sys.platform.startswith("win"):
        return [Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "krita"]
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "krita"]
    return [home / ".local" / "share" / "krita", home / ".var" / "app" / "org.kde.krita" / "data" / "krita"]


def kritarc_paths():
    home = Path.home()
    if sys.platform.startswith("win"):
        return [Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "kritarc"]
    if sys.platform == "darwin":
        return [home / "Library" / "Preferences" / "kritarc"]
    return [home / ".config" / "kritarc", home / ".var" / "app" / "org.kde.krita" / "config" / "kritarc"]


def find_pykrita():
    dirs = krita_data_dirs()
    return next((d / "pykrita" for d in dirs if d.exists()), None)


def find_kritarc():
    return next((p for p in kritarc_paths() if p.is_file()), None)


def krita_running():
    """True or False; None when it cannot be told."""
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq krita.exe", "/NH"], capture_output=True,
                                 text=True, timeout=10).stdout
            return "krita.exe" in out.lower()
        return subprocess.run(["pgrep", "-x", "krita"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


def installed_version(pykrita):
    try:
        text = (Path(pykrita) / PLUGIN / "config.py").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else "?"


def is_enabled(kritarc):
    try:
        lines = Path(kritarc).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    section = None
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
        elif section == "[python]" and s.replace(" ", "").lower() == ENABLE_KEY:
            return True
    return False


def setting(kritarc, section, key):
    """One value from kritarc, or None."""
    try:
        lines = Path(kritarc).read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, TypeError):
        return None
    current = None
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            current = s[1:-1]
        elif current == section and "=" in s and s.split("=", 1)[0].strip() == key:
            return s.split("=", 1)[1].strip()
    return None


def enable(kritarc):
    """Turn the plugin on in kritarc, leaving every other line as it is."""
    p = Path(kritarc)
    text = ""
    if p.exists():
        with open(p, encoding="utf-8", errors="replace", newline="") as f:  # keep Krita's line endings
            text = f.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    key = "enable_%s" % PLUGIN
    start = next((i for i, l in enumerate(lines) if l.strip() == "[python]"), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["[python]", ENABLE_KEY]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
        found = next((i for i in range(start + 1, end) if lines[i].split("=", 1)[0].strip() == key), None)
        if found is not None:
            lines[found] = ENABLE_KEY
        else:
            # after the section's last setting, before the blank line that separates sections
            at = end
            while at > start + 1 and not lines[at - 1].strip():
                at -= 1
            lines.insert(at, ENABLE_KEY)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(newline.join(lines) + newline, encoding="utf-8", newline="")  # keep Krita's line endings


def copy_plugin(src, pykrita):
    """Replace the plugin folder in one swap, rolling back if it fails."""
    pykrita = Path(pykrita)
    pykrita.mkdir(parents=True, exist_ok=True)
    staging, backup, target = pykrita / (".%s.new" % PLUGIN), pykrita / (".%s.old" % PLUGIN), pykrita / PLUGIN
    for d in (staging, backup):
        shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(Path(src) / PLUGIN, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    try:
        if target.exists():
            target.rename(backup)
        staging.rename(target)
    except OSError:
        if backup.exists() and not target.exists():
            backup.rename(target)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.copy2(Path(src) / (PLUGIN + ".desktop"), pykrita / (PLUGIN + ".desktop"))
    shutil.rmtree(backup, ignore_errors=True)
    return target


def install(args):
    src = plugin_source()
    if src is None:
        raise SystemExit("This copy of %s has no Krita plugin inside. Reinstall it (pip install -U %s) or "
                         "download the plugin zip from %s/releases" % (config.NAME, config.PACKAGE, config.HOMEPAGE))
    pykrita = Path(args.dest) if args.dest else find_pykrita()
    if pykrita is None:
        raise SystemExit("Krita's resource folder was not found - has Krita been started once? Or in Krita: "
                         "Settings > Manage Resources > Open Resource Folder, then run: "
                         "kritapilot install --dest <that folder>/pykrita")
    before = installed_version(pykrita)
    target = copy_plugin(src, pykrita)
    print("Plugin %s installed in %s%s" % (config.VERSION, target,
                                           " (was %s)" % before if before and before != config.VERSION else ""))
    if args.no_enable:
        return
    kritarc = Path(args.kritarc) if args.kritarc else (find_kritarc() or kritarc_paths()[0])
    running = krita_running() if not args.kritarc else False
    if is_enabled(kritarc):
        print("The plugin is turned on in Krita.")
    elif running:
        # Krita writes kritarc when it closes, which would undo the change
        print("Krita is open, so the plugin could not be turned on for you. Either close Krita and run "
              "`kritapilot install` again, or in Krita: Settings > Configure Krita > Python Plugin Manager > "
              "tick %s.\n" % config.NAME)
    else:
        enable(kritarc)
        print("The plugin is turned on in Krita (%s)." % kritarc)
    print("Next: %s Krita. The AI Chat is in Settings > Dockers > AI Chat." % ("restart" if running else "start"))


# ------------------------------------------------------------------------------------------------- doctor
def _bridge(port=None):
    from .bridge_client import BridgeClient
    c = BridgeClient(port=port, timeout=10)
    return c, c.call("app.info")


def _service(token):
    port = int(os.environ.get(config.AGENT_PORT_ENV) or config.AGENT_PORT)
    req = urllib.request.Request("http://%s:%d/health" % (config.BRIDGE_HOST, port),
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310 - loopback only
        return port, json.loads(r.read().decode("utf-8"))


def checks():
    """(ok, what, detail) for each link; ok is True, False, or None for 'not checked'."""
    out = [(True, "%s %s" % (config.NAME, config.version_text()), "Python %s, %s" % (
        sys.version.split()[0], sys.executable))]
    pykrita = find_pykrita()
    have = installed_version(pykrita) if pykrita else None
    if not have:
        out.append((False, "Krita plugin not installed", "run: kritapilot install"))
    elif have != config.VERSION:
        out.append((False, "Krita plugin %s, this command is %s" % (have, config.VERSION),
                    "run: kritapilot install (or update from the AI Chat ▸ About)"))
    else:
        out.append((True, "Krita plugin %s" % have, str(pykrita / PLUGIN)))
    kritarc = find_kritarc()
    if kritarc is None:
        out.append((None, "Krita settings not found", "start Krita once"))
    elif is_enabled(kritarc):
        out.append((True, "Plugin turned on in Krita", str(kritarc)))
    else:
        out.append((False, "Plugin not turned on in Krita",
                    "Settings > Configure Krita > Python Plugin Manager > tick %s, then restart Krita" % config.NAME))
    running = krita_running()
    out.append((running, "Krita is running" if running else ("Krita is not running" if running is False
                                                            else "Could not tell if Krita is running"), ""))
    token_file = config.home() / "token"
    token = os.environ.get("KRITAPILOT_TOKEN") or (token_file.read_text(encoding="utf-8").strip()
                                                   if token_file.is_file() else "")
    out.append((bool(token), "Bridge token" if token else "No bridge token",
                str(token_file) if token else "the plugin makes it when Krita starts with it turned on"))
    from .bridge_client import BridgeClient
    tried = BridgeClient().port
    set_port = setting(kritarc, config.SETTINGS, "port") if kritarc else None
    try:
        client, info = _bridge()
        moved = None
    except Exception as e:  # noqa: BLE001
        client = info = None
        moved, error = None, e
        if set_port and set_port.isdigit() and int(set_port) != tried:
            try:
                client, info = _bridge(int(set_port))
                moved = int(set_port)
            except Exception:  # noqa: BLE001
                pass
    if info is not None:
        docs = info.get("documents") or []
        out.append((True, "Bridge answers on port %d" % client.port, "Krita %s, plugin %s, %d document%s open" % (
            info.get("krita_version"), info.get("bridge_version"), len(docs), "" if len(docs) == 1 else "s")))
        if moved:
            out.append((False, "AI apps look for the bridge on port %d, Krita uses %d" % (tried, moved),
                        "restart Krita with plugin %s (it tells AI apps its port), or set %s=%d in your AI app"
                        % (config.VERSION, config.BRIDGE_PORT_ENV, moved)))
    else:
        out.append((False, "Bridge does not answer on port %d" % tried, str(error)))
    if token:
        try:
            port, health = _service(token)
            providers = health.get("providers") or {}
            ready = [n for n, s in providers.items() if n != "auto" and s.get("chat") and s.get("ready")]
            pics = [n for n, s in providers.items() if not s.get("chat") and s.get("ready")]
            out.append((True, "AI Chat service %s on port %d" % (health.get("version"), port),
                        "chat: %s; pictures: %s" % (", ".join(ready) or "none", ", ".join(pics) or "none")))
        except Exception as e:  # noqa: BLE001
            out.append((None, "AI Chat service not running", "it starts when the AI Chat panel opens (%s)" % e))
    return out


def doctor(_args):
    results = checks()
    mark = {True: "ok ", False: "!! ", None: "-- "}
    for ok, what, detail in results:
        print("%s %s%s" % (mark[ok], what, ("\n      " + detail) if detail else ""))
    bad = sum(1 for r in results if r[0] is False)
    print("\n%s" % ("Everything works." if not bad else "%d problem%s - see the lines marked !!" % (
        bad, "" if bad == 1 else "s")))
    if bad:
        raise SystemExit(1)

