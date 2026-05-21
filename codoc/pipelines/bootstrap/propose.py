"""
codoc.pipelines.bootstrap.propose — build INTRODUCE proposals from clustering results.

Phase 2 of bootstrap:
  For each cluster, call the LLM (bootstrap_clustering agent) to propose a feature
  name + intent + candidate bindings, then emit one INTRODUCE proposal transaction
  per proposed feature into the transaction log.
"""

from __future__ import annotations

import sys

from codoc.lang import Chunk
from codoc.agents.bootstrap_clustering import (
    ClusterInput,
    FeatureProposal,
    propose_features_for_cluster,
    propose_subtree,
    build_introduce_payload,
)
from codoc.model.transaction import Transaction, TransactionKind
from codoc.model.hlc import HLC
from codoc.core.log import TransactionLog
from codoc.core.fingerprint import fingerprint_chunk
from codoc.pipelines.bootstrap.cluster import ClusterNode


def build_cluster_inputs(
    chunks: list[Chunk],
    cluster_indices: list[list[int]],
    snippet_len: int = 300,
) -> list[ClusterInput]:
    """Convert clustering results into ClusterInput objects for the LLM.

    Parameters
    ----------
    chunks:
        Full list of extracted chunks (indexed by position).
    cluster_indices:
        List of clusters, each a list of chunk indices into *chunks*.
    snippet_len:
        Maximum number of characters taken from ``chunk.source`` for the
        ``source_snippet`` field shown to the LLM. Shorter is faster and
        cheaper; longer gives the model more context.

    Returns
    -------
    list[ClusterInput]
        One :class:`~codoc.agents.bootstrap_clustering.ClusterInput` per
        cluster, in the same order as *cluster_indices*.
    """
    inputs: list[ClusterInput] = []
    for cluster_id, indices in enumerate(cluster_indices):
        chunk_dicts: list[dict] = []
        for idx in indices:
            chunk = chunks[idx]
            chunk_dicts.append(
                {
                    "symbol_path": chunk.symbol_path,
                    "file": chunk.file,
                    "source_snippet": chunk.source[:snippet_len],
                }
            )
        inputs.append(ClusterInput(chunks=chunk_dicts, cluster_id=cluster_id))
    return inputs


def emit_introduce_proposal(
    proposal: FeatureProposal,
    chunks: list[Chunk],
    tx_log: TransactionLog,
    language_adapters: dict,  # {language: adapter instance}
    author: str = "bootstrap",
    parent_uuid: str | None = None,
) -> Transaction:
    """Create and append one INTRODUCE proposal transaction to *tx_log*.

    The transaction payload contains:

    - ``slug`` — kebab-case feature identifier from the proposal.
    - ``intent`` — 1-2 sentence description from the proposal.
    - ``candidate_bindings`` — list of ``{anchor: {file, symbol_path},
      fingerprint: str}`` dicts, one per candidate chunk.

    Parameters
    ----------
    proposal:
        :class:`~codoc.agents.bootstrap_clustering.FeatureProposal` returned
        by the LLM for one cluster.
    chunks:
        Full list of all extracted chunks; used to look up each candidate key.
    tx_log:
        :class:`~codoc.core.log.TransactionLog` to append the proposal to.
    language_adapters:
        Mapping of ``language → LanguageAdapter`` instance used for
        fingerprinting.  When the language for a chunk has no adapter in the
        dict, a plain SHA-256 of the source is used as the fingerprint fallback.
    author:
        Author field stamped on the transaction. Defaults to ``"bootstrap"``.

    Returns
    -------
    Transaction
        The stamped proposal transaction (after HLC assignment by *tx_log*).
    """
    # Build a symbol_path → chunk lookup for fast access.
    chunk_by_symbol: dict[str, Chunk] = {c.symbol_path: c for c in chunks}

    candidate_bindings: list[dict] = []
    for symbol_path in proposal.candidate_chunk_keys:
        chunk = chunk_by_symbol.get(symbol_path)
        if chunk is None:
            continue

        # Determine the language adapter for fingerprinting.
        from pathlib import Path
        from codoc.lang import detect_language

        language = detect_language(chunk.file)
        adapter = language_adapters.get(language) if language else None

        if adapter is not None:
            try:
                fp = fingerprint_chunk(chunk.source, adapter)
            except Exception:
                import hashlib
                fp = hashlib.sha256(chunk.source.encode("utf-8")).hexdigest()
        else:
            import hashlib
            fp = hashlib.sha256(chunk.source.encode("utf-8")).hexdigest()

        candidate_bindings.append(
            {
                "anchor": {
                    "file": chunk.file,
                    "symbol_path": chunk.symbol_path,
                },
                "fingerprint": fp,
            }
        )

    provisional_uuid = proposal.provisional_uuid or None
    payload = {
        "slug": proposal.slug,
        "title": proposal.title or proposal.slug,
        "intent": proposal.intent,
        "description": proposal.description,
        "candidate_bindings": candidate_bindings,
    }
    if provisional_uuid:
        payload["provisional_uuid"] = provisional_uuid
    if parent_uuid is not None:
        payload["parent_uuid"] = parent_uuid

    hlc = HLC.now(node_id="bootstrap")
    tx = Transaction(
        hlc=hlc,
        parent_hlcs=[],
        kind=TransactionKind.INTRODUCE,
        payload=payload,
        author=author,
        proposal=True,
    )

    stamped = tx_log.append_proposal(tx)
    return stamped


