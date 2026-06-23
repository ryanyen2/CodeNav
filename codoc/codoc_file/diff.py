"""Diff a parsed ``tree.codoc`` against the store.

Produces the user's direct edits as :class:`NodeOp`s — AMEND / MOVE_NODE /
RETIRE_NODE for existing nodes, ADD_NODE for hand-authored ones.

Proposal verdicts are NOT derived from the text any more. Proposals render as a
display-only diff block and are accepted/rejected through ``.codoc/inbox.json``
(see :mod:`codoc.loop.inbox`), driven by the IDE's Accept/Reject actions — so
there is no ``?``/``+``/``-`` syntax to type, and a stray edit can't flip one.

Deletions of live nodes are intentionally NOT treated as retire (too easy to do
by accident); retire requires changing the marker to ``~``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codoc.codoc_file.parse import ParsedTree, extract_bold, normalize_description
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import Store


@dataclass
class CodocDiff:
    user_ops: list[NodeOp] = field(default_factory=list)
    # Steering comments (`> …`) on live nodes: (feature_id, comment text).
    # Loop B turns each into a realize directive; the post-pass re-render
    # consumes them from the text (the store never holds them).
    comments: list[tuple[str, str]] = field(default_factory=list)
    # Steering comments on hand-added nodes (no ⟨f-id⟩ yet): (title, comment).
    # Loop B resolves the freshly-minted id by title after applying the ADD —
    # without this the note would be silently destroyed by the re-render.
    new_node_comments: list[tuple[str, str]] = field(default_factory=list)
    # feature_id → spans the author NEWLY bolded in this edit (new bold minus
    # old bold). Boldening is a focus signal stronger than other revision text:
    # it rides into the directive as a `Focus:` line, and an imperative bolded
    # span queues a directive even when the description as a whole reads
    # descriptive.
    emphasis: dict[str, list[str]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.user_ops and not self.comments


def diff_codoc(parsed: ParsedTree, store: Store) -> CodocDiff:
    diff = CodocDiff()

    live = {f.id: f for f in store.list_features()}
    for node in parsed.nodes:
        f = live.get(node.id) if node.id else None
        if f is None:
            diff.user_ops.append(NodeOp(
                kind=NodeOpKind.ADD_NODE,
                title=node.title,
                description=node.description,
                parent_id=node.parent_id,
                local_id=node.local_id,  # carry the webview's node id so the minted fid matches back
            ))
            for comment in node.comments:
                diff.new_node_comments.append((node.title, comment))
            continue

        if node.retired and not f.retired:
            # A comment on a node being retired in the same save is intentionally
            # dropped — the retire directive supersedes any steering on it.
            diff.user_ops.append(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id))
            continue

        for comment in node.comments:
            diff.comments.append((f.id, comment))

        # Compare descriptions in canonical form so a trailing-whitespace-only delta
        # is NOT a phantom edit (R19), regardless of how the store got its text (a
        # parser, or a non-normalizing agent/bootstrap write). Emit the canonical form
        # so the applied AMEND leaves the store canonical.
        new_desc = normalize_description(node.description)
        # A blank parsed title must NOT overwrite the real one — that produced the
        # empty `-   ⟨f-id⟩` node (a transient mid-edit/parse blank persisted as the
        # canonical title). Keep the stored title when the parsed one is empty.
        new_title = node.title if node.title.strip() else f.title
        if new_title != f.title or new_desc != normalize_description(f.description or ""):
            old_bold = set(extract_bold(f.description or ""))
            newly = [b for b in extract_bold(node.description) if b not in old_bold]
            if newly:
                diff.emphasis[f.id] = newly
            diff.user_ops.append(NodeOp(
                kind=NodeOpKind.AMEND,
                feature_id=f.id,
                title=new_title,
                description=new_desc,
            ))
        if node.parent_id != f.parent_id:
            diff.user_ops.append(NodeOp(
                kind=NodeOpKind.MOVE_NODE,
                feature_id=f.id,
                parent_id=node.parent_id,
            ))

    return diff
