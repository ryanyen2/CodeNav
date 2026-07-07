"""U6 — consult media: transient screenshot + the persistent reference-media
family (url/pdf/image/latex, codoc/blocks/reference.py). url/pdf additionally
declare LIFT (fetch/extract real content once — see test_reference_plugins.py);
screenshot/image/latex stay consult-only."""
from __future__ import annotations

from codoc.blocks.base import Capability
from codoc.blocks.builtins import register_builtins
from codoc.blocks.registry import BlockRegistry
from codoc.model.block import Block, BlockLifecycle


def _registry():
    return register_builtins(BlockRegistry())


def test_consult_only_media_declares_no_lift_or_lower():
    reg = _registry()
    for kind in ("screenshot", "image", "latex"):
        p = reg.require(kind)
        assert p.capabilities == frozenset({Capability.CONSULT})
        assert reg.for_capability(kind, Capability.LOWER) is None
        assert reg.for_capability(kind, Capability.LIFT) is None
        assert reg.for_capability(kind, Capability.CONSULT) is not None


def test_url_and_pdf_additionally_declare_lift():
    reg = _registry()
    for kind in ("url", "pdf"):
        p = reg.require(kind)
        assert p.capabilities == frozenset({Capability.LIFT, Capability.CONSULT})
        assert reg.for_capability(kind, Capability.LOWER) is None
        assert reg.for_capability(kind, Capability.LIFT) is not None
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

    class AudioPlugin(BlockPlugin):
        kind = "audio"
        capabilities = frozenset({Capability.CONSULT})
        binding_mode = BindingMode.AMBIENT

        def consult(self, block):
            return f"Consult audio: {block.content}"

    reg = _registry()
    reg.register(AudioPlugin())
    assert reg.for_capability("audio", Capability.CONSULT) is not None
