"""codoc MCP tool implementations — plain, testable functions.

Each function opens the store, routes through the same ``apply_op`` seam that
Loop A / the CLI use (so identity-minting, the ``UNIQUE(file, symbol_path)``
binding constraint, and rendering are all reused), re-renders ``tree.codoc`` +
the sidecar, and returns a JSON-ready dict. The FastMCP server in
:mod:`codoc.mcp.server` is a thin wrapper that resolves the ``.codoc`` dir from
the agent's cwd and calls these.

Agent-driven reflection (the code-first loop) is the reason these exist: the
agent that just wrote the code knows *why*, so it can submit precise structural
ops with real intent — richer than Loop A's blind index-diff can infer. Safe ops
(attach/detach/refresh/small-amend) apply immediately; structural ops
(add_node/move_node/retire_node, large amend) become ``applied=False`` proposals
the user reviews in the IDE.
"""
from __future__ import annotations

from pathlib import Path

from codoc.doclang import (
    detect_prose_language, language_tag_for, prose_letters, workspace_doc_language,
)
from codoc.loop.activity import PHASE_DONE, mark_feature_phase
from codoc.loop.apply import apply_op, should_auto_apply
from codoc.loop.ask import (
    MAX_STEPS,
    AskStep,
    build_walkthrough,
    clear_walkthrough as ask_clear,
    read_walkthrough as ask_read,
    write_walkthrough,
)
from codoc.loop.classify import suppressed_by_hold
from codoc.loop.edits import hold_set, read_manifest
from codoc.loop.inbox import read_verdicts as inbox_read
from codoc.loop.locks import loop_lock
from codoc.loop.reconcile import safe_write_tree
from codoc.model.event import (
    LOOP_A_AGENT_SOURCE,
    PLAN_SOURCE,
    NodeOp,
    NodeOpKind,
)
from codoc.store.db import Store, open_store

# Ops that update an existing live feature's content/bindings — marking these
# "done" resolves the IDE doc view's skeleton into the reflected content.
_LIVE_FEATURE_KINDS = {
    NodeOpKind.ATTACH, NodeOpKind.DETACH, NodeOpKind.REFRESH,
    NodeOpKind.AMEND, NodeOpKind.MOVE_NODE, NodeOpKind.RETIRE_NODE,
}


def _mark_reflected(codoc_dir: str, ops: list[NodeOp]) -> None:
    """Best-effort: flag the live features these ops touched as reflection-done."""
    fids = [op.feature_id for op in ops
            if op.kind in _LIVE_FEATURE_KINDS and op.feature_id]
    mark_feature_phase(codoc_dir, fids, PHASE_DONE)


def _parse_binds(binds: list[str] | None) -> list[tuple[str, str]]:
    """Parse "file.py::symbol" bind strings into ``(file, symbol_path)`` pairs.

    The stored ``symbol_path`` is the FULL "file::qualified" form the indexer
    emits (``codoc/lang/python.py`` builds ``f"{file}::{qualified}"``), so the
    binding matches a real chunk and Loop A can resolve / dedup against it. The
    ``file`` is the prefix before the first ``::``.

    A bind with no ``::`` names a file rather than a chunk. It used to be stored
    as ``(b, b)``, which produced a binding whose symbol_path could never match
    any indexed chunk — permanently dangling, and invisible to the temporal diff
    that would otherwise repair it. Dropping it is the honest outcome: the
    caller asked to bind something that is not addressable.
    """
    out: list[tuple[str, str]] = []
    for b in binds or []:
        if "::" in b:
            file = b.split("::", 1)[0]
            out.append((file, b))  # symbol_path keeps the full "file::symbol"
    return out


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _op_summary(op: NodeOp, store: Store) -> str:
    if op.kind is NodeOpKind.ADD_NODE:
        return f'add "{op.title}"'
    target = op.feature_id or ""
    f = store.get_feature(target) if target else None
    name = f.title if f else target
    return f"{op.kind.value} {name}".strip()


# ─── reads ────────────────────────────────────────────────────────────────────

