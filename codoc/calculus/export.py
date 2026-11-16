"""``tree.codoc``: the document as Markdown (the format is ``block_md``'s).

The export is the ``rejected`` read mode: an open plan is not the document
yet, so its words are left out, and a block holding nothing else is left out
whole. Comments and dead blocks are omitted. Headings carry their id; other
blocks do not.

The file is derived and one-way. Nothing reads it back into a store: the op
log is what a clone rebuilds from.
"""
from __future__ import annotations

from codoc.calculus import block_md
from codoc.calculus.block_md import BlockSpec
from codoc.calculus.derive import block_spec
from codoc.calculus.store import Store

MODE = "rejected"


def export_spec(store: Store, b) -> BlockSpec | None:
    """One block of the export. A function of the block and of which plans
    are open, so a caller may memoize it on ``(block_head[b.id], plans_head)``."""
    return block_spec(store, b, MODE)


def export_markdown(store: Store, spec=export_spec) -> str:
    return block_md.render([s for b in store.ordered() if (s := spec(store, b)) is not None], ids=True)
