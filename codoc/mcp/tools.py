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

from pathlib import Path

from codoc.codoc_file.render import write_tree
from codoc.loop.apply import apply_op, should_auto_apply
from codoc.model.event import (
    LOOP_A_AGENT_SOURCE,
    PLAN_SOURCE,
    NodeOp,
    NodeOpKind,
)
from codoc.store.db import Store, open_store


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
        write_tree(store, codoc_dir)
        return {"ok": True, "event_id": ev.id, "applied": applied,
                "summary": _op_summary(op, store)}
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
            applied_n += int(applied)
            proposed_n += int(not applied)
            results.append({"ok": True, "event_id": ev.id, "applied": applied,
                            "summary": _op_summary(op, store)})
        write_tree(store, codoc_dir)
        return {"ok": True, "applied": applied_n, "proposed": proposed_n, "results": results}
    finally:
        store.close()


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