def read_tree(
    codoc_dir: str,
    *,
    root_id: str | None = None,
    depth: int = 0,
    include_bindings: bool = False,
) -> dict:
    """The live feature tree (incl. ``realized`` + per-feature ``drift``) + pending
    proposals — SCOPED by default so the payload stays reasoning-sized.

    ``drift`` surfaces the loop-computed per-feature trust signal
    (``"questioned"`` / ``"binding-lost"``) the same way the IDE sidecar does, so an
    agent reconciling via ``/codoc:sync`` can see which features the last code-side
    pass questioned. It is read from ``.codoc/drift.json`` (one dict lookup per
    feature; no index read) — ``followed`` is the absence of an entry, so the field
    is omitted (None) for features the loop did not flag.

    Scoping (all optional): ``root_id`` limits output to that feature's subtree;
    ``depth`` (>0) caps levels below the root(s); ``include_bindings=False`` (the
    default) returns per-feature ``binding_count`` + ``files`` instead of every
    qualified symbol_path — measured, symbol paths were ~2/3 of the old payload
    and the least useful part for reasoning about intent. Pass
    ``include_bindings=True`` for the full lists when actually needed.
    """
    from codoc.loop.edits import read_drift

    drift = read_drift(codoc_dir)
    with open_store(codoc_dir) as store:
        all_feats = store.list_features()
        by_parent: dict[str | None, list] = {}
        for f in all_feats:
            by_parent.setdefault(f.parent_id, []).append(f)

        selected: list = []
        if root_id is None:
            roots = by_parent.get(None, [])
        else:
            root = store.get_feature(root_id)
            if root is None:
                return _err(f"unknown root_id {root_id!r}")
            roots = [root]

        seen: set[str] = set()

        def _walk(feats, level: int) -> None:
            for f in feats:
                if f.id in seen:  # a parent-cycle must not recurse forever
                    continue
                seen.add(f.id)
                selected.append(f)
                if depth <= 0 or level < depth:
                    _walk(by_parent.get(f.id, []), level + 1)

        _walk(roots, 1)
        # Nothing may silently vanish: features unreachable from the roots
        # (orphaned parent link, cycle members) still exist and still bind code
        # — surface them flat at the end of the whole-tree read instead of
        # hiding them from the agent the way a broken parent link hides them
        # from every render walk.
        if root_id is None and depth <= 0:
            selected.extend(f for f in all_feats if f.id not in seen)

        grouped = store.bindings_by_feature()  # one bulk read, not per-feature
        default = workspace_doc_language(codoc_dir)
        feats = []
        for f in selected:
            binds = grouped.get(f.id, [])
            row = {
                "id": f.id,
                "title": f.title,
                "description": f.description,
                "parent_id": f.parent_id,
                "realized": f.realized,
                "drift": drift.get(f.id),
                "binding_count": len(binds),
                "files": sorted({b.file for b in binds}),
                # Present only when this node reads as a DIFFERENT language than the
                # tree's — the absence means "the tree's language", the same
                # presence-keyed convention the sidecar uses. It is what tells an
                # agent that amending this particular node means writing English in
                # an otherwise-Chinese tree, which is the author's choice to keep.
                **_lang_field(f.description or f.title, default),
            }
            if include_bindings:
                row["bindings"] = [b.symbol_path for b in binds]
            feats.append(row)
        proposals = [
            {"event_id": e.id, "kind": e.op.kind.value, "feature_id": e.op.feature_id,
             "parent_id": e.op.parent_id, "title": e.op.title, "source": e.source,
             "rationale": e.op.rationale}
            for e in store.pending_events()
        ]
        return {"ok": True, "features": feats, "proposals": proposals,
                "truncated_to_depth": depth if depth > 0 else None,
                "doc_language": doc_language_block(codoc_dir)}


def read_context(
    codoc_dir: str,
    *,
    files: list[str] | None = None,
    feature_id: str | None = None,
    include_bindings: bool = True,
) -> dict:
    """The RELEVANT slice of the tree for what an agent is working on.

    This is the primary agent read: given the file(s) being edited (repo-relative
    paths) and/or a feature id, it runs the same ego-graph relevance selection
    Loop A uses for its own LLM context (features bound in those files, expanded
    one hop along call/import edges, plus parents/children) and returns that
    bounded subtree + a compact whole-tree title outline for orientation. Payload
    is proportional to the *edit*, not the repo — prefer this over ``codoc_tree``.
    """
    from codoc.agent.base import titles_outline
    from codoc.loop.subtree import select_context

    with open_store(codoc_dir) as store:
        file_set = set(files or [])
        extra_symbols: set[str] = set()
        if feature_id:
            f = store.get_feature(feature_id)
            if f is None:
                return _err(f"unknown feature_id {feature_id!r}")
            for b in store.bindings_for_feature(feature_id):
                file_set.add(b.file)
                extra_symbols.add(b.symbol_path)
        if not file_set and not extra_symbols:
            return _err("pass files=[...] and/or feature_id")
        subtree, all_titles, context = select_context(store, file_set, extra_symbols)
        default = workspace_doc_language(codoc_dir)
        for row in subtree:
            row.update(_lang_field(row.get("description") or row.get("title") or "",
                                   default))
        if not include_bindings:
            for row in subtree:
                row["binding_count"] = len(row.pop("bindings", []))
        return {
            "ok": True,
            "subtree": subtree,
            "titles_outline": titles_outline(all_titles),
            "graph": context,
            "doc_language": doc_language_block(codoc_dir),
        }


def doc_language_block(codoc_dir: str) -> dict:
    """The tree's authoring language, as an agent-readable block.

    Rides on every read an agent makes before it writes, because the agent is the
    one writer codoc cannot put a prompt in front of: Loop A's own LLM call gets
    the directive injected into its template, but a coding agent calling
    ``codoc_reflect`` has only what these tools told it. Without this, the
    considerate thing for the model to do — write documentation in English,
    because that is what documentation is usually in — silently mixes languages
    into somebody's Chinese tree.

    ``instruction`` is present only for a non-English tree, so the common case
    costs one short field rather than a paragraph.
    """
    lang = workspace_doc_language(codoc_dir)
    block = {"code": lang.code, "name": lang.name}
    if not lang.is_default:
        block["instruction"] = (
            f"Write prose you ORIGINATE in {lang.name} — a new node's title "
            f"(a title is {lang.title_rule}), description, and rationale. When you "
            f"AMEND an existing description, keep the language it is already "
            f"written in: a feature row carrying its own `lang` differs from the "
            f"tree's, and rewriting it into {lang.name} would translate the "
            f"author's words without being asked. Technical terms, library names, "
            f"and API names stay in the form readers use — {lang.name} prose with "
            f"English terms in it is correct, not something to clean up. Never "
            f"translate identifiers, symbol paths, file paths, or codoc: link "
            f"targets. Source code you write keeps the language its neighbours use."
        )
    return block


