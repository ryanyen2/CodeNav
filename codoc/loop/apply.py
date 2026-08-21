"""Routing + application of NodeOps.

``derive_auto_ops`` turns the trivially-resolvable parts of a change set into
safe ops (no LLM): a modified bound chunk → REFRESH, a removed bound chunk →
DETACH. ``apply_op`` writes an Event and, when ``applied``, mutates the store.

The thresholds that decide whether an LLM-proposed description edit lands
silently or surfaces for review live here. The question they answer is not "is
this edit small" but "does it keep what was already written" — and it is asked
more strictly of prose a person wrote than of prose the loop wrote, because
those are not the same thing to overwrite (see :func:`preserved_ratio`).
"""
from __future__ import annotations

import sys
from difflib import SequenceMatcher

from codoc.codoc_file.parse import normalize_description
from codoc.doclang import clause_chars
from codoc.loop.diff import ChangeSet
from codoc.model.binding import Binding
from codoc.model.event import (
    ACTOR_HUMAN, ACTOR_LOOP, MODE_AUTO, SAFE_OPS, Event, NodeOp, NodeOpKind,
    default_provenance,
)
from codoc.model.feature import Feature, Lifecycle
from codoc.store.db import Store

AMEND_SAFE_RATIO = 0.30  # description edits changing ≤30% of the text auto-apply

# How much of the existing prose must survive as recognisable, contiguous runs
# for an amend to count as a repair rather than a rewrite. Higher for text a
# person wrote: their wording is the thing being protected.
PRESERVE_RATIO_HUMAN = 0.85
PRESERVE_RATIO_MACHINE = 0.50
# A shared run must be at least this long to count as preserved. Below it,
# matches are just the vocabulary any two descriptions of the same code share
# ("the", "returns the", "when the file") — counting those would score a total
# rewrite as faithful.
#
# The floor is a CLAUSE, and a clause is a number of words — which is 24
# characters of English and about 8 of Chinese. Hard-coding the character count
# made this gate script-dependent in a way nobody chose: against a Chinese
# description almost no run reaches 24 characters (that is a whole sentence), so
# `preserved_ratio` scored ~0 for every real amend, no human-written prose could
# ever clear PRESERVE_RATIO_HUMAN, and every repair queued as a full rewrite for
# review. `doclang.clause_chars` computes it per script and returns the same 24
# for Latin text — this is a port of the constant's meaning, not a retune of its
# value.


def derive_auto_ops(cs: ChangeSet, store: Store) -> list[NodeOp]:
    """Safe ops resolvable by exact binding lookup — no LLM needed."""
    ops: list[NodeOp] = []
    for m in cs.modified:
        b = store.binding_at(m.file, m.symbol_path)
        if b:
            ops.append(NodeOp(kind=NodeOpKind.REFRESH, feature_id=b.feature_id,
                              bindings=[(m.file, m.symbol_path)]))
    for a in cs.added:
        b = store.binding_at(a.file, a.symbol_path)
        if b:  # re-added an already-bound anchor: just refresh
            ops.append(NodeOp(kind=NodeOpKind.REFRESH, feature_id=b.feature_id,
                              bindings=[(a.file, a.symbol_path)]))
    for r in cs.removed:
        b = store.binding_at(r.file, r.symbol_path)
        if b:
            ops.append(NodeOp(kind=NodeOpKind.DETACH, feature_id=b.feature_id,
                              bindings=[(r.file, r.symbol_path)]))
    return ops


def preserved_ratio(old: str, new: str) -> float:
    """How much of ``old`` survives in ``new`` as long, contiguous runs (0…1).

    The question an amend gate needs to answer is "did this keep what was
    there?", and overall similarity answers a different one. Two descriptions of
    the same code share most of their vocabulary, so a complete rewrite in the
    model's own voice scores as similar; meanwhile appending one true sentence
    to an accurate description scores as a large change, though it destroyed
    nothing. Both readings are backwards.

    Measuring only runs long enough to be a preserved *clause* separates the two
    cases: a repair leaves the surrounding prose intact and shows up as a couple
    of long matches, while a re-say of the same idea leaves only short scattered
    ones no matter how familiar it reads.
    """
    if not old:
        return 1.0
    # Measured against ``old``: it is the prose being protected, so its script is
    # the one that decides how long a preserved clause is.
    floor = clause_chars(old)
    matcher = SequenceMatcher(None, old, new, autojunk=False)
    kept = sum(b.size for b in matcher.get_matching_blocks() if b.size >= floor)
    return min(1.0, kept / len(old))


