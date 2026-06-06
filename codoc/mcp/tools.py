"""codoc MCP tool implementations — plain, testable functions.

Each function opens the store, routes through the same ``apply_op`` seam that
Loop A / the CLI use (so identity-minting, the ``UNIQUE(file, symbol_path)``
binding constraint, and rendering are all reused), re-renders ``tree.codoc`` +
the sidecar, and returns a JSON-ready dict. The FastMCP server in
:mod:`codoc.mcp.server` is a thin wrapper that resolves the ``.codoc`` dir from
the agent's cwd and calls these.

Agent-driven reflection (the code-first loop) is the reason these exist: the
agent that just wrote the code knows *why*, so it can submit precise structural
ops with real intent — richer than Loop A's blind index-diff can infer. Safe ops
(attach/detach/refresh/small-amend) apply immediately; structural ops
(add_node/move_node/retire_node, large amend) become ``applied=False`` proposals
the user reviews in the IDE.
"""
from __future__ import annotations

from codoc.loop.activity import PHASE_DONE, PHASE_EDITING, mark_feature_phase
from codoc.loop.apply import apply_op, should_auto_apply
from codoc.loop.inbox import drop_verdicts as _inbox_drop
from codoc.loop.inbox import read_verdicts as inbox_read
from codoc.loop.reconcile import safe_write_tree
from codoc.model.event import (
    LOOP_A_AGENT_SOURCE,
    PLAN_SOURCE,
    NodeOp,
    NodeOpKind,
)
from codoc.store.db import Store, open_store

# Ops that update an existing live feature's content/bindings — marking these
# "done" resolves the IDE doc view's skeleton into the reflected content.
_LIVE_FEATURE_KINDS = {
    NodeOpKind.ATTACH, NodeOpKind.DETACH, NodeOpKind.REFRESH,
    NodeOpKind.AMEND, NodeOpKind.MOVE_NODE, NodeOpKind.RETIRE_NODE,
}


def _mark_reflected(codoc_dir: str, ops: list[NodeOp]) -> None:
    """Best-effort: flag the live features these ops touched as reflection-done."""
    fids = [op.feature_id for op in ops
            if op.kind in _LIVE_FEATURE_KINDS and op.feature_id]
    mark_feature_phase(codoc_dir, fids, PHASE_DONE)


def _parse_binds(binds: list[str] | None) -> list[tuple[str, str]]:
    """Parse "file.py::symbol" bind strings into ``(file, symbol_path)`` pairs.

    The stored ``symbol_path`` is the FULL "file::qualified" form the indexer
    emits (``codoc/lang/python.py`` builds ``f"{file}::{qualified}"``), so the
    binding matches a real chunk and Loop A can resolve / dedup against it. The
    ``file`` is the prefix before the first ``::``.
    """
    out: list[tuple[str, str]] = []
    for b in binds or []:
        if "::" in b:
            file = b.split("::", 1)[0]
            out.append((file, b))  # symbol_path keeps the full "file::symbol"
        else:
            out.append((b, b))
    return out


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _op_summary(op: NodeOp, store: Store) -> str:
    if op.kind is NodeOpKind.ADD_NODE:
        return f'add "{op.title}"'
    target = op.feature_id or ""
    f = store.get_feature(target) if target else None
    name = f.title if f else target
    return f"{op.kind.value} {name}".strip()


# ─── reads ────────────────────────────────────────────────────────────────────

def read_tree(codoc_dir: str) -> dict:
    """The live feature tree (incl. ``realized``) + pending proposals."""
    store = open_store(codoc_dir)
    try:
        feats = []
        for f in store.list_features():
            feats.append({
                "id": f.id,
                "title": f.title,
                "description": f.description,
                "parent_id": f.parent_id,
                "realized": f.realized,
                "bindings": [b.symbol_path for b in store.bindings_for_feature(f.id)],
            })
        proposals = [
            {"event_id": e.id, "kind": e.op.kind.value, "feature_id": e.op.feature_id,
             "parent_id": e.op.parent_id, "title": e.op.title, "source": e.source,
             "rationale": e.op.rationale}
            for e in store.pending_events()
        ]
        return {"ok": True, "features": feats, "proposals": proposals}
    finally:
        store.close()


def read_status(codoc_dir: str) -> dict:
    """Feature / proposal counts + the current pipeline state."""
    from codoc.loop.status import refresh_status
    import json

    store = open_store(codoc_dir)
    try:
        feats = store.list_features()
        pending = store.pending_events()
        unrealized = [f.id for f in feats if not f.realized]
        try:
            st = refresh_status(codoc_dir, store)
            state = json.loads(st.read_text()).get("state", "in_sync")
        except Exception:
            state = "in_sync"
        return {
            "ok": True, "features": len(feats), "pending": len(pending),
            "unrealized": len(unrealized), "state": state,
        }
    finally:
        store.close()


# ─── single-op proposals / binds ───────────────────────────────────────────────

