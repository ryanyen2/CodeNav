"""Loop B — codoc → code.

Parse the edited ``tree.codoc`` → apply proposal verdicts + direct user edits +
live doc-ahead suggestions (payload intents — the loop, not the human, applies a
suggestion: see classify row 9) + inline ``> …`` steering comments (notes
addressed to the agent; always a directive, consumed from the text by the
end-of-pass re-render) → for edits that imply a code change, build a
directive from the feature's description + bound symbols and **queue it for the
live Claude Code session** by writing ``.codoc/realize.md`` (set status
``awaiting_impl``). An in-flight queue is appended to (via the manifest's
directive texts), never clobbered — steering works mid-realization. The session implements the queued directives via
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
from codoc.codoc_file.doc_parse import parse_doc_file
from codoc.codoc_file.parse import extract_bold, extract_links, parse_tree_file
from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop import edits as edits_channel
from codoc.loop import inbox, status
from codoc.loop.apply import apply_op
from codoc.loop.classify import implies_code, is_imperative
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
    steered: int = 0  # inline `> …` steering comments drained this pass
    canceled: int = 0  # queued directives withdrawn this pass (U6)
    directives: list[str] = field(default_factory=list)
    directive_ids: list[str] = field(default_factory=list)  # d-… ids, parallel to directives
    queued: bool = False  # directives written to .codoc/realize.md for the session
    queued_total: int = 0  # whole queue size after this pass (existing + new)
    # One human-readable note per applied user edit: the feature, a truncated snippet of
    # the new text, and whether it queued a directive ("→ realize") or was documentation
    # only ("doc-only"). Listed under the summary so a watcher can SEE what each edit was
    # and why it did/didn't decorate — instead of an opaque "edits 2".
    edit_notes: list[str] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        parts = [f"accepted {self.accepted}", f"rejected {self.rejected}", f"edits {self.user_edits}"]
        if self.steered:
            parts.append(f"steered {self.steered}")
        if self.canceled:
            parts.append(f"withdrew {self.canceled} directive(s)")
        if self.queued:
            total = self.queued_total or len(self.directives)
            parts.append(f"queued {total} directive(s) for the session")
        if self.error:
            parts.append(f"error: {self.error}")
        line = " · ".join(parts)
        if self.edit_notes:
            line += "".join(f"\n    • {n}" for n in self.edit_notes)
        return line


def _signal_lines(text: str | None, *, emphasis: list[str] | None = None) -> str:
    """``Focus:`` (bolded spans) + ``Consult:`` (external links) suffix lines for
    a directive. ``emphasis`` (the spans newly bolded by this edit) takes
    precedence over re-extracting every bold span from the text."""
    lines: list[str] = []
    spans = emphasis if emphasis is not None else extract_bold(text or "")
    if spans:
        joined = "; ".join(f'"{s}"' for s in spans)
        lines.append(f"  Focus: {joined}  (the author bolded these — highest-priority intent)")
    for link in extract_links(text or ""):
        label = f"  ({link.label})" if link.label else ""
        lines.append(f"  Consult: {link.url}{label}")
    return ("\n" + "\n".join(lines)) if lines else ""


def _edit_label(op: NodeOp, store: Store, will_queue: bool) -> str:
    """A one-line note for the watch log: which feature an edit touched, a truncated
    snippet of the new text, and whether it queued a code directive (``→ realize``) or
    was documentation only (``doc-only``). Lets a watcher SEE what each edit was — and
    why it did or didn't produce a pending decoration — instead of an opaque ``edits 2``."""
    f = store.get_feature(op.feature_id) if op.feature_id else None
    title = f.title if f else (op.title or op.feature_id or "?")
    text = (op.description or op.title or "").replace("\n", " ").strip()
    snippet = (text[:60] + "…") if len(text) > 60 else text
    tag = "→ realize" if will_queue else "doc-only"
    return f'{op.kind.value} "{title}" [{tag}]: {snippet!r}'