def is_small_amend(op: NodeOp, store: Store) -> bool:
    """True if an AMEND is a repair of the existing description, not a rewrite.

    A rewrite is not refused — it becomes a pending proposal, where the author
    sees the before/after and decides. That is the whole difference between a
    tool that maintains someone's document and one that gradually replaces it.
    """
    if op.kind is not NodeOpKind.AMEND:
        return False
    f = store.get_feature(op.feature_id) if op.feature_id else None
    old = (f.description if f else "") or ""
    new = op.description if op.description is not None else old
    if not old and not new:
        return True
    if not old:
        return True  # first prose on a bare node displaces nothing
    written_by = store.feature_writer_info(op.feature_id)[1] if op.feature_id else ""
    if written_by == ACTOR_HUMAN:
        # Only the author's own words are held to the strict bar; anything the
        # loop wrote it may freely revise.
        return preserved_ratio(old, new) >= PRESERVE_RATIO_HUMAN
    if preserved_ratio(old, new) >= PRESERVE_RATIO_MACHINE:
        return True
    change = 1.0 - SequenceMatcher(None, old, new).ratio()
    return change <= AMEND_SAFE_RATIO


def restates_current(op: NodeOp, store: Store) -> bool:
    """True if this AMEND asks for prose the feature already has.

    A pass with a schema that wants an op will sometimes hand back the description
    it was shown, reflowed or verbatim. The text it would write is not the problem;
    everything around the write is:

    * **it takes the paragraph over.** :func:`apply_op` stamps ``feature_writers``
      with whoever wrote last, so a restatement moves a human-written node to the
      loop — and the amend gate then holds the NEXT rewrite to the machine bar
      instead of the author's. One op that changed no words is enough to unlock
      somebody's prose.
    * **it spends a moment of the timeline on nothing.** The scrubber and the
      per-span blame read applied events, so a restatement is a change a reader can
      open and find nothing in — and it re-attributes spans the author wrote to
      whoever restated them. A ledger of changes that did not change anything is
      how a diff stops being worth reading.

    The authored side has refused this since merge3 landed, for the same reason and
    in almost the same words (``loop_b._resolve_content`` → ``NOOP``). This is the
    code side of that rule, and it compares with the same
    :func:`normalize_description` — whitespace the reader cannot see must not count
    as a change here either.

    A **plan** amend (``realized is False``) is never a restatement, however
    familiar its words. Prose that already says what the feature will do, marked as
    not yet built, is a request to build it; the words being unchanged is the point,
    not a sign there is nothing to do.
    """
    if op.kind is not NodeOpKind.AMEND or not op.feature_id:
        return False
    if op.realized is False:
        return False
    f = store.get_feature(op.feature_id)
    if f is None:
        return False
    said_anything = False
    if op.description is not None:
        said_anything = True
        if normalize_description(op.description) != normalize_description(f.description or ""):
            return False
    if op.title is not None:
        said_anything = True
        if op.title.strip() != (f.title or "").strip():
            return False
    return said_anything


def should_auto_apply(op: NodeOp, store: Store) -> bool:
    """Safe ops auto-apply; AMEND only when the edit is small; structural never.

    A PLAN amend is the exception the size test cannot see. ``builds=True`` mints an
    amend with ``realized=False`` — prose that says what the feature WILL do, whose
    code does not exist yet — and that is a request for work, not a reconciliation of
    work already done. Judging it by how much wording it preserved auto-applied the
    small ones: no proposal row, so no Accept & build, so no realize directive queued,
    and the doc then diffed the new words against the displaced ones and painted them
    in the CODE channel — reporting a build that never happened. A plan always awaits
    a verdict, however few words it changes.
    """
    if op.kind not in SAFE_OPS:
        return False
    if op.kind is NodeOpKind.AMEND:
        return op.realized is not False and is_small_amend(op, store)
    return True


