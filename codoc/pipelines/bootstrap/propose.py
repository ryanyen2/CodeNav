"""
codoc.pipelines.bootstrap.propose — build INTRODUCE proposals from structural grouping.

Phase 2 of bootstrap:
  For each StructuralGroup, emit one INTRODUCE proposal transaction.
  Optionally calls the LLM (once per group) to generate intent text.
"""

from __future__ import annotations

import sys

from codoc.core.logging import get_logger
from codoc.lang import Chunk

_log = get_logger(__name__)
from codoc.agents.bootstrap_clustering import (
    FeatureProposal,
    build_introduce_payload,
)
from codoc.model.transaction import Transaction, TransactionKind
from codoc.model.hlc import HLC
from codoc.core.log import TransactionLog
from codoc.core.fingerprint import fingerprint_chunk


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

        if adapter is None:
            _log.warning("bootstrap.no_adapter_for_chunk %s (file=%s) — skipping", symbol_path, chunk.file)
            continue
        try:
            fp = fingerprint_chunk(chunk.source, adapter)
        except Exception as exc:
            _log.warning("bootstrap.fingerprint_failed %s: %s", symbol_path, exc)
            continue

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
        "purpose": proposal.purpose,
        "rationale": proposal.rationale,
        "scenario": proposal.scenario,
        "needs": proposal.needs,
        "intent": proposal.intent or proposal.purpose,  # backward compat
        "description": proposal.description,            # backward compat
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


