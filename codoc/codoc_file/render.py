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

Pending proposals render as an **in-place overlay**. ADD and MOVE proposals emit
a ghost hunk in the text at their destination parent — a line carrying the diff op
char in column 0 (``+`` add, ``~`` move) followed by the node indented to its tree
depth, and a hidden ``⟨e-id⟩`` — because an add has no live node and a move shows
where the node will land. RETIRE and AMEND, by contrast, modify an *existing*
node, so they emit NO text: they ride in the sidecar's ``proposals`` map
(``_proposals_map``) and the IDE decorates the live node in place (strike-through
for retire, an inline title/description diff for amend). Keeping the live node's
text byte-identical to a clean render is what makes render→parse→diff a no-op for
retire/amend; the ADD/MOVE ghost hunks stay round-trip-safe because the parser
skips any block whose first line matches both the proposal shape and a ``⟨e-id⟩``
marker (live nodes carry ``⟨f-id⟩``). There is no accept/reject *syntax*; the
IDE's Accept/Reject actions write verdicts to ``.codoc/inbox.json`` (see
:mod:`codoc.loop.inbox`).
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

from codoc.model.event import LOOP_A_AGENT_SOURCE, PLAN_SOURCE, Event, NodeOpKind
from codoc.store.db import Store

TREE_FILENAME = "tree.codoc"
BINDINGS_FILENAME = "tree.bindings.json"

# Legacy sentinel that opened the old bottom-of-file pending-changes block.
# Proposals now render in situ, but the parser still honours this sentinel so it
# can read trees written by older versions of codoc.
PENDING_SENTINEL = "# ── pending changes"

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
    # lines: list[str] = [_HEADER.rstrip("\n"), ""]
    lines: list[str] = []

    pending = store.pending_events()
    # Only ADD/MOVE proposals emit *text* (a ghost node at the destination parent):
    # an ADD has no live node to anchor to, and a MOVE shows where the node will
    # land. RETIRE/AMEND mutate an existing node, so they ride in the sidecar
    # (`_proposals_map`) and the IDE decorates the live node in place — keeping the
    # node's text identical to a clean render, which preserves render→parse→diff.
    ghosts_by_parent: dict[str | None, list[Event]] = defaultdict(list)
    for e in pending:
        if e.op.kind in (NodeOpKind.ADD_NODE, NodeOpKind.MOVE_NODE):
            ghosts_by_parent[e.op.parent_id].append(e)
    emitted: set[str] = set()

    def emit_proposal(e: Event, depth: int) -> None:
        lines.extend(_render_proposal(e, store, depth))
        lines.append("")
        emitted.add(e.id)

    def walk(parent_id: str | None, depth: int) -> None:
        indent = "  " * depth
        for f in store.children(parent_id):
            marker = "~" if f.retired else "-"
            lines.append(f"{indent}{marker} {f.title}  ⟨{f.id}⟩")
            if f.description:
                lines.extend(_description_lines(f.description, indent))
            lines.append("")
            walk(f.id, depth + 1)
        for e in ghosts_by_parent.get(parent_id, []):
            emit_proposal(e, depth)

    walk(None, 0)
    # ADD/MOVE ghosts whose destination parent isn't in the live tree (stale ref)
    # still surface at the root so a verdict is always reachable.
    for e in pending:
        if e.id not in emitted and e.op.kind in (NodeOpKind.ADD_NODE, NodeOpKind.MOVE_NODE):
            emit_proposal(e, 0)

    return "\n".join(lines).rstrip() + "\n"


def _source_tag(e: Event) -> str:
    """Human-readable label for the origin of a proposal."""
    if e.source == PLAN_SOURCE:
        return "agent plan"
    if e.source == LOOP_A_AGENT_SOURCE:
        return "agent reflection"
    return "code drift"


def _proposals_map(store: Store) -> dict[str, dict]:
    """Sidecar payload describing pending proposals for the IDE to render in place.

    ``by_feature`` keys RETIRE/AMEND (and the *source* annotation of a MOVE) by
    the live ``feature_id`` they decorate; ``by_event`` keys ADD/MOVE *ghosts*
    (the text hunks) by ``event_id`` so the IDE can show details + Accept/Reject
    without re-parsing. ``by_parent`` lists the ADD/MOVE event ids landing under
    each destination parent (``""`` = top level) so the IDE can anchor an
    Accept/Reject affordance at the parent node, not only on the ghost line. Both
    halves carry the origin ``tag`` and ``rationale``.
    """
    by_feature: dict[str, dict] = {}
    by_event: dict[str, dict] = {}
    by_parent: dict[str, list[str]] = {}
    for e in store.pending_events():
        op = e.op
        tag = _source_tag(e)
        if op.kind is NodeOpKind.RETIRE_NODE and op.feature_id:
            by_feature[op.feature_id] = {
                "op": "retire", "event_id": e.id, "tag": tag, "rationale": op.rationale,
            }
        elif op.kind is NodeOpKind.AMEND and op.feature_id:
            by_feature[op.feature_id] = {
                "op": "amend", "event_id": e.id, "tag": tag, "rationale": op.rationale,
                "title": op.title, "description": op.description,
            }
        elif op.kind is NodeOpKind.ADD_NODE:
            by_event[e.id] = {
                "op": "add", "parent_id": op.parent_id, "tag": tag, "rationale": op.rationale,
                "title": op.title, "description": op.description,
            }
            by_parent.setdefault(op.parent_id or "", []).append(e.id)
        elif op.kind is NodeOpKind.MOVE_NODE:
            # The destination ghost (text) conveys the move; the IDE can dim the
            # source node by scanning `by_event` for op=="move" and its feature_id.
            by_event[e.id] = {
                "op": "move", "feature_id": op.feature_id, "parent_id": op.parent_id,
                "tag": tag, "rationale": op.rationale,
            }
            by_parent.setdefault(op.parent_id or "", []).append(e.id)
    return {"by_feature": by_feature, "by_event": by_event, "by_parent": by_parent}


