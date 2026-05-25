"""Bootstrap — build the initial tree from a fresh repo.

This is just Loop A run against an empty tree: index the repo, treat every chunk
as "added", batch by file (directory locality), and feed each batch to the SAME
``propose_tree_update`` call. The growing tree is passed back as ``subtree`` +
``all_titles`` each batch, so the single-pass de-duplication that prevents
duplicates within a batch also prevents them across batches. Bootstrap
auto-applies the resulting ADD_NODE / ATTACH ops; the user curates afterward by
editing ``tree.codoc``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from codoc.agent.tree_update import propose_tree_update
from codoc.loop.apply import apply_op
from codoc.store.db import Store, open_store


@dataclass
class BootstrapResult:
    chunks: int = 0
    features: int = 0
    batches: int = 0

    def summary(self) -> str:
        return f"{self.chunks} chunks → {self.features} features ({self.batches} LLM batches)"


def _tree_snapshot(store: Store) -> tuple[list[dict], list[dict]]:
    feats = store.list_features()
    subtree = [
        {"id": f.id, "title": f.title, "description": f.description, "parent_id": f.parent_id,
         "bindings": [b.symbol_path for b in store.bindings_for_feature(f.id)]}
        for f in feats
    ]
    all_titles = [{"id": f.id, "title": f.title, "parent_id": f.parent_id} for f in feats]
    return subtree, all_titles


def bootstrap_from_chunks(
    rows,
    store: Store,
    *,
    propose=propose_tree_update,
    repo_name: str = "codebase",
    max_per_call: int = 40,
    config=None,
) -> BootstrapResult:
    rows = sorted(rows, key=lambda r: r.file)
    batches = 0
    for i in range(0, len(rows), max_per_call):
        batch = rows[i : i + max_per_call]
        batches += 1
        changes = {
            "added": [
                {"file": r.file, "symbol_path": r.symbol_path, "source": (r.source or "")[:600]}
                for r in batch
            ],
            "removed": [],
            "modified": [],
        }
        subtree, all_titles = _tree_snapshot(store)
        fps = {(r.file, r.symbol_path): r.tokens_hash for r in batch}
        ops = propose(changes, subtree, all_titles, repo_name=repo_name, config=config)
        for op in ops:
            apply_op(op, store, source="bootstrap", applied=True, fp_lookup=fps)
    return BootstrapResult(chunks=len(rows), features=len(store.list_features()), batches=batches)


def run_bootstrap(
    root_dir: str,
    codoc_dir: str,
    *,
    repo_name: str | None = None,
    max_per_call: int = 40,
    config=None,
    do_index: bool = True,
) -> BootstrapResult:
    from codoc.codoc_file.render import write_tree
    from codoc.pipelines.indexing.reader import read_all_chunks
    from codoc.pipelines.indexing.runner import update_index

    if do_index:
        update_index(root_dir, codoc_dir)
    rows = read_all_chunks(codoc_dir)
    store = open_store(codoc_dir)
    try:
        res = bootstrap_from_chunks(
            rows, store, repo_name=repo_name or os.path.basename(os.path.abspath(root_dir)),
            max_per_call=max_per_call, config=config,
        )
        write_tree(store, codoc_dir)
        return res
    finally:
        store.close()


def run_init(root_dir: str, codoc_dir: str | None = None, **kwargs) -> BootstrapResult:
    """Create ``.codoc/`` (if needed), bootstrap, and render ``tree.codoc``."""
    from pathlib import Path

    cd = codoc_dir or str(Path(root_dir) / ".codoc")
    Path(cd).mkdir(parents=True, exist_ok=True)
    return run_bootstrap(root_dir, cd, **kwargs)
