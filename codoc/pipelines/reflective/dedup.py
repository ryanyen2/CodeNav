"""
codoc.pipelines.reflective.dedup — post-LLM proposal deduplication.

Runs after the per-chunk escalation loop to collapse proposals that refer to
the same code.  Three gates:

  1. Slug similarity: Levenshtein ratio >= 0.70 between two proposal slugs.
  2. Intent embedding cosine >= 0.85: proposals with near-identical intents
     are merged (the later one is dropped; candidate_bindings are unioned).
  3. Against-store: a proposal whose intent is >= 0.85 cosine vs an existing
     accepted feature is downgraded from INTRODUCE to ABSORB.

All gates are best-effort — if embeddings fail (no sentence-transformers),
only the slug-similarity gate runs.
"""

from __future__ import annotations

import re
from codoc.model.transaction import Transaction, TransactionKind
from codoc.core.logging import get_logger

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Slug similarity (Levenshtein)
# ---------------------------------------------------------------------------


def _levenshtein(a: str, b: str) -> float:
    """Return Levenshtein similarity ratio in [0, 1]."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    # Build edit-distance matrix
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[j] = prev[j - 1]
            else:
                dp[j] = 1 + min(prev[j - 1], prev[j], dp[j - 1])
    dist = dp[n]
    return 1.0 - dist / max(m, n)


# ---------------------------------------------------------------------------
# Intent embedding cosine
# ---------------------------------------------------------------------------


def _embed_text(text: str):
    """Return embedding vector or None on failure."""
    try:
        from codoc.config import embed as _embed
        return _embed(text)
    except Exception:
        return None


def _cosine(a, b) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def dedup_proposals(
    proposals: list[Transaction],
    store,
    slug_threshold: float = 0.60,
    intent_threshold: float = 0.85,
) -> list[Transaction]:
    """Deduplicate and downgrade proposals where appropriate.

    Returns a new list (original list is not mutated).  Each surviving
    Transaction is either unchanged or has its ``candidate_bindings`` merged
    with those from dropped duplicates.
    """
    if len(proposals) <= 1:
        return list(proposals)

    introduce_idxs = [
        i for i, tx in enumerate(proposals)
        if tx.kind == TransactionKind.INTRODUCE
    ]
    other_txs = [tx for tx in proposals if tx.kind != TransactionKind.INTRODUCE]

    if not introduce_idxs:
        return list(proposals)

    # ---- Gate 1: slug dedup (always runs) ----
    introduce_txs = [proposals[i] for i in introduce_idxs]
    kept_introduce = _dedup_by_slug(introduce_txs, slug_threshold)

    # ---- Gate 2: intent embedding dedup (best-effort) ----
    kept_introduce = _dedup_by_intent(kept_introduce, intent_threshold)

    # ---- Gate 3: against-store downgrade (best-effort) ----
    final_introduce, new_absorbs = _downgrade_against_store(kept_introduce, store, intent_threshold)

    return other_txs + final_introduce + new_absorbs


def _dedup_by_slug(
    txs: list[Transaction],
    threshold: float,
) -> list[Transaction]:
    """Merge transactions whose slugs are too similar."""
    kept: list[Transaction] = []
    for tx in txs:
        slug = tx.payload.get("slug", "")
        merged = False
        for kept_tx in kept:
            kept_slug = kept_tx.payload.get("slug", "")
            if _levenshtein(slug, kept_slug) >= threshold:
                # Merge candidate_bindings into kept_tx
                existing_bindings = kept_tx.payload.get("candidate_bindings", [])
                new_bindings = tx.payload.get("candidate_bindings", [])
                merged_bindings = _union_bindings(existing_bindings, new_bindings)
                # Mutate payload (Transaction is not frozen)
                kept_tx.payload["candidate_bindings"] = merged_bindings
                _log.debug("dedup: merged slug '%s' into '%s'", slug, kept_slug)
                merged = True
                break
        if not merged:
            kept.append(tx)
    return kept


def _dedup_by_intent(
    txs: list[Transaction],
    threshold: float,
) -> list[Transaction]:
    """Merge transactions whose intent embeddings are nearly identical."""
    embeddings: list = []
    for tx in txs:
        intent = tx.payload.get("intent", "")
        embeddings.append(_embed_text(intent))

    if all(e is None for e in embeddings):
        return txs  # embeddings unavailable

    kept: list[Transaction] = []
    kept_embeddings: list = []

    for i, tx in enumerate(txs):
        ei = embeddings[i]
        merged = False
        for j, kept_tx in enumerate(kept):
            ej = kept_embeddings[j]
            if ei is not None and ej is not None:
                sim = _cosine(ei, ej)
                if sim >= threshold:
                    existing_bindings = kept_tx.payload.get("candidate_bindings", [])
                    new_bindings = tx.payload.get("candidate_bindings", [])
                    kept_tx.payload["candidate_bindings"] = _union_bindings(existing_bindings, new_bindings)
                    _log.debug(
                        "dedup: merged intent '%s' into '%s' (cosine=%.3f)",
                        tx.payload.get("slug"), kept_tx.payload.get("slug"), sim,
                    )
                    merged = True
                    break
        if not merged:
            kept.append(tx)
            kept_embeddings.append(ei)

    return kept


def _downgrade_against_store(
    txs: list[Transaction],
    store,
    threshold: float,
) -> tuple[list[Transaction], list[Transaction]]:
    """Downgrade INTRODUCE → ABSORB when an existing feature is semantically close."""
    from codoc.model.transaction import TransactionKind
    from codoc.model.hlc import HLC

    try:
        existing_features = store.list_features()
    except Exception:
        return txs, []

    active = [f for f in existing_features if not f.retired]
    if not active:
        return txs, []

    existing_embeddings = {f.uuid: _embed_text(f.intent) for f in active}

    final_introduce: list[Transaction] = []
    new_absorbs: list[Transaction] = []

    for tx in txs:
        intent = tx.payload.get("intent", "")
        ei = _embed_text(intent)
        if ei is None:
            final_introduce.append(tx)
            continue

        best_sim, best_uuid = 0.0, None
        for f in active:
            ef = existing_embeddings.get(f.uuid)
            if ef is not None:
                s = _cosine(ei, ef)
                if s > best_sim:
                    best_sim, best_uuid = s, f.uuid

        if best_sim >= threshold and best_uuid is not None:
            _log.debug(
                "dedup: downgrading INTRODUCE '%s' → ABSORB (cosine=%.3f vs existing %s)",
                tx.payload.get("slug"), best_sim, best_uuid,
            )
            # Build an ABSORB transaction for each candidate binding
            for cb in tx.payload.get("candidate_bindings", []):
                absorb_payload = {
                    "feature_uuid": best_uuid,
                    "symbol_path": cb.get("anchor", {}).get("symbol_path", ""),
                    "file": cb.get("anchor", {}).get("file", ""),
                    "rationale": f"dedup: intent similarity {best_sim:.2f} vs existing feature",
                    "current_fingerprint": cb.get("fingerprint", ""),
                }
                absorb_tx = Transaction(
                    hlc=HLC.now(),
                    parent_hlcs=[],
                    kind=TransactionKind.ABSORB,
                    payload=absorb_payload,
                    author="reflective-dedup",
                    proposal=True,
                )
                new_absorbs.append(absorb_tx)
        else:
            final_introduce.append(tx)

    return final_introduce, new_absorbs


def _union_bindings(a: list[dict], b: list[dict]) -> list[dict]:
    """Union two candidate_bindings lists, deduped by anchor symbol_path."""
    seen: set[str] = set()
    result: list[dict] = []
    for cb in a + b:
        sp = cb.get("anchor", {}).get("symbol_path", "")
        if sp not in seen:
            seen.add(sp)
            result.append(cb)
    return result
