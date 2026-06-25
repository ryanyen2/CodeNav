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
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from codoc.agent.base import format_prompt, load_prompt
from codoc.blocks.base import Capability, LowerContext
from codoc.blocks.builtins import ensure_builtins
from codoc.codoc_file.diff import CodocDiff, diff_codoc
from codoc.loop.doc_presence import reconcile_doc_presence
from codoc.codoc_file.doc_parse import parse_doc_file
from codoc.codoc_file.parse import extract_bold, extract_links, parse_tree_file
from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop import edits as edits_channel
from codoc.loop import inbox, status
from codoc.loop.apply import apply_op
from codoc.loop.classify import edit_mints_directive
from codoc.loop.filenames import REALIZE_FILENAME
from codoc.loop.fsio import atomic_write_text
from codoc.loop.locks import loop_lock
from codoc.model.block import Block, BlockLifecycle, Provenance
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.ids import new_directive_id
from codoc.store.db import Store, open_store

_DIRECTIVE_ID_RE = re.compile(r"⟨(d-[0-9a-f]+)⟩")

# Directive kinds that are handed off to the agent the moment they are minted — an
# EXPLICIT code request, not a held documentation draft: a steer (a `> …` note
# addressed to the agent), a RETIRE (the destructive `~` marker), and a plan ADD
# (realized=False, an authored build placeholder). Everything else (an AMEND, a block
# content edit) is born held and realizes only on an explicit hand-off.
_EXPLICIT_REALIZE_KINDS = frozenset({
    "steer", NodeOpKind.RETIRE_NODE.value, NodeOpKind.ADD_NODE.value,
})


@dataclass
class LoopBResult:
    accepted: int = 0
    rejected: int = 0
    user_edits: int = 0
    steered: int = 0  # inline `> …` steering comments drained this pass
    canceled: int = 0  # queued directives withdrawn this pass (U6)
    soft_retired: int = 0  # nodes the human deleted from the doc → soft (detach-only) retire
    unretired: int = 0     # nodes that re-appeared (undo / re-author) → un-retired
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
        if self.soft_retired:
            parts.append(f"soft-retired {self.soft_retired} deleted node(s)")
        if self.unretired:
            parts.append(f"restored {self.unretired} node(s)")
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


def _consult_block_lines(feature_id: str, store: Store) -> str:
    """``Consult:`` suffix lines from a feature's CONSULT-capable PERSISTENT blocks
    (url / image / …) — the consult arrow (KTD5). A reference medium feeds the
    realizing agent context without round-tripping to code: Loop B dispatches by
    DECLARED capability and lets the plugin render its own consult text. Empty when
    the feature has no such media, so a feature with only prose is unchanged."""
    if not feature_id:
        return ""
    registry = ensure_builtins()
    lines: list[str] = []
    for b in store.blocks_for_feature(feature_id):
        plugin = registry.for_capability(b.kind, Capability.CONSULT)
        if plugin is None:
            continue
        text = plugin.consult(b).strip()
        if text:
            lines.append(f"  {text}")
    return ("\n" + "\n".join(lines)) if lines else ""


def _media_consult_line(kind: str, ref: str, feature_id: str) -> str:
    """One ``Consult:`` line for a TRANSIENT attachment riding a steer (U6) — e.g.
    a bug screenshot in a comment thread. The attachment is never a stored block;
    we build a throwaway transient :class:`Block` purely to dispatch the named
    CONSULT plugin, so realization reads it once and it is gone (KTD4)."""
    if not (kind and ref.strip()):
        return ""
    plugin = ensure_builtins().for_capability(kind, Capability.CONSULT)
    if plugin is None:
        return ""
    block = Block(feature_id=feature_id or "transient", kind=kind, content=ref,
                  lifecycle=BlockLifecycle.TRANSIENT, provenance=Provenance.HUMAN)
    text = plugin.consult(block).strip()
    return ("\n  " + text) if text else ""


