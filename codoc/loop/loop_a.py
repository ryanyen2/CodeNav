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
from codoc.loop.diff import ChangeSet, compute_changeset
from codoc.loop.subtree import select_relevant_subtree
from codoc.model.event import SAFE_OPS, NodeOp
from codoc.store.db import Store, open_store

_SNIPPET = 600


@dataclass
class LoopAResult:
    auto: dict[str, int] = field(default_factory=dict)        # safe-op kind → count
    applied_structural: list[NodeOp] = field(default_factory=list)
    proposed: list[NodeOp] = field(default_factory=list)      # pending review hunks
    llm_called: bool = False

    def summary(self) -> str:
        auto = ", ".join(f"{n} {k}" for k, n in sorted(self.auto.items())) or "none"
        parts = [f"auto: {auto}"]
        if self.proposed:
            kinds = Counter(op.kind.value for op in self.proposed)
            parts.append("proposed: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
        if self.applied_structural:
            parts.append(f"applied {len(self.applied_structural)} structural")
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

    # 1. Auto-apply the trivially-resolvable safe ops (no LLM).
    auto_ops = derive_auto_ops(cs, store)
    for op in auto_ops:
        apply_op(op, store, source=source, applied=True, fp_lookup=fp)
    result = LoopAResult(auto=dict(Counter(op.kind.value for op in auto_ops)))

    # 2. Features that just lost their last binding.
    emptied = {
        fid for fid in set(removed_owner.values())
        if not store.bindings_for_feature(fid)
        and (f := store.get_feature(fid)) and not f.retired
    }
    added_unbound = [a for a in cs.added if store.binding_at(a.file, a.symbol_path) is None]

    if not (added_unbound or emptied):
        return result

    # 3. The single LLM pass.
    result.llm_called = True
    changes = {
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
    subtree, all_titles = select_relevant_subtree(cs, store)
    ops = propose(changes, subtree, all_titles, repo_name=repo_name, config=config)

    # 4. Apply: safe → now; structural → pending proposal.
    for op in ops:
        applied = should_auto_apply(op, store)
        apply_op(op, store, source=source, applied=applied, fp_lookup=fp)
        if not applied:
            result.proposed.append(op)
        elif op.kind not in SAFE_OPS:
            result.applied_structural.append(op)
    return result


def run_loop_a(
    root_dir: str,
    codoc_dir: str,
    *,
    file_scope: set[str] | None = None,
    source: str = "loop_a",
    repo_name: str = "codebase",
    config=None,
) -> LoopAResult:
    cs = compute_changeset(root_dir, codoc_dir, file_scope=file_scope)
    store = open_store(codoc_dir)
    try:
        return apply_changeset(cs, store, source=source, repo_name=repo_name, config=config)
    finally:
        store.close()
