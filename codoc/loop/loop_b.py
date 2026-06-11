"""Loop B — codoc → code.

Parse the edited ``tree.codoc`` → apply proposal verdicts + direct user edits +
live doc-ahead suggestions (payload intents — the loop, not the human, applies a
suggestion: see classify row 9) → for edits that imply a code change, build a
directive from the feature's description + bound symbols and **queue it for the
live Claude Code session** by writing ``.codoc/realize.md`` (set status
``awaiting_impl``). The session implements the queued directives via
``/codoc:sync`` (Read → implement → ``codoc_reflect`` → delete the file); the
loop is then closed by the existing Stop-hook reflection
(``agent/hook._maybe_spawn_reflect``) or the watch daemon's epoch-close Loop A
pass — both reflect the freshly written code back into the tree. Loop B no
longer spawns a headless ``claude -p``.

Two ordering invariants keep text↔store coherent:
- the text diff is computed against the PRE-mutation store (verdict accepts and
  intent applies move the store ahead of the text; diffing after them would read
  the stale text as a human edit and silently revert the accepted change);
- a pass that mutated the store re-renders ``tree.codoc`` before returning, so
  the next pass diffs a caught-up text instead of a phantom one.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from codoc.agent.base import format_prompt, load_prompt
from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import parse_tree_file
from codoc.codoc_file.render import write_tree
from codoc.loop import edits as edits_channel
from codoc.loop import inbox, status
from codoc.loop.apply import apply_op
from codoc.loop.classify import implies_code
from codoc.loop.filenames import REALIZE_FILENAME
from codoc.loop.fsio import atomic_write_text
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.ids import new_directive_id
from codoc.store.db import Store, open_store


@dataclass
class LoopBResult:
    accepted: int = 0
    rejected: int = 0
    user_edits: int = 0
    directives: list[str] = field(default_factory=list)
    directive_ids: list[str] = field(default_factory=list)  # d-… ids, parallel to directives
    queued: bool = False  # directives written to .codoc/realize.md for the session
    error: str = ""

    def summary(self) -> str:
        parts = [f"accepted {self.accepted}", f"rejected {self.rejected}", f"edits {self.user_edits}"]
        if self.queued:
            parts.append(f"queued {len(self.directives)} directive(s) for the session")
        if self.error:
            parts.append(f"error: {self.error}")
        return " · ".join(parts)


def build_directive(op: NodeOp, store: Store) -> str:
    if op.kind is NodeOpKind.ADD_NODE:
        return f'NEW FEATURE: "{op.title}"\n  Intent: {op.description or "(none)"}\n  Implement this feature in the codebase.'
    if op.kind is NodeOpKind.AMEND:
        f = store.get_feature(op.feature_id)
        title = op.title or (f.title if f else op.feature_id)
        binds = [b.symbol_path for b in store.bindings_for_feature(op.feature_id)] if f else []
        loc = ", ".join(binds) if binds else "(no bound code yet)"
        files = _bound_files(op.feature_id, store)
        scope = ", ".join(files) if files else "(none yet — create where it fits)"
        return (f'UPDATE FEATURE: "{title}"\n  New intent: {op.description}\n'
                f'  Bound code: {loc}\n  Edit only: {scope}\n  Align the bound code with the new intent.')
    if op.kind is NodeOpKind.RETIRE_NODE:
        f = store.get_feature(op.feature_id)
        binds = [b.symbol_path for b in store.bindings_for_feature(op.feature_id)] if f else []
        loc = ", ".join(binds) if binds else "(no bound code)"
        files = _bound_files(op.feature_id, store)
        scope = ", ".join(files) if files else "(none)"
        return (f'RETIRE FEATURE: "{f.title if f else op.feature_id}"\n  Bound code: {loc}\n'
                f'  Edit only: {scope}\n  Remove or refactor this code so the feature no longer exists.')
    return ""


def _bound_files(feature_id: str | None, store: Store) -> list[str]:
    """Distinct repo-relative files owned by a feature — the agent's edit scope."""
    if not feature_id:
        return []
    seen: dict[str, None] = {}
    for b in store.bindings_for_feature(feature_id):
        seen.setdefault(b.file, None)
    return list(seen.keys())


