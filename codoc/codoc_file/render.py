"""Render the store as ``tree.codoc`` text.

Live features render depth-first as ``- Title  ⟨f-id⟩`` with indented description
lines. Pending proposals render below, each as a ``?``-prefixed block carrying its
``⟨e-id⟩``. Re-parsing freshly rendered text yields the identical tree and only
``?`` (pending) proposals, so render→parse→diff is a no-op (the round-trip
invariant).
"""
from __future__ import annotations

from pathlib import Path

from codoc.model.event import Event, NodeOpKind
from codoc.store.db import Store

TREE_FILENAME = "tree.codoc"

_HEADER = (
    "# codoc feature tree — edit titles/descriptions directly; this file is the source of truth.\n"
    "# Proposals appear as '?' blocks: change '?'→'+' to accept, '?'→'-' (or delete) to reject.\n"
)


def tree_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / TREE_FILENAME


def render_tree(store: Store) -> str:
    lines: list[str] = [_HEADER.rstrip("\n"), ""]

    def walk(parent_id: str | None, depth: int) -> None:
        indent = "  " * depth
        for f in store.children(parent_id):
            lines.append(f"{indent}- {f.title}  ⟨{f.id}⟩")
            if f.description:
                for dl in f.description.splitlines():
                    lines.append(f"{indent}    {dl}")
            lines.append("")
            walk(f.id, depth + 1)

    walk(None, 0)

    pending = store.pending_events()
    if pending:
        lines.append("# ── proposals ──────────────────────────────────")
        lines.append("")
        for e in pending:
            lines.extend(_render_proposal(e, store))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _title_of(store: Store, feature_id: str | None) -> str:
    if not feature_id:
        return "(root)"
    f = store.get_feature(feature_id)
    return f.title if f else feature_id


def _render_proposal(e: Event, store: Store) -> list[str]:
    op = e.op
    eid = e.id
    if op.kind is NodeOpKind.ADD_NODE:
        out = [f'? add "{op.title or "Untitled"}"  ⟨{eid}⟩']
        if op.description:
            out.append(f"?     {op.description}")
        meta = f"parent: {_title_of(store, op.parent_id)}"
        if op.rationale:
            meta += f" · {op.rationale}"
        out.append(f"?     {meta}")
        return out
    if op.kind is NodeOpKind.RETIRE_NODE:
        out = [f'? retire "{_title_of(store, op.feature_id)}"  ⟨{eid}⟩']
        if op.rationale:
            out.append(f"?     {op.rationale}")
        return out
    if op.kind is NodeOpKind.MOVE_NODE:
        out = [f'? move "{_title_of(store, op.feature_id)}" → {_title_of(store, op.parent_id)}  ⟨{eid}⟩']
        if op.rationale:
            out.append(f"?     {op.rationale}")
        return out
    if op.kind is NodeOpKind.AMEND:
        out = [f'? amend "{_title_of(store, op.feature_id)}"  ⟨{eid}⟩']
        if op.description:
            out.append(f"?     {op.description}")
        if op.rationale:
            out.append(f"?     · {op.rationale}")
        return out
    # safe ops are never pending, but render a generic line just in case
    return [f'? {op.kind.value}  ⟨{eid}⟩']


def write_tree(store: Store, codoc_dir: str | Path) -> Path:
    path = tree_path(codoc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_tree(store))
    return path
