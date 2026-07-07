"""Register the built-in reference plugins onto a registry.

Kept separate from :mod:`codoc.blocks.registry` so importing the registry type
never drags in the concrete plugins (and their deps). The loops call
:func:`ensure_builtins` to get a registry with prose (+ diagram, screenshot, and
the consult media as those units land) registered exactly once.
"""
from __future__ import annotations

from codoc.blocks.registry import BlockRegistry, default_registry

_loaded = False


def register_builtins(registry: BlockRegistry) -> BlockRegistry:
    """Idempotently register every available reference plugin onto ``registry``."""
    from codoc.blocks.prose import ProsePlugin

    if registry.get("prose") is None:
        registry.register(ProsePlugin())

    # Diagram (U5), screenshot (U6), and the reference-media family (url/pdf/
    # image/latex) register here. Each registration is independently guarded so a
    # missing optional dep (e.g. no `media` extras) never blocks the others.
    try:
        from codoc.blocks.diagram import DiagramPlugin
        if registry.get("diagram") is None:
            registry.register(DiagramPlugin())
    except ImportError:
        pass
    try:
        from codoc.blocks.screenshot import register_media
        register_media(registry)
    except ImportError:
        pass
    try:
        from codoc.blocks.reference import ImagePlugin, LatexPlugin, PdfPlugin, UrlPlugin
        for plugin_cls in (UrlPlugin, PdfPlugin, ImagePlugin, LatexPlugin):
            if registry.get(plugin_cls.kind) is None:
                registry.register(plugin_cls())
    except ImportError:
        pass
    return registry


def ensure_builtins() -> BlockRegistry:
    """Return the process-wide :data:`default_registry`, builtins registered once."""
    global _loaded
    if not _loaded:
        register_builtins(default_registry)
        _loaded = True
    return default_registry
