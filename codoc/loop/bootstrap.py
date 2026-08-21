"""Bootstrap — build the initial tree from a fresh repo.

A thin entry point over the hierarchical bootstrap
(:mod:`codoc.loop.bootstrap_hier`): index the repo, read its chunks, run the
per-file proposal pass + the organization pass, render ``tree.codoc``, and
write an initial status.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from codoc.doclang import resolve, workspace_doc_language, write_config
from codoc.loop.filenames import CONFIG_FILENAME
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
    # Files whose LLM call could not be used after a retry. Their chunks stay
    # unbound, so the next reconcile proposes them — a gap the user can see and
    # fill, rather than a bootstrap that produced nothing. A file crowded enough
    # to be described over several calls is named here if ANY of them failed;
    # which part it was belongs in the warning line, since this is a list of
    # paths and a caller has to be able to use them as such.
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        base = f"{self.chunks} chunks → {self.features} features ({self.batches} LLM batches)"
        return base + (f", {len(self.skipped)} file(s) skipped" if self.skipped else "")


def run_bootstrap(
    root_dir: str,
    codoc_dir: str,
    *,
    repo_name: str | None = None,
    config=None,
    do_index: bool = True,
    organize: bool = True,
    printer=None,
) -> BootstrapResult:
    """Index, propose, render. The authoring language is read from the workspace
    config (already written by :func:`run_init` when the caller chose one), so
    bootstrap prose comes out in the tree's language from the very first node —
    there is no English tree to translate afterwards."""
    from codoc.codoc_file.render import write_tree
    from codoc.pipelines.indexing.reader import read_all_chunks
    from codoc.pipelines.indexing.runner import update_index

    from codoc.graph.query import build_graph
    from codoc.loop.bootstrap_hier import bootstrap_hier_from_chunks
    from codoc.loop.loop_b import write_tree_doc
    from codoc.loop.status import refresh_status

    say = printer or (lambda *_a, **_k: None)

    if do_index:
        # Under the shared loop lock: update_index can WIPE + rebuild the LanceDB
        # index (embed-flag reconcile). Unlocked, that rmtree could land between a
        # concurrent daemon's own update_index and its read — an empty read that
        # mass-detaches every binding. Every other update_index caller is already
        # lock-covered; this was the one outside it.
        from codoc.loop.locks import loop_lock

        with loop_lock(codoc_dir):
            update_index(root_dir, codoc_dir)
    # Bootstrap reads only symbol_path/source/hashes — never the embedding vectors —
    # so materializing the whole embedding column here is pure memory pressure on a
    # large repo (hundreds of MB of 384-float rows for nothing).
    rows = read_all_chunks(codoc_dir, with_embeddings=False)
    if not rows:
        # No indexable Python/TypeScript. Don't silently render an empty tree that
        # reads as a bug — still create the store + status so the daemon runs, but
        # tell the user why the tree is empty.
        with open_store(codoc_dir) as store:
            write_tree(store, codoc_dir)
            write_tree_doc(store, codoc_dir)
            refresh_status(codoc_dir, store)
        say("  No supported source files found — codoc indexes Python & TypeScript. "
            "Add code and re-run `codoc init`, or start `codoc watch`.")
        return BootstrapResult()
    with open_store(codoc_dir) as store:
        # One atomic unit: a failed / interrupted LLM call mid-bootstrap (rate limit,
        # network blip, missing key on file N of M) rolls the store back to empty
        # rather than leaving a half-built tree that a re-run would DUPLICATE on top of
        # (every ADD mints a fresh id). A clean re-run then just works.
        with store.transaction():
            build_graph(store, rows)
            res = bootstrap_hier_from_chunks(
                rows, store, repo_name=repo_name or os.path.basename(os.path.abspath(root_dir)),
                config=config, organize=organize, printer=say, root_dir=root_dir,
                doc_language=workspace_doc_language(codoc_dir),
            )
        write_tree(store, codoc_dir)
        # Seed the doc projection too. The webview's document pane renders
        # tree.doc.json and nothing else; its outline comes from tree.codoc, so a
        # workspace missing the projection shows a full tree of titles beside a
        # blank page — which reads as "codoc wrote nothing" rather than as a
        # missing file. Loop B writes this on a mutating pass and `_render` on a
        # file change, so before this line a freshly-inited workspace stayed blank
        # until the user happened to edit some code.
        write_tree_doc(store, codoc_dir)
        # Write status so the IDE shows a real state (in_sync) on a freshly
        # bootstrapped repo instead of "not initialized".
        refresh_status(codoc_dir, store)
        return res


def run_init(root_dir: str, codoc_dir: str | None = None, *,
             doc_language: str | None = None, **kwargs) -> BootstrapResult:
    """Create ``.codoc/`` (if needed), bootstrap, and render ``tree.codoc``.

    ``doc_language`` is persisted BEFORE bootstrapping, not after: the bootstrap
    prompts read it from the workspace config, so setting it here is what makes
    the initial tree come out in the author's language instead of arriving in
    English and needing a rewrite of every node.
    """
    from pathlib import Path

    cd = codoc_dir or str(Path(root_dir) / ".codoc")
    Path(cd).mkdir(parents=True, exist_ok=True)
    _write_codoc_gitignore(cd)
    if doc_language:
        write_config(cd, doc_language=resolve(doc_language).code)
    return run_bootstrap(root_dir, cd, **kwargs)


# The tracked exceptions inside the otherwise-ignored `.codoc/`. Named here rather
# than inlined so `migrate` can heal a workspace whose .gitignore predates an
# addition to this list (see `migrate._heal_gitignore`).
TRACKED_IN_CODOC = (".gitignore", "tree.codoc", "tree.doc.json", CONFIG_FILENAME)


def _write_codoc_gitignore(codoc_dir: str) -> None:
    """Drop a ``.codoc/.gitignore`` so the derived index (LanceDB blobs, the SQLite
    store, embeddings) doesn't show up as untracked binary the user might commit.
    Only the human-facing exports (``tree.codoc``/``tree.doc.json``) and the
    authored settings (``config.json``) are left tracked.
    Written once, never overwritten (a user may have customized it)."""
    from pathlib import Path

    gi = Path(codoc_dir) / ".gitignore"
    if gi.exists():
        return
    gi.write_text(
        "# codoc-managed derived state — not for version control.\n"
        "# The feature tree lives in tree.codoc / tree.doc.json, and the authoring\n"
        "# settings in config.json (all kept tracked below).\n"
        "*\n"
        + "".join(f"!{name}\n" for name in TRACKED_IN_CODOC),
        encoding="utf-8",
    )