def apply_op(
    op: NodeOp,
    store: Store,
    *,
    source: str,
    applied: bool,
    fp_lookup: dict[tuple[str, str], str] | None = None,
    th_lookup: dict[tuple[str, str], str] | None = None,
    index_keys: set[tuple[str, str]] | None = None,
    actor: str = "",
    mode: str = "",
    caused_by: str = "",
    writer: str = "",
    claims_prose: bool = True,
) -> Event:
    """Log an Event for ``op``; if ``applied``, mutate the store accordingly.

    ``fp_lookup`` / ``th_lookup`` supply the chunk's ``tokens_hash`` /
    ``types_hash`` for any binding the op creates — recorded so staleness and
    rename detection have an anchor. Callers without the hashes pass neither;
    the binding stores empty strings (a re-bind that does have them backfills).

    ``actor`` / ``mode`` / ``caused_by`` stamp the change ledger. When the
    caller carries no explicit provenance (legacy paths), actor/mode are
    inferred from ``source`` via :func:`default_provenance`.

    ``claims_prose=False`` says this op rewrites an ADDRESS and not a word of the
    text — the citation-repointing pass after a rename (``loop_a._repoint_citations``)
    is the only caller. Such a write must not take the paragraph over: stamping
    ``feature_writers`` would make the loop the author of somebody's prose and so relax
    the amend gate over their next rewrite, and counting it again in the prose
    scorecard would score text that was already scored when it was written, against
    whoever is repointing rather than whoever wrote it. Both are the same laundering
    error :func:`restates_current` exists to refuse.

    ``writer`` names the editing session behind an authored command; every other
    caller falls back to ``source``. It is recorded per feature so a later command
    can tell "I am continuing my own edit" (base legitimately behind, because the
    projection has not caught up) from "someone else wrote here" (a real
    disagreement) — see :func:`codoc.loop.loop_b._resolve_content`. Recording it here,
    at the one write boundary, is what makes an agent's write count as someone else
    without every agent path having to remember to say so.
    """
    d_actor, d_mode = default_provenance(source, applied)
    # Write-boundary sanitization: authored text must never carry id-shaped
    # ⟨…⟩ tokens where the render→parse round-trip would read them as tree
    # STRUCTURE — a title id token hijacks the node's identity, a marker line
    # with an id inside a description forges a phantom node and truncates the
    # prose (see parse.sanitize_authored_*). One choke point for every writer:
    # LLM ops, MCP, webview commands, inbox accepts, bootstrap.
    if op.title is not None or op.description is not None:
        from codoc.codoc_file.parse import (
            sanitize_authored_description,
            sanitize_authored_title,
        )
        if op.title is not None:
            clean = sanitize_authored_title(op.title)
            # A title that was ONLY id tokens sanitizes to '' — dropping the
            # AMEND beats blanking a real title (ADD falls back to "Untitled").
            op.title = clean if (clean or op.kind is not NodeOpKind.AMEND) else None
        if op.description is not None:
            op.description = sanitize_authored_description(op.description)
        # The prose gate's own scorecard, kept at the same choke point for the same
        # reason: every path that writes prose passes through here, so this is the
        # only place the rate is a rate rather than a sample of whichever caller
        # remembered to measure. What it counts is prose a READER will meet -- after
        # whatever repair the generating pass already made, and including a pending
        # proposal, because the overlay materializes those into the document and a
        # sentence nobody has accepted yet is still a sentence somebody reads.
        #
        # A person's own words are never counted. They are not a defect when they
        # break a rule, they are the author writing, and averaging them into the
        # score would make our own prose look better every time somebody typed a
        # dash.
        if (actor or d_actor) != ACTOR_HUMAN and claims_prose:
            _record_prose(op, store)
    # Pre-mint the id for a directly-applied ADD so the creation event records
    # the real feature id (blame needs "who created this" findable by feature).
    # Pending proposals keep a bare op — their id mints on acceptance.
    if op.kind is NodeOpKind.ADD_NODE and applied and not op.feature_id:
        from codoc.model.ids import new_feature_id
        op.feature_id = new_feature_id()
    # Record what an applied op displaces, before _mutate destroys it — this is the only
    # moment the old value and its authorship both exist. A safe auto-amend asks nobody,
    # so without this the author cannot be shown what changed, only that the paragraph is
    # different from the one they remember; and the whole timeline (loop/revisions.py)
    # reads backwards, so an unrecorded prior value is a hole nothing downstream can fill.
    # Reading the writer here also matters: set_feature_writer below reassigns it to
    # whoever is writing now.
    if applied and op.feature_id:
        _record_displaced(op, store)
    event = Event(source=source, op=op, applied=applied,
                  actor=actor or d_actor, mode=mode or d_mode, caused_by=caused_by)
    store.append_event(event)
    if applied:
        _mutate(op, store, fp_lookup or {}, th_lookup or {}, index_keys, event=event)
        if op.feature_id and claims_prose \
                and (op.title is not None or op.description is not None):
            # The event's actor doubles as the writer's ROLE. It is already
            # resolved here (explicit provenance, else derived from source), so
            # rank arbitration reads the same authorship the ledger records
            # rather than a parallel notion that could disagree with it.
            # Content-bearing ops only: feature_writers answers "who put the
            # current TEXT here", and a MOVE/RETIRE would otherwise claim
            # authorship of prose it never touched — skewing the next
            # contended-edit arbitration toward whoever last dragged the node.
            store.set_feature_writer(op.feature_id, writer or source, event.actor)
        store.mark_applied(event.id)  # stamp accepted_at for the audit log
    return event


