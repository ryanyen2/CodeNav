"""Prose — plugin-zero. The incumbent feature-node path expressed as a plugin.

Prose is special: it is the **implicit block-zero** backed by ``feature.description``,
not a row in the ``blocks`` table. Registering it makes the *default* path itself a
plugin so the registry/dispatch model has no special-cased "real" path beside it —
everything is a plugin, prose included (R2, R14).

The actual prose transformations already live in the loops:

- ``lift`` (refresh / amend of a feature's prose from changed code) is decided by
  the existing Loop A LLM pass (``codoc/agent/tree_update.py``). The plugin's
  ``lift`` is therefore a deterministic *no-op signal* — it tells the dispatcher
  "prose has no block-local lift; let the incumbent tree-update path own it." U3
  routes prose through the existing path rather than calling this.
- ``lower`` (a prose edit implying code) is built by the existing Loop B directive
  path (``build_directive`` / ``build_steer_directive``). The plugin's ``lower``
  hands back the edited prose as a directive body so a generic block-driven caller
  still produces a sensible directive; the incumbent classify path remains the
  primary route in U3.

Keeping prose as a thin, honest shell (rather than re-implementing the loop here)
is what lets U4 land "wrap the existing path" without behavior change.
"""
from __future__ import annotations

from codoc.blocks.base import (
    BindingMode,
    BlockPlugin,
    Capability,
    Dispatch,
    LiftContext,
    LiftResult,
    LowerContext,
    LowerResult,
)


class ProsePlugin(BlockPlugin):
    kind = "prose"
    capabilities = frozenset({Capability.LIFT, Capability.LOWER})
    binding_mode = BindingMode.BOUND
    lift_dispatch = Dispatch.AGENT
    lower_dispatch = Dispatch.AGENT

    def lift(self, ctx: LiftContext) -> LiftResult:
        # Prose has no block-local lift: the incumbent Loop A tree-update path owns
        # refresh/amend of the feature description. Signal "nothing for me to do."
        return LiftResult.no_change()

    def lower(self, ctx: LowerContext) -> LowerResult:
        # A prose edit with no bound code is authorship, not a code change.
        if not ctx.bindings:
            return LowerResult.noop()
        body = (ctx.new_block.content or "").strip()
        if not body:
            return LowerResult.noop()
        return LowerResult.directive(body)


def register(registry) -> ProsePlugin:
    """Register prose onto a registry (called by U3/U4 wiring and tests)."""
    return registry.register(ProsePlugin())