def _edit_label(op: NodeOp, store: Store, will_queue: bool) -> str:
    """A one-line note for the watch log: which feature an edit touched, a truncated
    snippet of the new text, and what it produced — a held draft (``→ draft``, awaiting
    hand-off), an explicit realize (``→ realize`` — a RETIRE-with-code or plan ADD), or
    nothing code-bearing (``doc-only`` — a MOVE or a descriptive new node). Lets a
    watcher SEE what each edit was instead of an opaque ``edits 2``."""
    f = store.get_feature(op.feature_id) if op.feature_id else None
    title = f.title if f else (op.title or op.feature_id or "?")
    text = (op.description or op.title or "").replace("\n", " ").strip()
    snippet = (text[:60] + "…") if len(text) > 60 else text
    if not will_queue:
        tag = "doc-only"
    elif op.kind is NodeOpKind.RETIRE_NODE or (op.kind is NodeOpKind.ADD_NODE and op.realized is False):
        tag = "→ realize"  # explicit gesture — handed off on mint
    else:
        tag = "→ draft"    # AMEND — held until an explicit hand-off
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


def build_block_directive(feature_id: str, kind: str, intent_text: str, store: Store) -> str:
    """Wrap a block plugin's ``lower`` intent in the same Bound-code/Edit-only
    scaffolding a steer directive carries, so the realizing agent gets a precise,
    scoped instruction. The plugin supplies the *intended change* (the deterministic
    delta or a declared-prompt result); the realizing agent performs the code edit
    (KTD5: dispatch=agent means the agent transforms, the mapping was structural)."""
    f = store.get_feature(feature_id)
    if f is None or not intent_text.strip():
        return ""
    loc, files = _bound_code(feature_id, store)
    loc = loc or "(no bound code yet)"
    scope = ", ".join(files) if files else "(none yet — create where it fits)"
    note = intent_text.replace("\n", "\n    ")
    return (f'BLOCK EDIT [{kind}]: "{f.title}"\n  Intended change: {note}\n'
            f'  Bound code: {loc}\n  Edit only: {scope}\n'
            f"  Apply the intended change to this feature's code.")


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
    # INV8: directives currently in realize.md are being actively realized by the
    # agent. Superseding them mid-realization would waste the agent's in-progress
    # work and break the caused_by causality chain (the agent's reflect call cites
    # the directive id). A superseded in-flight directive is re-queued after the
    # epoch closes if the user's new description is still imperative.
    in_flight = _in_flight_directive_ids(codoc_dir)
    # Steers (additive notes) and block directives (independent structural edits) are
    # never superseded by a description AMEND — only AMEND/RETIRE directives are
    # replaced by the fresh directive for the same feature (R3-A / INV11).
    survivors = [d for d in existing
                 if d.kind == "steer" or d.kind.startswith("block:")
                 or d.feature_id not in fids
                 or (d.id and d.id in in_flight)]
    removed = len(existing) - len(survivors)
    if removed:
        _rewrite_queue(root_dir, codoc_dir, survivors)
    return removed


def _in_flight_directive_ids(codoc_dir: str) -> frozenset[str]:
    """Directive ids that have been HANDED OFF to the agent — the ids in ``realize.md``.

    These are protected from supersede: dropping a directive the agent may be
    mid-implementing would break its ``caused_by`` causality chain and waste work.
    ``realize.md`` is the structural signal — the held-draft model writes ONLY
    handed-off directives there (a held draft never appears), so a fresh edit's held
    draft (the default) coalesces freely, while a genuinely handed-off directive is
    protected. This deliberately does NOT consult ``activity.json``'s epoch: depending
    on that file made protection fragile (a stale/missing epoch would wrongly allow a
    being-realized directive to be superseded). Conservative by design — anything handed
    off is protected whether or not the agent has visibly started; a re-edit after
    hand-off becomes a new held draft rather than clobbering the in-flight one.
    """
    try:
        text = realize_path(codoc_dir).read_text()
        return frozenset(m.group(1) for m in _DIRECTIVE_ID_RE.finditer(text))
    except OSError:
        return frozenset()