# Enough prose to support a claim about its language. Below this a "description"
# is a fragment or a bare identifier, and any verdict on it is noise.
_MIN_PROSE_LETTERS = 12


def _lang_field(text: str, default) -> dict:
    """``{"lang": tag}`` when ``text`` reads as a different language than the tree's,
    else ``{}`` — so the field's presence is itself the signal and an all-one-language
    tree pays nothing for the feature."""
    tag = language_tag_for(text, default)
    return {} if tag == default.code else {"lang": tag}


def _language_advice(store: Store, op: NodeOp, default) -> str | None:
    """A warning when submitted prose is in the wrong language for its target.

    "Wrong" is decided per NODE, not per repo, because the tree is allowed to be
    bilingual: an author describing intent in Chinese may still have written one
    node in English, and an amend that translates it back is an unrequested rewrite
    of their words. So an AMEND is judged against the description it is replacing,
    and only an ADD — prose with nothing behind it — against the workspace default.

    Chinese prose carrying English technical terms is *correct* and never flagged;
    that falls out of :func:`codoc.doclang.detect_prose_language` weighting an
    unspaced script far more heavily than its character share.

    Deliberately advisory. Rejecting would throw away work the agent has already
    done and leave the tree describing code that has already changed — worse than
    one node in the wrong language, which the next amend can fix.
    """
    submitted = op.description or op.title or ""
    if prose_letters(submitted) < _MIN_PROSE_LETTERS:
        return None

    expected = default
    if op.kind is NodeOpKind.AMEND and op.feature_id:
        existing = store.get_feature(op.feature_id)
        prior = (existing.description if existing else "") or ""
        if prose_letters(prior) >= _MIN_PROSE_LETTERS:
            expected = detect_prose_language(prior, default)

    got = detect_prose_language(submitted, expected)
    if got.code == expected.code:
        return None
    if op.kind is NodeOpKind.AMEND:
        return (f"the description you replaced was written in {expected.name}, but "
                f"the new one reads as {got.name} — an amend should keep the "
                f"author's language unless they asked for the switch (identifiers "
                f"and paths stay as they are either way)")
    return (f"this tree is authored in {expected.name} but this prose reads as "
            f"{got.name} — amend it to {expected.name}. Technical terms may stay in "
            f"their original form; identifiers and paths always do")


def _dead_refs(codoc_dir: str) -> list[dict]:
    """Unresolved inline ``codoc:`` refs from the cross-reference registry.

    The registry (``.codoc/tree.index.json``, written by ``render.write_registry``)
    tags every authored ref ``resolved`` per the leaf-matching rule. We read it
    tolerantly (missing / corrupt → no dead refs) and return one entry per ref
    whose ``resolved`` is False, so an agent can fix dead links instead of only the
    IDE seeing the decoration."""
    from codoc.codoc_file.render import INDEX_FILENAME
    from codoc.loop.fsio import read_json

    data = read_json(Path(codoc_dir) / INDEX_FILENAME, default={})
    refs = data.get("refs", []) if isinstance(data, dict) else []
    return [
        {"feature_id": r.get("feature_id"), "file": r.get("file"),
         "symbol": r.get("symbol")}
        for r in refs
        if isinstance(r, dict) and not r.get("resolved", True)
    ]


def read_status(codoc_dir: str) -> dict:
    """Feature / proposal counts + the current pipeline state, plus a dead-ref
    summary (count + list) sourced from the cross-reference registry so an agent
    can see which inline ``codoc:`` links no longer resolve to a binding."""
    from codoc.loop.status import refresh_status
    import json

    # Fold the IDE's append log into edits.json first (locked, idempotent, cheap).
    # This status call is the first thing /codoc:sync runs, and when the daemon is
    # not running it is the only consumer left: without the merge, an edit the
    # author just typed exists only in edits.host.jsonl, the recomputed state says
    # in_sync, and the sync dispatch concludes there is nothing to do — which is
    # how a pilot edited a description, saw "in sync", and got nothing.
    try:
        from codoc.loop.edits import merge_host_ops
        merge_host_ops(codoc_dir)
    except Exception:  # noqa: BLE001 — status must stay readable regardless
        pass

    with open_store(codoc_dir) as store:
        feats = store.list_features()
        pending = store.pending_events()
        unrealized = [f.id for f in feats if not f.realized]
        try:
            st = refresh_status(codoc_dir, store)
            state = json.loads(st.read_text(encoding="utf-8")).get("state", "in_sync")
        except Exception:
            state = "in_sync"
        dead = _dead_refs(codoc_dir)
        # Recent author prompts (UserPromptSubmit capture) — lets a fresh
        # session resume where the author left off without re-asking.
        try:
            from codoc.loop.intent import recent_intent
            intent = recent_intent(codoc_dir)
        except Exception:  # noqa: BLE001 — advisory
            intent = []
        # Directives minted from the author's own edits and not yet handed off.
        # They are invisible to `state` by design (a held draft is the author's to
        # release), but a sync dispatch that cannot see them tells an author who
        # just typed an edit that there is nothing to do — the exact wrong answer.
        try:
            from codoc.loop.edits import read_manifest
            held = [{"feature_id": d.feature_id, "id": d.id}
                    for d in read_manifest(codoc_dir) if not d.handed_off]
        except Exception:  # noqa: BLE001 — advisory
            held = []
        return {
            "ok": True, "features": len(feats), "pending": len(pending),
            "unrealized": len(unrealized), "state": state,
            "held_drafts": len(held), "held_draft_list": held[:20],
            # Count is exact; the list is capped so a repo with hundreds of stale
            # refs doesn't flood the agent's context through a status call.
            "dead_refs": len(dead), "dead_ref_list": dead[:20],
            "recent_intent": intent,
            "doc_language": doc_language_block(codoc_dir),
        }


