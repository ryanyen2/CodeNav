"""Loop B — codoc → code.

Apply proposal verdicts + identity-keyed authored commands (the webview's
add/set_title/set_description/move/retire ops — U3/U4) + live doc-ahead
suggestions (payload intents — the loop, not the human, applies a suggestion: see
classify row 9) + inline steering comments (notes addressed to the agent, arriving
through ``edits.json``; always a directive) → for edits that imply a code change,
build a directive from the feature's description + bound symbols and **queue it for
the live Claude Code session** by writing ``.codoc/realize.md`` (set status
``awaiting_impl``). An in-flight queue is appended to (via the manifest's
directive texts), never clobbered — steering works mid-realization. The session
implements the queued directives via ``/codoc:sync`` (Read → implement →
``codoc_reflect`` → delete the file); the loop is then closed by the existing
Stop-hook reflection (``agent/hook._maybe_spawn_reflect``) or the watch daemon's
epoch-close Loop A pass — both reflect the freshly written code back into the tree.
Loop B no longer spawns a headless ``claude -p``.

User edits are NO LONGER inferred by diffing ``tree.codoc`` / ``tree.doc.json``
against the store (U7 / R18). Once the daemon became the sole writer of both files
(``tree.doc.json`` from U4, ``tree.codoc`` the read-only export from U6), reading
either back as user input was a feedback loop — the daemon diffing its own output —
which re-minted nodes and resurrected deletions. Every authored edit (including a
deletion, now an explicit ``retire`` command) arrives through the ``commands``
channel and is applied via ``apply_op``; ``_merge_channels`` returns an empty diff.

One ordering invariant survives: a pass that mutated the store re-renders
``tree.codoc`` and ``tree.doc.json`` before returning, so the webview's file-watch
re-read repaints from the store projection (the source of truth).
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
from codoc.codoc_file.diff import CodocDiff
from codoc.codoc_file.doc_render import build_doc_from_store
from codoc.codoc_file.parse import extract_bold, extract_links
from codoc.codoc_file.render import write_tree
from codoc.loop import edits as edits_channel
from codoc.loop import inbox, status
from codoc.loop.apply import apply_op
from codoc.loop.classify import edit_mints_directive
from codoc.loop.filenames import DOC_FILENAME, REALIZE_FILENAME
from codoc.loop.fsio import atomic_write_json, atomic_write_text
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


def _norm_title(t: str | None) -> str:
    """Normalize a title for the soft ``(normalized_title, parent_id)`` uniqueness
    key — mirrors ``loop_a._norm_title`` so the command-apply dedup (U3 / KTD3)
    folds duplicates the same way the Loop-A LLM-apply fold does."""
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def _command_to_op(cmd: "edits_channel.Command") -> NodeOp | None:
    """Map an identity-keyed command (U3) onto a ``NodeOp`` for ``apply_op``.

    ``add`` → ADD_NODE (carries title/description/parent_id + the webview's
    ``local_id`` for minted-fid correlation); ``set_title``/``set_description`` →
    AMEND (only the changed field set); ``move`` → MOVE_NODE; ``retire`` →
    RETIRE_NODE (detach-only — never ``delete_code`` from this channel). Returns
    None for an unhandled kind (defensive; ``read_commands`` already filters)."""
    p = cmd.payload or {}
    if cmd.kind == "add":
        return NodeOp(kind=NodeOpKind.ADD_NODE, title=p.get("title") or "",
                      description=p.get("description") or "",
                      parent_id=p.get("parent_id"), local_id=cmd.local_id)
    if cmd.kind == "set_title":
        return NodeOp(kind=NodeOpKind.AMEND, feature_id=cmd.feature_id,
                      title=p.get("title", ""))
    if cmd.kind == "set_description":
        return NodeOp(kind=NodeOpKind.AMEND, feature_id=cmd.feature_id,
                      description=p.get("description", ""))
    if cmd.kind == "move":
        return NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=cmd.feature_id,
                      parent_id=p.get("parent_id"))
    if cmd.kind == "retire":
        return NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=cmd.feature_id)
    return None


@dataclass
class LoopBResult:
    accepted: int = 0
    rejected: int = 0
    user_edits: int = 0
    commands: int = 0  # identity-keyed authored commands applied this pass (U3)
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
    # local_id → minted feature id for each `add` command applied this pass (U3): the
    # webview correlates its in-progress node back to the store fid (KTD8) so it adopts
    # the right node without title/order guessing. Echoed back via the host (U4).
    fids_by_local: dict[str, str] = field(default_factory=dict)
    error: str = ""

    def summary(self) -> str:
        parts = [f"accepted {self.accepted}", f"rejected {self.rejected}", f"edits {self.user_edits}"]
        if self.commands:
            parts.append(f"commands {self.commands}")
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


def doc_path(codoc_dir: str | os.PathLike) -> Path:
    return Path(codoc_dir) / DOC_FILENAME


def write_tree_doc(store: Store, codoc_dir: str | Path) -> Path:
    """Render the store's live feature tree into ``tree.doc.json`` (KTD9).

    The daemon is the SOLE writer of this file: the webview reads it as the
    store projection (identity + marks + comments per U2) and emits identity-keyed
    commands instead of authoring it. Written atomically alongside ``write_tree``
    at the end of a Loop B pass so a file-watch re-read repaints the webview."""
    dest = doc_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, build_doc_from_store(store))
    return dest


def _write_realize(codoc_dir: str, prompt: str) -> None:
    """Queue the realization directives for the live session (atomic write)."""
    dest = realize_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(dest, prompt)


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
    """RETIRED (U7 / R18). User edits no longer come from diffing a file against the
    store — they arrive as identity-keyed ``commands`` (step 0.5), applied via
    ``apply_op``. Both former inference inputs were feedback loops once the daemon
    became the sole writer of the two files:

    - the doc channel (``parse_doc_file`` → ``diff_codoc(has_local_ids=True)``) diffed
      ``tree.doc.json`` — which U4 made the daemon's own projection output — so reading
      it back as user input would diff the daemon against itself and re-mint nodes;
    - the text channel (``parse_tree_file`` → ``diff_codoc``) diffed ``tree.codoc``,
      which U6 made a read-only export the daemon renders from the store, so there are
      no raw-text user edits and the diff is structurally circular.

    Inline ``> …`` text steers were the text channel's last live input; once the
    webview stopped writing ``tree.codoc`` (U6) a comment arrives through ``edits.json``
    instead (step 2.8 ``drain_steers``), so the text steer path is dead too.

    This returns an empty :class:`CodocDiff` to keep the pre-mutation snapshot shape
    intact for the surviving phases (verdicts, commands, intents, steers, block edits,
    the directive/realize/hold pipeline) without doing any inference. ``diff_codoc``
    itself survives for the non-destructive write guards in ``reconcile.py``
    (``safe_write_tree`` / the watch daemon's pending-edit checks).
    """
    return CodocDiff(), []


@dataclass
class _PreMutation:
    """State captured BEFORE any store mutation, so the per-feature inline-diff
    baselines and authorship annotations are read against the pre-edit store.

    ``diff`` is the (now always empty — U7) :class:`CodocDiff` from
    ``_merge_channels``. User edits arrive as ``commands`` (step 0.5) applied via
    ``apply_op``, so there is no longer a text/doc diff to misorder; the field and
    snapshot object survive to keep the phase shape and the baselines-seeding order
    intact (and so a future inference re-introduction would have a single seam).

    ``baselines`` is SEEDED here (from the manifest, before any mutation) and then
    AUGMENTED per-feature in the edit phase (each capture reads the pre-edit
    description before its own ``apply_op``), so it stays a live dict, not frozen.
    """
    diff: object                       # CodocDiff — empty post-U7; commands carry user edits
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
    3. ``_merge_channels`` — retired to an empty diff (U7); user edits are commands.
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

    # Deletion is an EXPLICIT `retire` command now (U4 emits one when the human deletes
    # a node in the webview), applied via apply_op in step 0.5 — not inferred from a
    # doc-vs-previous-doc presence delta. The former soft-delete reconciliation
    # (reconcile_doc_presence + doc-fids.json, U7/R18) is retired: once the daemon
    # became the sole writer of tree.doc.json (U4), diffing it against its own prior
    # output would have detected the daemon's own renders as human deletions. The
    # `soft_retired` / `unretired` result fields stay (default 0) so existing callers
    # and the summary line are undisturbed.

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

    # 0.5 Identity-keyed authored commands (U3 / KTD3). The webview emits an
    #     EXPLICIT op (add/set_title/set_description/move/retire) keyed by feature/
    #     local id, instead of Loop B INFERRING it from a doc diff. Drained + applied
    #     BEFORE the legacy annotation `edits` list (step 2) so a structural retire/
    #     move resolves before authorship stamps land on the post-command state.
    #     Idempotent on the store ledger (KTD8): a re-sent / crash-replayed id is a
    #     no-op. ADD reuses _accept_with_fid for minted-fid correlation back to the
    #     submitted local_id, and is rejected (skipped, no mint) when a live feature
    #     already owns the same (normalized_title, parent_id) — the soft-uniqueness
    #     guard the Loop-A LLM-apply fold has but apply_op does not (KTD3). A command
    #     whose target feature_id no longer exists is skipped without crashing.
    #     Dry runs leave the channel queued (a command is a real authored intent, not
    #     a deferrable directive) — same one-shot-only-on-a-real-pass guard as steers.
    #
    #     A command that implies code (an AMEND from set_title/set_description, a
    #     plan ADD, a RETIRE owning bound code) ALSO queues a realize directive here
    #     (U7): with the doc-diff inference retired, the command apply path is the only
    #     thing left to drive the codoc→code half of the loop. The directive pipeline
    #     keys off feature_id (not the diff), so it is unchanged downstream — born held
    #     by default, handed off on the same explicit-gesture rule as a step-2 edit.
    fids_by_local: dict[str, str] = {}  # local_id → minted fid (echoed back to the host, U4)
    command_amend_fids: set[str] = set()  # AMEND'd features → coalesce supersede (see below)
    if not dry_run:
        live_title_parent = {
            (_norm_title(f.title), f.parent_id) for f in store.list_features()
        }
        applied_cmd_ids: set[str] = set()  # ids to clear from the channel AFTER apply (KTD8)
        for cmd in edits_channel.drain_commands(codoc_dir):
            if store.command_applied(cmd.id):
                # Already on the ledger (re-sent / crash-replayed) — never re-apply.
                # Still clear it from the channel so a settled-but-uncleared command
                # (a crash AFTER apply, BEFORE clear) drops out on the re-run.
                applied_cmd_ids.add(cmd.id)
                continue
            op = _command_to_op(cmd)
            if op is None:
                store.mark_command_applied(cmd.id)
                applied_cmd_ids.add(cmd.id)
                continue
            fid = op.feature_id or ""
            # Crash-consistency (KTD8): claim the id on the ledger and run apply_op's
            # store mutation inside ONE transaction, so a crash between them rolls back
            # the claim too (the command is re-delivered, never silently dropped). The
            # claim wins only on first insert; a concurrent / replayed apply loses it
            # and is skipped. mutated=True records that this branch wrote to the store
            # under the transaction (so a non-mutating fold/skip can stay outside one).
            if op.kind is NodeOpKind.ADD_NODE:
                # Fold (skip, ledger-stamp, no mint) when a LIVE feature already owns
                # this local_id — the strongest identity (KTD8): a re-emitted add with
                # the SAME local_id but a CHANGED title must NOT mint a second feature.
                # Fall back to the (normalized_title, parent_id) soft guard for an add
                # carrying no local_id (the Loop-A LLM-apply fold's key, KTD3).
                existing = store.feature_by_local_id(cmd.local_id) if cmd.local_id else None
                key = (_norm_title(op.title), op.parent_id)
                if existing is not None:
                    if cmd.local_id:
                        fids_by_local[cmd.local_id] = existing.id  # re-echo the prior mint
                    store.mark_command_applied(cmd.id)
                    applied_cmd_ids.add(cmd.id)
                    continue
                if key in live_title_parent:
                    # Duplicate (re-sent / replayed) add — fold, don't mint (KTD3).
                    store.mark_command_applied(cmd.id)
                    applied_cmd_ids.add(cmd.id)
                    continue
                with store.transaction():
                    if not store.try_claim_command(cmd.id):
                        applied_cmd_ids.add(cmd.id)
                        continue
                    fid = _accept_with_fid(op)
                op.feature_id = fid  # so build_directive / supersede key off the minted id
                if fid:
                    live_title_parent.add(key)
                    if cmd.local_id:
                        fids_by_local[cmd.local_id] = fid
            elif op.feature_id and store.get_feature(op.feature_id) is None:
                # Target vanished (already retired / never existed): skip, don't crash.
                store.mark_command_applied(cmd.id)
                applied_cmd_ids.add(cmd.id)
                continue
            else:
                # Snapshot the pre-edit description for the IDE's in-situ diff (AMEND
                # only; first edit per feature wins) — mirrors step 2's baseline capture.
                # Compute the NEWLY-bolded spans (new bold minus old bold) the same way
                # diff_codoc did, so a command-driven AMEND's Focus: line lists only the
                # spans this edit emphasized, not every bold span in the description.
                if op.kind is NodeOpKind.AMEND and op.feature_id:
                    prev = store.get_feature(op.feature_id)
                    prev_desc = (prev.description or "") if prev else ""
                    if op.feature_id not in baselines:
                        baselines[op.feature_id] = prev_desc
                    if op.description is not None:
                        old_bold = set(extract_bold(prev_desc))
                        newly = [b for b in extract_bold(op.description) if b not in old_bold]
                        if newly:
                            diff.emphasis[op.feature_id] = newly
                # Authorship stamp: the IDE host annotates each settle with WHO authored
                # it (edits.json); an edit with no annotation defaults to human/pen. Same
                # stamping step 2 applied to a diff op, now applied to the command op.
                ann = annotations.get(op.feature_id or "")
                with store.transaction():
                    if not store.try_claim_command(cmd.id):
                        applied_cmd_ids.add(cmd.id)
                        continue
                    apply_op(op, store, source="user", applied=True,
                             actor=(ann.actor if ann else ""), mode=(ann.mode if ann else ""),
                             caused_by=(ann.suggestion_id if ann else ""))
                    # A command `retire` is a SOFT, DETACH-ONLY retire (mirrors the
                    # verdict-accept RETIRE branch): mark retired AND detach the
                    # feature's bindings so the code isn't left bound to a now-hidden
                    # feature (reconcile would treat it as covered → silently orphaned).
                    # It must NEVER queue a code-deletion directive — none of the five
                    # webview command kinds set delete_code, so a human deleting a node
                    # in the doc removes the FEATURE, not the code (the old
                    # reconcile_doc_presence behavior). Code-deletion is reserved for an
                    # explicit delete_code retire (the agent-side `~`, step 2 / inbox).
                    if op.kind is NodeOpKind.RETIRE_NODE and op.feature_id:
                        for b in store.bindings_for_feature(op.feature_id):
                            store.delete_binding(b.file, b.symbol_path)
                # A command-driven retire supersedes prior pending directives for the
                # feature (add+retire churn — INV1/N15), mirroring the text retire path.
                if op.kind is NodeOpKind.RETIRE_NODE and op.feature_id:
                    _supersede_directives(root_dir, codoc_dir, {op.feature_id})
                elif op.kind is NodeOpKind.AMEND and op.feature_id:
                    # A fresh AMEND coalesces with this feature's earlier held draft
                    # (one directive per feature, never a stack) — the same coalesce the
                    # text-diff path drove via diff.user_ops, now driven by the command.
                    command_amend_fids.add(op.feature_id)
            store.mark_command_applied(cmd.id)
            applied_cmd_ids.add(cmd.id)
            res.commands += 1
            # Queue a directive for a code-implying command (the loop's codoc→code half).
            if op.kind is NodeOpKind.ADD_NODE and not fid:
                continue  # add minted nothing (shouldn't happen post-_accept_with_fid)
            # A command `retire` is detach-only (above) and must queue NO directive — the
            # webview never sets delete_code, so deleting a doc node never deletes code.
            if op.kind is NodeOpKind.RETIRE_NODE:
                res.edit_notes.append(_edit_label(op, store, False))
                continue
            will_queue = edit_mints_directive(op, store)
            res.edit_notes.append(_edit_label(op, store, will_queue))
            if will_queue:
                # The directive's cause: the annotation's suggestion id when present
                # (a doc-ahead suggestion the host applied), else the command id.
                ann = annotations.get(fid or "")
                cause = ann.suggestion_id if (ann and ann.suggestion_id) else cmd.id
                directive_ops.append((op, fid, cause))
        edits_channel.clear_commands(codoc_dir, applied_cmd_ids)
        res.fids_by_local = fids_by_local

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

    # 2. (RETIRED — U7 / FIX E) Direct text-diff user edits. ``diff.user_ops`` is now
    #    always empty (``_merge_channels`` returns an empty CodocDiff), because every
    #    authored edit arrives as an identity-keyed command (step 0.5) applied via
    #    ``apply_op`` — not inferred from a tree.codoc/tree.doc.json diff. The former
    #    ``for op in diff.user_ops:`` apply loop here was structurally dead code and is
    #    deleted. ``diff.emphasis`` still carries the newly-bolded spans (populated in
    #    step 0.5, consumed at directive render below); only the dead apply loop is gone.

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

    # 2.7 (RETIRED — U7 / FIX E) Text `> …` steering comments. ``diff.comments`` /
    #     ``diff.new_node_comments`` are now always empty (``_merge_channels`` returns
    #     an empty CodocDiff): once the webview stopped writing tree.codoc (U6), an
    #     inline comment arrives through edits.json (step 2.8 ``drain_steers``), not as
    #     a `> …` text line. The former text-comment → STEER loop here was dead and is
    #     deleted. ``amend_cause`` (a co-occurring command's cause, for the IDE cascade
    #     cue) and the ``steered`` accumulator survive for step 2.8.
    amend_cause = {fid: cause for _op, fid, cause in directive_ops if fid}
    steered: list[tuple[str, str, str]] = []  # (directive text, feature_id, cause)

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
    # (verdict accepts / intent applies / applied commands) — otherwise the next
    # pass would diff the stale text and read it as a human edit reverting the
    # change. User edits are absorbed above (commands → apply_op), so regenerating
    # from the store loses nothing.
    if (res.accepted or res.user_edits or res.commands or block_store_changed
            or (res.steered and not dry_run)):
        write_tree(store, codoc_dir)
        # KTD9: the daemon is the sole writer of tree.doc.json — re-render the store
        # projection so the webview's file-watch re-read repaints from the source of
        # truth. Skipped on a dry pass (no durable state mutation).
        if not dry_run:
            write_tree_doc(store, codoc_dir)

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
    # Every AMEND now arrives as a command (step 0.5) — ``diff.user_ops`` is empty
    # (U7 / FIX E), so the coalesce set is just the command-driven AMEND fids.
    edited_fids = set(command_amend_fids)
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