def _record_prose(op: NodeOp, store: Store) -> None:
    """Count this op's prose against the gate's rules, for :func:`prose.defect_rate`.

    The context (what this node binds, whether it has children, how deep it sits) is
    built by :func:`prose.advise`, which is also what tells an agent what to fix --
    one builder, so the number and the advice can never describe different nodes.

    Advisory throughout: a statistic that can fail a write is worse than no
    statistic, so every part of this is inside one try.
    """
    from codoc.loop import prose
    try:
        prose.record(store, checked=1, defects=prose.advise(store, op))
    except Exception:  # noqa: BLE001 -- a scorecard must never sink a write
        pass


def _log_child_promotion(
    store: Store, child_id: str, retiree_id: str,
    new_parent_id: str | None, cause: Event | None,
) -> None:
    """Log the MOVE that retiring ``retiree_id`` forced on one of its children.

    Applied directly (the mutation already happened in ``_mutate``) and stamped
    ``caused_by`` the retire event, so the IDE groups the promotions under the retire
    that caused them rather than showing a fleet of unexplained moves. Provenance is
    INHERITED from the retire: whoever retired the parent is the author of its
    consequences. A missing ``cause`` (a direct ``_mutate`` call in a test) still logs
    the move — the causal link is the part that degrades, never the record itself.
    """
    store.append_event(Event(
        source=cause.source if cause else "loop",
        op=NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=child_id,
                  parent_id=new_parent_id, prev_parent_id=retiree_id,
                  rationale="promoted when its parent was retired"),
        applied=True,
        actor=cause.actor if cause else ACTOR_LOOP,
        mode=MODE_AUTO,
        caused_by=cause.id if cause else "",
    ))