def feature_history(codoc_dir: str, feature_id: str, limit: int = 20) -> dict:
    """The blame timeline of one feature: every applied change newest-first,
    each with who (actor) / how (mode) / why (caused_by + rationale), plus the
    title/description snapshot an AMEND left behind — enough to reconstruct how
    the feature's story evolved without reading the whole ledger."""
    from datetime import datetime, timezone

    fid = feature_id.strip("⟨⟩")
    with open_store(codoc_dir) as store:
        f = store.get_feature(fid)
        if f is None:
            return {"ok": False, "error": f"no feature {feature_id!r}"}
        entries = []
        for e in store.events_for_feature(fid, limit=limit):
            entry: dict = {
                "event_id": e.id,
                "at": datetime.fromtimestamp(
                    e.at.wall_clock / 1000, tz=timezone.utc).isoformat(),
                "kind": e.op.kind.value,
                "source": e.source,
                "actor": e.actor,
                "mode": e.mode,
            }
            if e.caused_by:
                entry["caused_by"] = e.caused_by
            if e.op.rationale:
                entry["rationale"] = e.op.rationale
            if e.op.title is not None:
                entry["title"] = e.op.title
            if e.op.description is not None:
                entry["description"] = e.op.description
            # What the change DISPLACED. Both this tool's docstring and the CLI's have
            # promised these since the field shipped, and neither emitted them — so an
            # agent asking "what did this feature used to say?" got the current text back
            # and no way to tell it apart from an answer.
            if e.op.prev_title is not None:
                entry["prev_title"] = e.op.prev_title
            if e.op.prev_description is not None:
                entry["prev_description"] = e.op.prev_description
            if e.op.prev_parent_id is not None:
                entry["prev_parent_id"] = e.op.prev_parent_id
            if e.op.bindings:
                entry["bindings"] = [s for (_, s) in e.op.bindings]
            entries.append(entry)
        return {"ok": True, "feature_id": fid, "title": f.title,
                "events": entries}


# ─── single-op proposals / binds ───────────────────────────────────────────────

def _auto_apply_allowed(codoc_dir: str, op: NodeOp, held: set[str], own_holds: set[str]) -> bool:
    """Whether an agent op may write straight through, or must become a proposal.

    Doc always wins (classify row 13): a feature with pending doc-ahead intent is
    being re-specified by its author, so a code-side amend must not rewrite the
    prose under their cursor. Loop A has honoured this from the start; the MCP path
    never did, which is how an agent's ``codoc_reflect`` could silently overwrite an
    author's in-progress description — the very "the model rewrote what I was
    writing" failure the hold exists to prevent.

    Suppressed here means PROPOSED, not dropped. Loop A can drop its own
    observation and recompute it next pass; an agent's reflection is a one-shot
    report of work already done, so discarding it would lose the information for
    good. As a proposal it reaches the author's review surface intact.

    ``own_holds`` is the exception that keeps the loop closing: when an agent
    finishes realizing a directive it reflects the result while that very directive
    still holds the feature. It is completing the hold, not fighting it.
    """
    if op.feature_id in own_holds:
        return True
    return not suppressed_by_hold(op, held)


def _hold_context(codoc_dir: str, caused_by: str) -> tuple[set[str], set[str]]:
    """(features currently held, features this call is itself the completion of)."""
    held = hold_set(codoc_dir)
    own: set[str] = set()
    if caused_by:
        own = {d.feature_id for d in read_manifest(codoc_dir)
               if d.id == caused_by and d.feature_id}
    return held, own


