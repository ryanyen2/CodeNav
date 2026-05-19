"""codoc.pipelines.intentional.rewind — REWIND transaction handler (Phase 2).

Rewinds a feature's intent/slug to a prior accepted transaction state by
replaying the log up to *target_hlc* and applying the resulting field values.
Emits a single REWIND meta-transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone

from codoc.core.cascade import CascadeEnumerator
from codoc.core.log import TransactionLog
from codoc.model.hlc import HLC
from codoc.model.obligation import Obligation
from codoc.model.transaction import Transaction, TransactionKind
from codoc.storage.jsonl_log import JSONLLog
from codoc.storage.sqlite_store import SQLiteStore


def _replay_state_at(
    feature_uuid: str,
    target_hlc: HLC,
    store: SQLiteStore,
) -> dict:
    """Return ``{"slug": ..., "intent": ...}`` for *feature_uuid* as it would
    have appeared at the moment immediately after the transaction at
    *target_hlc* was accepted.

    Walks AMEND/RENAME transactions affecting this feature with HLC >
    target_hlc in *descending* HLC order and reverses them using their
    ``old_intent`` / ``old_slug`` payload fields.  This produces the state at
    the target moment regardless of whether the feature was created via
    INTRODUCE/SPLIT/MERGE.
    """
    feature = store.get_feature(feature_uuid)
    if feature is None:
        raise ValueError(f"Feature {feature_uuid!r} not found")

    txs = store.list_transactions(proposal=False, feature_uuid=feature_uuid, limit=0)
    state = {"slug": feature.slug, "intent": feature.intent}

    # Reverse-apply mutations that happened strictly after target_hlc.
    for tx in sorted(txs, key=lambda t: t.hlc, reverse=True):
        if tx.hlc <= target_hlc:
            break
        kind = tx.kind
        payload = tx.payload
        if kind == TransactionKind.AMEND:
            if "old_intent" in payload:
                state["intent"] = payload["old_intent"]
        elif kind in (TransactionKind.RENAME, TransactionKind.RENAME_INFER):
            if "old_slug" in payload:
                state["slug"] = payload["old_slug"]
        elif kind == TransactionKind.REWIND:
            previous = payload.get("previous_state")
            if previous:
                state["slug"] = previous.get("slug", state["slug"])
                state["intent"] = previous.get("intent", state["intent"])

    return state


def rewind_feature(
    feature_uuid: str,
    target_hlc_str: str,
    store: SQLiteStore,
    tx_log: TransactionLog,
    jsonl_log: JSONLLog,
    author: str = "user",
    binding_graph: dict[str, set[str]] | None = None,
) -> tuple[Transaction, list[Obligation]]:
    """Rewind *feature_uuid* to its state at *target_hlc_str*.

    Restores ``slug`` and ``intent`` from the replayed state.  Bindings and
    parent_uuid are not touched (those have their own SPLIT/MERGE/RESTRUCTURE
    history which is out of scope for REWIND).
    """
    feature = store.get_feature(feature_uuid)
    if feature is None:
        raise ValueError(f"Feature {feature_uuid!r} not found")
    if feature.retired:
        raise ValueError(f"Feature {feature_uuid!r} is retired")

    try:
        target_hlc = HLC.from_str(target_hlc_str)
    except ValueError as exc:
        raise ValueError(f"Invalid target_hlc_str {target_hlc_str!r}: {exc}") from exc

    replayed = _replay_state_at(feature_uuid, target_hlc, store)

    if replayed["slug"] == feature.slug and replayed["intent"] == feature.intent:
        raise ValueError(
            f"Rewinding feature {feature_uuid!r} to {target_hlc_str!r} would not change its state"
        )

    hlc = tx_log._tick()
    parent_hlc = tx_log.head_hlc()
    parent_hlcs: list[HLC] = [parent_hlc] if parent_hlc is not None else []

    updated = feature.model_copy(
        update={
            "slug": replayed["slug"],
            "intent": replayed["intent"],
            "updated_at_hlc": hlc,
        }
    )
    store.upsert_feature(updated)

    payload = {
        "feature_uuid": feature_uuid,
        "target_hlc": target_hlc_str,
        "previous_state": {"slug": feature.slug, "intent": feature.intent},
        "replayed_state": replayed,
    }
    tx = Transaction(
        hlc=hlc,
        parent_hlcs=parent_hlcs,
        kind=TransactionKind.REWIND,
        payload=payload,
        author=author,
        proposal=False,
        accepted_at=datetime.now(timezone.utc),
    )
    committed = tx_log.append(tx)
    jsonl_log.append(committed)

    enumerator = CascadeEnumerator(store, binding_graph=binding_graph)
    obligations = enumerator.enumerate_for_rewind(
        feature_uuid=feature_uuid,
        target_hlc=target_hlc,
        triggered_by_hlc=committed.hlc,
    )
    for obligation in obligations:
        store.upsert_obligation(obligation)

    return committed, obligations