def build_directive(op: NodeOp, store: Store, *, emphasis: list[str] | None = None) -> str:
    if op.kind is NodeOpKind.ADD_NODE:
        return (f'NEW FEATURE: "{op.title}"\n  Intent: {op.description or "(none)"}\n'
                f'  Implement this feature in the codebase.'
                + _signal_lines(op.description))
    if op.kind is NodeOpKind.AMEND:
        f = store.get_feature(op.feature_id)
        title = op.title or (f.title if f else op.feature_id)
        loc, files = _bound_code(op.feature_id, store) if f else ("", [])
        loc = loc or "(no bound code yet)"
        scope = ", ".join(files) if files else "(none yet — create where it fits)"
        return (f'UPDATE FEATURE: "{title}"\n  New intent: {op.description}\n'
                f'  Bound code: {loc}\n  Edit only: {scope}\n  Align the bound code with the new intent.'
                + _signal_lines(op.description, emphasis=emphasis))
    if op.kind is NodeOpKind.RETIRE_NODE:
        f = store.get_feature(op.feature_id)
        loc, files = _bound_code(op.feature_id, store) if f else ("", [])
        loc = loc or "(no bound code)"
        scope = ", ".join(files) if files else "(none)"
        return (f'RETIRE FEATURE: "{f.title if f else op.feature_id}"\n  Bound code: {loc}\n'
                f'  Edit only: {scope}\n  Remove or refactor this code so the feature no longer exists.')
    return ""


def build_steer_directive(feature_id: str, comment: str, store: Store) -> str:
    """An inline ``> …`` comment is an explicit note to the agent — imperative by
    construction (the author addressed the agent, not the prose). It steers the
    feature's code without rewriting its description: useful mid-generation,
    when editing the description directly is the wrong tool."""
    f = store.get_feature(feature_id)
    if f is None:
        return ""
    loc, files = _bound_code(feature_id, store)
    loc = loc or "(no bound code yet)"
    scope = ", ".join(files) if files else "(none yet — create where it fits)"
    note = comment.replace("\n", "\n    ")
    return (f'STEER FEATURE: "{f.title}"\n  Author note: {note}\n'
            f'  Bound code: {loc}\n  Edit only: {scope}\n'
            f'  Apply the note to this feature\'s code; where it conflicts with the '
            f'description, the note wins.'
            + _signal_lines(comment))


def _bound_code(feature_id: str | None, store: Store) -> tuple[str, list[str]]:
    """One bindings fetch → (joined symbol paths, distinct repo-relative files).

    The symbols are the directive's ``Bound code:`` line, the files its
    ``Edit only:`` scope."""
    if not feature_id:
        return "", []
    binds = store.bindings_for_feature(feature_id)
    files = list(dict.fromkeys(b.file for b in binds))
    return ", ".join(b.symbol_path for b in binds), files


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


def _reinsert_comments(codoc_dir: str, targets: list[tuple[str, str]]) -> None:
    """A dry pass re-rendered the text (store-driven, so the ``> …`` lines are
    gone) but must not consume the un-queued notes — re-insert each under its
    feature's title line so a later real pass can drain it."""
    path = tree_path(codoc_dir)
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    # Group by feature so MULTIPLE notes on one feature re-insert as DISTINCT
    # `>` runs separated by a blank line. Adjacent `>` lines would merge into a
    # single comment on the next parse (parse.py: a contiguous run is one
    # comment), silently collapsing two steering notes into one.
    by_fid: dict[str, list[str]] = {}
    for fid, comment in targets:
        if fid:
            by_fid.setdefault(fid, []).append(comment)
    for fid, comments in by_fid.items():
        marker = f"⟨{fid}⟩"
        for i, ln in enumerate(lines):
            if marker in ln:
                indent = " " * (len(ln) - len(ln.lstrip()) + 4)
                block: list[str] = []
                for j, comment in enumerate(comments):
                    if j:
                        block.append("")  # blank separates distinct `>` runs
                    block.extend(f"{indent}> {cl}".rstrip()
                                 for cl in comment.splitlines())
                lines[i + 1:i + 1] = block
                break
    atomic_write_text(path, "\n".join(lines) + "\n")


