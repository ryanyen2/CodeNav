"""Render the store as ``tree.codoc`` text.

Live features render depth-first as ``- Title  ⟨f-id⟩`` with indented description
lines. Pending proposals render below, each as a ``?``-prefixed block carrying its
``⟨e-id⟩``. Re-parsing freshly rendered text yields the identical tree and only
``?`` (pending) proposals, so render→parse→diff is a no-op (the round-trip
invariant).

Bindings are surfaced inline as a ``↪ refs:`` line (parse.py skips these so they
never appear in the description or break round-trip). The full registry is also
written to ``.codoc/tree.bindings.json`` for the IDE extension to consume without
an HTTP server.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from codoc.model.event import Event, NodeOpKind
from codoc.store.db import Store

TREE_FILENAME = "tree.codoc"
BINDINGS_FILENAME = "tree.bindings.json"
_REFS_MAX_FILES = 4       # files shown on a refs line before "+N more files"
_REFS_MAX_PER_FILE = 4    # symbols shown per file before "+N"

_HEADER = (
    "# codoc feature tree — edit titles/descriptions directly; this file is the source of truth.\n"
    "# Proposals appear as '?' blocks: change '?'→'+' to accept, '?'→'-' (or delete) to reject.\n"
)


def tree_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / TREE_FILENAME


def _leaf(symbol_path: str) -> str:
    """The display name of a chunk: the qualified part after ``::``.

    ``"adapters.py::HTTPAdapter.send"`` → ``"HTTPAdapter.send"``; a module-level
    chunk ``"certs.py::__module__"`` → ``"‹module›"``.
    """
    qualified = symbol_path.split("::", 1)[1] if "::" in symbol_path else symbol_path
    return "‹module›" if qualified == "__module__" else qualified


def _refs_line(bindings: list, indent: str) -> str:
    """One inline reference line, grouped by file: ``file › a, b, c +N  ·  …``.

    Grouping by file keeps the filename from repeating once per symbol and makes
    a feature's spread across files legible at a glance.
    """
    by_file: dict[str, list[str]] = {}
    for b in bindings:
        by_file.setdefault(b.file, []).append(_leaf(b.symbol_path))

    files = sorted(by_file)
    segments: list[str] = []
    for f in files[:_REFS_MAX_FILES]:
        leaves = sorted(by_file[f])
        shown = leaves[:_REFS_MAX_PER_FILE]
        seg = f"{f} › {', '.join(shown)}"
        extra = len(leaves) - len(shown)
        if extra > 0:
            seg += f" +{extra}"
        segments.append(seg)

    line = f"{indent}↪ refs: " + "  ·  ".join(segments)
    extra_files = len(files) - _REFS_MAX_FILES
    if extra_files > 0:
        line += f"  ·  +{extra_files} more file{'s' if extra_files != 1 else ''}"
    return line


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
            bindings = store.bindings_for_feature(f.id)
            if bindings:
                lines.append(_refs_line(bindings, indent + "  "))
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
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(sidecar, indent=2))
    tmp.rename(dest)


def write_tree(store: Store, codoc_dir: str | Path) -> Path:
    path = tree_path(codoc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_tree(store))
    _write_sidecar(store, codoc_dir)
    return path
