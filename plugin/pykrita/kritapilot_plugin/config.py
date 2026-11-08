"""The KritaPilot plugin's global settings, in one place: names, links, the version, addresses and folders.

Releases stamp VERSION, BUILD and COMMIT (scripts/release_version.py); a copy run from source keeps the values
below and shows "1.0.0 (dev)". The server has a twin, kritapilot/config.py: the shared values must match, and a
test checks that they do. No Qt or Krita imports here, so every part of the plugin can read it.
"""
import os
from pathlib import Path

# ---- names and links
NAME = "KritaPilot"
PACKAGE = "kritapilot"  # on PyPI and for `import`
COMMAND = "kritapilot"  # the server command
REPO = "jowinjohnchemban/kritapilot"
HOMEPAGE = "https://github.com/" + REPO
DOCS_URL = HOMEPAGE + "/tree/main/docs"
CHANGELOG_URL = HOMEPAGE + "/blob/main/CHANGELOG.md"
ISSUES_URL = HOMEPAGE + "/issues/new/choose"
LICENSE_URL = HOMEPAGE + "/blob/main/LICENSE"
PYPI_URL = "https://pypi.org/p/" + PACKAGE

# ---- the plugin in Krita
PACKAGE_DIR = "kritapilot_plugin"  # the plugin's folder in pykrita; Krita knows the plugin by it
SETTINGS = "kritapilot"  # the Krita settings group the plugin's settings are saved under
UPDATE_EVERY = 6 * 3600  # seconds between automatic update checks
SKILLS_SITE = "https://skills.sh/"

# ---- version (stamped by releases)
VERSION = "1.0.0"  # MAJOR.MINOR.PATCH; each release stamps the next PATCH
BUILD = "0"  # the build id: the UTC build date and the commit, YYYYMMDD-COMMIT; "0" = dev
COMMIT = ""  # the git commit it was built from (updates also compare it)

# ---- addresses and folders (each can be changed with the environment variable named here)
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 5679  # the Krita plugin's bridge, which AI apps connect to
BRIDGE_PORT_ENV = "KRITAPILOT_PORT"
AGENT_PORT = 5680  # the AI Chat assistant service
AGENT_PORT_ENV = "KRITAPILOT_AGENT_PORT"
HOME_DIR = ".kritapilot"  # the data folder in your home folder: keys, logs, skills and the token
HOME_ENV = "KRITAPILOT_HOME"
UPDATE_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com",
                "release-assets.githubusercontent.com", "github-releases.githubusercontent.com")


def home():
    """The data folder: KRITAPILOT_HOME, else ~/.kritapilot."""
    return Path(os.environ.get(HOME_ENV) or (Path.home() / HOME_DIR)).expanduser()


def version_text():
    """"1.0.4 (build 20261108-057a829)" for a release; "1.0.0 (dev)" for a copy run from source."""
    if BUILD in ("", "0"):
        return "%s (dev)" % VERSION
    return "%s (build %s)" % (VERSION, BUILD)