def _title_of(store: Store, feature_id: str | None) -> str:
    if not feature_id:
        return "(root)"
    f = store.get_feature(feature_id)
    return f.title if f else feature_id


def _render_proposal(e: Event, store: Store, depth: int) -> list[str]:
    """An ADD/MOVE proposal as an in-situ ghost hunk (the only proposals in text).

    Column 0 is the diff op char (``+`` add, ``~`` move); a single space follows;
    then the ghost node rendered at its tree ``depth`` (``{indent}- Title  ⟨e-id⟩``),
    so the IDE colours the block like a git diff and can recover the event id from
    the hidden marker. Continuation lines repeat the op char so the parser can skip
    the whole block; a blank line terminates it. (RETIRE/AMEND never reach here —
    they decorate the live node via the sidecar ``_proposals_map``.)
    """
    op = e.op
    eid = e.id
    tag = _source_tag(e)
    indent = "  " * depth

    def title_line(o: str, marker: str, title: str) -> str:
        return f"{o} {indent}{marker} {title}  ⟨{eid}⟩"

    def cont(o: str, text: str) -> list[str]:
        return [f"{o} {indent}    {dl}" for dl in text.split("\n") if dl.strip()]

    if op.kind is NodeOpKind.ADD_NODE:
        meta = f"{tag} · {op.rationale}" if op.rationale else tag
        return [title_line("+", "-", op.title or "Untitled"),
                *cont("+", op.description or ""), *cont("+", meta)]
    if op.kind is NodeOpKind.MOVE_NODE:
        meta = f"move → {_title_of(store, op.parent_id)} · {tag}" + (f" · {op.rationale}" if op.rationale else "")
        return [title_line("~", "-", _title_of(store, op.feature_id)), *cont("~", meta)]
    # RETIRE/AMEND/safe ops are not emitted as text; render a generic hunk just in case.
    return [title_line("~", "-", op.kind.value)]


def _compute_feature_edges(store: Store) -> dict[str, list[dict]]:
    """Aggregate symbol-level call/import edges into feature-level coupling.

    Returns ``{src_feature_id: [{to: dst_feature_id, weight: int, kinds: list[str]}]}``.
    Used by the VS Code extension for dependency-focus opacity dimming.
    """
    sym2feat = {b.symbol_path: b.feature_id for b in store.all_bindings()}
    agg: dict[tuple[str, str], dict] = {}  # (src_fid, dst_fid) → {weight, kinds}
    for e in store.all_edges(internal_only=True):
        dst = e["dst_symbol"]
        if not dst:
            continue
        sf = sym2feat.get(e["src_symbol"])
        df = sym2feat.get(dst)
        if not sf or not df or sf == df:
            continue
        slot = agg.setdefault((sf, df), {"weight": 0, "kinds": set()})
        slot["weight"] += 1
        slot["kinds"].add(e["kind"])
    out: dict[str, list[dict]] = {}
    for (sf, df), v in agg.items():
        out.setdefault(sf, []).append(
            {"to": df, "weight": v["weight"], "kinds": sorted(v["kinds"])}
        )
    return out


def _write_sidecar(store: Store, codoc_dir: str | Path) -> None:
    """Write ``.codoc/tree.bindings.json`` atomically (tmp → rename)."""
    features = store.list_features()
    by_feature: dict[str, list[dict]] = {}
    by_file: dict[str, list[dict]] = {}
    feats_meta: dict[str, dict] = {}

    for f in features:
        bindings = store.bindings_for_feature(f.id)
        by_feature[f.id] = [{"file": b.file, "symbol": b.symbol_path} for b in bindings]
        feats_meta[f.id] = {"title": f.title, "parent_id": f.parent_id, "realized": f.realized}
        for b in bindings:
            by_file.setdefault(b.file, []).append(
                {"symbol": b.symbol_path, "feature_id": f.id, "feature_title": f.title}
            )

    sidecar = {"version": 3, "by_feature": by_feature, "by_file": by_file, "features": feats_meta, "feature_edges": _compute_feature_edges(store), "proposals": _proposals_map(store)}
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
