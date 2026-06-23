"""U1 — the block-plugin contract: capability declaration, registration validation,
and capability-scoped dispatch."""
from __future__ import annotations

import pytest

from codoc.blocks.base import (
    BindingMode,
    BlockPlugin,
    Capability,
    LiftContext,
    LiftResult,
    LowerContext,
    LowerResult,
)
from codoc.blocks.registry import (
    BlockRegistry,
    PluginContractError,
    UnknownBlockKind,
)
from codoc.model.block import Block
from codoc.model.feature import Feature


# ── fixtures: minimal plugins exercising each capability subset ──
class LiftLowerPlugin(BlockPlugin):
    kind = "diagramish"
    capabilities = frozenset({Capability.LIFT, Capability.LOWER})

    def lift(self, ctx: LiftContext) -> LiftResult:
        return LiftResult.refresh("graph")

    def lower(self, ctx: LowerContext) -> LowerResult:
        return LowerResult.directive("do it")


class ConsultOnlyPlugin(BlockPlugin):
    kind = "url"
    capabilities = frozenset({Capability.CONSULT})
    binding_mode = BindingMode.AMBIENT

    def consult(self, block: Block) -> str:
        return f"fetch {block.content}"


class BrokenPlugin(BlockPlugin):
    # declares LOWER but never overrides lower()
    kind = "broken"
    capabilities = frozenset({Capability.LOWER})


def test_registers_and_resolves_by_kind():
    reg = BlockRegistry()
    reg.register(LiftLowerPlugin())
    assert reg.get("diagramish") is not None
    assert reg.kinds() == ["diagramish"]


def test_unknown_kind_raises_on_require():
    reg = BlockRegistry()
    with pytest.raises(UnknownBlockKind):
        reg.require("nope")
    assert reg.get("nope") is None


def test_declared_capability_without_method_fails_registration():
    reg = BlockRegistry()
    with pytest.raises(PluginContractError):
        reg.register(BrokenPlugin())


def test_empty_kind_or_no_capabilities_fails():
    reg = BlockRegistry()

    class NoKind(BlockPlugin):
        capabilities = frozenset({Capability.CONSULT})

        def consult(self, block):
            return ""

    class NoCaps(BlockPlugin):
        kind = "x"

    with pytest.raises(PluginContractError):
        reg.register(NoKind())
    with pytest.raises(PluginContractError):
        reg.register(NoCaps())


def test_capability_scoped_dispatch():
    reg = BlockRegistry()
    reg.register(LiftLowerPlugin())
    reg.register(ConsultOnlyPlugin())

    # diagramish does lift + lower, not consult
    assert reg.for_capability("diagramish", Capability.LIFT) is not None
    assert reg.for_capability("diagramish", Capability.LOWER) is not None
    assert reg.for_capability("diagramish", Capability.CONSULT) is None

    # url is consult-only — the loops asking for lift/lower get a clean skip
    assert reg.for_capability("url", Capability.CONSULT) is not None
    assert reg.for_capability("url", Capability.LIFT) is None
    assert reg.for_capability("url", Capability.LOWER) is None

    # unknown kind → None everywhere, no raise
    assert reg.for_capability("ghost", Capability.LIFT) is None


def test_consult_only_plugin_runs():
    reg = BlockRegistry()
    reg.register(ConsultOnlyPlugin())
    p = reg.require("url")
    blk = Block(feature_id="f-1", kind="url", content="https://example.com")
    assert p.consult(blk) == "fetch https://example.com"


def test_prose_plugin_zero_registers():
    from codoc.blocks.prose import register

    reg = BlockRegistry()
    p = register(reg)
    assert p.kind == "prose"
    assert reg.for_capability("prose", Capability.LIFT) is not None
    assert reg.for_capability("prose", Capability.LOWER) is not None
    # prose lower on a feature with no bindings is authorship, not code change
    f = Feature(title="x")
    blk = Block(feature_id=f.id, kind="prose", content="some prose")
    res = p.lower(LowerContext(feature=f, old_block=None, new_block=blk, bindings=[]))
    assert res.kind == "noop"
