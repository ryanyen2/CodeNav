"""Bootstrap — build the initial tree from a fresh repo.

A thin entry point over the hierarchical bootstrap
(:mod:`codoc.loop.bootstrap_hier`): index the repo, read its chunks, run the
per-file proposal pass + the organization pass, render ``tree.codoc``, and
write an initial status.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from codoc.store.db import open_store


def _title_from_file(file: str) -> str:
    """A readable feature title derived from a file path, for the fallback node."""
    stem = os.path.splitext(os.path.basename(file))[0]
    words = stem.replace("-", " ").replace("_", " ").strip()
    return (words[:1].upper() + words[1:]) if words else file


@dataclass
class BootstrapResult:
    chunks: int = 0
    features: int = 0
    batches: int = 0

    def summary(self) -> str:
        return f"{self.chunks} chunks → {self.features} features ({self.batches} LLM batches)"


def run_bootstrap(
    root_dir: str,
    codoc_dir: str,
    *,
    repo_name: str | None = None,
    config=None,
    do_index: bool = True,
    organize: bool = True,
) -> BootstrapResult:
    from codoc.codoc_file.render import write_tree
    from codoc.pipelines.indexing.reader import read_all_chunks
    from codoc.pipelines.indexing.runner import update_index

    from codoc.graph.query import build_graph
    from codoc.loop.bootstrap_hier import bootstrap_hier_from_chunks

    if do_index:
        update_index(root_dir, codoc_dir)
    rows = read_all_chunks(codoc_dir)
    with open_store(codoc_dir) as store:
        build_graph(store, rows)
        res = bootstrap_hier_from_chunks(
            rows, store, repo_name=repo_name or os.path.basename(os.path.abspath(root_dir)),
            config=config, organize=organize,
        )
        write_tree(store, codoc_dir)
        # Write status so the IDE shows a real state (in_sync) on a freshly
        # bootstrapped repo instead of "not initialized".
        from codoc.loop.status import refresh_status
        refresh_status(codoc_dir, store)
        return res


def run_init(root_dir: str, codoc_dir: str | None = None, **kwargs) -> BootstrapResult:
    """Create ``.codoc/`` (if needed), bootstrap, and render ``tree.codoc``."""
    from pathlib import Path

    cd = codoc_dir or str(Path(root_dir) / ".codoc")
    Path(cd).mkdir(parents=True, exist_ok=True)
    return run_bootstrap(root_dir, cd, **kwargs)
