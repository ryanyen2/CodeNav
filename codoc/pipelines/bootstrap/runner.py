"""Top-level bootstrap pipeline orchestrator (cocoindex-driven).

Runs the bootstrap pipeline on a fresh codebase (no features yet):

  1. Run the cocoindex incremental indexer over the repo — chunks + embeddings
     land in ``.codoc/lancedb`` (resumable, only re-processes changed files).
  2. Read indexed chunks back from LanceDB.
  3. Cluster files using composite similarity (embeddings + imports + lexical).
  4. Walk the cluster tree top-down, calling the LLM at each level so the agent
     sees parent + sibling context and can calibrate abstraction.
  5. Emit INTRODUCE proposals for the user to review.

Users never write the tree from scratch — the LLM proposes an initial tree and
the user curates it (accept / edit / reject).  Pass ``with_intent=False`` only
for offline testing when no API key is available.

After the user reviews proposals, they call ``finish_bootstrap()`` to sweep
unattributed chunks into the ``unattributed_intentional`` registry and switch
the system to reflective mode.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from codoc.core.log import TransactionLog
from codoc.lang import Chunk
from codoc.pipelines.indexing.reader import (
    ChunkRow,
    per_file_mean_embeddings,
    read_all_chunks,
)
from codoc.pipelines.indexing.runner import update_index
from codoc.storage.jsonl_log import JSONLLog
from codoc.storage.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_chunk(row: ChunkRow) -> Chunk:
    """Adapt a LanceDB-backed :class:`ChunkRow` to the legacy :class:`Chunk` shape.

    Downstream clustering and proposal code expects ``Chunk`` instances; the
    extra fields on ``ChunkRow`` (embedding, hashes) are routed separately.
    """
    return Chunk(
        symbol_path=row.symbol_path,
        file=row.file,
        start_byte=row.start_byte,
        end_byte=row.end_byte,
        source=row.source,
    )


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


def _collect_attributed_symbol_paths(store: SQLiteStore) -> set[str]:
    """Return all symbol_paths currently bound to an accepted feature.

    We look in:
    - ``bindings`` table (each binding's anchor.symbol_path)
    - Accepted INTRODUCE transactions whose candidate_bindings were accepted
    """
    attributed: set[str] = set()

    for binding in store.get_all_bindings():
        if binding.anchor.symbol_path:
            attributed.add(binding.anchor.symbol_path)

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
    """Wipe codoc + cocoindex state in *codoc_dir* so bootstrap can start fresh.

    Removes: codoc.db, log.jsonl, tree/, unattributed.json, lancedb/, cocoindex.db/.
    Does NOT remove the .codoc/ directory itself.
    """
    import shutil

    codoc_path = Path(codoc_dir)
    for name in ("codoc.db", "log.jsonl", "unattributed.json"):
        p = codoc_path / name
        if p.exists():
            p.unlink()
    for name in ("tree", "lancedb", "cocoindex.db"):
        p = codoc_path / name
        if p.exists():
            shutil.rmtree(p)
    print(f"[bootstrap] reset: wiped {codoc_dir}", file=sys.stderr)


def run_bootstrap(
    root_dir: str,
    codoc_dir: str,
    repo_name: str = "codebase",
    node_id: str = "default",
    with_intent: bool = True,
    reset: bool = False,
    mode: str | None = None,  # accepted for back-compat; ignored
) -> dict:
    """Bootstrap the feature tree using semantic clustering over cocoindex output.

    Flow:
      1. ``update_index`` — incremental cocoindex run; cheap when up to date.
      2. ``read_all_chunks`` — load chunks + embeddings from LanceDB.
      3. ``build_hierarchical_clusters`` — cluster files (embedding + import + lexical).
      4. ``propose_subtree`` — LLM call per cluster generates feature proposals.
      5. Emit INTRODUCE proposals.

    Cost: O(clusters) LLM calls. 0 API calls with ``with_intent=False``.

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
        If True, call the LLM to generate feature tree proposals.
    reset:
        If True, wipe existing codoc state before running.
    mode:
        Ignored. Retained for backward compatibility with old CLIs.

    Returns
    -------
    dict
        ``{chunk_count, group_count, proposal_count, proposals}``.
    """
    codoc_path = Path(codoc_dir)
    codoc_path.mkdir(parents=True, exist_ok=True)

    if reset:
        reset_codoc(codoc_dir)

    db_path = str(codoc_path / "codoc.db")
    jsonl_path = str(codoc_path / "log.jsonl")

    # --- 1. Incremental index (resumes from where it left off if interrupted) ---
    print("[bootstrap] indexing chunks via cocoindex...", file=sys.stderr)
    update_index(root_dir, codoc_dir)

    # --- 2. Read indexed chunks + embeddings ---
    rows = read_all_chunks(codoc_dir)
    if not rows:
        return {
            "chunk_count": 0,
            "group_count": 0,
            "proposal_count": 0,
            "proposals": [],
        }

    chunks = [_row_to_chunk(r) for r in rows]
    file_embeddings_np = per_file_mean_embeddings(rows)
    # Hand the clusterer plain lists so the existing similarity helpers work.
    file_embeddings: dict[str, list[float] | None] = {
        f: (v.tolist() if v is not None else None)
        for f, v in file_embeddings_np.items()
    }

    print(
        f"[bootstrap] {len(chunks)} chunks across {len(file_embeddings)} files.",
        file=sys.stderr,
    )

    language_adapters = _build_language_adapters(chunks)

    with SQLiteStore(db_path) as store:
        tx_log = TransactionLog(store, node_id=node_id)
        jsonl_log = JSONLLog(jsonl_path)

        proposals = _run_semantic_bootstrap(
            chunks=chunks,
            root_dir=root_dir,
            tx_log=tx_log,
            language_adapters=language_adapters,
            with_intent=with_intent,
            repo_name=repo_name,
            file_embeddings=file_embeddings,
        )
        group_count = len(proposals)

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
        "group_count": group_count,
        "proposal_count": len(proposals),
        "proposals": proposal_summaries,
    }


def _run_semantic_bootstrap(
    chunks: list,
    root_dir: str,
    tx_log,
    language_adapters: dict,
    with_intent: bool,
    repo_name: str,
    file_embeddings: dict[str, list[float] | None],
) -> list:
    """Semantic bootstrap: cluster files, then call LLM per cluster.

    Walks the SemanticGroup tree top-down, calling propose_subtree at each
    level so the LLM sees parent+sibling context and can calibrate abstraction.
    Returns all emitted INTRODUCE proposal transactions.
    """
    from codoc.agents.bootstrap_clustering import propose_subtree
    from codoc.pipelines.bootstrap.propose import emit_introduce_proposal
    from codoc.pipelines.bootstrap.semantic_cluster import (
        build_cluster_input,
        build_hierarchical_clusters,
        cluster_into_parents,
    )

    root_group = build_hierarchical_clusters(
        chunks, root_dir=root_dir, file_embeddings=file_embeddings,
    )

    if not root_group.children:
        # Degenerate: all chunks in one group — treat as single cluster
        root_group.children = [root_group]

    # Flatten-wide guard — if >6 top-level groups, merge into parents.
    if len(root_group.children) > 6:
        root_group.children = cluster_into_parents(
            root_group.children, chunks, root_dir=root_dir, n_target=5,
            file_embeddings=file_embeddings,
        )

    print(
        f"[bootstrap] {len(root_group.children)} top-level clusters found.",
        file=sys.stderr,
    )

    all_proposals: list = []
    existing_summaries: list[dict] = []

    def _walk(
        group,
        parent_uuid: str | None,
        parent_title: str,
        parent_intent: str,
        depth: int,
    ):
        sibling_titles: list[str] = []

        if not with_intent:
            import uuid as _uuid

            from codoc.model.hlc import HLC
            from codoc.model.transaction import Transaction, TransactionKind

            cand = _build_candidate_bindings(group, chunks, language_adapters)
            if not cand:
                return
            provisional = str(_uuid.uuid4())
            slug = f"group-{group.group_id}"
            payload: dict = {
                "slug": slug,
                "title": slug,
                "intent": "",
                "description": "",
                "provisional_uuid": provisional,
                "candidate_bindings": cand,
            }
            if parent_uuid:
                payload["parent_uuid"] = parent_uuid
            tx = Transaction(
                hlc=HLC.now(node_id="bootstrap"),
                parent_hlcs=[],
                kind=TransactionKind.INTRODUCE,
                payload=payload,
                author="bootstrap",
                proposal=True,
            )
            stamped = tx_log.append_proposal(tx)
            all_proposals.append(stamped)
            for child in group.children:
                _walk(child, provisional, slug, "", depth + 1)
            return

        # Build cluster input for this group
        cluster_input = build_cluster_input(group, chunks, root_dir=root_dir)
        if not cluster_input.chunks:
            return

        try:
            feature_proposals = propose_subtree(
                cluster=cluster_input,
                parent_feature_title=parent_title,
                parent_feature_intent=parent_intent,
                sibling_titles=sibling_titles,
                existing_feature_summaries=existing_summaries,
                depth=depth,
                repo_name=repo_name,
            )
        except Exception as exc:
            print(
                f"[bootstrap] LLM failed for group {group.group_id}: {exc}",
                file=sys.stderr,
            )
            return

        group_symbol_paths = {chunks[i].symbol_path for i in group.chunk_indices}

        for fp in feature_proposals:
            fp.candidate_chunk_keys = [
                k for k in fp.candidate_chunk_keys if k in group_symbol_paths
            ]
            tx = emit_introduce_proposal(
                proposal=fp,
                chunks=chunks,
                tx_log=tx_log,
                language_adapters=language_adapters,
                author="bootstrap",
                parent_uuid=parent_uuid,
            )
            all_proposals.append(tx)
            existing_summaries.append({"slug": fp.slug, "intent": fp.intent})
            sibling_titles.append(fp.title or fp.slug)

            for child in group.children:
                _walk(child, fp.provisional_uuid, fp.title or fp.slug, fp.intent, depth + 1)

    for top_group in root_group.children:
        _walk(top_group, None, "<repo-root>", "", 0)

    return all_proposals


def _build_candidate_bindings(group, chunks: list, language_adapters: dict) -> list[dict]:
    """Build fingerprinted candidate_bindings for a group (no-LLM path)."""
    from codoc.core.fingerprint import fingerprint_chunk
    from codoc.lang import detect_language

    result: list[dict] = []
    for i in group.chunk_indices:
        chunk = chunks[i]
        language = detect_language(chunk.file)
        adapter = language_adapters.get(language) if language else None
        if adapter is None:
            continue
        try:
            fp = fingerprint_chunk(chunk.source, adapter)
        except Exception:
            continue
        result.append({
            "anchor": {"file": chunk.file, "symbol_path": chunk.symbol_path},
            "fingerprint": fp,
        })
    return result


def finish_bootstrap(codoc_dir: str, node_id: str = "default") -> dict:
    """Mark bootstrap as complete and record unattributed chunks.

    Reads the cocoindex-managed chunk index (LanceDB) to find every symbol path
    currently visible in the repo, then subtracts the symbol paths already
    attributed via accepted INTRODUCE transactions or concrete bindings. The
    remainder is written to ``{codoc_dir}/unattributed.json`` as
    ``unattributed_intentional`` — the user is aware they exist and has
    intentionally left them unattributed for now.

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
    unattributed_path = codoc_path / "unattributed.json"

    rows = read_all_chunks(codoc_dir)
    all_symbol_paths: list[str] = [r.symbol_path for r in rows]

    if not Path(db_path).exists():
        attributed: set[str] = set()
    else:
        with SQLiteStore(db_path) as store:
            attributed = _collect_attributed_symbol_paths(store)

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