def _close_satisfied_queue(codoc_dir: str, store: Store) -> bool:
    """After a mutation that may have satisfied a queued directive (a bind flipping a
    plan placeholder realized, a reflect citing a ⟨d-…⟩ id), prune the realize queue
    and re-derive status so the IDE stops asking for work that is demonstrably done.

    Loop B does this once per pass, but a /codoc:plan session may never cause a Loop B
    pass at all — the daemon suppresses doc/inbox batches while an agent epoch is open
    and runs only Loop A at epoch close — so the MCP write path closes the queue on
    its own evidence. Runs BEFORE the caller's re-render, so the sidecar holds the
    render emits already reflect the closed entries (the "sent" badge clears in the
    same projection that shows the new binding). Best-effort: queue hygiene must never
    fail the op that triggered it."""
    from codoc.loop.loop_b import prune_satisfied_directives
    from codoc.loop.status import refresh_status
    try:
        root_dir = str(Path(codoc_dir).resolve().parent)
        if prune_satisfied_directives(store, root_dir, codoc_dir):
            refresh_status(codoc_dir, store)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _apply_single(codoc_dir: str, op: NodeOp, *, source: str,
                  caused_by: str = "", actor: str = "") -> dict:
    # Hold the shared codoc-loop lock across the agent's mutation + re-render so an MCP
    # op never interleaves with a concurrent Loop A/Loop B pass (loop/locks.py). Reentrant,
    # so safe_write_tree re-acquiring it inside is fine.
    with loop_lock(codoc_dir), open_store(codoc_dir) as store:
        # Validation: targets must exist for ops that reference them.
        if op.kind in (NodeOpKind.AMEND, NodeOpKind.RETIRE_NODE, NodeOpKind.MOVE_NODE,
                       NodeOpKind.ATTACH):
            if not op.feature_id or store.get_feature(op.feature_id) is None:
                return _err(f"unknown feature_id {op.feature_id!r}")
        if op.kind in (NodeOpKind.ADD_NODE, NodeOpKind.MOVE_NODE) and op.parent_id:
            if store.get_feature(op.parent_id) is None:
                return _err(f"unknown parent_id {op.parent_id!r}")

        held, own_holds = _hold_context(codoc_dir, caused_by)
        applied = should_auto_apply(op, store) and _auto_apply_allowed(
            codoc_dir, op, held, own_holds)
        ev = apply_op(op, store, source=source, applied=applied,
                      caused_by=caused_by, actor=actor)
        _close_satisfied_queue(codoc_dir, store)
        wrote = safe_write_tree(store, codoc_dir)
        _mark_reflected(codoc_dir, [op])
        out = {"ok": True, "event_id": ev.id, "applied": applied,
               "rendered": wrote, "summary": _op_summary(op, store)}
        warning = _language_advice(store, op, workspace_doc_language(codoc_dir))
        if warning:
            out["warning"] = warning
        return out


def propose_add(codoc_dir: str, *, title: str, description: str = "",
                parent_id: str | None = None, binds: list[str] | None = None,
                rationale: str = "", source: str = LOOP_A_AGENT_SOURCE,
                realized: bool | None = None, caused_by: str = "",
                actor: str = "", after_id: str = "", before_id: str = "") -> dict:
    """``after_id``/``before_id`` name the siblings the new node goes between — the same
    identity-anchored ordering a human drag emits. Both empty means no opinion, which
    appends (every caller's behaviour before ordering existed)."""
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title=title, description=description,
                parent_id=parent_id, bindings=_parse_binds(binds),
                rationale=rationale, realized=realized,
                after_id=after_id, before_id=before_id)
    return _apply_single(codoc_dir, op, source=source, caused_by=caused_by, actor=actor)


def propose_amend(codoc_dir: str, *, feature_id: str, title: str | None = None,
                  description: str | None = None, rationale: str = "",
                  source: str = LOOP_A_AGENT_SOURCE, caused_by: str = "",
                  actor: str = "") -> dict:
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id=feature_id, title=title,
                description=description, rationale=rationale)
    return _apply_single(codoc_dir, op, source=source, caused_by=caused_by, actor=actor)


def propose_move(codoc_dir: str, *, feature_id: str, parent_id: str | None,
                 rationale: str = "", source: str = LOOP_A_AGENT_SOURCE,
                 caused_by: str = "", actor: str = "",
                 after_id: str = "", before_id: str = "") -> dict:
    """``after_id``/``before_id`` name the siblings the node lands between, so a move can
    say WHERE and not only under whom. Without them every agent move appended, which made
    ordering a human-only capability even though the rank machinery is symmetric: an agent
    could not put a newly-split feature back beside the one it came from. Reordering
    within one parent is a move whose ``parent_id`` is unchanged and whose anchors move."""
    op = NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=feature_id,
                parent_id=parent_id, rationale=rationale,
                after_id=after_id, before_id=before_id)
    return _apply_single(codoc_dir, op, source=source, caused_by=caused_by, actor=actor)


def propose_retire(codoc_dir: str, *, feature_id: str, rationale: str = "",
                   delete_code: bool = False, source: str = LOOP_A_AGENT_SOURCE,
                   caused_by: str = "", actor: str = "") -> dict:
    """Propose retiring a feature. ``delete_code=False`` (default) is detach-only:
    accepting untracks the feature without removing code. ``delete_code=True`` is the
    agent-side parity for a human ``~`` retire — accepting queues a code-removal
    directive (use only when the code should genuinely be deleted)."""
    op = NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=feature_id, rationale=rationale,
                delete_code=delete_code)
    return _apply_single(codoc_dir, op, source=source, caused_by=caused_by, actor=actor)


