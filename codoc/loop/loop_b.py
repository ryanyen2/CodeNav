"""Loop B — codoc → code.

Parse the edited ``tree.codoc`` → apply proposal verdicts + direct user edits →
for edits that imply a code change, build a directive from the feature's
description + bound symbols and **queue it for the live Claude Code session** by
writing ``.codoc/realize.md`` (set status ``awaiting_impl``). The session
implements the queued directives via ``/codoc:realize`` (Read → implement →
``codoc_reflect`` → delete the file); the loop is then closed by the existing
Stop-hook reflection (``agent/hook._maybe_spawn_reflect``) or the watch daemon's
epoch-close Loop A pass — both reflect the freshly written code back into the
tree. Loop B no longer spawns a headless ``claude -p``.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from codoc.agent.base import format_prompt, load_prompt
from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import parse_tree_file
from codoc.loop import inbox, status
from codoc.loop.apply import apply_op
from codoc.loop.filenames import REALIZE_FILENAME
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import Store, open_store


@dataclass
class LoopBResult:
    accepted: int = 0
    rejected: int = 0
    user_edits: int = 0
    directives: list[str] = field(default_factory=list)
    queued: bool = False  # directives written to .codoc/realize.md for the session
    error: str = ""

    def summary(self) -> str:
        parts = [f"accepted {self.accepted}", f"rejected {self.rejected}", f"edits {self.user_edits}"]
        if self.queued:
            parts.append(f"queued {len(self.directives)} directive(s) for the session")
        if self.error:
            parts.append(f"error: {self.error}")
        return " · ".join(parts)


# Obligation/directive phrases that mark a description as a REQUEST for code
# rather than a description of code that already exists. Case-insensitive.
_IMPERATIVE_CUES = (
    r"\bshould\b", r"\bmust\b", r"\bshall\b", r"\bneeds?\s+to\b", r"\bhas\s+to\b",
    r"\bhave\s+to\b", r"\bought\s+to\b", r"\bTODO\b", r"\bFIXME\b",
)
# Base-form (imperative-mood) verbs. Descriptive prose uses the 3rd person
# ("Adds", "Validates", "Provides") or a noun phrase; a directive opens a
# sentence with the bare verb ("Add …", "Validate …"). We only match these at a
# sentence start so they don't fire mid-prose.
_IMPERATIVE_VERBS = frozenset({
    "add", "implement", "create", "make", "support", "remove", "delete",
    "rename", "refactor", "introduce", "replace", "extend", "build", "write",
    "change", "update", "allow", "enable", "handle", "wire", "hook", "expose",
    "validate", "ensure", "raise", "split", "merge", "move", "rewrite", "fix",
})


def _is_imperative(text: str | None) -> bool:
    """Heuristic: does this description REQUEST a code change (imperative mood)
    rather than DESCRIBE existing code?

    Two signals: (1) an obligation cue ("should", "must", "needs to", "TODO");
    (2) a sentence that opens with a bare base-form verb ("Add …", "Validate …")
    — descriptive prose uses the 3rd person ("Adds", "Validates") or a noun
    phrase. Intentionally a cheap, deterministic gate; an LLM classifier can
    replace it later if precision matters.
    """
    if not text or not text.strip():
        return False
    for cue in _IMPERATIVE_CUES:
        if re.search(cue, text, re.IGNORECASE):
            return True
    for sentence in re.split(r"(?:[.\n!?]+)", text):
        s = sentence.strip()
        if not s:
            continue
        first = re.split(r"[\s,;:]+", s, maxsplit=1)[0].lower()
        if first in _IMPERATIVE_VERBS:
            return True
    return False


def _implies_code(op: NodeOp, store: Store) -> bool:
    """Does this tree edit REQUEST a code change (→ spawn the coding agent)?

    The contract is *imperative detection*: documenting existing code never
    writes code. A tree edit only realizes into code when intent is explicit.
      - AMEND: spawn iff the new description is imperative ("should validate …").
        A descriptive edit ("validates …") just persists the prose, no spawn.
      - ADD_NODE: spawn iff it is an explicit plan placeholder (``realized`` is
        False) or its description is imperative. A title-only / descriptive
        hand-added node is a node, not a build request.
      - RETIRE_NODE: spawn iff the feature actually owns code to remove.
    """
    k = op.kind
    if k is NodeOpKind.AMEND:
        return _is_imperative(op.description)
    if k is NodeOpKind.ADD_NODE:
        if op.realized is False:
            return True
        return _is_imperative(op.description)
    if k is NodeOpKind.RETIRE_NODE:
        return bool(op.feature_id and store.bindings_for_feature(op.feature_id))
    return False


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


def build_realize_prompt(directives: list[str], root_dir: str) -> str:
    body = "\n\n".join(f"### {i + 1}. {d}" for i, d in enumerate(directives))
    return format_prompt(load_prompt("realize"), root_dir=root_dir, directives=body)


def realize_path(codoc_dir: str | os.PathLike) -> Path:
    return Path(codoc_dir) / REALIZE_FILENAME


def _write_realize(codoc_dir: str, prompt: str) -> None:
    """Queue the realization directives for the live session (atomic write)."""
    dest = realize_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".md.tmp")
    tmp.write_text(prompt)
    os.replace(tmp, dest)


def run_loop_b(root_dir: str, codoc_dir: str, *, dry_run: bool = False) -> LoopBResult:
    store = open_store(codoc_dir)
    try:
        return _apply_edits(store, root_dir, codoc_dir, dry_run=dry_run)
    finally:
        store.close()


def _apply_edits(store, root_dir, codoc_dir, *, dry_run) -> LoopBResult:
    res = LoopBResult()
    directive_ops: list[NodeOp] = []

    # 1. Proposal verdicts — drained from the IDE's inbox, not parsed from text.
    for v in inbox.read_verdicts(codoc_dir):
        e = store.get_event(v.event_id)
        if e is None:
            continue
        if v.accept:
            apply_op(e.op, store, source="user", applied=True)
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
                if e.op.delete_code and _implies_code(e.op, store):
                    directive_ops.append(e.op)
                else:
                    for b in store.bindings_for_feature(e.op.feature_id):
                        store.delete_binding(b.file, b.symbol_path)
            elif _implies_code(e.op, store):
                directive_ops.append(e.op)
        else:
            store.delete_event(e.id)
            res.rejected += 1
    inbox.clear(codoc_dir)

    # 2. Direct user edits (intentional → applied immediately).
    parsed = parse_tree_file(codoc_dir)
    if parsed.errors:
        import logging
        _log = logging.getLogger(__name__)
        for err in parsed.errors:
            _log.warning("tree.codoc parse warning: %s", err)
        res.error = "; ".join(parsed.errors)
    diff = diff_codoc(parsed, store)
    for op in diff.user_ops:
        apply_op(op, store, source="user", applied=True)
        res.user_edits += 1
        if _implies_code(op, store):
            directive_ops.append(op)

    res.directives = [build_directive(op, store) for op in directive_ops]
    res.directives = [d for d in res.directives if d]

    if dry_run or not res.directives:
        status.refresh_status(codoc_dir, store)
        return res

    # 3. Hand the directives to the live session: write .codoc/realize.md and set
    #    status `awaiting_impl`. No headless `claude -p`. The session implements
    #    via /codoc:realize; the loop closes when the Stop-hook reflection (or the
    #    watch daemon's epoch-close Loop A) reflects the written code back.
    prompt = build_realize_prompt(res.directives, root_dir)
    _write_realize(codoc_dir, prompt)
    res.queued = True
    status.refresh_status(
        codoc_dir, store, awaiting_impl=True, pending=len(res.directives),
        detail=f"{len(res.directives)} change(s) ready to implement — run /codoc:realize",
    )
    return res