def _merge_channels(codoc_dir: str, store: Store) -> tuple[CodocDiff, list[str]]:
    """Merge both edit channels at the feature level (INV3), after first restoring
    minted fids to new nodes in both channels (INV7).

    Channel arbitration rules:
    - doc-path AMEND/MOVE/ADD is authoritative for features it has edits for.
    - text-path ops cover features not in the doc's edit set (raw-text-editor edits,
      CLI-only repos, first run).
    - RETIRE_NODE from the text path beats a concurrent AMEND from the doc path for
      the same feature — lifecycle intent is always stronger than description intent.
    - Steers (``> …`` comments) from both channels are additive and always included.

    Returns ``(merged_diff, errors)`` where ``merged_diff`` feeds ``_snapshot_pre_mutation``
    as the frozen pre-mutation snapshot and ``errors`` are parse warnings.

    This replaces the previous ``_pick_parsed`` winner-take-all approach, which would
    silently drop a raw-text-editor edit to feature B whenever the webview had a
    pending edit for feature A (attack A2/A6).
    """
    doc_parsed = parse_doc_file(codoc_dir)
    text_parsed = parse_tree_file(codoc_dir)

    errors = list((doc_parsed.errors if doc_parsed else []) + text_parsed.errors)

    # Identity is resolved INSIDE diff_codoc now (INV7): the doc channel diffs with
    # has_local_ids=True, so a heading whose author-stable local_id maps to an
    # existing feature is recognized as that feature — even when TipTap's undo reset
    # its fid to null. No pre-pass mutation of the parsed tree. The text channel has
    # no local_id signal, so it keeps the fid+title snapshot diff.
    if doc_parsed is None:
        # CLI-only / first run — no webview doc yet: text is the only source.
        return diff_codoc(text_parsed, store), errors

    doc_diff = diff_codoc(doc_parsed, store, has_local_ids=True)
    if doc_diff.is_empty():
        # Doc has no pending edits (store is caught up): text path is authoritative.
        return diff_codoc(text_parsed, store), errors

    # Both channels have ops. Merge per-feature.
    doc_fids = {op.feature_id for op in doc_diff.user_ops if op.feature_id}
    text_diff = diff_codoc(text_parsed, store)

    # RETIRE from text beats AMEND from doc for the same feature (lifecycle > description).
    text_retire_fids = {
        op.feature_id for op in text_diff.user_ops
        if op.kind is NodeOpKind.RETIRE_NODE and op.feature_id in doc_fids
    }
    doc_ops = [op for op in doc_diff.user_ops if op.feature_id not in text_retire_fids]
    text_extra_ops = [
        op for op in text_diff.user_ops
        if not op.feature_id or op.feature_id not in doc_fids
        or op.feature_id in text_retire_fids
    ]

    return CodocDiff(
        user_ops=doc_ops + text_extra_ops,
        # Steers (> …) only come from the text path; doc_diff.comments is always empty.
        # Merging preserves steers for both doc-path and text-path features.
        comments=doc_diff.comments + text_diff.comments,
        new_node_comments=doc_diff.new_node_comments + text_diff.new_node_comments,
        # Doc emphasis wins (bold spans in the webview are the stronger signal).
        emphasis={**text_diff.emphasis, **doc_diff.emphasis},
    ), errors


@dataclass
class _PreMutation:
    """Everything the rest of a Loop B pass diffs against, captured BEFORE any
    store mutation — the ordering invariant (D6) made structural.

    The danger this guards: verdict accepts and intent applies move the store
    *ahead* of the on-disk text. If the text diff were recomputed after them, the
    now-stale text would read as a human edit and silently REVERT the accepted
    change. Bundling the snapshot into one object built by
    :func:`_snapshot_pre_mutation` — and passing it into the mutation phases —
    means there is no second ``diff_codoc`` call to misorder: a future reordering
    of the phases cannot reintroduce the bug because the diff is a frozen input,
    not a line that happens to sit early.

    ``baselines`` is SEEDED here (from the manifest, before any mutation) and then
    AUGMENTED per-feature in the edit phase (each capture reads the pre-edit
    description before its own ``apply_op``), so it stays a live dict, not frozen.
    """
    diff: object                       # CodocDiff — the text↔store delta (the load-bearing snapshot)
    annotations: dict                  # edits.json authorship annotations (drained)
    baselines: dict[str, str]          # feature_id → pre-episode description baseline (seeded; augmented later)
    errors: list[str] = field(default_factory=list)