def attach(codoc_dir: str, *, feature_id: str, binds: list[str],
           rationale: str = "", source: str = LOOP_A_AGENT_SOURCE,
           caused_by: str = "", actor: str = "") -> dict:
    op = NodeOp(kind=NodeOpKind.ATTACH, feature_id=feature_id,
                bindings=_parse_binds(binds), rationale=rationale)
    return _apply_single(codoc_dir, op, source=source, caused_by=caused_by, actor=actor)


# ─── bulk reflection ────────────────────────────────────────────────────────────

def reflect(codoc_dir: str, *, ops: list[dict], rationale: str = "",
            source: str = LOOP_A_AGENT_SOURCE, caused_by: str = "",
            actor: str = "") -> dict:
    """Submit the whole change set the agent just made, in one call.

    Each op is ``{kind, feature_id?, parent_id?, title?, description?, binds?,
    rationale?, realized?, after_id?, before_id?}``. Safe ops apply immediately;
    structural ops become proposals. Returns per-op results plus applied/proposed counts.

    ``after_id``/``before_id`` (add_node / move_node) name the siblings the node goes
    between. They used to be dropped here, which silently made every agent-side move an
    append — the one tree gesture a human could make and an agent could not.
    """
    results: list[dict] = []
    applied_ops: list[NodeOp] = []
    applied_n = proposed_n = mismatches = 0
    lang = workspace_doc_language(codoc_dir)
    # Serialize the whole reflection (mutation + render) against the loops (loop/locks.py).
    with loop_lock(codoc_dir), open_store(codoc_dir) as store:
        held, own_holds = _hold_context(codoc_dir, caused_by)
        for raw in ops:
            try:
                kind = NodeOpKind(raw["kind"])
            except (KeyError, ValueError):
                results.append(_err(f"bad op kind {raw.get('kind')!r}"))
                continue
            op = NodeOp(
                kind=kind,
                feature_id=raw.get("feature_id"),
                parent_id=raw.get("parent_id"),
                title=raw.get("title"),
                description=raw.get("description"),
                bindings=_parse_binds(raw.get("binds")),
                rationale=raw.get("rationale") or rationale,
                realized=raw.get("realized"),
                # Sibling order, by neighbour IDENTITY (never an index — by the time this
                # applies, Loop A may have added or retired a sibling and "third child"
                # would mean something else).
                after_id=str(raw.get("after_id") or ""),
                before_id=str(raw.get("before_id") or ""),
            )
            # Validate references.
            if kind in (NodeOpKind.AMEND, NodeOpKind.RETIRE_NODE, NodeOpKind.MOVE_NODE,
                        NodeOpKind.ATTACH) and (
                    not op.feature_id or store.get_feature(op.feature_id) is None):
                results.append(_err(f"unknown feature_id {op.feature_id!r}"))
                continue
            if kind in (NodeOpKind.ADD_NODE, NodeOpKind.MOVE_NODE) and op.parent_id \
                    and store.get_feature(op.parent_id) is None:
                results.append(_err(f"unknown parent_id {op.parent_id!r}"))
                continue

            op_cause = raw.get("caused_by") or caused_by
            op_own = own_holds
            if op_cause != caused_by:
                _, op_own = _hold_context(codoc_dir, op_cause)
            applied = should_auto_apply(op, store) and _auto_apply_allowed(
                codoc_dir, op, held, op_own)
            ev = apply_op(op, store, source=source, applied=applied,
                          caused_by=op_cause, actor=actor)
            applied_ops.append(op)
            applied_n += int(applied)
            proposed_n += int(not applied)
            row = {"ok": True, "event_id": ev.id, "applied": applied,
                   "summary": _op_summary(op, store)}
            warning = _language_advice(store, op, lang)
            if warning:
                row["warning"] = warning
                mismatches += 1
            results.append(row)
        _close_satisfied_queue(codoc_dir, store)
        wrote = safe_write_tree(store, codoc_dir)
        _mark_reflected(codoc_dir, applied_ops)
        out = {"ok": True, "applied": applied_n, "proposed": proposed_n,
               "rendered": wrote, "results": results}
        if mismatches:
            out["doc_language"] = doc_language_block(codoc_dir)
        return out


# ─── realize progress ────────────────────────────────────────────────────────

def realize_progress(codoc_dir: str, *, done: int, total: int, current: str = "") -> dict:
    """Stamp ``done/total`` realize progress into ``status.json`` so the IDE shows
    "implementing M of N" while the live session works through ``.codoc/realize.md``.
    """
    from codoc.loop.sdk_realize import format_realize_detail
    from codoc.loop.status import REALIZING, write_status
    # One shared shape ("implementing N/M: title") for BOTH progress producers (this
    # MCP tool + sdk_realize), so the IDE's anchored parser has a single head to
    # match and a stray "d/d" in some other detail can't be misread as progress.
    detail = format_realize_detail(done, total, current)
    try:
        write_status(codoc_dir, REALIZING, pending=max(0, total - done), detail=detail)
    except Exception:  # noqa: BLE001 — progress is advisory
        return {"ok": False}
    return {"ok": True, "done": done, "total": total}


# ─── plan loop ──────────────────────────────────────────────────────────────────

