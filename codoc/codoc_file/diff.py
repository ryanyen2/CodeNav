"""Diff a parsed ``tree.codoc`` against the store.

Produces the user's direct edits as :class:`NodeOp`s — AMEND / MOVE_NODE /
RETIRE_NODE for existing nodes, ADD_NODE for hand-authored ones.

Proposal verdicts are NOT derived from the text any more. Proposals render as a
display-only diff block and are accepted/rejected through ``.codoc/inbox.json``
(see :mod:`codoc.loop.inbox`), driven by the IDE's Accept/Reject actions — so
there is no ``?``/``+``/``-`` syntax to type, and a stray edit can't flip one.

Deletions of live nodes are intentionally NOT treated as retire (too easy to do
by accident); retire requires changing the marker to ``~``.

Identity (``has_local_ids=True``, the doc channel): a node's identity is its
author-stable ``local_id`` (round-tripped via ``Feature.local_id`` in SQLite),
NOT the advisory ``fid`` field and NOT the title. A node whose ``local_id`` maps
to ANY existing feature (live or retired) is — by construction — never an ADD.
This single rule makes the undo-duplicate / zombie-clone / minted-fid attack
class impossible WITHOUT the guard-stacking they previously required: it folds
in the old ``_apply_minted_fids`` pre-pass (TipTap undo resets fid→null) and the
zombie-clone retired-set guard (a crash leaves a stale live marker). The raw-text
channel (``has_local_ids=False``) has no ``local_id`` signal, so it keeps the
``fid`` + title snapshot diff unchanged.
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


def diff_codoc(parsed: ParsedTree, store: Store, *, has_local_ids: bool = False) -> CodocDiff:
    diff = CodocDiff()

    live = {f.id: f for f in store.list_features()}
    all_feats = {f.id: f for f in store.list_features(include_retired=True)}
    retired_ids = all_feats.keys() - live.keys()
    # Doc-channel identity key: author-stable local_id → fid. Built once. A node
    # carrying a local_id that is in this map IS that feature, whatever its fid
    # field says (null after a TipTap undo, stale after a crash) — this is what
    # makes a duplicate ADD impossible without a separate guard.
    lid_to_fid = (
        {f.local_id: f.id for f in all_feats.values() if f.local_id}
        if has_local_ids else {}
    )
    # Defensive: a cloned subtree (copy-paste / heading-split) can carry a duplicate
    # local_id before the editor re-mints it. The FIRST occurrence claims the
    # feature; a later node with the same local_id is treated as a genuine ADD, never
    # a silent clobber of the original.
    claimed_local_ids: set[str] = set()

    for node in parsed.nodes:
        # Resolve identity: declared local_id (doc channel) wins; fid is advisory
        # fallback (raw-text channel, or a doc node minted before local_ids existed).
        resolved_fid: str | None = None
        if has_local_ids and node.local_id:
            if node.local_id not in claimed_local_ids:
                claimed_local_ids.add(node.local_id)
                resolved_fid = lid_to_fid.get(node.local_id)
            # else: duplicate local_id → leave resolved_fid None → ADD path below
        if resolved_fid is None and node.id and node.local_id not in claimed_local_ids:
            # fid fallback only when local_id did not already claim a feature
            resolved_fid = node.id
        elif resolved_fid is None and node.id and not node.local_id:
            resolved_fid = node.id

        f = live.get(resolved_fid) if resolved_fid else None
        if f is None:
            # A local_id / fid that maps to a RETIRED feature is a reappearing node
            # (undo / re-author) or stale crash debris — never a new ADD. Skip it;
            # reconcile_doc_presence performs the un-retire from the doc-presence delta.
            if resolved_fid and resolved_fid in retired_ids:
                continue
            # A featureHeading with no title is a mid-creation state (user typed `## `
            # but hasn't typed the title). Skip — the next settle emits the real ADD
            # once a title exists. This is now UNAMBIGUOUS: local_id keying means a
            # title-clear on an EXISTING feature resolves to that feature (the AMEND
            # branch below), never here — so a blank title in THIS branch can only be a
            # not-yet-titled new node, not an overloaded three-way guess.
            if not node.title.strip():
                continue
            diff.user_ops.append(NodeOp(
                kind=NodeOpKind.ADD_NODE,
                title=node.title,
                description=node.description,
                parent_id=node.parent_id,
                local_id=node.local_id,  # carry the webview's node id so the minted fid matches back
                realized=node.realized,  # an authored PLAN (realized=False) mints a build directive
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
        # Title-clear (Step 4 — fixes the silent-revert data-loss). A blank title is a
        # DELIBERATE clear on the doc channel, where the rich heading's title content is
        # authoritative (the user emptied it) — so apply title="". On the TEXT channel a
        # blank `-  ⟨f-id⟩` line has no structured signal and could be a transient
        # mid-edit/parse blank, so the stored title is preserved (the R19 guard). The
        # channel flag IS the "declared vs inferred" signal — no separate lifecycle attr.
        new_title = node.title if (node.title.strip() or has_local_ids) else f.title
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