def _snapshot_pre_mutation(store: Store, codoc_dir: str) -> _PreMutation:
    """Capture the pre-mutation snapshot in ONE place, doing NO store mutation.

    Order matters and is fixed here so callers never have to get it right:
    1. seed ``baselines`` from the manifest (must precede any later cancellation
       drain so a still-pending episode keeps its stable diff baseline — R5/R6);
    2. drain edits.json authorship annotations (a control-file read/clear, not a
       store write);
    3. merge both edit channels (INV3/INV7) and diff against the PRE-mutation store.
    """
    baselines: dict[str, str] = {
        d.feature_id: d.baseline
        for d in edits_channel.read_manifest(codoc_dir)
        if d.feature_id and d.baseline
    }
    annotations = edits_channel.drain_annotations(codoc_dir)
    diff, errors = _merge_channels(codoc_dir, store)
    return _PreMutation(diff=diff, annotations=annotations, baselines=baselines,
                        errors=errors)


def run_loop_b(root_dir: str, codoc_dir: str, *, dry_run: bool = False) -> LoopBResult:
    # The shared codoc-loop lock serializes this whole pass against Loop A and any other
    # Loop B (daemon / CLI / hub / Stop-hook) so no two passes interleave between store
    # mutation and the write_tree re-render (the phantom-revert race). See loop/locks.py.
    with loop_lock(codoc_dir):
        with open_store(codoc_dir) as store:
            return _apply_edits(store, root_dir, codoc_dir, dry_run=dry_run)