def _record_displaced(op: NodeOp, store: Store) -> None:
    """Stamp ``op.prev_*`` with the values this applied op is about to destroy.

    ONE rule for every kind: record what the target feature held a moment ago. The
    timeline reconstructs the past by walking the ledger BACKWARDS from the live tree,
    so a field it cannot un-apply is a permanent hole — and the honest failure (a
    revision reported as unreconstructible) still costs the reader the answer they came
    for. Only the fields an op actually overwrites are captured, so an event stays as
    small as the change it records:

    * AMEND — title and/or description, whichever the op replaces and actually changes.
    * RETIRE_NODE — title AND description unconditionally, because a retired feature
      leaves the live projection entirely: without its text the timeline can show that
      a node used to be there but not what it said, which is the one thing worth seeing.
    * MOVE_NODE — the prior parent (``""`` for a root; see ``NodeOp.prev_parent_id``).

    Never overwrites a value the caller already supplied: a re-applied op (an accepted
    proposal, a crash-replayed command) carries the base it was computed against, and
    the store's current value is no longer that base.
    """
    prior = store.get_feature(op.feature_id or "")
    if prior is None:
        return
    if op.kind is NodeOpKind.AMEND:
        if op.description is not None and op.prev_description is None \
                and (prior.description or "") != op.description:
            op.prev_description = prior.description or ""
        if op.title is not None and op.prev_title is None and prior.title != op.title:
            op.prev_title = prior.title
        # Authorship of the displaced PROSE — weighted by the IDE (the loop revising its
        # own bootstrap wording is housekeeping; the loop overwriting a person is not).
        if (op.prev_description is not None or op.prev_title is not None) \
                and not op.prev_written_by:
            op.prev_written_by = store.feature_writer_info(op.feature_id or "")[1]
    elif op.kind is NodeOpKind.RETIRE_NODE:
        if op.prev_title is None:
            op.prev_title = prior.title
        if op.prev_description is None:
            op.prev_description = prior.description or ""
        # The retire leaves the parent alone, but the FEATURE leaves the live tree — so
        # "where did it sit?" is unanswerable from the projection a moment later, and a
        # timeline could show that a node used to exist without being able to put it back
        # where it was.
        if op.prev_parent_id is None:
            op.prev_parent_id = prior.parent_id or ""
        if not op.prev_written_by:
            op.prev_written_by = store.feature_writer_info(op.feature_id or "")[1]
    elif op.kind is NodeOpKind.MOVE_NODE:
        if op.prev_parent_id is None:
            op.prev_parent_id = prior.parent_id or ""


def _live_parent_id(store: Store, parent_id: str | None) -> str | None:
    """Resolve ``parent_id`` to a parent that is actually visible in the tree.

    A destination parent that is retired (or has been deleted out from under a
    stale ADD/MOVE) is filtered out of ``children()``, so a node parented to it
    becomes a live-but-invisible orphan — the exact catastrophe the cycle guard
    warns about, reached by a different door (accept a MOVE/ADD whose destination
    was retired in the meantime). Walk up to the nearest LIVE ancestor, falling
    back to ``None`` (a root) so the node is always reachable from some root."""
    seen: set[str] = set()
    pid = parent_id
    while pid is not None and pid not in seen:
        seen.add(pid)
        parent = store.get_feature(pid)
        if parent is None:
            return None  # destination vanished → root
        if parent.lifecycle is not Lifecycle.RETIRED:
            return pid  # a live parent — use it
        pid = parent.parent_id  # retired → try its parent
    return None


def _addressable(file: str, symbol: str) -> bool:
    """Whether ``(file, symbol)`` could name a real chunk.

    Every chunk the indexer emits is addressed ``<file>::<qualified>`` (see
    ``lang/python.py`` and ``lang/typescript.py``), so a symbol_path that does
    not carry its own file as a prefix cannot match anything, ever. It becomes a
    binding that points at nothing: invisible to the temporal diff, which only
    reasons about chunks the index knows, and therefore never repaired.

    The pairs come from a model — bootstrap and Loop A both let it supply the
    two elements independently — and a model occasionally drops the basename out
    of the middle of the path. One such binding survived a real bootstrap of
    psf/requests (764 of 765 satisfied this invariant; the one that did not was
    unreachable from the moment it was written). Checking the shape costs
    nothing and needs no index handle, so it can guard every writer.
    """
    return symbol.startswith(file + "::")