def _rewrite_queue(root_dir: str, codoc_dir: str, survivors: list) -> None:
    """Persist ``survivors`` as the realization queue: rewrite ``realize.json`` and
    rebuild ``realize.md`` from the survivors carrying rendered text — or remove BOTH
    when the queue empties (so status falls back to in_sync/code_drift and no stale
    directive lingers). Shared by withdraw (U6) and per-feature supersede. Legacy
    text-less entries stay in the manifest (they still hold their feature) but can't be
    re-rendered into realize.md; a later real pass rewrites the queue."""
    if not survivors:
        try:
            realize_path(codoc_dir).unlink()
        except OSError:
            pass
        edits_channel.clear_manifest(codoc_dir)
        return
    edits_channel.write_manifest(codoc_dir, survivors)
    # realize.md carries handed-off directives only; held drafts stay in the manifest
    # (surfaced as the in-situ diff) without a trigger. No handed-off survivor ⇒ remove
    # the trigger but keep the manifest (the drafts live on until hand-off).
    handed = [d for d in survivors if d.text and d.handed_off]
    if handed:
        _write_realize(codoc_dir, build_realize_prompt(
            [d.text for d in handed], root_dir, [d.id for d in handed]))
    else:
        try:
            realize_path(codoc_dir).unlink()
        except OSError:
            pass


def _apply_cancellations(root_dir: str, codoc_dir: str) -> int:
    """U6 — withdraw queued realizations. Drain the host's cancellations (feature
    ids) and prune the matching directives from the queue (rewrite the manifest +
    rebuild/remove ``realize.md`` via :func:`_rewrite_queue`). Pruning releases the
    doc-wins hold for those features (``hold_set`` reads the manifest). The committed
    prose is untouched — withdraw cancels the code work, not the documented intent.

    Returns the number of directives removed (0 when nothing matched — a cancel for
    an already-realized / never-queued feature is a harmless no-op)."""
    cancels = set(edits_channel.drain_cancellations(codoc_dir))
    if not cancels:
        return 0
    existing = edits_channel.read_manifest(codoc_dir)
    survivors = [d for d in existing if d.feature_id not in cancels]
    removed = len(existing) - len(survivors)
    if removed:
        _rewrite_queue(root_dir, codoc_dir, survivors)
    return removed


def _supersede_directives(root_dir: str, codoc_dir: str, fids: set[str]) -> int:
    """A fresh user AMEND to a feature SUPERSEDES its earlier un-synced directives:
    drop them so iterating / undoing / rewording one feature doesn't stack N directives,
    and reverting it to a descriptive (non code-implying) text withdraws the queued
    change entirely. Steers (additive author notes) and other features' directives are
    preserved. Returns the count dropped."""
    existing = edits_channel.read_manifest(codoc_dir)
    survivors = [d for d in existing if d.kind == "steer" or d.feature_id not in fids]
    removed = len(existing) - len(survivors)
    if removed:
        _rewrite_queue(root_dir, codoc_dir, survivors)
    return removed


def _pick_parsed(codoc_dir: str, store: Store):
    """U2b — choose the edit-detection source. Prefer ``tree.doc.json`` (the
    webview's authored intent; the single-writer model means the host no longer
    writes ``tree.codoc``) when it carries a pending feature edit; otherwise the
    daemon-owned ``tree.codoc`` text — which still serves raw-text-editor edits and
    is the only source before any webview has authored a doc. Read-only (the
    ``diff_codoc`` probe never mutates), so it is safe ahead of the step-0 snapshot."""
    doc_parsed = parse_doc_file(codoc_dir)
    if doc_parsed is not None and not diff_codoc(doc_parsed, store).is_empty():
        return doc_parsed
    return parse_tree_file(codoc_dir)


def run_loop_b(root_dir: str, codoc_dir: str, *, dry_run: bool = False) -> LoopBResult:
    with open_store(codoc_dir) as store:
        return _apply_edits(store, root_dir, codoc_dir, dry_run=dry_run)


