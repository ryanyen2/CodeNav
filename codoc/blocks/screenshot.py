"""Transient consult media (U6): the bug-screenshot dropped in a comment thread.

This is the cheapest medium to add — ``consult``-only, no codec (KTD5). It proves
the transient lifecycle: the host attaches the screenshot to a one-shot ``steer``
(the existing drained-once steering channel), so realization reads it as
consultation and it is **discarded on the next render — never a durable block**
(it is excluded from the sidecar ``blocks`` slice by being TRANSIENT).

Persistent reference media (url / pdf / image / latex) live in
``codoc/blocks/reference.py``.
"""
from __future__ import annotations

from codoc.blocks.base import BindingMode, BlockPlugin, Capability
from codoc.model.block import Block, BlockLifecycle


class ScreenshotPlugin(BlockPlugin):
    kind = "screenshot"
    capabilities = frozenset({Capability.CONSULT})
    binding_mode = BindingMode.AMBIENT
    lifecycle = BlockLifecycle.TRANSIENT

    def consult(self, block: Block) -> str:
        ref = (block.content or "").strip() or "(screenshot)"
        return f"Consult this screenshot of the problem before implementing: {ref}"


def register_media(registry) -> None:
    """Register the transient screenshot plugin onto a registry (idempotent)."""
    if registry.get(ScreenshotPlugin.kind) is None:
        registry.register(ScreenshotPlugin())
