"""Render the store as ``tree.codoc`` text.

Live features render depth-first as ``- Title  ⟨f-id⟩`` with indented description
lines beneath. The ``⟨f-id⟩`` marker is the durable identity anchor; the IDE
collapses it with a decoration so a human never sees or types it (it is also
mirrored into ``tree.bindings.json``). Authors never write ids: a hand-added
``- Title`` line gets one minted on the next render.

Descriptions are free prose and may span multiple paragraphs (blank lines are
preserved); the node boundary is the *next* feature-marker line, never a blank
line. Code is cited inline with markdown links — ``[label](codoc:file.py#symbol)``
— so refs live inside the sentence they explain. Derived bindings (computed by
Loop A) are NOT written into the text; they ride in ``tree.bindings.json`` and
the IDE renders them as inlay-hint chips.

Pending structural proposals render last, as a git-style diff block under a
``# ── pending changes`` sentinel: ``+`` add, ``-`` retire, ``~`` move/amend,
each carrying a hidden ``⟨e-id⟩``. There is no accept/reject *syntax*; the IDE's
Accept/Reject actions write verdicts to ``.codoc/inbox.json`` (see
:mod:`codoc.loop.inbox`). The parser ignores everything past the sentinel, so
render→parse→diff stays a no-op (the round-trip invariant).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from codoc.model.event import PLAN_SOURCE, Event, NodeOpKind
from codoc.store.db import Store

TREE_FILENAME = "tree.codoc"
BINDINGS_FILENAME = "tree.bindings.json"

# Sentinel that opens the pending-changes diff block. The parser stops collecting
# features once it sees this, so proposal hunks never leak into the live tree.
PENDING_SENTINEL = "# ── pending changes"
_PENDING_HEADER = (
    "# ── pending changes ─────────────────────────────────────────────\n"
    "# Proposed by codoc — use the Accept / Reject actions above each change.\n"
)

_HEADER = (
    "# codoc feature tree — edit titles and descriptions freely; this file is the source of truth.\n"
    "# Cite code inline with markdown links: [label](codoc:file.py#symbol).\n"
)


def tree_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / TREE_FILENAME


def _description_lines(description: str, indent: str) -> list[str]:
    """Indent each prose line by ``indent + 4``; keep blank lines truly blank.

    Blank lines are paragraph breaks inside one description — the parser keeps
    them, so they must round-trip as empty lines (no indentation to strip)."""
    out: list[str] = []
    for dl in description.split("\n"):
        out.append(f"{indent}    {dl}" if dl.strip() else "")
    return out


def render_tree(store: Store) -> str:
    lines: list[str] = [_HEADER.rstrip("\n"), ""]

    def walk(parent_id: str | None, depth: int) -> None:
        indent = "  " * depth
        for f in store.children(parent_id):
            marker = "~" if f.retired else "-"
            lines.append(f"{indent}{marker} {f.title}  ⟨{f.id}⟩")
            if f.description:
                lines.extend(_description_lines(f.description, indent))
            lines.append("")
            walk(f.id, depth + 1)

    walk(None, 0)

    pending = store.pending_events()
    if pending:
        lines.append(_PENDING_HEADER.rstrip("\n"))
        lines.append("")
        for e in pending:
            lines.extend(_render_proposal(e, store))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _source_tag(e: Event) -> str:
    """Human-readable label for the origin of a proposal hunk."""
    return "agent plan" if e.source == PLAN_SOURCE else "code drift"


def _title_of(store: Store, feature_id: str | None) -> str:
    if not feature_id:
        return "(root)"
    f = store.get_feature(feature_id)
    return f.title if f else feature_id


def _proposal_desc(op_char: str, description: str) -> list[str]:
    return [f"{op_char}     {dl}" for dl in description.split("\n") if dl.strip()]


def _render_proposal(e: Event, store: Store) -> list[str]:
    """One proposal as a diff hunk. Col-0 op char (``+``/``-``/``~``) + a normal
    feature line ``- Title  ⟨e-id⟩``, so the IDE colours it like a git diff and
    can recover the event id from the hidden marker."""
    op = e.op
    eid = e.id
    tag = _source_tag(e)
    if op.kind is NodeOpKind.ADD_NODE:
        out = [f"+ - {op.title or 'Untitled'}  ⟨{eid}⟩"]
        out.extend(_proposal_desc("+", op.description or ""))
        meta = f"under {_title_of(store, op.parent_id)} · {tag}"
        if op.rationale:
            meta += f" · {op.rationale}"
        out.append(f"+     {meta}")
        return out
    if op.kind is NodeOpKind.RETIRE_NODE:
        out = [f"- ~ {_title_of(store, op.feature_id)}  ⟨{eid}⟩"]
        meta = f"retire · {tag}"
        if op.rationale:
            meta += f" · {op.rationale}"
        out.append(f"-     {meta}")
        return out
    if op.kind is NodeOpKind.MOVE_NODE:
        out = [f"~ - {_title_of(store, op.feature_id)}  ⟨{eid}⟩"]
        meta = f"move → {_title_of(store, op.parent_id)} · {tag}"
        if op.rationale:
            meta += f" · {op.rationale}"
        out.append(f"~     {meta}")
        return out
    if op.kind is NodeOpKind.AMEND:
        out = [f"~ - {_title_of(store, op.feature_id)}  ⟨{eid}⟩"]
        out.extend(_proposal_desc("~", op.description or ""))
        amend_meta = f"amend · {tag}"
        if op.rationale:
            amend_meta += f" · {op.rationale}"
        out.append(f"~     {amend_meta}")
        return out
    # safe ops are never pending, but render a generic hunk just in case
    return [f"~ - {op.kind.value}  ⟨{eid}⟩"]


def _write_sidecar(store: Store, codoc_dir: str | Path) -> None:
    """Write ``.codoc/tree.bindings.json`` atomically (tmp → rename)."""
    features = store.list_features()
    by_feature: dict[str, list[dict]] = {}
    by_file: dict[str, list[dict]] = {}
    feats_meta: dict[str, dict] = {}

    for f in features:
        bindings = store.bindings_for_feature(f.id)
        by_feature[f.id] = [{"file": b.file, "symbol": b.symbol_path} for b in bindings]
        feats_meta[f.id] = {"title": f.title, "parent_id": f.parent_id}
        for b in bindings:
            by_file.setdefault(b.file, []).append(
                {"symbol": b.symbol_path, "feature_id": f.id, "feature_title": f.title}
            )

    sidecar = {"version": 1, "by_feature": by_feature, "by_file": by_file, "features": feats_meta}
    dest = Path(codoc_dir) / BINDINGS_FILENAME
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sidecar, indent=2))
    os.replace(tmp, dest)


def write_tree(store: Store, codoc_dir: str | Path) -> Path:
    path = tree_path(codoc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_tree(store))
    _write_sidecar(store, codoc_dir)
    return path