def propose_all(
    clusters: list[list[int]],
    chunks: list[Chunk],
    tx_log: TransactionLog,
    existing_feature_summaries: list[dict],
    repo_name: str = "codebase",
    language_adapters: dict | None = None,
) -> list[Transaction]:
    """Run the LLM for every cluster and emit INTRODUCE proposals.

    For each cluster the LLM may return *multiple* feature proposals (when the
    cluster contains semantically distinct groups).  One proposal transaction is
    emitted per returned :class:`~codoc.agents.bootstrap_clustering.FeatureProposal`.

    Parameters
    ----------
    clusters:
        List of clusters produced by :func:`~codoc.pipelines.bootstrap.cluster.cluster_chunks`.
        Each element is a list of chunk indices into *chunks*.
    chunks:
        All extracted chunks.
    tx_log:
        Transaction log for proposal emission.
    existing_feature_summaries:
        List of ``{slug, intent}`` dicts already in the feature tree.  Used as
        context for the LLM so it avoids duplicating existing features.
    repo_name:
        Human-readable repository name passed to the clustering prompt.
    language_adapters:
        Mapping of ``language → adapter`` for fingerprinting.  When *None* an
        empty dict is used and fingerprints fall back to plain SHA-256.

    Returns
    -------
    list[Transaction]
        All emitted proposal transactions, in cluster order.
    """
    if language_adapters is None:
        language_adapters = {}

    cluster_inputs = build_cluster_inputs(chunks, clusters)

    running_summaries: list[dict] = list(existing_feature_summaries)
    all_transactions: list[Transaction] = []
    llm_error_count = 0

    for cluster_input in cluster_inputs:
        if not cluster_input.chunks:
            continue

        try:
            proposals = propose_features_for_cluster(
                cluster=cluster_input,
                existing_feature_summaries=running_summaries,
                repo_name=repo_name,
            )
        except Exception as exc:
            llm_error_count += 1
            print(
                f"[bootstrap] cluster {cluster_input.cluster_id}: LLM call failed — {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue

        for proposal in proposals:
            try:
                tx = emit_introduce_proposal(
                    proposal=proposal,
                    chunks=chunks,
                    tx_log=tx_log,
                    language_adapters=language_adapters,
                )
                all_transactions.append(tx)
                running_summaries.append(
                    {"slug": proposal.slug, "intent": proposal.intent}
                )
            except Exception as exc:
                print(
                    f"[bootstrap] cluster {cluster_input.cluster_id}: failed to emit proposal — {exc}",
                    file=sys.stderr,
                )

    non_empty = sum(1 for ci in cluster_inputs if ci.chunks)
    if non_empty > 0 and llm_error_count == non_empty:
        raise RuntimeError(
            f"All {llm_error_count} cluster LLM calls failed. "
            "Check your LLM config: CODOC_PROVIDER, OPENAI_API_KEY, CODOC_BASE_URL."
        )

    return all_transactions


def _build_cluster_input_from_node(
    node: ClusterNode,
    chunks: list[Chunk],
    snippet_len: int = 300,
    max_chunks: int = 20,
) -> ClusterInput:
    """Build a ClusterInput for a ClusterNode, sampling up to max_chunks."""
    sample = node.chunk_indices[:max_chunks]
    chunk_dicts = [
        {
            "symbol_path": chunks[i].symbol_path,
            "file": chunks[i].file,
            "source_snippet": chunks[i].source[:snippet_len],
        }
        for i in sample
    ]
    return ClusterInput(chunks=chunk_dicts, cluster_id=node.depth)


def propose_recursive(
    root_node: ClusterNode,
    chunks: list[Chunk],
    tx_log: TransactionLog,
    repo_name: str = "codebase",
    language_adapters: dict | None = None,
    parent_proposal: FeatureProposal | None = None,
    parent_tx_uuid: str | None = None,
    running_summaries: list[dict] | None = None,
    sibling_titles: list[str] | None = None,
) -> list[Transaction]:
    """Recursively emit INTRODUCE proposals for a ClusterNode tree.

    Walks *root_node* pre-order. For each non-leaf node, calls the LLM with
    parent context to produce one feature per child cluster; for leaf nodes the
    child's chunks are attributed to the parent feature rather than creating a
    new level.

    Parameters
    ----------
    root_node:
        Root of the cluster tree produced by ``cluster_recursive()``.
    chunks:
        All extracted chunks (indexed by position).
    tx_log:
        Transaction log for proposal emission.
    repo_name:
        Human-readable repository name.
    language_adapters:
        Mapping of ``language → adapter`` for fingerprinting.
    parent_proposal:
        The FeatureProposal that covers *root_node* (or None at the very top).
    parent_tx_uuid:
        The provisional UUID of the already-emitted parent transaction so
        children can set ``parent_uuid`` correctly.
    running_summaries:
        Accumulated {slug, title, intent} dicts across the whole tree (dedup).
    sibling_titles:
        Titles of features already proposed at this same level (context).

    Returns
    -------
    list[Transaction]
        All INTRODUCE proposal transactions emitted.
    """
    if language_adapters is None:
        language_adapters = {}
    if running_summaries is None:
        running_summaries = []
    if sibling_titles is None:
        sibling_titles = []

    all_transactions: list[Transaction] = []

    # If this node has no children (leaf) we don't recurse further.
    if root_node.is_leaf:
        return all_transactions

    parent_title = parent_proposal.title if parent_proposal else f"<{repo_name} root>"
    parent_intent = parent_proposal.intent if parent_proposal else ""

    proposed_at_this_level: list[FeatureProposal] = []
    child_proposal_by_node: list[tuple[ClusterNode, FeatureProposal]] = []

    # Propose one feature per child cluster using LLM with parent context.
    for child_node in root_node.children:
        if not child_node.chunk_indices:
            continue

        cluster_input = _build_cluster_input_from_node(child_node, chunks)
        current_sibling_titles = sibling_titles + [p.title for p in proposed_at_this_level]

        try:
            proposals = propose_subtree(
                cluster=cluster_input,
                parent_feature_title=parent_title,
                parent_feature_intent=parent_intent,
                sibling_titles=current_sibling_titles,
                existing_feature_summaries=running_summaries,
                depth=child_node.depth,
                repo_name=repo_name,
            )
        except Exception as exc:
            print(
                f"[bootstrap] depth={child_node.depth} LLM call failed — {exc}",
                file=sys.stderr,
            )
            continue

        # Use only the first proposal per child cluster (one feature per cluster).
        if not proposals:
            continue
        proposal = proposals[0]
        proposed_at_this_level.append(proposal)
        child_proposal_by_node.append((child_node, proposal))

    # Now emit transactions for all proposals at this level, then recurse.
    for child_node, proposal in child_proposal_by_node:
        try:
            tx = emit_introduce_proposal(
                proposal=proposal,
                chunks=chunks,
                tx_log=tx_log,
                language_adapters=language_adapters,
                parent_uuid=parent_tx_uuid,
            )
            all_transactions.append(tx)
            running_summaries.append({
                "slug": proposal.slug,
                "title": proposal.title,
                "intent": proposal.intent,
            })
        except Exception as exc:
            print(f"[bootstrap] emit failed — {exc}", file=sys.stderr)
            continue

        # The provisional_uuid in the payload IS the child's identity for its own children.
        child_parent_uuid = tx.payload.get("provisional_uuid") or tx.hlc.to_str()

        # Recurse into child's sub-tree.
        sub_txs = propose_recursive(
            root_node=child_node,
            chunks=chunks,
            tx_log=tx_log,
            repo_name=repo_name,
            language_adapters=language_adapters,
            parent_proposal=proposal,
            parent_tx_uuid=child_parent_uuid,
            running_summaries=running_summaries,
            sibling_titles=[],
        )
        all_transactions.extend(sub_txs)

    return all_transactions


def propose_hierarchical(
    hierarchical_clusters: list[list[list[int]]],
    chunks: list[Chunk],
    tx_log: TransactionLog,
    existing_feature_summaries: list[dict],
    repo_name: str = "codebase",
    language_adapters: dict | None = None,
) -> list[Transaction]:
    """Run the hierarchical bootstrap: emit chapter proposals then leaf proposals.

    Parameters
    ----------
    hierarchical_clusters:
        Output of ``cluster_hierarchical()`` — chapters → sections → chunk indices.
    chunks:
        All extracted chunks.
    tx_log:
        Transaction log for proposal emission.
    existing_feature_summaries:
        Already-known features for LLM context.
    repo_name:
        Human-readable repository name.
    language_adapters:
        Mapping of ``language → LanguageAdapter`` for fingerprinting.

    Returns
    -------
    list[Transaction]
        All emitted INTRODUCE proposal transactions (chapters first, then leaves).
    """
    if language_adapters is None:
        language_adapters = {}

    running_summaries: list[dict] = list(existing_feature_summaries)
    all_transactions: list[Transaction] = []

    for chapter_idx, sections in enumerate(hierarchical_clusters):
        # Flatten chapter to get representative chunks for chapter-level naming.
        chapter_chunk_indices = [idx for section in sections for idx in section]
        if not chapter_chunk_indices:
            continue

        chapter_input = ClusterInput(
            chunks=[
                {
                    "symbol_path": chunks[i].symbol_path,
                    "file": chunks[i].file,
                    "source_snippet": chunks[i].source[:300],
                }
                for i in chapter_chunk_indices[:20]  # sample for LLM
            ],
            cluster_id=chapter_idx,
        )

        try:
            chapter_proposals = propose_features_for_cluster(
                cluster=chapter_input,
                existing_feature_summaries=running_summaries,
                repo_name=repo_name,
            )
        except Exception as exc:
            print(
                f"[bootstrap] chapter {chapter_idx}: LLM call failed — {exc}",
                file=sys.stderr,
            )
            chapter_proposals = []

        # Emit one chapter-level feature (first proposal from the LLM).
        chapter_uuid: str | None = None
        if chapter_proposals:
            cp = chapter_proposals[0]
            try:
                tx = emit_introduce_proposal(
                    proposal=cp, chunks=chunks, tx_log=tx_log,
                    language_adapters=language_adapters,
                    parent_uuid=None,
                )
                all_transactions.append(tx)
                chapter_uuid = tx.hlc.to_str()  # placeholder; real UUID assigned on accept
                running_summaries.append({"slug": cp.slug, "title": cp.title, "intent": cp.intent})
            except Exception as exc:
                print(f"[bootstrap] chapter {chapter_idx}: emit failed — {exc}", file=sys.stderr)

        # Emit leaf proposals for each section, parented under the chapter.
        for section_idx, section_indices in enumerate(sections):
            if not section_indices:
                continue
            section_input = ClusterInput(
                chunks=[
                    {
                        "symbol_path": chunks[i].symbol_path,
                        "file": chunks[i].file,
                        "source_snippet": chunks[i].source[:300],
                    }
                    for i in section_indices
                ],
                cluster_id=section_idx,
            )

            try:
                leaf_proposals = propose_features_for_cluster(
                    cluster=section_input,
                    existing_feature_summaries=running_summaries,
                    repo_name=repo_name,
                )
            except Exception as exc:
                print(
                    f"[bootstrap] chapter {chapter_idx} section {section_idx}: LLM failed — {exc}",
                    file=sys.stderr,
                )
                continue

            for proposal in leaf_proposals:
                try:
                    tx = emit_introduce_proposal(
                        proposal=proposal, chunks=chunks, tx_log=tx_log,
                        language_adapters=language_adapters,
                        parent_uuid=chapter_uuid,
                    )
                    all_transactions.append(tx)
                    running_summaries.append({"slug": proposal.slug, "title": proposal.title, "intent": proposal.intent})
                except Exception as exc:
                    print(f"[bootstrap] section emit failed — {exc}", file=sys.stderr)

    return all_transactions
