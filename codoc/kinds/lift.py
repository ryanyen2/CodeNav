"""The loop's lift pass: every live kind block with a ``lift`` is re-derived
from the code after a reflect pass. Content that did not change writes
nothing; content that did goes through ``write_block`` with ``actor=loop`` on
the trunk (a lift is attribution, like a binding refresh, never intent). A
block on a held feature (unsent ink or queued work) is left alone: the
document wins.
"""
from __future__ import annotations

from codoc.calculus.derive import HELD, phase
from codoc.kinds import LiftContext, get, plain
from codoc.kinds.write import write_block
from codoc.substrate.workspace import Workspace



def lift_pass(ws: Workspace, graph=None, codoc_dir: str | None = None, only: set[str] | None = None) -> int:
    """Returns the number of blocks whose content changed. ``only`` limits the
    pass to blocks whose feature is in the set."""
    store = ws.store
    changed = 0
    for block in list(store.ordered()):
        kind = get(block.kind)
        if kind is None or kind.lift is None:
            continue
        fid = store.feature_of(block.id)
        if only is not None and fid not in only:
            continue
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
        w = write_block(ws, block.id, delta, actor="loop", derived=True)
        if w.ops:
            changed += 1
    return changed