def _apply_single(codoc_dir: str, op: NodeOp, *, source: str) -> dict:
    store = open_store(codoc_dir)
    try:
        # Validation: targets must exist for ops that reference them.
        if op.kind in (NodeOpKind.AMEND, NodeOpKind.RETIRE_NODE, NodeOpKind.MOVE_NODE,
                       NodeOpKind.ATTACH):
            if not op.feature_id or store.get_feature(op.feature_id) is None:
                return _err(f"unknown feature_id {op.feature_id!r}")
        if op.kind in (NodeOpKind.ADD_NODE, NodeOpKind.MOVE_NODE) and op.parent_id:
            if store.get_feature(op.parent_id) is None:
                return _err(f"unknown parent_id {op.parent_id!r}")

        applied = should_auto_apply(op, store)
        ev = apply_op(op, store, source=source, applied=applied)
        wrote = safe_write_tree(store, codoc_dir)
        _mark_reflected(codoc_dir, [op])
        return {"ok": True, "event_id": ev.id, "applied": applied,
                "rendered": wrote, "summary": _op_summary(op, store)}
    finally:
        store.close()


def propose_add(codoc_dir: str, *, title: str, description: str = "",
                parent_id: str | None = None, binds: list[str] | None = None,
                rationale: str = "", source: str = LOOP_A_AGENT_SOURCE,
                realized: bool | None = None) -> dict:
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title=title, description=description,
                parent_id=parent_id, bindings=_parse_binds(binds),
                rationale=rationale, realized=realized)
    return _apply_single(codoc_dir, op, source=source)


def propose_amend(codoc_dir: str, *, feature_id: str, title: str | None = None,
                  description: str | None = None, rationale: str = "",
                  source: str = LOOP_A_AGENT_SOURCE) -> dict:
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id=feature_id, title=title,
                description=description, rationale=rationale)
    return _apply_single(codoc_dir, op, source=source)


def propose_move(codoc_dir: str, *, feature_id: str, parent_id: str | None,
                 rationale: str = "", source: str = LOOP_A_AGENT_SOURCE) -> dict:
    op = NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=feature_id,
                parent_id=parent_id, rationale=rationale)
    return _apply_single(codoc_dir, op, source=source)


def propose_retire(codoc_dir: str, *, feature_id: str, rationale: str = "",
                   source: str = LOOP_A_AGENT_SOURCE) -> dict:
    op = NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=feature_id, rationale=rationale)
    return _apply_single(codoc_dir, op, source=source)


def attach(codoc_dir: str, *, feature_id: str, binds: list[str],
           rationale: str = "", source: str = LOOP_A_AGENT_SOURCE) -> dict:
    op = NodeOp(kind=NodeOpKind.ATTACH, feature_id=feature_id,
                bindings=_parse_binds(binds), rationale=rationale)
    return _apply_single(codoc_dir, op, source=source)


# ─── bulk reflection ────────────────────────────────────────────────────────────

def reflect(codoc_dir: str, *, ops: list[dict], rationale: str = "",
            source: str = LOOP_A_AGENT_SOURCE) -> dict:
    """Submit the whole change set the agent just made, in one call.

    Each op is ``{kind, feature_id?, parent_id?, title?, description?, binds?,
    rationale?, realized?}``. Safe ops apply immediately; structural ops become
    proposals. Returns per-op results plus applied/proposed counts.
    """
    store = open_store(codoc_dir)
    results: list[dict] = []
    applied_ops: list[NodeOp] = []
    applied_n = proposed_n = 0
    try:
        for raw in ops:
            try:
                kind = NodeOpKind(raw["kind"])
            except (KeyError, ValueError):
                results.append(_err(f"bad op kind {raw.get('kind')!r}"))
                continue
            op = NodeOp(
                kind=kind,
                feature_id=raw.get("feature_id"),
                parent_id=raw.get("parent_id"),
                title=raw.get("title"),
                description=raw.get("description"),
                bindings=_parse_binds(raw.get("binds")),
                rationale=raw.get("rationale") or rationale,
                realized=raw.get("realized"),
            )
            # Validate references.
            if kind in (NodeOpKind.AMEND, NodeOpKind.RETIRE_NODE, NodeOpKind.MOVE_NODE,
                        NodeOpKind.ATTACH) and (
                    not op.feature_id or store.get_feature(op.feature_id) is None):
                results.append(_err(f"unknown feature_id {op.feature_id!r}"))
                continue
            if kind in (NodeOpKind.ADD_NODE, NodeOpKind.MOVE_NODE) and op.parent_id \
                    and store.get_feature(op.parent_id) is None:
                results.append(_err(f"unknown parent_id {op.parent_id!r}"))
                continue

            applied = should_auto_apply(op, store)
            ev = apply_op(op, store, source=source, applied=applied)
            applied_ops.append(op)
            applied_n += int(applied)
            proposed_n += int(not applied)
            results.append({"ok": True, "event_id": ev.id, "applied": applied,
                            "summary": _op_summary(op, store)})
        wrote = safe_write_tree(store, codoc_dir)
        _mark_reflected(codoc_dir, applied_ops)
        return {"ok": True, "applied": applied_n, "proposed": proposed_n,
                "rendered": wrote, "results": results}
    finally:
        store.close()


# ─── realize progress ────────────────────────────────────────────────────────