def plan_add(codoc_dir: str, *, title: str, description: str = "",
             parent_id: str | None = None, binds: list[str] | None = None,
             rationale: str = "") -> dict:
    """Propose a PLAN placeholder node (source='plan', realized=False).

    Accepted in the IDE, it enters the tree as an unrealized placeholder; the
    first code bound to it (via :func:`attach` / :func:`reflect`) flips it real.
    """
    return propose_add(codoc_dir, title=title, description=description,
                       parent_id=parent_id, binds=binds, rationale=rationale,
                       source=PLAN_SOURCE, realized=False)


def await_verdicts(codoc_dir: str, *, event_ids: list[str],
                   timeout: float = 86400.0, poll_interval: float = 1.0) -> dict:
    """Block until the user Accepts/Rejects the given proposals in the IDE.

    This is the in-session realization trigger (modeled on plannotator's blocking
    review hook): instead of ending the turn at "stop here", ``/codoc:plan`` calls
    this after proposing nodes, waits for every ``event_ids`` proposal to resolve
    (or the timeout), and the same turn continues to implement what was accepted.

    Loop B is the SOLE applier of verdicts. With a live ``codoc watch`` daemon
    this tool only OBSERVES — racing the daemon (the old behaviour) made accept
    semantics depend on who won: an await-applied accept minted no realize
    directive, a daemon-applied one did. With no daemon, an in-process Loop B
    pass drains the inbox here, so the semantics are identical either way
    (directives minted, tree rendered, status stamped).

    Outcomes are recovered from the event LEDGER, not from the inbox: accepting a
    proposal applies a new event stamped ``caused_by=<proposal id>`` and deletes
    the proposal row, so a proposal that is gone resolves to **accepted** (an
    applied event cites it — its op carries the feature id an ADD minted) or
    **rejected** (nothing cites it; rejection, withdrawal, and supersede all mean
    "will never be applied", which is what the caller must know so it doesn't
    implement it). This is the lookup the old ``ev is None`` branch promised in a
    comment and never performed — verdicts that landed before the first poll came
    back as empty lists with no error.

    Returns ``{accepted:[{event_id, feature_id, title}], rejected:[event_id],
    pending:[event_id], timed_out}``.
    """
    import time as _time

    from codoc.loop.watch import daemon_running

    targets = list(dict.fromkeys(event_ids))  # de-dupe, preserve order
    accepted: list[dict] = []
    rejected: list[str] = []
    resolved: set[str] = set()
    deadline = _time.monotonic() + max(0.0, timeout)
    root_dir = str(Path(codoc_dir).resolve().parent)

    def _drain_if_daemonless() -> None:
        wanted = {v.event_id for v in inbox_read(codoc_dir)} & set(targets)
        if not wanted or daemon_running(codoc_dir):
            return  # nothing for us yet, or the daemon owns the drain
        from codoc.loop.loop_b import run_loop_b
        try:
            run_loop_b(root_dir, codoc_dir)
        except Exception:  # noqa: BLE001 — the next poll retries; observation still runs
            pass

    def _observe() -> None:
        with loop_lock(codoc_dir), open_store(codoc_dir) as store:
            for eid in targets:
                if eid in resolved or store.get_event(eid) is not None:
                    continue  # already answered / still awaiting its verdict
                done = store.applied_event_for_cause(eid)
                if done is not None:
                    fid = done.op.feature_id
                    feat = store.get_feature(fid) if fid else None
                    accepted.append({"event_id": eid, "feature_id": fid,
                                     "title": (feat.title if feat else done.op.title) or ""})
                else:
                    rejected.append(eid)
                resolved.add(eid)

    while True:
        _drain_if_daemonless()
        _observe()
        if resolved >= set(targets) or _time.monotonic() >= deadline:
            break
        _time.sleep(poll_interval)

    # Deliberately NO phase stamp here. Accepting is not implementing: bulk-marking
    # every accepted placeholder "editing" grayed all of their prose the instant the
    # user clicked — for up to the phase TTL (120 s) on nodes the session had not
    # reached yet, since it implements one at a time. The truthful signals are the
    # ones tied to actual work: the tool hook stamps "editing" per feature when a
    # bound file is really written, and codoc_realize_progress narrates the pass.

    pending = [e for e in targets if e not in resolved]
    return {"ok": True, "accepted": accepted, "rejected": rejected,
            "pending": pending, "timed_out": bool(pending)}


def plan_status(codoc_dir: str) -> dict:
    """Which plan placeholders are still unrealized vs realized — AND what the
    realize queue still holds.

    These are two different ledgers (the store's lifecycle bit vs the queued
    directives in ``.codoc/realize.json``), and reporting only the first is how a
    session could honestly say "all realized" while the IDE status bar kept counting
    directives "to implement". The queue is pruned against current evidence first,
    so what remains in ``queued_directives`` is genuinely outstanding work — e.g. a
    tree edit the user made while the plan was being implemented."""
    with loop_lock(codoc_dir), open_store(codoc_dir) as store:
        _close_satisfied_queue(codoc_dir, store)
        unrealized = [{"id": f.id, "title": f.title}
                      for f in store.list_features() if not f.realized]
        queued = [{"id": d.id, "feature_id": d.feature_id, "kind": d.kind}
                  for d in read_manifest(codoc_dir) if d.handed_off]
        out = {"ok": True, "unrealized": unrealized,
               "all_realized": len(unrealized) == 0,
               "queued_directives": queued}
        if queued:
            out["note"] = (
                "Directives remain queued in .codoc/realize.md (edits made outside "
                "this plan — e.g. the user changed a description while you worked). "
                "Read that file and implement them, reflecting each with "
                "caused_by=<its ⟨d-…⟩ id> — or tell the user to run /codoc:sync.")
        return out


