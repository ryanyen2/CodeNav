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


def propose_structural(
    root_group,
    chunks: list[Chunk],
    tx_log: TransactionLog,
    language_adapters: dict | None = None,
    with_intent: bool = False,
    repo_name: str = "codebase",
    *,
    _parent_uuid: str | None = None,
    _results: list | None = None,
) -> list[Transaction]:
    """Emit INTRODUCE proposals from a structure-derived StructuralGroup tree.

    Default path (``with_intent=False``): zero LLM calls.  Slug and title come
    from the directory/class name; intent is empty and the user fills it in
    via ``codoc edit`` or ``codoc plan``.

    ``with_intent=True``: batch-calls the LLM with 5 groups per prompt to
    generate a 1-2 sentence intent for each feature before emitting.

    Parameters
    ----------
    root_group:
        Root :class:`~codoc.pipelines.bootstrap.directory_grouping.StructuralGroup`
        (level=0).  Non-root children become INTRODUCE proposals.
    chunks:
        All extracted chunks (indexed by position).
    tx_log:
        Transaction log for proposal emission.
    language_adapters:
        Mapping ``{language: adapter}`` for fingerprinting.
    with_intent:
        If True, batch-call LLM to generate intent text before emitting.
    repo_name:
        Human-readable repo name forwarded to the LLM when with_intent=True.
    """
    import uuid as _uuid

    if language_adapters is None:
        language_adapters = {}
    if _results is None:
        _results = []

    # Walk all non-root children of root_group.
    for group in root_group.children:
        if not group.chunk_indices:
            continue

        provisional_uuid = str(_uuid.uuid4())

        # Compute candidate_bindings for this group (fingerprinted chunks).
        candidate_bindings = _structural_candidate_bindings(
            group.chunk_indices, chunks, language_adapters
        )
        if not candidate_bindings:
            continue

        intent_text = ""
        if with_intent:
            intent_text = _fetch_group_intent(group, chunks, repo_name)

        payload: dict = {
            "slug": group.slug,
            "title": group.title,
            "intent": intent_text,
            "description": "",
            "provisional_uuid": provisional_uuid,
            "candidate_bindings": candidate_bindings,
        }
        if _parent_uuid is not None:
            payload["parent_uuid"] = _parent_uuid

        hlc = HLC.now(node_id="bootstrap")
        tx = Transaction(
            hlc=hlc,
            parent_hlcs=[],
            kind=TransactionKind.INTRODUCE,
            payload=payload,
            author="bootstrap",
            proposal=True,
        )
        stamped = tx_log.append_proposal(tx)
        _results.append(stamped)

        # Recurse into children using this group's provisional UUID as parent.
        if group.children:
            propose_structural(
                root_group=group,
                chunks=chunks,
                tx_log=tx_log,
                language_adapters=language_adapters,
                with_intent=with_intent,
                repo_name=repo_name,
                _parent_uuid=provisional_uuid,
                _results=_results,
            )

    return _results


def _structural_candidate_bindings(
    chunk_indices: list[int],
    chunks: list[Chunk],
    language_adapters: dict,
) -> list[dict]:
    """Build candidate_bindings list for the given chunk indices."""
    from codoc.lang import detect_language

    result: list[dict] = []
    for i in chunk_indices:
        chunk = chunks[i]
        language = detect_language(chunk.file)
        adapter = language_adapters.get(language) if language else None
        if adapter is None:
            continue
        try:
            fp = fingerprint_chunk(chunk.source, adapter)
        except Exception as exc:
            _log.warning("structural.fingerprint_failed %s: %s", chunk.symbol_path, exc)
            continue
        result.append({
            "anchor": {"file": chunk.file, "symbol_path": chunk.symbol_path},
            "fingerprint": fp,
        })
    return result


def _fetch_group_intent(group, chunks: list[Chunk], repo_name: str) -> str:
    """Call LLM to generate a 1-2 sentence intent for *group*.

    Falls back to an empty string on any failure.  The prompt is minimal:
    slug + title + up to 5 representative symbol_paths + file.
    """
    from codoc.agents.base import get_client
    from codoc.config import get_llm_config

    sample = group.chunk_indices[:5]
    symbols = [
        f"  {chunks[i].symbol_path} ({chunks[i].file})"
        for i in sample
    ]
    prompt = (
        f"You are helping document a codebase called '{repo_name}'.\n"
        f"Feature slug: {group.slug}\n"
        f"Feature title: {group.title}\n"
        f"Representative code chunks:\n" + "\n".join(symbols) + "\n\n"
        "Write a single sentence (max 120 chars) describing what this feature is "
        "responsible for. Output only the sentence, no quotes, no punctuation at end."
    )
    try:
        client = get_client()
        config = get_llm_config()
        response = client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        _log.warning("structural.intent_llm_failed %s: %s", group.slug, exc)
        return ""