def realize_progress(codoc_dir: str, *, done: int, total: int, current: str = "") -> dict:
    """Stamp ``done/total`` realize progress into ``status.json`` so the IDE shows
    "implementing M of N" while the live session works through ``.codoc/realize.md``.
    """
    from codoc.loop.status import REALIZING, write_status
    detail = f"{done}/{total}" + (f": {current}" if current else "")
    try:
        write_status(codoc_dir, REALIZING, pending=max(0, total - done), detail=detail)
    except Exception:  # noqa: BLE001 — progress is advisory
        return {"ok": False}
    return {"ok": True, "done": done, "total": total}


# ─── plan loop ──────────────────────────────────────────────────────────────────

def plan_add(codoc_dir: str, *, title: str, description: str = "",
             parent_id: str | None = None, binds: list[str] | None = None,
             rationale: str = "") -> dict:
    """Propose a PLAN placeholder node (source='plan', realized=False).

    Accepted in the IDE, it enters the tree as an unrealized placeholder; the
    first code bound to it (via :func:`attach` / :func:`reflect`) flips it real.
    """
    return propose_add(codoc_dir, title=title, description=description,
                       parent_id=parent_id, binds=binds, rationale=rationale,
                       source=PLAN_SOURCE, realized=False)


def await_verdicts(codoc_dir: str, *, event_ids: list[str],
                   timeout: float = 86400.0, poll_interval: float = 1.0) -> dict:
    """Block until the user Accepts/Rejects the given proposals in the IDE.

    This is the in-session realization trigger (modeled on plannotator's blocking
    review hook): instead of ending the turn at "stop here", ``/codoc:plan`` calls
    this after proposing nodes. It polls ``.codoc/inbox.json`` — the same verdict
    channel Loop B drains — applying each verdict as it arrives (accept → ``apply_op``
    + delete event; reject → delete event), and returns once every ``event_ids``
    proposal is resolved (or the timeout elapses). The same turn then continues to
    implement the accepted nodes, so there is no idle gap and no daemon dependency.

    Returns ``{accepted:[{event_id, feature_id, title}], rejected:[event_id], pending:[event_id], timed_out}``.
    ``feature_id`` is the now-live node (ADD mints a fresh id on accept, recovered
    by diffing the feature set) so the caller can bind code to it.
    """
    import time as _time

    targets = list(dict.fromkeys(event_ids))  # de-dupe, preserve order
    accepted: list[dict] = []
    rejected: list[str] = []
    resolved: set[str] = set()
    deadline = _time.monotonic() + max(0.0, timeout)

    def _resolve_once() -> None:
        verdicts = {v.event_id: v.accept for v in inbox_read(codoc_dir)}
        consumed: set[str] = set()
        store = open_store(codoc_dir)
        try:
            for eid in targets:
                if eid in resolved:
                    continue
                ev = store.get_event(eid)
                if eid in verdicts:
                    accept = verdicts[eid]
                    consumed.add(eid)
                    if ev is None:        # already drained elsewhere — treat as done
                        resolved.add(eid)
                        continue
                    if accept:
                        before = {f.id for f in store.list_features()}
                        apply_op(ev.op, store, source="user", applied=True)
                        after = {f.id for f in store.list_features()}
                        new = after - before
                        fid = ev.op.feature_id or (next(iter(new)) if new else None)
                        feat = store.get_feature(fid) if fid else None
                        store.delete_event(eid)
                        accepted.append({"event_id": eid, "feature_id": fid,
                                         "title": (feat.title if feat else ev.op.title) or ""})
                    else:
                        store.delete_event(eid)
                        rejected.append(eid)
                    resolved.add(eid)
                elif ev is None:
                    # No verdict for us, yet the event is gone → the watch daemon
                    # drained it. Infer the outcome from whether the node went live.
                    resolved.add(eid)
            if consumed:
                _inbox_drop(codoc_dir, consumed)
            if resolved >= set(targets):
                safe_write_tree(store, codoc_dir)
                from codoc.loop.status import refresh_status
                refresh_status(codoc_dir, store)
        finally:
            store.close()

    while True:
        _resolve_once()
        if resolved >= set(targets) or _time.monotonic() >= deadline:
            break
        _time.sleep(poll_interval)

    # Mark accepted (unrealized) placeholders as "editing" now so the IDE doc view
    # shimmers them as being-implemented immediately — each resolves to realized
    # content when §4's codoc_reflect/attach marks it PHASE_DONE.
    fids = [a["feature_id"] for a in accepted if a.get("feature_id")]
    if fids:
        mark_feature_phase(codoc_dir, fids, PHASE_EDITING)

    pending = [e for e in targets if e not in resolved]
    return {"ok": True, "accepted": accepted, "rejected": rejected,
            "pending": pending, "timed_out": bool(pending)}


def plan_status(codoc_dir: str) -> dict:
    """Which plan placeholders are still unrealized vs realized."""
    store = open_store(codoc_dir)
    try:
        unrealized = [{"id": f.id, "title": f.title}
                      for f in store.list_features() if not f.realized]
        return {"ok": True, "unrealized": unrealized,
                "all_realized": len(unrealized) == 0}
    finally:
        store.close()