# ─── ask / walkthrough ─────────────────────────────────────────────────────────

def _norm_ws(text: str) -> str:
    """Whitespace-collapsed form, for matching a quote against prose that may have
    been re-wrapped between the read and the write."""
    return " ".join((text or "").split())


def _quotable_blocks(title: str, description: str) -> list[str]:
    """The spans a highlight can actually cover: the title, and each PARAGRAPH of
    the description.

    Paragraph-wise rather than whole-description on purpose — the editor draws a
    highlight as one decoration inside one block, so a quote straddling a
    paragraph break is one the IDE could not render. Accepting it here would put
    the failure on screen instead of in the tool result.
    """
    blocks = [title or ""]
    para: list[str] = []
    for line in (description or "").split("\n"):
        if line.strip():
            para.append(line)
        elif para:
            blocks.append("\n".join(para))
            para = []
    if para:
        blocks.append("\n".join(para))
    return blocks


def walkthrough(codoc_dir: str, *, question: str, answer: str = "",
                steps: list[dict] | None = None) -> dict:
    """Lay a numbered reading path over features that already exist.

    Validates every step against the store BEFORE writing, because a step naming a
    feature that is gone would render as a numbered chip on nothing, and a quote
    that is not in the description would highlight nothing — both of which read to
    the user as the tool being broken rather than the answer being wrong.
    """
    rows = list(steps or [])
    if not rows:
        return _err("pass steps=[{feature_id, note, quote?, group?, file?, symbol?, line?}, ...]")

    with open_store(codoc_dir) as store:
        feats = {f.id: f for f in store.list_features()}
        by_title = {f.title.strip().lower(): f for f in feats.values() if f.title}

        built: list[AskStep] = []
        dropped: list[dict] = []
        unresolved: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                dropped.append({"step": str(row), "why": "not an object"})
                continue
            fid = str(row.get("feature_id") or "").strip()
            feat = feats.get(fid)
            if feat is None and fid:
                # A title is what a human would paste, and the agent has just read
                # the tree — accept it rather than failing the whole walkthrough.
                feat = by_title.get(fid.lower())
            if feat is None:
                dropped.append({"feature_id": fid, "why": "no such live feature"})
                continue
            if feat.id in seen:
                # One chip per feature: a node carrying "1b" and "3a" at once has
                # no legible rendering, and the reader cannot tell which note
                # belongs to which number. The first visit is the one that counts.
                dropped.append({"feature_id": feat.id, "why": "already a stop on this path"})
                continue
            seen.add(feat.id)
            quote = str(row.get("quote") or "").strip()
            if quote:
                needle = _norm_ws(quote)
                blocks = _quotable_blocks(feat.title, feat.description)
                if not any(needle in _norm_ws(b) for b in blocks):
                    unresolved.append({"feature_id": feat.id, "quote": quote})
                    quote = ""  # keep the step; drop the highlight that would miss
            line = row.get("line")
            built.append(AskStep(
                feature_id=feat.id,
                note=str(row.get("note") or ""),
                quote=quote,
                group=str(row.get("group") or ""),
                file=str(row.get("file") or ""),
                symbol=str(row.get("symbol") or ""),
                line=int(line) if isinstance(line, (int, float)) else None,
            ))

        if not built:
            return _err("no step named a live feature — read the tree with "
                        "codoc_context or codoc_tree first, and pass real f-ids")

        walk = build_walkthrough(question, answer, built)
        write_walkthrough(codoc_dir, walk)

    out: dict = {"ok": True, "id": walk.id, "steps": len(walk.steps),
                 "labels": [s.label for s in walk.steps]}
    if len(built) > len(walk.steps):
        out["truncated"] = len(built) - len(walk.steps)
        out["note"] = (f"kept the first {MAX_STEPS} steps — a walkthrough longer "
                       f"than that stops being followed in order")
    if dropped:
        out["dropped"] = dropped
    if unresolved:
        out["unresolved_quotes"] = unresolved
        out["quote_note"] = ("these quotes are not present in their feature's prose, "
                             "so those steps show without a highlight — quote the "
                             "description verbatim to highlight a span")
    return out


def clear_walkthrough_tool(codoc_dir: str) -> dict:
    """Dismiss the overlay. Idempotent — clearing nothing is not an error."""
    return {"ok": True, "cleared": ask_clear(codoc_dir)}


def read_walkthrough_tool(codoc_dir: str) -> dict:
    """The overlay currently on screen, or ``{"ok": True, "walkthrough": None}``."""
    return {"ok": True, "walkthrough": ask_read(codoc_dir)}
