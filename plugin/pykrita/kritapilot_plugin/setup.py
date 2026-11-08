"""Getting started: shown the first time the AI Chat panel opens (and from Services > Getting started...).

Three ways in, one click each:
    Start free          chat on Auto with the free models, and turn on the free picture services
    I have an AI account  open Services to sign in (an API key, or the browser for OpenRouter)
    Use my own GPU      find a ComfyUI server on this computer - including the one krita-ai-diffusion runs - and
                        use it for pictures
"""
import html
import os

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
except ImportError:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import config, extension
from .util import i18n

COMFYUI_URL = "http://127.0.0.1:8188"
_RICH = getattr(getattr(Qt, "TextFormat", Qt), "RichText")


def krita_ai_diffusion_installed():
    """True when the krita-ai-diffusion plugin is installed next to KritaPilot (its server is a ComfyUI)."""
    pykrita = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.local/share"), "krita", "pykrita")
    return any(os.path.exists(os.path.join(pykrita, n)) for n in ("ai_diffusion", "ai_diffusion.desktop"))


def needed():
    return extension.setting("setup_done", "") != "1"


class SetupDialog(QDialog):
    def __init__(self, docker):
        super().__init__(docker)
        self.docker = docker
        self.setWindowTitle(i18n("Get started with %s") % config.NAME)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        intro = QLabel("<b>%s</b><br><span style='color:#999'>%s</span>" % (
            html.escape(i18n("How would you like to use AI in Krita?")),
            html.escape(i18n("You can change this any time in AI Chat ▸ Services."))))
        intro.setTextFormat(_RICH)
        lay.addWidget(intro)
        self.choices = {}
        for key, title, text in (
                ("free", i18n("Start free - no account"),
                 i18n("Chat with free AI models right away, and make pictures with the free services "
                      "(Pollinations, AI Horde). Slower and simpler than paid models.")),
                ("account", i18n("I have an AI account"),
                 i18n("Sign in to Gemini, Claude, OpenAI, OpenRouter (with your browser) or 40 other services. "
                      "Auto uses yours first and the free ones when your credit runs out.")),
                ("gpu", i18n("Use my own GPU"),
                 i18n("Make pictures on your computer with ComfyUI - including the server krita-ai-diffusion "
                      "installs. Free and private."))):
            btn = QPushButton(title)
            btn.setStyleSheet("QPushButton { text-align: left; padding: 8px 12px; font-weight: 600; }")
            btn.clicked.connect(lambda _=False, k=key: self.choose(k))
            note = QLabel(text)
            note.setWordWrap(True)
            note.setStyleSheet("color:#999; margin: 0 0 6px 12px")
            lay.addWidget(btn)
            lay.addWidget(note)
            self.choices[key] = btn
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(_RICH)
        self.result_label.hide()
        lay.addWidget(self.result_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.close_btn = QPushButton(i18n("Skip"))
        self.close_btn.clicked.connect(self.finish)
        row.addWidget(self.close_btn)
        lay.addLayout(row)

    def say(self, text, ok=True):
        self.result_label.setText("<span style='color:%s'>%s</span>" % ("#4a4" if ok else "#c96", text))
        self.result_label.show()
        self.close_btn.setText(i18n("Done"))

    def finish(self):
        extension.set_setting("setup_done", "1")
        self.accept()

    def choose(self, key):
        extension.set_setting("chat_provider", "auto")
        if key == "free":
            for name in ("pollinations", "horde"):
                self.docker.sign_in_service(name, self, "on")
            self.say(html.escape(i18n("Done: chat is on Auto with the free models, and free pictures are on. "
                                      "Type what you want in AI Chat.")))
        elif key == "account":
            self.finish()
            self.docker._services()
        else:
            self.test_gpu()

    def test_gpu(self):
        self.say(html.escape(i18n("Looking for ComfyUI on this computer…")))

        def found(r):
            if r.get("ok"):
                body = {"provider": "comfyui", "key": "", "base_url": COMFYUI_URL, "verify": False}
                from . import chat
                self.docker._background(lambda: chat._request("POST", "/signin", body),
                                        lambda res: self.docker._signed(res, "comfyui"))
                self.say(html.escape(i18n("Found it: %s. Pictures are made on your GPU from now on.") % r["detail"]))
            elif krita_ai_diffusion_installed():
                self.say(html.escape(i18n(
                    "krita-ai-diffusion is installed: its ComfyUI server works with KritaPilot too. Start it in "
                    "its panel (Settings ▸ Dockers ▸ AI Image Generation ▸ Configure ▸ Launch), then click "
                    "Use my own GPU again.")), ok=False)
            else:
                self.say(html.escape(i18n(
                    "No ComfyUI is running here. Install ComfyUI (or the krita-ai-diffusion plugin, which sets it "
                    "up for you), start it, then click Use my own GPU again.")), ok=False)

        from . import chat
        self.docker._background(lambda: chat._ensure_service() and chat._request(
            "POST", "/check", {"provider": "comfyui", "base_url": COMFYUI_URL}, timeout=20), found)


def show(docker):
    dlg = SetupDialog(docker)
    dlg.exec()
    return dlg
