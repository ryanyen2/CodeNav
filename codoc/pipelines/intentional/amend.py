"""codoc.pipelines.intentional.amend — AMEND transaction handler.

Edits a feature's intent prose. Phase 1 intentional operation: no cascade,
no agent dispatch — pure structural write to the feature store, committed
directly to the log (no proposal/review step).
"""

from __future__ import annotations

from datetime import datetime, timezone

from codoc.model.feature import Feature
from codoc.model.transaction import Transaction, TransactionKind
from codoc.model.hlc import HLC
from codoc.storage.sqlite_store import SQLiteStore
from codoc.storage.jsonl_log import JSONLLog
from codoc.core.log import TransactionLog


def amend_feature(
    feature_uuid: str,
    new_intent: str,
    store: SQLiteStore,
    tx_log: TransactionLog,
    jsonl_log: JSONLLog,
    author: str = "user",
) -> Transaction:
    """Edit a feature's intent prose.

    Validates:
    - feature_uuid must exist in store
    - new_intent must be a non-empty string

    Applies:
    - Updates feature.intent in store
    - Updates feature.updated_at_hlc
    - Writes AMEND transaction to log and JSONL

    Returns the committed Transaction.
    """
    # --- Validate inputs ---
    feature = store.get_feature(feature_uuid)
    if feature is None:
        raise ValueError(f"Feature {feature_uuid!r} not found")

    if not isinstance(new_intent, str) or not new_intent.strip():
        raise ValueError("new_intent must be a non-empty string")

    # --- Tick HLC and build transaction ---
    hlc = tx_log._tick()
    parent_hlc = tx_log.head_hlc()
    parent_hlcs: list[HLC] = [parent_hlc] if parent_hlc is not None else []

    tx = Transaction(
        hlc=hlc,
        parent_hlcs=parent_hlcs,
        kind=TransactionKind.AMEND,
        payload={
            "feature_uuid": feature_uuid,
            "old_intent": feature.intent,
            "new_intent": new_intent,
        },
        author=author,
        proposal=False,
        accepted_at=datetime.now(timezone.utc),
    )

    # --- Apply mutation to feature store ---
    updated_feature = feature.model_copy(
        update={
            "intent": new_intent,
            "updated_at_hlc": hlc,
        }
    )
    store.upsert_feature(updated_feature)

    # --- Commit transaction to log ---
    committed = tx_log.append(tx)

    # --- Append to JSONL audit log ---
    jsonl_log.append(committed)

    return committed
