"""Routing + application of NodeOps.

``derive_auto_ops`` turns the trivially-resolvable parts of a change set into
safe ops (no LLM): a modified bound chunk → REFRESH, a removed bound chunk →
DETACH. ``apply_op`` writes an Event and, when ``applied``, mutates the store.

The single similarity threshold in the whole system lives here:
``AMEND_SAFE_RATIO`` decides whether an LLM-proposed description edit is small
enough to auto-apply or large enough to surface for review.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from codoc.loop.diff import ChangeSet
from codoc.model.binding import Binding
from codoc.model.event import SAFE_OPS, Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.store.db import Store

AMEND_SAFE_RATIO = 0.30  # description edits changing ≤30% of the text auto-apply


def derive_auto_ops(cs: ChangeSet, store: Store) -> list[NodeOp]:
    """Safe ops resolvable by exact binding lookup — no LLM needed."""
    ops: list[NodeOp] = []
    for m in cs.modified:
        b = store.binding_at(m.file, m.symbol_path)
        if b:
            ops.append(NodeOp(kind=NodeOpKind.REFRESH, feature_id=b.feature_id,
                              bindings=[(m.file, m.symbol_path)]))
    for a in cs.added:
        b = store.binding_at(a.file, a.symbol_path)
        if b:  # re-added an already-bound anchor: just refresh
            ops.append(NodeOp(kind=NodeOpKind.REFRESH, feature_id=b.feature_id,
                              bindings=[(a.file, a.symbol_path)]))
    for r in cs.removed:
        b = store.binding_at(r.file, r.symbol_path)
        if b:
            ops.append(NodeOp(kind=NodeOpKind.DETACH, feature_id=b.feature_id,
                              bindings=[(r.file, r.symbol_path)]))
    return ops


def is_small_amend(op: NodeOp, store: Store) -> bool:
    """True if an AMEND changes ≤ AMEND_SAFE_RATIO of the existing description."""
    if op.kind is not NodeOpKind.AMEND:
        return False
    f = store.get_feature(op.feature_id) if op.feature_id else None
    old = (f.description if f else "") or ""
    new = op.description if op.description is not None else old
    if not old and not new:
        return True
    change = 1.0 - SequenceMatcher(None, old, new).ratio()
    return change <= AMEND_SAFE_RATIO


def should_auto_apply(op: NodeOp, store: Store) -> bool:
    """Safe ops auto-apply; AMEND only when the edit is small; structural never."""
    if op.kind not in SAFE_OPS:
        return False
    if op.kind is NodeOpKind.AMEND:
        return is_small_amend(op, store)
    return True


def apply_op(
    op: NodeOp,
    store: Store,
    *,
    source: str,
    applied: bool,
    fp_lookup: dict[tuple[str, str], str] | None = None,
    th_lookup: dict[tuple[str, str], str] | None = None,
) -> Event:
    """Log an Event for ``op``; if ``applied``, mutate the store accordingly.

    ``fp_lookup`` / ``th_lookup`` supply the chunk's ``tokens_hash`` /
    ``types_hash`` for any binding the op creates — recorded so staleness and
    rename detection have an anchor. Callers without the hashes pass neither;
    the binding stores empty strings (a re-bind that does have them backfills).
    """
    event = Event(source=source, op=op, applied=applied)
    store.append_event(event)
    if applied:
        _mutate(op, store, fp_lookup or {}, th_lookup or {})
        store.mark_applied(event.id)  # stamp accepted_at for the audit log
    return event


def _mutate(op: NodeOp, store: Store, fp: dict[tuple[str, str], str],
            th: dict[tuple[str, str], str]) -> None:
    k = op.kind
    if k in (NodeOpKind.ATTACH, NodeOpKind.REFRESH):
        for file, symbol in op.bindings:
            store.upsert_binding(Binding(feature_id=op.feature_id, file=file,
                                         symbol_path=symbol, fingerprint=fp.get((file, symbol), ""),
                                         types_hash=th.get((file, symbol), "")))
        # Realization transition: the first code bound to a plan placeholder makes
        # it a real, implemented feature.
        if op.bindings and op.feature_id:
            owner = store.get_feature(op.feature_id)
            if owner and not owner.realized:
                store.mark_realized(op.feature_id)
    elif k is NodeOpKind.DETACH:
        for file, symbol in op.bindings:
            store.delete_binding(file, symbol)
    elif k is NodeOpKind.AMEND:
        f = store.get_feature(op.feature_id)
        if f:
            if op.title is not None:
                f.title = op.title
            if op.description is not None:
                f.description = op.description
            f.updated_at = HLC.now()
            store.upsert_feature(f)
    elif k is NodeOpKind.ADD_NODE:
        # ``realized`` defaults True: a node is a real feature unless an explicit
        # plan path (propose.propose_plan / mcp.tools.plan_add) marks it a
        # placeholder with realized=False. We deliberately do NOT infer
        # "unrealized" from empty bindings — org-pass theme PARENTS are
        # legitimately binding-less yet fully real, and marking them placeholders
        # would mis-fire the IDE's unrealized decoration on every theme node.
        f = Feature(title=op.title or "Untitled", description=op.description or "",
                    parent_id=op.parent_id,
                    realized=(op.realized if op.realized is not None else True))
        if op.feature_id:
            f.id = op.feature_id
        store.upsert_feature(f)
        for file, symbol in op.bindings:
            store.upsert_binding(Binding(feature_id=f.id, file=file, symbol_path=symbol,
                                         fingerprint=fp.get((file, symbol), ""),
                                         types_hash=th.get((file, symbol), "")))
    elif k is NodeOpKind.MOVE_NODE:
        f = store.get_feature(op.feature_id)
        if f:
            f.parent_id = op.parent_id
            f.updated_at = HLC.now()
            store.upsert_feature(f)
    elif k is NodeOpKind.RETIRE_NODE:
        if op.feature_id:
            store.retire_feature(op.feature_id)
