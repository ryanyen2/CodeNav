"""Loop A — code → codoc.

Deterministic change set → auto-apply the safe parts → if anything needs
judgment, ONE LLM pass returns the minimal node ops → safe ops auto-apply,
structural ops are logged as pending proposals for review in the .codoc file.

``apply_changeset`` holds the logic and takes an injectable ``propose`` callable,
so it is unit-testable with a fake store and a mocked LLM. ``run_loop_a`` wires
it to the real index + store.
"""
from __future__ import annotations

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
) -> LoopAResult:
    if cs.is_empty():
        return LoopAResult()

    fp = cs.fingerprints()

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
        apply_op(op, store, source=source, applied=True, fp_lookup=fp)
    result = LoopAResult(auto=dict(Counter(op.kind.value for op in auto_ops)))

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
        apply_op(reloc, store, source=source, applied=True, fp_lookup=fp)
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

    # 4. Apply: safe → now; structural → pending proposal.
    for op in ops:
        applied = should_auto_apply(op, store)
        apply_op(op, store, source=source, applied=applied, fp_lookup=fp)
        if not applied:
            result.proposed.append(op)
        elif op.kind not in SAFE_OPS:
            result.applied_structural.append(op)

    # 5. Coverage net: never silently drop an added chunk the LLM failed to place.
    #    A chunk named in any op (applied ATTACH/ADD_NODE *or* a pending ADD_NODE
    #    proposal) is already placed; only genuinely unplaced chunks fall through.
    covered_by_ops = {b for op in ops for b in op.bindings}
    _cover_uncovered_adds(added_unbound, covered_by_ops, store, result, fp, source)
    return result


def _cover_uncovered_adds(
    added_unbound: list,
    covered_by_ops: set[tuple[str, str]],
    store: Store,
    result: LoopAResult,
    fp: dict[tuple[str, str], str],
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
            apply_op(op, store, source=source, applied=True, fp_lookup=fp)
        else:
            op = NodeOp(
                kind=NodeOpKind.ADD_NODE,
                title=a.symbol_path.split("::", 1)[-1],
                description="",
                bindings=[(a.file, a.symbol_path)],
                rationale="coverage: unplaced added chunk — needs a home",
            )
            apply_op(op, store, source=source, applied=False, fp_lookup=fp)
            result.proposed.append(op)


def run_loop_a(
    root_dir: str,
    codoc_dir: str,
    *,
    file_scope: set[str] | None = None,
    source: str = "loop_a",
    repo_name: str = "codebase",
    config=None,
) -> LoopAResult:
    from codoc.graph.query import update_graph

    cs = compute_changeset(root_dir, codoc_dir, file_scope=file_scope)
    store = open_store(codoc_dir)
    try:
        update_graph(store, cs.rows, cs.touched_files())
        result = apply_changeset(cs, store, source=source, repo_name=repo_name, config=config)
        from codoc.loop.status import refresh_status

        refresh_status(codoc_dir, store)
        return result
    finally:
        store.close()
