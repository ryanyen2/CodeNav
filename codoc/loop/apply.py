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
from codoc.model.event import SAFE_OPS, Event, NodeOp, NodeOpKind, default_provenance
from codoc.model.feature import Feature, Lifecycle
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
    actor: str = "",
    mode: str = "",
    caused_by: str = "",
    writer: str = "",
) -> Event:
    """Log an Event for ``op``; if ``applied``, mutate the store accordingly.

    ``fp_lookup`` / ``th_lookup`` supply the chunk's ``tokens_hash`` /
    ``types_hash`` for any binding the op creates — recorded so staleness and
    rename detection have an anchor. Callers without the hashes pass neither;
    the binding stores empty strings (a re-bind that does have them backfills).

    ``actor`` / ``mode`` / ``caused_by`` stamp the change ledger. When the
    caller carries no explicit provenance (legacy paths), actor/mode are
    inferred from ``source`` via :func:`default_provenance`.

    ``writer`` names the editing session behind an authored command; every other
    caller falls back to ``source``. It is recorded per feature so a later command
    can tell "I am continuing my own edit" (base legitimately behind, because the
    projection has not caught up) from "someone else wrote here" (a real
    disagreement) — see :func:`codoc.loop.loop_b._base_conflict`. Recording it here,
    at the one write boundary, is what makes an agent's write count as someone else
    without every agent path having to remember to say so.
    """
    d_actor, d_mode = default_provenance(source, applied)
    # Write-boundary sanitization: authored text must never carry id-shaped
    # ⟨…⟩ tokens where the render→parse round-trip would read them as tree
    # STRUCTURE — a title id token hijacks the node's identity, a marker line
    # with an id inside a description forges a phantom node and truncates the
    # prose (see parse.sanitize_authored_*). One choke point for every writer:
    # LLM ops, MCP, webview commands, inbox accepts, bootstrap.
    if op.title is not None or op.description is not None:
        from codoc.codoc_file.parse import (
            sanitize_authored_description,
            sanitize_authored_title,
        )
        if op.title is not None:
            clean = sanitize_authored_title(op.title)
            # A title that was ONLY id tokens sanitizes to '' — dropping the
            # AMEND beats blanking a real title (ADD falls back to "Untitled").
            op.title = clean if (clean or op.kind is not NodeOpKind.AMEND) else None
        if op.description is not None:
            op.description = sanitize_authored_description(op.description)
    # Pre-mint the id for a directly-applied ADD so the creation event records
    # the real feature id (blame needs "who created this" findable by feature).
    # Pending proposals keep a bare op — their id mints on acceptance.
    if op.kind is NodeOpKind.ADD_NODE and applied and not op.feature_id:
        from codoc.model.ids import new_feature_id
        op.feature_id = new_feature_id()
    event = Event(source=source, op=op, applied=applied,
                  actor=actor or d_actor, mode=mode or d_mode, caused_by=caused_by)
    store.append_event(event)
    if applied:
        _mutate(op, store, fp_lookup or {}, th_lookup or {})
        if op.feature_id:
            # The event's actor doubles as the writer's ROLE. It is already
            # resolved here (explicit provenance, else derived from source), so
            # rank arbitration reads the same authorship the ledger records
            # rather than a parallel notion that could disagree with it.
            store.set_feature_writer(op.feature_id, writer or source, event.actor)
        store.mark_applied(event.id)  # stamp accepted_at for the audit log
    return event


def _live_parent_id(store: Store, parent_id: str | None) -> str | None:
    """Resolve ``parent_id`` to a parent that is actually visible in the tree.

    A destination parent that is retired (or has been deleted out from under a
    stale ADD/MOVE) is filtered out of ``children()``, so a node parented to it
    becomes a live-but-invisible orphan — the exact catastrophe the cycle guard
    warns about, reached by a different door (accept a MOVE/ADD whose destination
    was retired in the meantime). Walk up to the nearest LIVE ancestor, falling
    back to ``None`` (a root) so the node is always reachable from some root."""
    seen: set[str] = set()
    pid = parent_id
    while pid is not None and pid not in seen:
        seen.add(pid)
        parent = store.get_feature(pid)
        if parent is None:
            return None  # destination vanished → root
        if parent.lifecycle is not Lifecycle.RETIRED:
            return pid  # a live parent — use it
        pid = parent.parent_id  # retired → try its parent
    return None


