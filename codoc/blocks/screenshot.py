"""Consult media (U6): transient bug-screenshot + persistent reference URL/image.

These are the cheapest media to add — ``consult``-only, no codec (KTD5). They
prove that "register a new medium" is trivial when the medium only feeds the agent
context and never round-trips to code.

- **screenshot** — TRANSIENT + AMBIENT. A bug screenshot dropped in a comment
  thread: the host attaches it to a one-shot ``steer`` (the existing
  drained-once steering channel), so realization reads it as consultation and it
  is **discarded on the next render — never a durable block** (it is excluded from
  the sidecar ``blocks`` slice by being TRANSIENT). This is the transient lifecycle
  reusing infrastructure, not new machinery.
- **url** — PERSISTENT + AMBIENT. A reference link the agent WebFetches before
  implementing (the existing ``Consult:`` mechanism, now a first-class block).
- **image** — PERSISTENT + BOUND. A reference image (e.g. a UI mock) that lives in
  the doc; consult-only for v1 (a ``lift`` re-render path is future work).

None declare ``lower``: editing a reference implies no code change, so Loop B's
capability-scoped dispatch returns ``None`` and queues nothing.
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


class UrlPlugin(BlockPlugin):
    kind = "url"
    capabilities = frozenset({Capability.CONSULT})
    binding_mode = BindingMode.AMBIENT
    lifecycle = BlockLifecycle.PERSISTENT

    def consult(self, block: Block) -> str:
        return f"Consult: {(block.content or '').strip()}"


class ImagePlugin(BlockPlugin):
    kind = "image"
    capabilities = frozenset({Capability.CONSULT})
    binding_mode = BindingMode.BOUND
    lifecycle = BlockLifecycle.PERSISTENT

    def consult(self, block: Block) -> str:
        ref = (block.content or "").strip() or "(image)"
        return f"Reference image for this feature: {ref}"


def register_media(registry) -> None:
    """Register the consult-only reference media onto a registry (idempotent)."""
    for plugin in (ScreenshotPlugin(), UrlPlugin(), ImagePlugin()):
        if registry.get(plugin.kind) is None:
            registry.register(plugin)
