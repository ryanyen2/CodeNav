"""The block-plugin registry — the loops' dispatch table, keyed by ``kind``.

Registration is *validated*: a plugin that declares a capability must implement
its method (override the base), so a contract violation fails loudly at startup
rather than silently no-oping mid-loop. Dispatch helpers return ``None`` when a
``kind`` is unknown or the requested capability isn't declared, so the loops can
ask "does this medium do X?" without branching on the registry's internals.
"""
from __future__ import annotations

from codoc.blocks.base import BlockPlugin, Capability


class PluginContractError(ValueError):
    """A plugin's declaration doesn't match its implementation (declared a
    capability without overriding its method, or registered an empty ``kind``)."""


class UnknownBlockKind(KeyError):
    """No plugin is registered for a block ``kind``. Hosts degrade to an inert
    placeholder; the loops skip the block."""


# capability → the base method that a subclass must override to honor it
_CAP_METHOD = {
    Capability.LIFT: "lift",
    Capability.LOWER: "lower",
    Capability.CONSULT: "consult",
}


class BlockRegistry:
    def __init__(self) -> None:
        self._by_kind: dict[str, BlockPlugin] = {}

    def register(self, plugin: BlockPlugin) -> BlockPlugin:
        if not plugin.kind:
            raise PluginContractError("plugin has no `kind`")
        if not plugin.capabilities:
            raise PluginContractError(f"{plugin.kind}: declares no capabilities")
        for cap in plugin.capabilities:
            method = _CAP_METHOD[cap]
            # The capability is honored only if the subclass overrode the base
            # method — comparing the unbound functions detects a missing override.
            if getattr(type(plugin), method) is getattr(BlockPlugin, method):
                raise PluginContractError(
                    f"{plugin.kind}: declares {cap.value} but does not implement {method}()"
                )
        self._by_kind[plugin.kind] = plugin
        return plugin

    def get(self, kind: str) -> BlockPlugin | None:
        return self._by_kind.get(kind)

    def require(self, kind: str) -> BlockPlugin:
        try:
            return self._by_kind[kind]
        except KeyError as e:
            raise UnknownBlockKind(kind) from e

    def kinds(self) -> list[str]:
        return sorted(self._by_kind)

    # ── capability-scoped lookups (the loops' dispatch entry points) ──
    def for_capability(self, kind: str, cap: Capability) -> BlockPlugin | None:
        """The plugin for ``kind`` iff it declares ``cap`` — else ``None``. Loop A
        asks ``for_capability(kind, LIFT)``; Loop B asks for ``LOWER``; realization
        asks for ``CONSULT``. ``None`` means "this medium doesn't do that
        direction", which the loop treats as a clean skip."""
        p = self._by_kind.get(kind)
        return p if (p is not None and p.has(cap)) else None


# Process-wide default registry. Reference plugins register onto it at import
# (prose in U4, diagram in U5, screenshot/consult media in U6).
default_registry = BlockRegistry()
