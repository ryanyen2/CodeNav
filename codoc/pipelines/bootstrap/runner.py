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
from pathlib import Path

from codoc.storage.sqlite_store import SQLiteStore
from codoc.storage.faiss_index import FaissIndex
from codoc.storage.jsonl_log import JSONLLog
from codoc.core.log import TransactionLog
from codoc.pipelines.bootstrap.cluster import (
    extract_all_chunks,
    embed_chunks,
    cluster_chunks,
)
from codoc.pipelines.bootstrap.propose import propose_all


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


def run_bootstrap(
    root_dir: str,
    codoc_dir: str,
    repo_name: str = "codebase",
    target_cluster_size: int = 8,
    node_id: str = "default",
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

    db_path = str(codoc_path / "codoc.db")
    jsonl_path = str(codoc_path / "log.jsonl")
    faiss_dir = str(codoc_path / "faiss")

    # ------------------------------------------------------------------
    # Stage 1: extract
    # ------------------------------------------------------------------
    chunks = extract_all_chunks(root_dir)

    if not chunks:
        return {
            "chunk_count": 0,
            "cluster_count": 0,
            "proposal_count": 0,
            "proposals": [],
        }

    # ------------------------------------------------------------------
    # Stage 2: embed + index
    # ------------------------------------------------------------------
    vectors = embed_chunks(chunks)

    # Persist embeddings to the FAISS index so subsequent reflective runs can
    # reuse them without re-embedding the entire codebase.
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
    # Stage 3: cluster
    # ------------------------------------------------------------------
    clusters = cluster_chunks(chunks, vectors, target_cluster_size=target_cluster_size)

    # ------------------------------------------------------------------
    # Stage 4 + 5: LLM proposals → transaction log
    # ------------------------------------------------------------------
    language_adapters = _build_language_adapters(chunks)

    with SQLiteStore(db_path) as store:
        tx_log = TransactionLog(store, node_id=node_id)

        # Give the LLM context about features already in the tree (for
        # incremental / partial runs where some features pre-exist).
        existing_summaries = _collect_existing_feature_summaries(store)

        proposals = propose_all(
            clusters=clusters,
            chunks=chunks,
            tx_log=tx_log,
            existing_feature_summaries=existing_summaries,
            repo_name=repo_name,
            language_adapters=language_adapters,
        )

        # Mirror proposals to the JSONL audit log.
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

    return {
        "chunk_count": len(chunks),
        "cluster_count": len(clusters),
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