def _bindable(
    op: NodeOp, index_keys: set[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """``op.bindings`` minus any pair that cannot address a chunk.

    Shape alone catches a mangled path but not a well-formed guess. A proposal
    accepted on flask's sansio split bound ``sansio/app.py::App._make_timedelta``
    and ``sansio/app.py::create_jinja_environment`` — both correctly shaped, and
    neither a symbol that exists (the first is not a method of ``App``, the
    second is not module-level). When the caller can supply the index's key set,
    membership is the real check; ``index_keys=None`` means the caller has no
    view of the index and only the shape rule applies.
    """
    good, bad = [], []
    for file, symbol in op.bindings:
        ok = _addressable(file, symbol) and (
            index_keys is None or (file, symbol) in index_keys
        )
        (good if ok else bad).append((file, symbol))
    if bad:
        # Loud, because the alternative is a permanently dangling binding and a
        # feature that looks attributed while owning nothing.
        print(
            f"[codoc] dropped {len(bad)} unaddressable binding(s) on "
            f"{op.kind.value}: {bad[:3]}",
            file=sys.stderr,
        )
    return good


def _mutate(op: NodeOp, store: Store, fp: dict[tuple[str, str], str],
            th: dict[tuple[str, str], str],
            index_keys: set[tuple[str, str]] | None = None,
            *, event: Event | None = None) -> None:
    k = op.kind
    if k in (NodeOpKind.ATTACH, NodeOpKind.REFRESH):
        for file, symbol in _bindable(op, index_keys):
            store.upsert_binding(Binding(feature_id=op.feature_id, file=file,
                                         symbol_path=symbol, fingerprint=fp.get((file, symbol), ""),
                                         types_hash=th.get((file, symbol), "")))
        # Named lifecycle transition (A1): the first code bound to a plan
        # placeholder promotes it planned→active. store.mark_realized is guarded to
        # `planned` rows, so this is the one explicit transition point — no silent
        # bool flip, and a retired feature can never be resurrected by a stray bind.
        if op.bindings and op.feature_id:
            owner = store.get_feature(op.feature_id)
            if owner and owner.lifecycle is Lifecycle.PLANNED:
                store.mark_realized(op.feature_id)
    elif k is NodeOpKind.DETACH:
        for file, symbol in op.bindings:
            store.delete_binding(file, symbol)
    elif k is NodeOpKind.AMEND:
        f = store.get_feature(op.feature_id)
        if f:
            if op.title is not None:
                f.title = op.title
            if op.description is not None:
                f.description = op.description
            # advance() (not HLC.now()) guarantees the new updated_at is STRICTLY greater
            # than the feature's prior one even for two edits in the same wall-clock ms —
            # HLC.now() always returns logical_time=0, so same-ms edits tied and the
            # webview's "strictly newer" doc-gate could miss a real change (P2). Bumping
            # the logical counter off the feature's own clock keeps per-feature edits
            # monotonic without any process-global state.
            f.updated_at = f.updated_at.advance()
            store.upsert_feature(f)
    elif k is NodeOpKind.ADD_NODE:
        # ``realized`` defaults True: a node is a real feature unless an explicit
        # plan path (propose.propose_plan / mcp.tools.plan_add) marks it a
        # placeholder with realized=False. We deliberately do NOT infer
        # "unrealized" from empty bindings — org-pass theme PARENTS are
        # legitimately binding-less yet fully real, and marking them placeholders
        # would mis-fire the IDE's unrealized decoration on every theme node.
        # A stale ADD (proposal accepted after its destination was retired, or an
        # MCP plan_add under a since-retired parent) must not bury the new node
        # under a retired ancestor — resolve to the nearest live parent.
        add_parent_id = _live_parent_id(store, op.parent_id)
        f = Feature(title=op.title or "Untitled", description=op.description or "",
                    parent_id=add_parent_id, local_id=op.local_id,
                    rank=store.rank_between(add_parent_id, op.after_id, op.before_id),
                    realized=(op.realized if op.realized is not None else True))
        if op.feature_id:
            f.id = op.feature_id
        store.upsert_feature(f)
        for file, symbol in _bindable(op, index_keys):
            store.upsert_binding(Binding(feature_id=f.id, file=file, symbol_path=symbol,
                                         fingerprint=fp.get((file, symbol), ""),
                                         types_hash=th.get((file, symbol), "")))
    elif k is NodeOpKind.MOVE_NODE:
        f = store.get_feature(op.feature_id)
        if f:
            # Reject a cycle-forming move (destination is the node itself or a
            # descendant). Silently dropping the move is far safer than applying it:
            # a cycle detaches the subtree from every root, so render_tree's walk, the
            # doc projection, and the sidecar all drop it — the features stay live and
            # bound (their chunks read as "covered", so Loop A never re-homes them) but
            # are invisible and unrecoverable from any UI. The move source (webview
            # command / proposal accept / MCP / hub) all funnel through here, so this is
            # the single chokepoint that keeps the tree acyclic.
            if store.would_move_create_cycle(op.feature_id, op.parent_id):
                import logging
                logging.getLogger(__name__).warning(
                    "codoc: rejected cycle-forming move of %s under %s (no-op)",
                    op.feature_id, op.parent_id)
            else:
                # Same orphan hazard as the cycle case: a MOVE whose destination
                # was retired (or deleted) since the op was minted would strand the
                # node under an invisible ancestor. Land it on the nearest LIVE
                # parent instead of the requested (dead) one.
                new_parent = _live_parent_id(store, op.parent_id)
                # Order is resolved HERE, at the write boundary, because only the
                # store knows what the destination's children currently are. A move
                # that crosses parents must always be re-ranked — the old key was a
                # position among different siblings and means nothing here — while a
                # reorder within one parent re-ranks from the neighbours it was given.
                if new_parent != f.parent_id or op.after_id or op.before_id:
                    f.rank = store.rank_between(new_parent, op.after_id, op.before_id)
                f.parent_id = new_parent
                f.updated_at = f.updated_at.advance()
                store.upsert_feature(f)
    elif k is NodeOpKind.RETIRE_NODE:
        if op.feature_id:
            # Re-parent LIVE children to the grandparent BEFORE retiring. A retired
            # feature is filtered out of `children()`, so any child left pointing at it
            # becomes an orphan — invisible to render/projection/sidecar (all walk from
            # the roots) yet still live + bound, i.e. unrecoverable. Promoting the
            # children to the retiree's own parent keeps the tree connected (a root's
            # children become roots). Retiring a subtree is done child-first or cascades
            # correctly: each retire only lifts its own direct children one level.
            owner = store.get_feature(op.feature_id)
            if owner is not None:
                for child in store.children(op.feature_id):
                    child.parent_id = owner.parent_id
                    # A cross-parent move must always be re-ranked (same rule as
                    # MOVE_NODE above): the old key was a position among the
                    # retiree's children and means nothing among the grandparent's.
                    # children() iterates in rank order, so appending keeps the
                    # promoted siblings' relative order.
                    child.rank = store.rank_for_append(owner.parent_id)
                    child.updated_at = child.updated_at.advance()
                    store.upsert_feature(child)
                    # This promotion is a real tree mutation and until now it was the one
                    # the ledger did not record — a feature silently changed parents and
                    # `codoc history` showed nothing, so a reader who noticed had no way
                    # to learn why, and a backwards replay lost the subtree wholesale.
                    # It is logged as what it is: a MOVE caused by this retire.
                    _log_child_promotion(store, child.id, op.feature_id,
                                         owner.parent_id, event)
            # Mark retired only. Binding detach is a PATH decision, not a property of
            # the op: an inbox/auto retire detaches (untrack — Loop B does it), while
            # a human `~` retire keeps its bindings so Loop B can build the code-removal
            # directive (the code is deleted by the agent, and reconcile detaches then).
            store.retire_feature(op.feature_id)
