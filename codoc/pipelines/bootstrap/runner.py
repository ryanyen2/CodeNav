"""
codoc.pipelines.bootstrap.runner — top-level bootstrap pipeline orchestrator.

Runs the full staged attribution pipeline on a fresh codebase (no features yet):

  1. Extract all chunks from the repo via language adapters.
  2. Embed all chunks.
  3. Cluster embeddings with FAISS k-means.
  4. Call the LLM per cluster to produce INTRODUCE proposals.
  5. Emit all proposals to the transaction log.

After the user reviews proposals (accept / edit / reject via CLI or API), they
call ``finish_bootstrap()`` to sweep unattributed chunks into the
``unattributed_intentional`` registry and switch the system to reflective mode.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from codoc.storage.sqlite_store import SQLiteStore
from codoc.storage.faiss_index import FaissIndex
from codoc.storage.jsonl_log import JSONLLog
from codoc.core.log import TransactionLog
from codoc.pipelines.bootstrap.cluster import (
    extract_all_chunks,
    embed_chunks,
    cluster_chunks,
    cluster_hierarchical,
    cluster_recursive,
)
from codoc.pipelines.bootstrap.propose import (
    propose_all,
    propose_hierarchical,
    propose_recursive,
    propose_structural,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_language_adapters(chunks) -> dict:
    """Build a {language: adapter} dict covering all languages seen in *chunks*."""
    from codoc.lang import detect_language, get_adapter

    adapters: dict = {}
    for chunk in chunks:
        language = detect_language(chunk.file)
        if language and language not in adapters:
            try:
                adapters[language] = get_adapter(language)
            except ValueError:
                pass
    return adapters


def _collect_existing_feature_summaries(store: SQLiteStore) -> list[dict]:
    """Return {slug, intent} for every non-retired feature in the store."""
    features = store.list_features()
    return [
        {"slug": f.slug, "intent": f.intent}
        for f in features
        if not f.retired
    ]


def _collect_attributed_symbol_paths(store: SQLiteStore) -> set[str]:
    """Return all symbol_paths currently bound to an accepted feature.

    We look in:
    - ``bindings`` table (each binding's anchor.symbol_path)
    - Accepted INTRODUCE transactions whose candidate_bindings were accepted
    """
    attributed: set[str] = set()

    # From concrete bindings (already materialised into the feature graph).
    for binding in store.get_all_bindings():
        if binding.anchor.symbol_path:
            attributed.add(binding.anchor.symbol_path)

    # From accepted INTRODUCE transactions: their payload.candidate_bindings
    # hold the symbol paths that were approved by the user.
    from codoc.model.transaction import TransactionKind

    for tx in store.list_transactions(proposal=False, limit=0):
        if tx.kind == TransactionKind.INTRODUCE:
            for cb in tx.payload.get("candidate_bindings", []):
                sp = cb.get("anchor", {}).get("symbol_path")
                if sp:
                    attributed.add(sp)

    return attributed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reset_codoc(codoc_dir: str) -> None:
    """Wipe all codoc state in *codoc_dir* so bootstrap can start fresh.

    Removes: codoc.db, log.jsonl, tree/, faiss/, unattributed.json.
    Does NOT remove the .codoc/ directory itself.
    """
    import shutil

    codoc_path = Path(codoc_dir)
    for name in ("codoc.db", "log.jsonl", "unattributed.json"):
        p = codoc_path / name
        if p.exists():
            p.unlink()
    for name in ("tree", "faiss"):
        p = codoc_path / name
        if p.exists():
            shutil.rmtree(p)
    print(f"[bootstrap] reset: wiped {codoc_dir}", file=sys.stderr)


def run_bootstrap(
    root_dir: str,
    codoc_dir: str,
    repo_name: str = "codebase",
    target_cluster_size: int = 8,
    node_id: str = "default",
    hierarchical: bool = False,
    reset: bool = False,
) -> dict:
    """Run the full bootstrap pipeline on *root_dir*.

    Opens (or creates) the SQLite store and JSONL log inside *codoc_dir*,
    extracts + embeds + clusters chunks, calls the LLM for each cluster, and
    emits INTRODUCE proposal transactions.

    Parameters
    ----------
    root_dir:
        Root directory of the codebase to analyse.
    codoc_dir:
        Path to the ``.codoc/`` working directory where codoc persists its
        state (e.g. ``/path/to/repo/.codoc``).
    repo_name:
        Human-readable repository name, forwarded to the clustering LLM prompt.
    target_cluster_size:
        Desired average number of chunks per cluster (passed to
        :func:`~codoc.pipelines.bootstrap.cluster.cluster_chunks`).
    node_id:
        HLC node identifier for transactions generated in this run.

    Returns
    -------
    dict
        Summary with keys:

        - ``chunk_count`` — total chunks extracted.
        - ``cluster_count`` — number of clusters formed.
        - ``proposal_count`` — number of INTRODUCE proposals emitted.
        - ``proposals`` — list of ``{hlc, slug, intent, candidate_count}``
          dicts for each emitted proposal.
    """
    codoc_path = Path(codoc_dir)
    codoc_path.mkdir(parents=True, exist_ok=True)

    if reset:
        reset_codoc(codoc_dir)

    db_path = str(codoc_path / "codoc.db")
    jsonl_path = str(codoc_path / "log.jsonl")
    faiss_dir = str(codoc_path / "faiss")

    # ------------------------------------------------------------------
    # Stage 1: extract
    # ------------------------------------------------------------------
    print("[bootstrap] extracting chunks...", file=sys.stderr)
    chunks = extract_all_chunks(root_dir)

    if not chunks:
        return {
            "chunk_count": 0,
            "cluster_count": 0,
            "proposal_count": 0,
            "proposals": [],
        }

    print(f"[bootstrap] {len(chunks)} chunks. Embedding...", file=sys.stderr)

    # ------------------------------------------------------------------
    # Stage 2: embed + index
    # ------------------------------------------------------------------
    vectors = embed_chunks(chunks)

    faiss_index = FaissIndex(faiss_dir)
    faiss_index.open()
    for chunk, vector in zip(chunks, vectors):
        faiss_index.add(
            key=chunk.symbol_path,
            vector=vector,
            metadata={"file": chunk.file, "symbol_path": chunk.symbol_path},
        )
    faiss_index.save()

    # ------------------------------------------------------------------
    # Stage 3: cluster (recursive by default; flat and legacy hierarchical kept)
    # ------------------------------------------------------------------
    # Always use recursive clustering now — it produces arbitrary-depth trees.
    root_node = cluster_recursive(
        chunks, vectors,
        target_leaf_size=target_cluster_size,
        max_depth=5,
    )

    def _count_nodes(node) -> int:
        return 1 + sum(_count_nodes(c) for c in node.children)

    node_count = _count_nodes(root_node)
    print(
        f"[bootstrap] recursive tree: {node_count} nodes, depth≤5. Calling LLM...",
        file=sys.stderr,
    )
    cluster_count = node_count

    # ------------------------------------------------------------------
    # Stage 4 + 5: LLM proposals → transaction log
    # ------------------------------------------------------------------
    language_adapters = _build_language_adapters(chunks)

    with SQLiteStore(db_path) as store:
        tx_log = TransactionLog(store, node_id=node_id)
        existing_summaries = _collect_existing_feature_summaries(store)

        proposals = propose_recursive(
            root_node=root_node,
            chunks=chunks,
            tx_log=tx_log,
            repo_name=repo_name,
            language_adapters=language_adapters,
            running_summaries=existing_summaries,
        )

        # Post-hoc deduplication: merge near-duplicate sibling proposals.
        from codoc.pipelines.bootstrap.dedupe import dedup_proposals
        proposals = dedup_proposals(proposals)

        jsonl_log = JSONLLog(jsonl_path)
        for tx in proposals:
            jsonl_log.append(tx)

        proposal_summaries = [
            {
                "hlc": tx.hlc.to_str(),
                "slug": tx.payload.get("slug", ""),
                "intent": tx.payload.get("intent", ""),
                "candidate_count": len(tx.payload.get("candidate_bindings", [])),
            }
            for tx in proposals
        ]

    print(f"[bootstrap] {len(proposals)} proposals emitted.", file=sys.stderr)

    return {
        "chunk_count": len(chunks),
        "cluster_count": cluster_count,
        "proposal_count": len(proposals),
        "proposals": proposal_summaries,
    }


def run_bootstrap_structural(
    root_dir: str,
    codoc_dir: str,
    repo_name: str = "codebase",
    node_id: str = "default",
    with_intent: bool = False,
    reset: bool = False,
) -> dict:
    """Bootstrap by reading directory/class structure — zero LLM calls by default.

    This replaces the expensive k-means + per-cluster LLM approach with a
    structure-first pipeline inspired by (but not copying) Leiden community
    detection (Traag et al., Sci. Rep. 2019) and RAPTOR hierarchical
    summarization (Sarthi et al., ICLR 2024):

    - Directories map to top-level features (free, zero LLM).
    - Classes / module prefixes within directories map to child features (free).
    - Intent text is empty by default — the user fills it in via ``codoc edit``
      or ``codoc plan``.  Pass ``with_intent=True`` to batch-call the LLM
      (one call per feature, sequential) and generate intent text up front.

    Cost: 0 API calls by default.  O(features) calls with ``--with-intent``.

    Parameters
    ----------
    root_dir:
        Root directory of the codebase to analyse.
    codoc_dir:
        Path to the ``.codoc/`` working directory.
    repo_name:
        Human-readable name forwarded to the LLM when ``with_intent=True``.
    node_id:
        HLC node identifier for emitted transactions.
    with_intent:
        If True, call the LLM once per feature to generate intent text.
    reset:
        If True, wipe existing codoc state before running.

    Returns
    -------
    dict
        ``{chunk_count, group_count, proposal_count, proposals}``.
    """
    from codoc.pipelines.bootstrap.structural import build_structural_tree, count_groups

    codoc_path = Path(codoc_dir)
    codoc_path.mkdir(parents=True, exist_ok=True)

    if reset:
        reset_codoc(codoc_dir)

    db_path = str(codoc_path / "codoc.db")
    jsonl_path = str(codoc_path / "log.jsonl")

    print("[bootstrap:structural] extracting chunks...", file=sys.stderr)
    chunks = extract_all_chunks(root_dir)

    if not chunks:
        return {"chunk_count": 0, "group_count": 0, "proposal_count": 0, "proposals": []}

    print(f"[bootstrap:structural] {len(chunks)} chunks extracted. Building structure tree...",
          file=sys.stderr)

    root_group = build_structural_tree(chunks)
    group_count = count_groups(root_group) - 1  # exclude root

    if with_intent:
        print(f"[bootstrap:structural] {group_count} groups. Generating intent via LLM...",
              file=sys.stderr)
    else:
        print(f"[bootstrap:structural] {group_count} groups. Emitting proposals (no LLM)...",
              file=sys.stderr)

    language_adapters = _build_language_adapters(chunks)

    with SQLiteStore(db_path) as store:
        tx_log = TransactionLog(store, node_id=node_id)

        proposals = propose_structural(
            root_group=root_group,
            chunks=chunks,
            tx_log=tx_log,
            language_adapters=language_adapters,
            with_intent=with_intent,
            repo_name=repo_name,
        )

        jsonl_log = JSONLLog(jsonl_path)
        for tx in proposals:
            jsonl_log.append(tx)

        proposal_summaries = [
            {
                "hlc": tx.hlc.to_str(),
                "slug": tx.payload.get("slug", ""),
                "intent": tx.payload.get("intent", ""),
                "candidate_count": len(tx.payload.get("candidate_bindings", [])),
            }
            for tx in proposals
        ]

    print(f"[bootstrap:structural] {len(proposals)} proposals emitted.", file=sys.stderr)

    return {
        "chunk_count": len(chunks),
        "group_count": group_count,
        "proposal_count": len(proposals),
        "proposals": proposal_summaries,
    }


def finish_bootstrap(codoc_dir: str, node_id: str = "default") -> dict:
    """Mark bootstrap as complete and record unattributed chunks.

    Scans the codebase (re-using previously extracted chunks stored via the
    FAISS index metadata) to find any chunks not yet mentioned in an accepted
    INTRODUCE transaction or in the concrete bindings table.  Those chunks are
    written to ``{codoc_dir}/unattributed.json`` as ``unattributed_intentional``
    — meaning the user is aware they exist and has intentionally left them
    unattributed for now.

    After this call the system should switch to reflective mode (the caller or
    CLI is responsible for setting the mode flag in its own config).

    Parameters
    ----------
    codoc_dir:
        Path to the ``.codoc/`` directory used by this repo.
    node_id:
        HLC node identifier (unused here but kept for signature consistency).

    Returns
    -------
    dict
        ``{"unattributed_count": int}``
    """
    codoc_path = Path(codoc_dir)
    db_path = str(codoc_path / "codoc.db")
    faiss_dir = str(codoc_path / "faiss")
    unattributed_path = codoc_path / "unattributed.json"

    # ------------------------------------------------------------------
    # Collect all known symbol_paths from the FAISS index.
    # ------------------------------------------------------------------
    faiss_index = FaissIndex(faiss_dir)
    faiss_index.open()
    all_symbol_paths: list[str] = faiss_index.all_keys()

    # ------------------------------------------------------------------
    # Collect attributed symbol_paths from the store.
    # ------------------------------------------------------------------
    if not Path(db_path).exists():
        # No database at all → everything is unattributed.
        attributed: set[str] = set()
    else:
        with SQLiteStore(db_path) as store:
            attributed = _collect_attributed_symbol_paths(store)

    # ------------------------------------------------------------------
    # Compute unattributed set.
    # ------------------------------------------------------------------
    unattributed = [sp for sp in all_symbol_paths if sp not in attributed]

    record = {
        "status": "unattributed_intentional",
        "symbol_paths": unattributed,
    }

    unattributed_path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {"unattributed_count": len(unattributed)}
