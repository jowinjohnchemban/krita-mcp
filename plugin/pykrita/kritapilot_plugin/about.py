"""About KritaPilot: the version and build, what it runs on, where its files are, and the project's links.

Opened from the AI Bridge (MCP) panel and from Tools > Scripts > KritaPilot: About. **Copy details** puts the
same facts on the clipboard for a bug report (nothing secret is in them: no keys, no token).
"""
import html
import platform
import threading

try:
    from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR, Qt, QTimer
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
except ImportError:
    from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR, Qt, QTimer
    from PyQt5.QtGui import QGuiApplication
    from PyQt5.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import config
from .util import i18n

LINKS = [("Website and source", config.HOMEPAGE), ("Documentation", config.DOCS_URL),
         ("What's new", config.CHANGELOG_URL), ("PyPI", config.PYPI_URL),
         ("Report a problem", config.ISSUES_URL), ("Licence: MIT", config.LICENSE_URL)]
_RICH = getattr(getattr(Qt, "TextFormat", Qt), "RichText")
_SELECTABLE = getattr(getattr(Qt, "TextInteractionFlag", Qt), "TextBrowserInteraction")


def krita_version():
    try:
        from krita import Krita
        return Krita.instance().version()
    except Exception:  # noqa: BLE001 - outside Krita (tests)
        return "?"


def facts(server=None):
    """(label, value) pairs: the plugin, the server, Krita, the system and the folders."""
    from . import extension
    return [
        (i18n("Plugin"), config.version_text()),
        (i18n("AI Chat assistant service"), server or i18n("not running")),
        (i18n("Krita"), krita_version()),
        (i18n("Python"), platform.python_version()),
        (i18n("Qt / PyQt"), "%s / %s" % (QT_VERSION_STR, PYQT_VERSION_STR)),
        (i18n("System"), "%s (%s)" % (platform.platform(), platform.machine())),
        (i18n("Data folder"), str(config.home())),
        (i18n("MCP bridge"), "%s:%s" % (config.BRIDGE_HOST, extension.bridge_port())),
    ]


def details_text(server=None):
    """The facts as plain text, for a bug report."""
    return "%s %s\n%s" % (config.NAME, config.version_text(),
                          "\n".join("%s: %s" % kv for kv in facts(server)[1:]))


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n("About %s") % config.NAME)
        self.setMinimumWidth(460)
        self.server = None
        lay = QVBoxLayout(self)
        title = QLabel("<span style='font-size:20pt; font-weight:600'>%s</span><br>"
                       "<span style='color:#999'>%s</span>" % (
                           config.NAME, html.escape(i18n("The Krita MCP server and AI assistant"))))
        title.setTextFormat(_RICH)
        lay.addWidget(title)
        self.table = QLabel()
        self.table.setTextFormat(_RICH)
        self.table.setTextInteractionFlags(_SELECTABLE)
        lay.addWidget(self.table)
        links = QLabel(" · ".join('<a href="%s">%s</a>' % (url, html.escape(i18n(text))) for text, url in LINKS))
        links.setTextFormat(_RICH)
        links.setWordWrap(True)
        links.setOpenExternalLinks(True)
        lay.addWidget(links)
        row = QHBoxLayout()
        self.copy_btn = QPushButton(i18n("Copy details"))
        self.copy_btn.setToolTip(i18n("For a bug report: versions and folders, no keys"))
        self.copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(details_text(self.server)))
        close = QPushButton(i18n("Close"))
        close.clicked.connect(self.accept)
        row.addWidget(self.copy_btn)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self._show()
        self._ask_server()

    def _show(self):
        rows = "".join("<tr><td style='color:#999; padding-right:12px'>%s</td><td>%s</td></tr>" % (
            html.escape(k), html.escape(str(v))) for k, v in facts(self.server))
        self.table.setText("<table>%s</table>" % rows)

    def _ask_server(self):
        """The AI Chat assistant service's version, if it is running (asked off the main thread)."""
        found = {}

        def ask():
            try:
                from . import chat
                found["version"] = chat._request("GET", "/health", timeout=2).get("version")
            except Exception:  # noqa: BLE001 - not running
                pass

        worker = threading.Thread(target=ask, daemon=True)
        worker.start()

        def done():
            if worker.is_alive():
                return QTimer.singleShot(200, done)
            if found.get("version"):
                self.server = found["version"]
                self._show()
        QTimer.singleShot(200, done)


def show(parent=None):
    dlg = AboutDialog(parent)
    dlg.exec()
    return dlg
