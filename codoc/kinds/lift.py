"""The loop's lift pass: every live kind block with a ``lift`` is re-derived
from the code after a reflect pass. Content that did not change writes
nothing; content that did goes through ``write_block`` with ``actor=loop`` on
the trunk (a lift is attribution, like a binding refresh, never intent). It
passes ``derived=True``, which is what keeps it on the trunk however much of
the picture changed: the machine gate protects words somebody meant, and a
field read off the code holds none. A block on a held feature (unsent ink or
queued work) is left alone: the document wins. So is a block an open plan
wrote (``derive.unvetted_block``): a url a suggester proposed is not fetched
before somebody accepts it.
"""
from __future__ import annotations

from codoc.calculus.derive import HELD, phase, unvetted_block
from codoc.calculus.ids import LOOP
from codoc.kinds import LiftContext, get, plain
from codoc.kinds.write import write_block
from codoc.workspace import Workspace


def lift_pass(ws: Workspace, graph=None, codoc_dir: str | None = None) -> int:
    """Returns the number of blocks whose content changed."""
    store = ws.store
    changed = 0
    for block in list(store.ordered()):
        kind = get(block.kind)
        if kind is None or kind.lift is None or unvetted_block(store, block):
            continue
        fid = store.feature_of(block.id)
        if fid and phase(store, fid) in HELD:
            continue
        bindings = [b for b in store.bindings.values() if b.feature == fid] if fid else []
        new = kind.lift(LiftContext(store=store, block=block, feature=fid, bindings=bindings, graph=graph, codoc_dir=codoc_dir))
        if not new:
            continue
        current = plain(block, store, mode="live")
        delta = {k: v for k, v in new.items() if current.get(k) != v}
        if not delta:
            continue
        w = write_block(ws, block.id, delta, actor=LOOP, derived=True)
        if w.ops:
            changed += 1
    return changed