def _apply_edits(store, root_dir, codoc_dir, *, dry_run) -> LoopBResult:
    res = LoopBResult()
    # (op, feature_id-or-"", caused_by) per code-implying edit — feature_id feeds
    # the doc-wins hold set, caused_by the causality chain (suggestion/event id).
    directive_ops: list[tuple[NodeOp, str, str]] = []

    # 0. Snapshot EVERYTHING this pass diffs against, BEFORE any store mutation —
    #    captured as one explicit object so the ordering invariant is structural,
    #    not a fragile "this line must come first" (D6). ``baselines`` is the
    #    feature_id → start-of-episode description map the IDE diffs against for the
    #    in-situ inline diff (seeded from the manifest so iterating/undo within one
    #    episode doesn't erode it to the previous keystroke — R5/R6); it is seeded
    #    here and augmented per-feature in step 2.
    snap = _snapshot_pre_mutation(store, codoc_dir)
    diff = snap.diff
    annotations = snap.annotations
    baselines = snap.baselines
    if snap.errors:
        import logging
        _log = logging.getLogger(__name__)
        for err in snap.errors:
            _log.warning("tree.codoc parse warning: %s", err)
        res.error = "; ".join(snap.errors)

    # U6 — withdraw: prune any cancelled directives from the queue so the rest of
    # the pass (and step 3's manifest append) sees the survivors, and the hold for a
    # withdrawn feature is released this pass. Runs AFTER the snapshot: it mutates
    # only the control-file queue (never the store), and the baselines were already
    # seeded from the pre-cancellation manifest, so a dry pass still drains the
    # request (withdraw is a real intent, not a directive to defer).
    res.canceled = _apply_cancellations(root_dir, codoc_dir)

    # Soft-delete reconciliation (doc-vs-previous-doc): a feature the human removed
    # from the doc since the last pass is soft-retired (detach-only, recoverable — NO
    # code deletion); a re-appeared one is un-retired (undo / re-author). Safe against
    # agent-added features (only a previously-seen fid can be "removed"), so this is
    # what finally STOPS a deleted node from resurrecting on the next render. Skipped
    # on dry runs (it mutates the tree). Runs before the edit loops so the rest of the
    # pass and the re-render see the post-retire state.
    _current_doc_fids: set[str] = set()
    if not dry_run:
        res.soft_retired, res.unretired, _current_doc_fids = reconcile_doc_presence(
            store, codoc_dir
        )

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
                if e.op.delete_code and edit_mints_directive(e.op, store):
                    directive_ops.append((e.op, fid, e.id))
                else:
                    for b in store.bindings_for_feature(e.op.feature_id):
                        store.delete_binding(b.file, b.symbol_path)
            elif e.op.kind is NodeOpKind.ADD_NODE and e.op.realized is False:
                # An accepted PLAN placeholder (realized=False) is a build request →
                # mint a directive. Every other accepted proposal (a descriptive AMEND
                # reflecting code that already changed, an ADD binding existing code, a
                # MOVE) reconciles the tree to EXISTING code — it must NOT mint a realize
                # directive (that would tell the agent to re-write code to match a
                # description derived from that very code). This deliberately does NOT
                # route through edit_mints_directive, whose AMEND→always-True is for the
                # doc-AUTHORING path, not for reconciling-to-code accepts.
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
        # Structural gate (no prose heuristic): AMEND always mints a directive,
        # plan-ADD/RETIRE-with-code do too. Whether it realizes now or waits as a
        # held draft is the finalize step's hand-off decision. Newly-bolded spans
        # still ride into the directive as a `Focus:` line (diff.emphasis →
        # build_directive); they no longer gate whether a directive is minted.
        will_queue = edit_mints_directive(op, store)
        res.edit_notes.append(_edit_label(op, store, will_queue))
        if will_queue:
            # RETIRE supersedes any prior pending directives for this feature
            # (add+retire churn prevention — INV1/N15). A retire intent is always
            # stronger than a queued code-add or code-update for the same feature.
            if op.kind is NodeOpKind.RETIRE_NODE and op.feature_id:
                _supersede_directives(root_dir, codoc_dir, {op.feature_id})
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
        will_queue = edit_mints_directive(op, store)
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
            # A transient consult attachment (U6) — a bug screenshot — rides the
            # steer and is folded into its directive as a `Consult:` line, then
            # discarded (the steer is already drained-once). Never a stored block.
            if d and s.media:
                d += _media_consult_line(s.media_kind or "screenshot", s.media, s.feature_id)
            if d:
                steered.append((d, s.feature_id, s.comment_id or amend_cause.get(s.feature_id, "")))
    res.steered = len(steered)

    # 2.9 Typed-media block edits (U3) — the block→code (`lower`) direction. The host
    #     hands an edit to a diagram/latex/… block through edits.json (stable block
    #     id, KTD8). We dispatch by DECLARED capability (KTD5): only a plugin that
    #     declares LOWER produces a directive; consult-only media (url/image) never do.
    #     The store is the source of truth for block content, so an add/edit upserts
    #     the block row and a remove drops it — but a REMOVE drops only the projection,
    #     NEVER the code (destructive asymmetry, KTD2): no directive is queued for it.
    #     Drained one-shot only on a real pass (a dry/no-realize pass leaves them for a
    #     later real pass — same guard as steers). The resulting directives flow into
    #     the SAME manifest/realize.md pipeline below, inheriting the draft gate.
    block_specs: list[tuple[str, str, str, str]] = []  # (text, fid, cause, kind)
    force_draft_fids: set[str] = set()                 # lossy `lower` → held draft (KTD2)
    block_store_changed = False
    if not dry_run:
        registry = ensure_builtins()
        for be in edits_channel.drain_block_edits(codoc_dir):
            f = store.get_feature(be.feature_id)
            if f is None or f.retired:
                continue
            if be.action == "remove":
                # Drop the projection only. The code stays; at most a future opt-in
                # could propose a removal — v1 default queues nothing.
                store.delete_block(be.block_id)
                block_store_changed = True
                continue
            # add / edit: persist the block as the store's source of truth.
            existing = store.get_block(be.block_id)
            ordv = existing.ord if existing else len(store.blocks_for_feature(be.feature_id))
            store.upsert_block(Block(
                id=be.block_id, feature_id=be.feature_id, kind=be.kind,
                content=be.content, lifecycle=BlockLifecycle.PERSISTENT,
                provenance=Provenance.HUMAN, ord=ordv))
            block_store_changed = True
            plugin = registry.for_capability(be.kind, Capability.LOWER)
            if plugin is None:
                continue  # consult-only / lift-only medium: a content edit implies no code
            prev = (existing if existing else None)
            new_block = store.get_block(be.block_id)
            result = plugin.lower(LowerContext(
                feature=f, old_block=prev, new_block=new_block,
                bindings=store.bindings_for_feature(be.feature_id), store=store))
            if result.kind == "noop" or not result.text.strip():
                continue
            text = build_block_directive(be.feature_id, be.kind, result.text, store)
            if not text:
                continue
            block_specs.append((text, be.feature_id, be.block_id, f"block:{be.kind}"))
            if result.kind == "draft":
                force_draft_fids.add(be.feature_id)  # ambiguous → held for confirmation

    # Re-render tree.codoc when this pass moved the store ahead of the text
    # (verdict accepts / intent applies) or consumed steering comments —
    # otherwise the next pass would diff the stale text and read it as a human
    # edit reverting the change (or re-queue the same comment). User text edits
    # were absorbed above, so regenerating from the store loses nothing.
    # Steering comments are consumed ONLY when their directives will actually be
    # queued: a dry/no-realize pass must leave the `>` lines in the text for a
    # later real pass — consuming without queueing would destroy the note.
    if (res.accepted or res.user_edits or block_store_changed or res.soft_retired
            or res.unretired or (res.steered and not dry_run)):
        write_tree(store, codoc_dir)
        if dry_run and comment_targets:
            # The dry re-render (store-driven) just dropped the `> …` lines even
            # though we are not queueing them — put them back.
            _reinsert_comments(codoc_dir, comment_targets)
        # INV5: write doc-fids.json AFTER write_tree so the two files stay
        # co-consistent. A crash between reconcile and write_tree leaves doc-fids.json
        # at the old state — the zombie-clone guard in diff_codoc (N8) catches the
        # resulting stale live marker in tree.codoc on restart.
        if not dry_run and _current_doc_fids:
            from codoc.loop.doc_presence import write_doc_fids
            write_doc_fids(codoc_dir, _current_doc_fids)

    rendered = [
        (build_directive(op, store, emphasis=diff.emphasis.get(fid)), fid, cause, op.kind.value)
        for op, fid, cause in directive_ops
    ]
    rendered += [(text, fid, cause, "steer") for text, fid, cause in steered]
    rendered += block_specs  # block `lower` directives (U3); already (text, fid, cause, kind)
    rendered = [r for r in rendered if r[0]]
    # Attach CONSULT-capable persistent-block context (url/image/…) once per feature
    # whose directive is realized this pass — the consult arrow (KTD5/AE3). Ambient
    # reference media never produce a directive of their own; they enrich the
    # directive a code-implying edit already queued for the same feature.
    consulted: set[str] = set()
    with_consult: list[tuple[str, str, str, str]] = []
    for text, fid, cause, kind in rendered:
        if fid and fid not in consulted:
            consulted.add(fid)
            text += _consult_block_lines(fid, store)
        with_consult.append((text, fid, cause, kind))
    rendered = with_consult
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

    # Held-draft model: a doc AMEND/block edit is born HELD (handed_off=False) and
    # reaches the agent only via an explicit hand-off. An explicit gesture — a steer
    # (note addressed to the agent), a RETIRE (the destructive ~ marker), or a plan
    # ADD (realized=False) — is handed off the moment it is minted. The positive
    # hand-off signal for held drafts is the one-shot ``handoffs`` channel (the
    # webview's commit / ⌘S, or ``codoc realize``). This is the deletion of the
    # is_imperative prose-guess: the SYSTEM never decides from English mood; the USER
    # decides by handing off.
    handoffs = set(edits_channel.drain_handoffs(codoc_dir))
    res.directive_ids = [new_directive_id() for _ in rendered]
    all_directives = existing + [
        edits_channel.Directive(id=did, feature_id=fid, kind=kind, caused_by=cause, text=text,
                                baseline=baselines.get(fid, ""), handed_off=False)
        for did, (text, fid, cause, kind) in zip(res.directive_ids, rendered)
    ]
    for d in all_directives:
        if d.handed_off:
            continue  # STICKY: a directive already in realize.md / sent to the agent is
                      # never demoted (legacy manifest, or handed off on a prior pass) —
                      # demoting it mid-realization would break the caused_by chain.
        if d.feature_id in force_draft_fids:
            d.handed_off = False  # lossy block `lower` (KTD2) → held until confirmed
        elif d.kind in _EXPLICIT_REALIZE_KINDS or d.kind.startswith("block:"):
            # An explicit / deterministic code request realizes on mint: a steer (note
            # to the agent), a RETIRE (the destructive ~), a plan ADD (realized=False),
            # or a block `lower` (a diagram/latex edit has an UNAMBIGUOUS code delta —
            # not the prose ambiguity the held-draft default guards against).
            d.handed_off = True
        else:
            # AMEND (a prose description edit) — held until its feature is explicitly
            # handed off. The SYSTEM never guesses from prose whether to realize.
            d.handed_off = d.feature_id in handoffs
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
