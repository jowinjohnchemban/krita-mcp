"""KritaPilot: the Krita MCP server and AI assistant. Global settings live in config.py."""
from .config import BUILD as __build__
from .config import COMMIT as __commit__
from .config import VERSION as __version__
from .config import version_text

__all__ = ["__build__", "__commit__", "__version__", "version_text"]
