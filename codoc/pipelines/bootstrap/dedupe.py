"""Post-hoc deduplication of bootstrap INTRODUCE proposals.

After propose_recursive() completes, the full proposal set may still contain
near-duplicate features whose LLM calls independently coined similar slugs.
This module:

  1. Embeds each proposal's (title + description).
  2. Detects near-duplicates within the same parent by cosine similarity ≥ threshold.
  3. Merges duplicate proposals by keeping the higher-level one and re-attributing
     the other's candidate_bindings.
  4. Enforces global slug uniqueness by suffixing with parent slug where needed.
"""

from __future__ import annotations

import sys

from codoc.core.log import TransactionLog
from codoc.model.transaction import Transaction, TransactionKind
from codoc.storage.sqlite_store import SQLiteStore


def _cosine_sim(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def dedup_proposals(
    proposals: list[Transaction],
    similarity_threshold: float = 0.92,
) -> list[Transaction]:
    """Remove near-duplicate INTRODUCE proposals within the same parent.

    Parameters
    ----------
    proposals:
        All INTRODUCE proposal transactions from a bootstrap run.
    similarity_threshold:
        Cosine similarity above which two features are considered duplicates.
        0.92 is conservative — only very close paraphrases merge.

    Returns
    -------
    list[Transaction]
        De-duplicated list.  Duplicates are removed; their candidate_bindings
        are merged into the surviving proposal's payload.
    """
    introduce = [tx for tx in proposals if tx.kind == TransactionKind.INTRODUCE]
    other = [tx for tx in proposals if tx.kind != TransactionKind.INTRODUCE]

    if len(introduce) < 2:
        return proposals

    # Embed each proposal's title + description for similarity comparison.
    from codoc.config import embed, get_embedder_config

    config = get_embedder_config()
    texts = [
        f"{tx.payload.get('title', tx.payload.get('slug', ''))} "
        f"{tx.payload.get('description', tx.payload.get('intent', ''))}"
        for tx in introduce
    ]

    try:
        vectors = embed(texts, config)
    except Exception as exc:
        print(f"[dedupe] embedding failed, skipping dedup: {exc}", file=sys.stderr)
        return proposals

    # Group proposals by parent_uuid for sibling-level dedup.
    by_parent: dict[str | None, list[int]] = {}
    for i, tx in enumerate(introduce):
        parent = tx.payload.get("parent_uuid")
        by_parent.setdefault(parent, []).append(i)

    removed: set[int] = set()

    for parent_key, indices in by_parent.items():
        if len(indices) < 2:
            continue
        for i_pos, i in enumerate(indices):
            if i in removed:
                continue
            for j in indices[i_pos + 1 :]:
                if j in removed:
                    continue
                sim = _cosine_sim(vectors[i], vectors[j])
                if sim >= similarity_threshold:
                    # Keep i (earlier / higher-level), absorb j's bindings.
                    survivor_payload = dict(introduce[i].payload)
                    victim_bindings = introduce[j].payload.get("candidate_bindings", [])
                    existing_bindings = survivor_payload.get("candidate_bindings", [])
                    survivor_payload["candidate_bindings"] = existing_bindings + victim_bindings
                    introduce[i] = introduce[i].model_copy(
                        update={"payload": survivor_payload}
                    )
                    removed.add(j)
                    print(
                        f"[dedupe] merged '{introduce[j].payload.get('slug')}' "
                        f"into '{introduce[i].payload.get('slug')}' (sim={sim:.3f})",
                        file=sys.stderr,
                    )

    kept = [tx for i, tx in enumerate(introduce) if i not in removed]

    # Enforce global slug uniqueness.
    seen_slugs: dict[str, int] = {}
    result: list[Transaction] = []
    for tx in kept:
        slug = tx.payload.get("slug", "feature")
        if slug in seen_slugs:
            parent_slug = tx.payload.get("parent_uuid", "")[:8]
            new_slug = f"{slug}-{parent_slug}" if parent_slug else f"{slug}-{seen_slugs[slug]}"
            payload = dict(tx.payload)
            payload["slug"] = new_slug
            tx = tx.model_copy(update={"payload": payload})
        seen_slugs[slug] = seen_slugs.get(slug, 0) + 1
        result.append(tx)

    return result + other
