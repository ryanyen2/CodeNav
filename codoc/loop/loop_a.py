"""Loop A — code → codoc.

Deterministic change set → auto-apply the safe parts → if anything needs
judgment, ONE LLM pass returns the minimal node ops → safe ops auto-apply,
structural ops are logged as pending proposals for review in the .codoc file.

``apply_changeset`` holds the logic and takes an injectable ``propose`` callable,
so it is unit-testable with a fake store and a mocked LLM. ``run_loop_a`` wires
it to the real index + store.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from codoc.agent.tree_update import propose_tree_update
from codoc.loop.apply import apply_op, derive_auto_ops, should_auto_apply
from codoc.loop.diff import ChangeSet, ChunkRef, compute_changeset
from codoc.loop.subtree import select_relevant_subtree
from codoc.model.event import NodeOp, NodeOpKind, SAFE_OPS
from codoc.store.db import Store, open_store

_SNIPPET = 600


def _detect_relocations(
    cs: ChangeSet,
    removed_owner: dict[tuple[str, str], str],
) -> list[tuple[ChunkRef, ChunkRef, str]]:
    """Pair removed↔added chunks that are the same code relocated.

    A *move* is an exact content match (``tokens_hash``); a *rename* is an
    AST-shape match (``types_hash``) in the same file, required to be 1:1 so an
    incidental shape collision never mis-pairs unrelated symbols. Only removed
    chunks that were actually bound (in ``removed_owner``) are candidates, since
    the point is to carry an existing feature attribution across.
    Returns ``(added, removed, kind)`` triples.
    """
    added = [a for a in cs.added]
    removed = [r for r in cs.removed if (r.file, r.symbol_path) in removed_owner]
    used_removed: set[tuple[str, str]] = set()
    out: list[tuple[ChunkRef, ChunkRef, str]] = []

    # Pass 1 — move: identical content (tokens_hash).
    by_tok: dict[str, list[ChunkRef]] = {}
    for r in removed:
        by_tok.setdefault(r.fingerprint, []).append(r)
    matched_added: set[tuple[str, str]] = set()
    for a in added:
        cand = next(
            (c for c in by_tok.get(a.fingerprint, [])
             if (c.file, c.symbol_path) not in used_removed),
            None,
        )
        if cand:
            used_removed.add((cand.file, cand.symbol_path))
            matched_added.add((a.file, a.symbol_path))
            out.append((a, cand, "move"))

    # Pass 2 — rename: same file, unique AST-shape (types_hash) on both sides.
    add_typ_count = Counter(
        a.types_hash for a in added if (a.file, a.symbol_path) not in matched_added
    )
    for a in added:
        if (a.file, a.symbol_path) in matched_added or not a.types_hash:
            continue
        cands = [
            c for c in removed
            if c.types_hash == a.types_hash
            and c.file == a.file
            and (c.file, c.symbol_path) not in used_removed
        ]
        if len(cands) == 1 and add_typ_count[a.types_hash] == 1:
            cand = cands[0]
            used_removed.add((cand.file, cand.symbol_path))
            out.append((a, cand, "rename"))

    return out


def _pending_coverage(store: Store) -> tuple[set[tuple[str, str]], set[str]]:
    """What the *pending proposals* already cover, so Loop A doesn't duplicate them.

    Returns ``(claimed_chunks, claimed_features)``:
    - ``claimed_chunks`` — every ``(file, symbol_path)`` named in a pending op's
      bindings (e.g. an agent-submitted ADD_NODE or a prior proposal). Re-proposing
      a home for these would create a duplicate node.
    - ``claimed_features`` — feature ids with a pending RETIRE/AMEND/MOVE. The agent
      (or an earlier pass) already raised that structural change.

    This is the dedup that lets agent-driven MCP reflection and the automatic Loop A
    verification net coexist without double proposals.
    """
    claimed_chunks: set[tuple[str, str]] = set()
    claimed_features: set[str] = set()
    for e in store.pending_events():
        op = e.op
        for b in op.bindings:
            claimed_chunks.add((b[0], b[1]))
        if op.kind in (NodeOpKind.RETIRE_NODE, NodeOpKind.AMEND, NodeOpKind.MOVE_NODE) \
                and op.feature_id:
            claimed_features.add(op.feature_id)
    return claimed_chunks, claimed_features


def _norm_title(t: str | None) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def _unbound_features_by_title(store: Store) -> dict[str, str]:
    """``normalized title → feature_id`` for every live feature that owns NO code.

    These are adoptable: an ADD_NODE the LLM/coverage net would mint with the
    same title is the SAME concept (e.g. a hand-added empty node the model
    re-proposed), so we bind into the existing node instead of minting a
    duplicate-titled sibling.
    """
    out: dict[str, str] = {}
    for f in store.list_features():
        if f.retired or store.bindings_for_feature(f.id):
            continue
        out.setdefault(_norm_title(f.title), f.id)
    return out


def _placeholder_owner(store: Store, symbol_path: str, *, sole_ok: bool) -> str | None:
    """An unrealized, still-unbound plan placeholder that should ADOPT this new
    symbol rather than have a duplicate node minted for it.

    Prefers a placeholder whose title/description names the symbol; falls back to
    the *sole* placeholder when ``sole_ok`` (a Loop B post-implementation reflect,
    where the agent wrote this code expressly for the one accepted plan node).
    Adopting flips the placeholder ``realized`` via the ATTACH in ``_mutate``.
    """
    placeholders = [
        f for f in store.list_features()
        if not f.realized and not f.retired and not store.bindings_for_feature(f.id)
    ]
    if not placeholders:
        return None
    leaf = symbol_path.split("::", 1)[-1].split(".")[-1].lower()
    leaf_compact = leaf.replace("_", "")
    for f in placeholders:
        hay = f"{f.title} {f.description or ''}".lower()
        hay_compact = re.sub(r"[\s_]+", "", hay)
        if leaf and (leaf in hay or (leaf_compact and leaf_compact in hay_compact)):
            return f.id
    if sole_ok and len(placeholders) == 1:
        return placeholders[0].id
    return None


def _gc_superseded_proposals(store: Store) -> int:
    """Drop pending ADD_NODE proposals whose every chunk is now bound elsewhere.

    Without this a duplicate/obsolete proposal lingers forever, pinning status at
    ``code_drift`` so a no-op ``codoc sync`` never converges to ``in_sync``. A
    proposal is superseded once a live feature already owns all the code it would
    have introduced.
    """
    dropped = 0
    for e in store.pending_events():
        op = e.op
        if op.kind is NodeOpKind.ADD_NODE and op.bindings and all(
            store.binding_at(f, s) is not None for f, s in op.bindings
        ):
            store.delete_event(e.id)
            dropped += 1
    return dropped


def _compute_impacted(cs: ChangeSet, store: Store) -> dict[str, list[str]]:
    """Phase 4: upstream dependents of changed/removed symbols.

    Returns {feature_id → [symbol_paths]} for features whose code directly
    calls or imports the changed symbols. Advisory only; never auto-applied.
    """
    changed_syms = {c.symbol_path for c in (cs.modified + cs.removed)}
    if not changed_syms:
        return {}

    dependents: set[str] = set()
    for sym in changed_syms:
        for e in store.edges_in(sym, internal_only=True):
            if e["kind"] in {"call", "import", "inherit"}:
                dependents.add(e["src_symbol"])
    dependents -= changed_syms

    dep_features: dict[str, list[str]] = {}
    for sym in dependents:
        if "::" not in sym:
            continue
        file, _ = sym.split("::", 1)
        b = store.binding_at(file, sym)
        if b:
            dep_features.setdefault(b.feature_id, []).append(sym)
    return dep_features


@dataclass
class LoopAResult:
    auto: dict[str, int] = field(default_factory=dict)        # safe-op kind → count
    applied_structural: list[NodeOp] = field(default_factory=list)
    proposed: list[NodeOp] = field(default_factory=list)      # pending review hunks
    llm_called: bool = False
    impacted: list[str] = field(default_factory=list)         # feature IDs of upstream dependents

    def summary(self) -> str:
        auto = ", ".join(f"{n} {k}" for k, n in sorted(self.auto.items())) or "none"
        parts = [f"auto: {auto}"]
        if self.proposed:
            kinds = Counter(op.kind.value for op in self.proposed)
            parts.append("proposed: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
        if self.applied_structural:
            parts.append(f"applied {len(self.applied_structural)} structural")
        if self.impacted:
            parts.append(f"{len(self.impacted)} impacted features")
        return " · ".join(parts)


def apply_changeset(
    cs: ChangeSet,
    store: Store,
    *,
    source: str = "loop_a",
    propose=propose_tree_update,
    repo_name: str = "codebase",
    config=None,
    adopt_placeholders: bool = False,
) -> LoopAResult:
    # GC stale proposals first so a no-op pass can converge to in_sync even when
    # there is no change set to process.
    gc = _gc_superseded_proposals(store)
    if cs.is_empty():
        return LoopAResult(auto={"gc": gc} if gc else {})

    fp = cs.fingerprints()
    th = cs.types_hashes()

    # Which feature did each removed chunk belong to (captured before detach)?
    removed_owner: dict[tuple[str, str], str] = {}
    for r in cs.removed:
        b = store.binding_at(r.file, r.symbol_path)
        if b:
            removed_owner[(r.file, r.symbol_path)] = b.feature_id

    # 1. Auto-apply the trivially-resolvable safe ops (no LLM): the removed-bound
    #    chunks DETACH here, freeing their (file, symbol) so a relocation can rebind.
    auto_ops = derive_auto_ops(cs, store)
    for op in auto_ops:
        apply_op(op, store, source=source, applied=True, fp_lookup=fp, th_lookup=th)
    result = LoopAResult(auto=dict(Counter(op.kind.value for op in auto_ops)))
    if gc:
        result.auto["gc"] = gc

    # 1b. Correspondence: a remove+add of the same code is a move/rename, not new
    #     work. Carry the existing feature attribution to the new location with a
    #     deterministic ATTACH — no LLM, no risk of the model dropping the chunk.
    relocations = _detect_relocations(cs, removed_owner)
    relocated_added: set[tuple[str, str]] = set()
    for added_ref, removed_ref, _kind in relocations:
        owner = removed_owner[(removed_ref.file, removed_ref.symbol_path)]
        reloc = NodeOp(
            kind=NodeOpKind.ATTACH,
            feature_id=owner,
            bindings=[(added_ref.file, added_ref.symbol_path)],
            rationale=f"{_kind}: {removed_ref.symbol_path} → {added_ref.symbol_path}",
        )
        apply_op(reloc, store, source=source, applied=True, fp_lookup=fp, th_lookup=th)
        relocated_added.add((added_ref.file, added_ref.symbol_path))
    if relocations:
        result.auto["relocate"] = result.auto.get("relocate", 0) + len(relocations)

    # 2. Features that just lost their last binding (after relocations rebind).
    emptied = {
        fid for fid in set(removed_owner.values())
        if not store.bindings_for_feature(fid)
        and (f := store.get_feature(fid)) and not f.retired
    }
    added_unbound = [
        a for a in cs.added
        if store.binding_at(a.file, a.symbol_path) is None
        and (a.file, a.symbol_path) not in relocated_added
    ]

    # Verification-net dedup: drop anything a pending proposal already covers
    # (e.g. the agent reflected via MCP just before this pass). This makes Loop A
    # a safety net that only surfaces the GAPS, never a second proposal for the
    # same change — and lets it skip the LLM entirely when the agent covered all.
    claimed_chunks, claimed_features = _pending_coverage(store)
    added_unbound = [a for a in added_unbound if (a.file, a.symbol_path) not in claimed_chunks]
    emptied = {fid for fid in emptied if fid not in claimed_features}

    # 2b. Placeholder adoption (deterministic, no LLM): a new unbound chunk that
    #     an unrealized plan placeholder was created to host binds to THAT
    #     placeholder — not a fresh duplicate node. This is what stops the
    #     "accepted plan node ends with 0 bindings while Loop A mints function_v2"
    #     desync. ``adopt_placeholders`` (set by Loop B's post-implement reflect)
    #     lets the SOLE placeholder adopt code even without a name match.
    still_unbound = []
    for a in added_unbound:
        owner = _placeholder_owner(store, a.symbol_path, sole_ok=adopt_placeholders)
        if owner:
            apply_op(
                NodeOp(kind=NodeOpKind.ATTACH, feature_id=owner,
                       bindings=[(a.file, a.symbol_path)],
                       rationale="adopt: bound to the plan placeholder it implements"),
                store, source=source, applied=True, fp_lookup=fp, th_lookup=th,
            )
            result.auto["adopt"] = result.auto.get("adopt", 0) + 1
        else:
            still_unbound.append(a)
    added_unbound = still_unbound

    # Phase 4: compute upstream dependents before early return (observability).
    dep_features = _compute_impacted(cs, store)
    result.impacted = list(dep_features.keys())

    if not (added_unbound or emptied):
        return result

    # 3. The single LLM pass.
    result.llm_called = True
    changes: dict = {
        "added": [
            {"file": a.file, "symbol_path": a.symbol_path, "source": a.source[:_SNIPPET]}
            for a in added_unbound
        ],
        "removed": [
            {"file": r.file, "symbol_path": r.symbol_path,
             "current_feature_id": removed_owner[(r.file, r.symbol_path)]}
            for r in cs.removed
            if removed_owner.get((r.file, r.symbol_path)) in emptied
        ],
        "modified": [
            {"file": m.file, "symbol_path": m.symbol_path, "source": m.source[:_SNIPPET],
             "current_feature_id": (b.feature_id if (b := store.binding_at(m.file, m.symbol_path)) else None)}
            for m in cs.modified
        ],
    }
    subtree, all_titles, graph_ctx = select_relevant_subtree(cs, store)
    if graph_ctx.get("edges") or graph_ctx.get("recent"):
        changes["graph"] = graph_ctx
    if dep_features:
        changes["impacted"] = [
            {
                "feature_id": fid,
                "feature_title": (f.title if (f := store.get_feature(fid)) else fid),
                "dependent_symbols": syms[:5],
            }
            for fid, syms in dep_features.items()
        ]

    ops = propose(changes, subtree, all_titles, repo_name=repo_name, config=config)

    # 4. Apply: safe → now; structural → pending proposal. An ADD_NODE whose
    #    (title) already names a live, still-unbound feature is the SAME concept
    #    (e.g. a hand-added empty node the model re-proposed) — rewrite it to an
    #    ATTACH onto that node so we never mint a duplicate-titled sibling.
    unbound_titles = _unbound_features_by_title(store)
    for op in ops:
        if op.kind is NodeOpKind.ADD_NODE and op.bindings:
            existing = unbound_titles.get(_norm_title(op.title))
            if existing:
                apply_op(
                    NodeOp(kind=NodeOpKind.ATTACH, feature_id=existing,
                           bindings=op.bindings,
                           rationale="dedup: bound to existing same-title node"),
                    store, source=source, applied=True, fp_lookup=fp, th_lookup=th,
                )
                result.auto["attach"] = result.auto.get("attach", 0) + 1
                continue
        applied = should_auto_apply(op, store)
        apply_op(op, store, source=source, applied=applied, fp_lookup=fp, th_lookup=th)
        if not applied:
            result.proposed.append(op)
        elif op.kind not in SAFE_OPS:
            result.applied_structural.append(op)
        else:
            # An applied safe op (e.g. an LLM AMEND/ATTACH small enough to
            # auto-apply) is a real tree mutation — surface it in the summary so
            # the user is never told "nothing changed" while a description was
            # silently rewritten.
            result.auto[op.kind.value] = result.auto.get(op.kind.value, 0) + 1

    # 5. Coverage net: never silently drop an added chunk the LLM failed to place.
    #    A chunk named in any op (applied ATTACH/ADD_NODE *or* a pending ADD_NODE
    #    proposal) is already placed; only genuinely unplaced chunks fall through.
    covered_by_ops = {b for op in ops for b in op.bindings}
    _cover_uncovered_adds(added_unbound, covered_by_ops, store, result, fp, th, source)
    return result


def _cover_uncovered_adds(
    added_unbound: list,
    covered_by_ops: set[tuple[str, str]],
    store: Store,
    result: LoopAResult,
    fp: dict[tuple[str, str], str],
    th: dict[tuple[str, str], str],
    source: str,
) -> None:
    from codoc.graph.query import neighbor_feature

    for a in added_unbound:
        if (a.file, a.symbol_path) in covered_by_ops:
            continue  # placed by an LLM op (applied or pending proposal)
        if store.binding_at(a.file, a.symbol_path) is not None:
            continue  # already bound
        owner = neighbor_feature(store, a.symbol_path)
        if owner:
            op = NodeOp(
                kind=NodeOpKind.ATTACH,
                feature_id=owner,
                bindings=[(a.file, a.symbol_path)],
                rationale="coverage: attached to graph-neighbor feature",
            )
            apply_op(op, store, source=source, applied=True, fp_lookup=fp, th_lookup=th)
            result.auto["attach"] = result.auto.get("attach", 0) + 1
        else:
            op = NodeOp(
                kind=NodeOpKind.ADD_NODE,
                title=a.symbol_path.split("::", 1)[-1],
                description="",
                bindings=[(a.file, a.symbol_path)],
                rationale="coverage: unplaced added chunk — needs a home",
            )
            apply_op(op, store, source=source, applied=False, fp_lookup=fp, th_lookup=th)
            result.proposed.append(op)


def run_loop_a(
    root_dir: str,
    codoc_dir: str,
    *,
    file_scope: set[str] | None = None,
    source: str = "loop_a",
    repo_name: str = "codebase",
    config=None,
    adopt_placeholders: bool = False,
) -> LoopAResult:
    from codoc.graph.query import update_graph

    cs = compute_changeset(root_dir, codoc_dir, file_scope=file_scope)
    store = open_store(codoc_dir)
    try:
        update_graph(store, cs.rows, cs.touched_files())
        result = apply_changeset(cs, store, source=source, repo_name=repo_name,
                                 config=config, adopt_placeholders=adopt_placeholders)
        from codoc.loop.status import refresh_status

        refresh_status(codoc_dir, store)
        return result
    finally:
        store.close()


def _state_changeset(rows, store: Store, file_scope: set[str] | None) -> ChangeSet:
    """Build a change set by comparing the index to the store's BINDINGS, not to a
    prior index snapshot. State-based ⇒ idempotent and self-healing.

    - a chunk with no binding → ``added`` (an attribution gap to close);
    - a bound chunk whose ``tokens_hash`` ≠ the binding's fingerprint → ``modified``;
    - a binding whose ``(file, symbol)`` is gone from the index → ``removed``.

    Unlike :func:`compute_changeset` (which diffs the index over time and so goes
    blind once the index advances without a reflection), this recovers a missed
    cycle: it always re-derives the full divergence between code and the tree."""
    scoped = rows if file_scope is None else [r for r in rows if r.file in file_scope]
    index_keys = {(r.file, r.symbol_path) for r in scoped}

    added, modified = [], []
    for r in scoped:
        b = store.binding_at(r.file, r.symbol_path)
        ref = ChunkRef(r.file, r.symbol_path, r.tokens_hash, r.source, r.types_hash)
        if b is None:
            added.append(ref)
        elif b.fingerprint and b.fingerprint != r.tokens_hash:
            modified.append(ref)

    bindings = (store.bindings_in_files(file_scope) if file_scope is not None
                else store.all_bindings())
    removed = [
        # carry the binding's stored types_hash so a rename (shape match, new
        # name) is still recognised after the old symbol left the index.
        ChunkRef(b.file, b.symbol_path, b.fingerprint, types_hash=b.types_hash)
        for b in bindings if (b.file, b.symbol_path) not in index_keys
    ]
    return ChangeSet(added=added, removed=removed, modified=modified, rows=rows)


def reconcile_drift(
    root_dir: str,
    codoc_dir: str,
    *,
    file_scope: set[str] | None = None,
    source: str = "loop_a",
    repo_name: str = "codebase",
    config=None,
    adopt_placeholders: bool = False,
) -> LoopAResult:
    """Reflect code → tree by reconciling the index against the store's bindings.

    The recovery-grade counterpart to :func:`run_loop_a`: where the latter relies
    on the temporal index diff (and so silently no-ops if a cycle was missed and
    the index already advanced), this re-derives the full code↔tree divergence
    from current state. Idempotent — safe to run on daemon startup, from the Stop
    hook, and from ``codoc sync`` without producing duplicate work."""
    from codoc.graph.query import update_graph
    from codoc.loop.status import refresh_status
    from codoc.pipelines.indexing.reader import read_all_chunks
    from codoc.pipelines.indexing.runner import update_index

    update_index(root_dir, codoc_dir)
    rows = read_all_chunks(codoc_dir)
    store = open_store(codoc_dir)
    try:
        cs = _state_changeset(rows, store, file_scope)
        update_graph(store, cs.rows, cs.touched_files() or {r.file for r in rows})
        result = apply_changeset(cs, store, source=source, repo_name=repo_name,
                                 config=config, adopt_placeholders=adopt_placeholders)
        refresh_status(codoc_dir, store)
        return result
    finally:
        store.close()