def _apply_edits(store, root_dir, codoc_dir, *, dry_run) -> LoopBResult:
    res = LoopBResult()
    # (op, feature_id-or-"", caused_by) per code-implying edit — feature_id feeds
    # the doc-wins hold set, caused_by the causality chain (suggestion/event id).
    directive_ops: list[tuple[NodeOp, str, str]] = []
    # feature_id → the description at the START of its current pending episode (the
    # STABLE baseline the IDE diffs against to render the in-situ inline diff). SEED
    # from any directive already queued for the feature, so iterating/rewording/undo
    # within one pending episode does NOT erode the baseline to the previous keystroke
    # (R5/R6 — the field "decoration vanished when I deleted a char" bug). A feature
    # with no pending directive captures a fresh baseline in step 2 (a new episode's
    # first edit). Read before the cancellation drain so the seed survives the pass.
    baselines: dict[str, str] = {
        d.feature_id: d.baseline
        for d in edits_channel.read_manifest(codoc_dir)
        if d.feature_id and d.baseline
    }

    # U6 — withdraw: prune any cancelled directives from the queue FIRST so the rest
    # of the pass (and step 3's manifest append) sees the survivors, and the hold for
    # a withdrawn feature is released this pass. A dry pass still drains the request
    # (withdraw is a real intent, not a directive to defer).
    res.canceled = _apply_cancellations(root_dir, codoc_dir)

    # 0. Snapshot the text diff BEFORE any store mutation. Verdict accepts and
    #    intent applies move the store ahead of the on-disk text; diffing after
    #    them would read the stale text as a human edit and revert the change.
    annotations = edits_channel.drain_annotations(codoc_dir)
    parsed = _pick_parsed(codoc_dir, store)
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
        # Snapshot the pre-edit description BEFORE apply_op mutates the store, so the
        # IDE can later show what changed (AMEND only; first edit per feature wins).
        if op.kind is NodeOpKind.AMEND and op.feature_id and op.feature_id not in baselines:
            prev = store.get_feature(op.feature_id)
            baselines[op.feature_id] = (prev.description or "") if prev else ""
        ann = annotations.get(op.feature_id or "")
        ev = apply_op(op, store, source="user", applied=True,
                      actor=(ann.actor if ann else ""), mode=(ann.mode if ann else ""),
                      caused_by=(ann.suggestion_id if ann else ""))
        res.user_edits += 1
        # Boldening amplifies the imperative gate: a span the author NEWLY
        # bolded that itself reads imperative queues a directive even when the
        # description as a whole is descriptive — emphasis is a stronger intent
        # signal than other revision text.
        bolded = diff.emphasis.get(op.feature_id or "", [])
        will_queue = implies_code(op, store) or any(is_imperative(s) for s in bolded)
        res.edit_notes.append(_edit_label(op, store, will_queue))
        if will_queue:
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
        will_queue = implies_code(op, store)
        res.edit_notes.append(_edit_label(op, store, will_queue))
        if will_queue:
            directive_ops.append((op, f.id, intent.id))

    # 2.7 Steering comments (`> …` in the text, snapshotted in step 0) — explicit
    #     notes to the agent, imperative by construction. Each becomes a STEER
    #     directive; the end-of-pass re-render consumes them from the text (the
    #     store never holds them). A steer's caused_by is the co-occurring
    #     AMEND's cause on the same feature when there is one (the comment rode
    #     along with that edit), so the IDE's cascade cue can group them.
    #     Comments on hand-added nodes resolve their freshly-minted id by title
    #     (step 2 just applied the ADD).
    amend_cause = {fid: cause for _op, fid, cause in directive_ops if fid}
    steered: list[tuple[str, str, str]] = []  # (directive text, feature_id, cause)
    comment_targets = list(diff.comments)
    if diff.new_node_comments:
        by_title: dict[str, str] = {}
        for f2 in store.list_features():
            by_title.setdefault(f2.title, f2.id)
        comment_targets += [(by_title.get(title, ""), comment)
                            for title, comment in diff.new_node_comments]
    for fid, comment in comment_targets:
        d = build_steer_directive(fid, comment, store) if fid else ""
        if d:
            steered.append((d, fid, amend_cause.get(fid, "")))

    # 2.8 Inline-comment steers via edits.json (U2b): once the host stopped writing
    #     tree.codoc, a webview comment can't ride the `> …` text round-trip, so it
    #     arrives as a one-shot steer here. Drained exactly once → a STEER directive
    #     (caused_by the comment's thread id so the host can mark it sent). A dry/
    #     no-realize pass leaves them queued by NOT draining (consuming without
    #     queueing would lose the note) — mirroring the `> …` re-insert guard below.
    if not dry_run:
        for s in edits_channel.drain_steers(codoc_dir):
            d = build_steer_directive(s.feature_id, s.text, store)
            if d:
                steered.append((d, s.feature_id, s.comment_id or amend_cause.get(s.feature_id, "")))
    res.steered = len(steered)

    # Re-render tree.codoc when this pass moved the store ahead of the text
    # (verdict accepts / intent applies) or consumed steering comments —
    # otherwise the next pass would diff the stale text and read it as a human
    # edit reverting the change (or re-queue the same comment). User text edits
    # were absorbed above, so regenerating from the store loses nothing.
    # Steering comments are consumed ONLY when their directives will actually be
    # queued: a dry/no-realize pass must leave the `>` lines in the text for a
    # later real pass — consuming without queueing would destroy the note.
    if res.accepted or res.user_edits or (res.steered and not dry_run):
        write_tree(store, codoc_dir)
        if dry_run and comment_targets:
            # The dry re-render (store-driven) just dropped the `> …` lines even
            # though we are not queueing them — put them back.
            _reinsert_comments(codoc_dir, comment_targets)

    rendered = [
        (build_directive(op, store, emphasis=diff.emphasis.get(fid)), fid, cause, op.kind.value)
        for op, fid, cause in directive_ops
    ]
    rendered += [(text, fid, cause, "steer") for text, fid, cause in steered]
    rendered = [r for r in rendered if r[0]]
    res.directives = [r[0] for r in rendered]

    # Coalesce per feature (fixes the "weird count": iterating one feature stacked N
    # directives). A fresh user AMEND supersedes that feature's earlier un-synced
    # directive — drop it BEFORE the early-return so a revert-to-descriptive (no new
    # directive) still withdraws the queued change; step 3 then appends this pass's
    # directives onto the pruned queue, yielding one per feature. Dry passes never
    # mutate the live queue.
    edited_fids = {op.feature_id for op in diff.user_ops
                   if op.feature_id and op.kind is NodeOpKind.AMEND}
    if edited_fids and not dry_run:
        _supersede_directives(root_dir, codoc_dir, edited_fids)

    if dry_run:
        status.refresh_status(codoc_dir, store)
        return res

    # 3. Finalize the queue. Merge this pass's new directives into the manifest and
    #    derive each directive's `handed_off` from the LIVE drafts set (the webview's
    #    suggesting-mode holds): a held draft stays OUT of realize.md until the human
    #    hands it off (the host removes it from `drafts`). realize.md — the agent's
    #    trigger — is (re)built from handed-off directives ONLY, then status is
    #    awaiting_impl. This block runs even with NO new directive, so a hand-off (a
    #    drafts-set change with no fresh edit) is processed. With no drafts set, every
    #    directive is handed off ⇒ today's immediate-realize (the gate is additive).
    #    The session implements via /codoc:sync; the Stop-hook reflection / epoch-close
    #    Loop A closes the loop. Drafts surface via the in-situ diff + pending dots
    #    (hold_set reads the manifest), no realize.md needed.
    existing = edits_channel.read_manifest(codoc_dir)
    if not existing and not res.directives:
        status.refresh_status(codoc_dir, store)
        return res

    drafts_set = edits_channel.read_drafts(codoc_dir)
    res.directive_ids = [new_directive_id() for _ in rendered]
    all_directives = existing + [
        edits_channel.Directive(id=did, feature_id=fid, kind=kind, caused_by=cause, text=text,
                                baseline=baselines.get(fid, ""))
        for did, (text, fid, cause, kind) in zip(res.directive_ids, rendered)
    ]
    for d in all_directives:
        d.handed_off = d.feature_id not in drafts_set
    # Manifest first (its no-realize.md-but-drafts state is the source of truth);
    # realize.md (the agent trigger) is rebuilt from handed-off directives only.
    edits_channel.write_manifest(codoc_dir, all_directives)
    handed = [d for d in all_directives if d.handed_off and d.text]
    if handed:
        _write_realize(codoc_dir, build_realize_prompt(
            [d.text for d in handed], root_dir, [d.id for d in handed]))
        res.queued = True
        res.queued_total = len(handed)
        status.refresh_status(
            codoc_dir, store, awaiting_impl=True, pending=len(handed),
            detail=f"{len(handed)} change(s) ready to implement — run /codoc:sync",
        )
    else:
        # Only held drafts (nothing handed off this pass) — remove the trigger; the
        # drafts persist in the manifest and surface as the in-situ diff + pending dots.
        try:
            realize_path(codoc_dir).unlink()
        except OSError:
            pass
        status.refresh_status(codoc_dir, store)
    return res
