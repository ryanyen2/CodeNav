"""codoc.pipelines.intentional.retire — RETIRE transaction handler.

Marks a feature as retired (sets retired=True). Phase 1 intentional
operation: no cascade, committed directly to the log without a
proposal/review step. The feature stays in the tree with Deprecated state;
bindings are not deleted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from codoc.model.feature import Feature
from codoc.model.transaction import Transaction, TransactionKind
from codoc.model.hlc import HLC
from codoc.storage.sqlite_store import SQLiteStore
from codoc.storage.jsonl_log import JSONLLog
from codoc.core.log import TransactionLog


def retire_feature(
    feature_uuid: str,
    store: SQLiteStore,
    tx_log: TransactionLog,
    jsonl_log: JSONLLog,
    author: str = "user",
) -> Transaction:
    """Mark a feature as retired.

    Validates:
    - feature_uuid must exist
    - feature must not already be retired

    Applies:
    - Sets feature.retired = True in store
    - Updates feature.updated_at_hlc
    - Writes RETIRE transaction to log and JSONL
    - Does NOT delete bindings — feature stays in the tree with Deprecated state.

    Returns the committed Transaction.
    """
    # --- Validate inputs ---
    feature = store.get_feature(feature_uuid)
    if feature is None:
        raise ValueError(f"Feature {feature_uuid!r} not found")

    if feature.retired:
        raise ValueError(f"Feature {feature_uuid!r} is already retired")

    # --- Tick HLC and build transaction ---
    hlc = tx_log._tick()
    parent_hlc = tx_log.head_hlc()
    parent_hlcs: list[HLC] = [parent_hlc] if parent_hlc is not None else []

    tx = Transaction(
        hlc=hlc,
        parent_hlcs=parent_hlcs,
        kind=TransactionKind.RETIRE,
        payload={
            "feature_uuid": feature_uuid,
        },
        author=author,
        proposal=False,
        accepted_at=datetime.now(timezone.utc),
    )

    # --- Apply mutation to feature store ---
    updated_feature = feature.model_copy(
        update={
            "retired": True,
            "updated_at_hlc": hlc,
        }
    )
    store.upsert_feature(updated_feature)

    # --- Commit transaction to log ---
    committed = tx_log.append(tx)

    # --- Append to JSONL audit log ---
    jsonl_log.append(committed)

    return committed
