"""KritaPilot plugin entry point.

Registers the extension only when running inside Krita, so the Qt-free
modules (protocol, util) stay importable for tests.
"""
try:
    import krita  # noqa: F401
except ImportError:  # running outside Krita (tests)
    krita = None

if krita is not None:
    from .extension import register

    register()
