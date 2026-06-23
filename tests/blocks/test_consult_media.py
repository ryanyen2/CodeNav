"""U6 — consult media: transient screenshot + persistent url/image, all consult-only."""
from __future__ import annotations

from codoc.blocks.base import Capability
from codoc.blocks.builtins import register_builtins
from codoc.blocks.registry import BlockRegistry
from codoc.model.block import Block, BlockLifecycle


def _registry():
    return register_builtins(BlockRegistry())


def test_media_register_with_consult_only():
    reg = _registry()
    for kind in ("screenshot", "url", "image"):
        p = reg.require(kind)
        assert p.capabilities == frozenset({Capability.CONSULT})
        # consult-only: the loops asking for lift/lower get a clean skip
        assert reg.for_capability(kind, Capability.LOWER) is None
        assert reg.for_capability(kind, Capability.LIFT) is None
        assert reg.for_capability(kind, Capability.CONSULT) is not None


def test_screenshot_is_transient():
    reg = _registry()
    assert reg.require("screenshot").lifecycle is BlockLifecycle.TRANSIENT
    assert reg.require("url").lifecycle is BlockLifecycle.PERSISTENT


def test_consult_text():
    reg = _registry()
    blk = Block(feature_id="f-1", kind="url", content="https://docs.example/spec")
    assert reg.require("url").consult(blk) == "Consult: https://docs.example/spec"
    shot = Block(feature_id="f-1", kind="screenshot", content="bug-42.png")
    assert "bug-42.png" in reg.require("screenshot").consult(shot)


def test_adding_a_new_consult_medium_needs_no_codec():
    """The cheapest extension: a brand-new reference medium is a 4-line plugin."""
    from codoc.blocks.base import BindingMode, BlockPlugin

    class PdfPlugin(BlockPlugin):
        kind = "pdf"
        capabilities = frozenset({Capability.CONSULT})
        binding_mode = BindingMode.AMBIENT

        def consult(self, block):
            return f"Consult PDF: {block.content}"

    reg = _registry()
    reg.register(PdfPlugin())
    assert reg.for_capability("pdf", Capability.CONSULT) is not None
