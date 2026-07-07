"""Loop A's block `lift` pass — code → block refresh (U3).

After an authoritative Loop A pass has updated the index, bindings, and the
dependency graph, persistent blocks that declare ``LIFT`` are re-derived from the
fresh code so they never go stale (a diagram redraws when its bound code changes).

Two invariants from the plan are enforced here:

- **Attribution, not intent (KTD2):** ``lift`` only *reads* code and *replaces a
  block's content*; it never queues a directive or touches code.
- **Doc-wins (KTD3):** a block on a *held* feature (a pending block edit / queued
  directive) is SKIPPED — the human's in-progress edit wins until it is realized,
  so a concurrent code-derived refresh can never clobber it. This is the same
  doc-wins rule the phase projection applies, reused for block content.

``lift`` only refreshes blocks that already exist — it never *creates* one
(authoring a diagram is a host action), so it is a safe, idempotent pass.
"""
from __future__ import annotations

from codoc.blocks.base import Capability, LiftContext
from codoc.blocks.builtins import ensure_builtins
from codoc.blocks.registry import BlockRegistry
from codoc.loop.edits import hold_set
from codoc.model.block import BlockLifecycle, Provenance
from codoc.store.db import Store


def refresh_lift_blocks(store: Store, codoc_dir: str,
                        registry: BlockRegistry | None = None) -> int:
    """Re-derive every persistent, LIFT-capable, non-held block from current code.
    Returns the number of blocks whose content changed."""
    registry = registry or ensure_builtins()
    held = hold_set(codoc_dir)
    changed = 0
    for blk in store.all_blocks():
        if blk.lifecycle is not BlockLifecycle.PERSISTENT:
            continue
        if blk.feature_id in held:
            continue  # doc-wins: never clobber a pending human edit
        plugin = registry.for_capability(blk.kind, Capability.LIFT)
        if plugin is None:
            continue
        f = store.get_feature(blk.feature_id)
        if f is None or f.retired:
            continue
        res = plugin.lift(LiftContext(
            feature=f, bindings=store.bindings_for_feature(blk.feature_id),
            block=blk, store=store, codoc_dir=codoc_dir))
        if res.changed and res.content is not None and res.content != blk.content:
            store.upsert_block(blk.model_copy(update={
                "content": res.content, "provenance": Provenance.DERIVED}))
            changed += 1
    return changed