def _mutate(op: NodeOp, store: Store, fp: dict[tuple[str, str], str],
            th: dict[tuple[str, str], str]) -> None:
    k = op.kind
    if k in (NodeOpKind.ATTACH, NodeOpKind.REFRESH):
        for file, symbol in op.bindings:
            store.upsert_binding(Binding(feature_id=op.feature_id, file=file,
                                         symbol_path=symbol, fingerprint=fp.get((file, symbol), ""),
                                         types_hash=th.get((file, symbol), "")))
        # Named lifecycle transition (A1): the first code bound to a plan
        # placeholder promotes it planned→active. store.mark_realized is guarded to
        # `planned` rows, so this is the one explicit transition point — no silent
        # bool flip, and a retired feature can never be resurrected by a stray bind.
        if op.bindings and op.feature_id:
            owner = store.get_feature(op.feature_id)
            if owner and owner.lifecycle is Lifecycle.PLANNED:
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
            # advance() (not HLC.now()) guarantees the new updated_at is STRICTLY greater
            # than the feature's prior one even for two edits in the same wall-clock ms —
            # HLC.now() always returns logical_time=0, so same-ms edits tied and the
            # webview's "strictly newer" doc-gate could miss a real change (P2). Bumping
            # the logical counter off the feature's own clock keeps per-feature edits
            # monotonic without any process-global state.
            f.updated_at = f.updated_at.advance()
            store.upsert_feature(f)
    elif k is NodeOpKind.ADD_NODE:
        # ``realized`` defaults True: a node is a real feature unless an explicit
        # plan path (propose.propose_plan / mcp.tools.plan_add) marks it a
        # placeholder with realized=False. We deliberately do NOT infer
        # "unrealized" from empty bindings — org-pass theme PARENTS are
        # legitimately binding-less yet fully real, and marking them placeholders
        # would mis-fire the IDE's unrealized decoration on every theme node.
        # A stale ADD (proposal accepted after its destination was retired, or an
        # MCP plan_add under a since-retired parent) must not bury the new node
        # under a retired ancestor — resolve to the nearest live parent.
        add_parent_id = _live_parent_id(store, op.parent_id)
        f = Feature(title=op.title or "Untitled", description=op.description or "",
                    parent_id=add_parent_id, local_id=op.local_id,
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
            # Reject a cycle-forming move (destination is the node itself or a
            # descendant). Silently dropping the move is far safer than applying it:
            # a cycle detaches the subtree from every root, so render_tree's walk, the
            # doc projection, and the sidecar all drop it — the features stay live and
            # bound (their chunks read as "covered", so Loop A never re-homes them) but
            # are invisible and unrecoverable from any UI. The move source (webview
            # command / proposal accept / MCP / hub) all funnel through here, so this is
            # the single chokepoint that keeps the tree acyclic.
            if store.would_move_create_cycle(op.feature_id, op.parent_id):
                import logging
                logging.getLogger(__name__).warning(
                    "codoc: rejected cycle-forming move of %s under %s (no-op)",
                    op.feature_id, op.parent_id)
            else:
                # Same orphan hazard as the cycle case: a MOVE whose destination
                # was retired (or deleted) since the op was minted would strand the
                # node under an invisible ancestor. Land it on the nearest LIVE
                # parent instead of the requested (dead) one.
                f.parent_id = _live_parent_id(store, op.parent_id)
                f.updated_at = f.updated_at.advance()
                store.upsert_feature(f)
    elif k is NodeOpKind.RETIRE_NODE:
        if op.feature_id:
            # Re-parent LIVE children to the grandparent BEFORE retiring. A retired
            # feature is filtered out of `children()`, so any child left pointing at it
            # becomes an orphan — invisible to render/projection/sidecar (all walk from
            # the roots) yet still live + bound, i.e. unrecoverable. Promoting the
            # children to the retiree's own parent keeps the tree connected (a root's
            # children become roots). Retiring a subtree is done child-first or cascades
            # correctly: each retire only lifts its own direct children one level.
            owner = store.get_feature(op.feature_id)
            if owner is not None:
                for child in store.children(op.feature_id):
                    child.parent_id = owner.parent_id
                    child.updated_at = child.updated_at.advance()
                    store.upsert_feature(child)
            # Mark retired only. Binding detach is a PATH decision, not a property of
            # the op: an inbox/auto retire detaches (untrack — Loop B does it), while
            # a human `~` retire keeps its bindings so Loop B can build the code-removal
            # directive (the code is deleted by the agent, and reconcile detaches then).
            store.retire_feature(op.feature_id)
