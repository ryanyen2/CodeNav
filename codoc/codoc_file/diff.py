"""Diff a parsed ``tree.codoc`` against the store.

Produces (a) the user's direct edits as :class:`NodeOp`s — AMEND / MOVE_NODE /
RETIRE_NODE for existing nodes, ADD_NODE for hand-authored ones — and (b)
verdicts on pending proposals based on each block's leading action char:
``+`` accept, ``-`` or deleted = reject, ``?`` = still pending (no verdict).

Deletions of live nodes are intentionally NOT treated as retire (too easy to do
by accident); retire requires changing the marker to ``~``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codoc.codoc_file.parse import ParsedTree
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import Store


@dataclass
class Verdict:
    event_id: str
    accept: bool


@dataclass
class CodocDiff:
    user_ops: list[NodeOp] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.user_ops and not self.verdicts


def diff_codoc(parsed: ParsedTree, store: Store) -> CodocDiff:
    diff = CodocDiff()

    # 1. Proposal verdicts.
    for e in store.pending_events():
        action = parsed.proposal_actions.get(e.id)  # None ⇒ block removed
        if action == "+":
            diff.verdicts.append(Verdict(e.id, accept=True))
        elif action is None or action == "-":
            diff.verdicts.append(Verdict(e.id, accept=False))
        # '?' → still pending, no verdict

    # 2. Direct user edits to live nodes.
    live = {f.id: f for f in store.list_features()}
    for node in parsed.nodes:
        f = live.get(node.id) if node.id else None
        if f is None:
            diff.user_ops.append(NodeOp(
                kind=NodeOpKind.ADD_NODE,
                title=node.title,
                description=node.description,
                parent_id=node.parent_id,
            ))
            continue

        if node.retired and not f.retired:
            diff.user_ops.append(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id))
            continue

        if node.title != f.title or node.description != (f.description or ""):
            diff.user_ops.append(NodeOp(
                kind=NodeOpKind.AMEND,
                feature_id=f.id,
                title=node.title,
                description=node.description,
            ))
        if node.parent_id != f.parent_id:
            diff.user_ops.append(NodeOp(
                kind=NodeOpKind.MOVE_NODE,
                feature_id=f.id,
                parent_id=node.parent_id,
            ))

    return diff