def build_realize_prompt(directives: list[str], root_dir: str,
                         directive_ids: list[str] | None = None) -> str:
    """Number the directives into the realize prompt. Each heading carries its
    ``⟨d-id⟩`` (when minted) so the implementing agent can cite it back as
    ``caused_by`` when reflecting — the causality chain that lets the IDE group
    the surfaced-back changes under the doc edit that triggered them."""
    ids = directive_ids or []
    body = "\n\n".join(
        f"### {i + 1}. {f'⟨{ids[i]}⟩ ' if i < len(ids) and ids[i] else ''}{d}"
        for i, d in enumerate(directives)
    )
    return format_prompt(load_prompt("realize"), root_dir=root_dir, directives=body)


def realize_path(codoc_dir: str | os.PathLike) -> Path:
    return Path(codoc_dir) / REALIZE_FILENAME


def _write_realize(codoc_dir: str, prompt: str) -> None:
    """Queue the realization directives for the live session (atomic write)."""
    dest = realize_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(dest, prompt)


def run_loop_b(root_dir: str, codoc_dir: str, *, dry_run: bool = False) -> LoopBResult:
    with open_store(codoc_dir) as store:
        return _apply_edits(store, root_dir, codoc_dir, dry_run=dry_run)


def _apply_edits(store, root_dir, codoc_dir, *, dry_run) -> LoopBResult:
    res = LoopBResult()
    # (op, feature_id-or-"", caused_by) per code-implying edit — feature_id feeds
    # the doc-wins hold set, caused_by the causality chain (suggestion/event id).
    directive_ops: list[tuple[NodeOp, str, str]] = []

    # 0. Snapshot the text diff BEFORE any store mutation. Verdict accepts and
    #    intent applies move the store ahead of the on-disk text; diffing after
    #    them would read the stale text as a human edit and revert the change.
    annotations = edits_channel.drain_annotations(codoc_dir)
    parsed = parse_tree_file(codoc_dir)
    if parsed.errors:
        import logging
        _log = logging.getLogger(__name__)
        for err in parsed.errors:
            _log.warning("tree.codoc parse warning: %s", err)
        res.error = "; ".join(parsed.errors)
    diff = diff_codoc(parsed, store)

    def _accept_with_fid(op: NodeOp) -> str:
        """Apply an accepted op, recovering a freshly-minted ADD feature id by
        set-diff (the op itself carries none until applied)."""
        if op.kind is NodeOpKind.ADD_NODE and not op.feature_id:
            before = {f.id for f in store.list_features()}
            apply_op(op, store, source="user", applied=True)
            new = {f.id for f in store.list_features()} - before
            return next(iter(new)) if new else ""
        apply_op(op, store, source="user", applied=True)
        return op.feature_id or ""

    # 1. Proposal verdicts — drained from the IDE's inbox, not parsed from text.
    for v in inbox.read_verdicts(codoc_dir):
        e = store.get_event(v.event_id)
        if e is None:
            continue
        if v.accept:
            fid = _accept_with_fid(e.op)
            store.delete_event(e.id)
            res.accepted += 1
            # RETIRE accepted from the inbox is detach-only by default: mark retired
            # (apply_op) AND detach its bindings here, so the code isn't left bound to
            # a now-hidden feature (which all_bindings still returns → reconcile treats
            # it as covered → silently orphaned). Detaching frees the chunks for the
            # next state pass to re-home, making a false retire self-healing. It must
            # NEVER queue a code-deletion directive by default — a retire Loop A
            # proposed off transient drift could be a false positive, and deleting
            # code on accept is the most destructive failure mode.
            #   EXCEPTION: an explicit delete-code retire (op.delete_code — set by an
            #   agent via codoc_propose_retire(delete_code=True), the MCP-side parity
            #   for a human `~` edit) keeps its bindings and queues a removal directive,
            #   exactly like the human text path (step 2). The code is removed by the
            #   agent and reconcile detaches then.
            if e.op.kind is NodeOpKind.RETIRE_NODE:
                if e.op.delete_code and implies_code(e.op, store):
                    directive_ops.append((e.op, fid, e.id))
                else:
                    for b in store.bindings_for_feature(e.op.feature_id):
                        store.delete_binding(b.file, b.symbol_path)
            elif implies_code(e.op, store):
                directive_ops.append((e.op, fid, e.id))
        else:
            store.delete_event(e.id)
            res.rejected += 1
    inbox.clear(codoc_dir)

    # 2. Direct user edits (intentional → applied immediately; diff snapshot from
    #    step 0). The IDE host annotates each settle with WHO authored it
    #    (edits.json); ops on features with no annotation default to human/pen
    #    (a raw-text edit).
    for op in diff.user_ops:
        ann = annotations.get(op.feature_id or "")
        ev = apply_op(op, store, source="user", applied=True,
                      actor=(ann.actor if ann else ""), mode=(ann.mode if ann else ""),
                      caused_by=(ann.suggestion_id if ann else ""))
        res.user_edits += 1
        if implies_code(op, store):
            # The directive's cause: the doc-ahead suggestion this settle applied
            # (if the host told us), else the user-op event itself.
            cause = (ann.suggestion_id if ann and ann.suggestion_id else ev.id)
            directive_ops.append((op, op.feature_id or "", cause))

    # 2.5 Doc-ahead suggestions (classify row 9). The host registers each
    #     suggesting-mode edit as a payload-carrying intent in edits.json; the
    #     LOOP applies it — the agent-side "apply". The human's only verb on
    #     their own suggestion is Withdraw, which removes the intent before this
    #     drain ever sees it. Application is the same row-7/8 path as a settle:
    #     a descriptive suggestion just persists, an imperative one also queues
    #     a directive whose caused_by is the suggestion id. An intent whose
    #     payload already matches the store is satisfied → skipped (the host
    #     clears it from edits.json on its next pass; stateless dedup).
    now_ms = int(time.time() * 1000)
    for intent in edits_channel.read_intents(codoc_dir):
        if intent.title is None and intent.description is None:
            continue  # hold-only intent (no payload) — nothing to apply
        if intent.ts and now_ms - intent.ts > edits_channel.INTENT_STALE_MS:
            continue  # abandoned suggestion — the hold backstop ignores it too
        f = store.get_feature(intent.feature_id)
        if f is None or f.retired:
            continue
        title = intent.title if intent.title is not None and intent.title != f.title else None
        desc = (intent.description
                if intent.description is not None and intent.description != (f.description or "")
                else None)
        if title is None and desc is None:
            continue  # satisfied — already applied (or the text caught up)
        op = NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id, title=title, description=desc,
                    rationale="doc-ahead suggestion")
        apply_op(op, store, source="user", applied=True,
                 actor=intent.actor or "human", mode="suggest", caused_by=intent.id)
        res.user_edits += 1
        if implies_code(op, store):
            directive_ops.append((op, f.id, intent.id))

    # Re-render tree.codoc when this pass moved the store ahead of the text
    # (verdict accepts / intent applies) — otherwise the next pass would diff the
    # stale text and read it as a human edit reverting the change. User text
    # edits were absorbed above, so regenerating from the store loses nothing.
    if res.accepted or res.user_edits:
        write_tree(store, codoc_dir)

    rendered = [(build_directive(op, store), fid, cause, op) for op, fid, cause in directive_ops]
    rendered = [r for r in rendered if r[0]]
    res.directives = [r[0] for r in rendered]

    if dry_run or not res.directives:
        status.refresh_status(codoc_dir, store)
        return res

    # 3. Hand the directives to the live session: write .codoc/realize.md (each
    #    heading carries its minted ⟨d-id⟩) + the machine-readable realize.json
    #    manifest (ids → features → causes: the hold set + causality chain), and
    #    set status `awaiting_impl`. No headless `claude -p`. The session
    #    implements via /codoc:sync; the loop closes when the Stop-hook
    #    reflection (or the watch daemon's epoch-close Loop A) reflects the
    #    written code back.
    res.directive_ids = [new_directive_id() for _ in rendered]
    prompt = build_realize_prompt(res.directives, root_dir, res.directive_ids)
    _write_realize(codoc_dir, prompt)
    edits_channel.write_manifest(codoc_dir, [
        edits_channel.Directive(id=did, feature_id=fid, kind=op.kind.value, caused_by=cause)
        for did, (_, fid, cause, op) in zip(res.directive_ids, rendered)
    ])
    res.queued = True
    status.refresh_status(
        codoc_dir, store, awaiting_impl=True, pending=len(res.directives),
        detail=f"{len(res.directives)} change(s) ready to implement — run /codoc:sync",
    )
    return res
