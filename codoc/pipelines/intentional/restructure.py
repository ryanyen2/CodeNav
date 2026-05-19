"""codoc.pipelines.intentional.restructure — RESTRUCTURE transaction handler (Phase 2).

Changes a feature's parent in the tree.  Emits a single RESTRUCTURE
meta-transaction; cascade obligations are enumerated for the affected subtree
(tree-structural recursion per Q8) plus 1-hop binding-graph neighbours.
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


def _would_create_cycle(
    feature_uuid: str,
    new_parent_uuid: str,
    store: SQLiteStore,
) -> bool:
    """True iff *new_parent_uuid* is *feature_uuid* itself or one of its descendants."""
    if new_parent_uuid == feature_uuid:
        return True
    cursor: str | None = new_parent_uuid
    seen: set[str] = set()
    while cursor is not None and cursor not in seen:
        seen.add(cursor)
        if cursor == feature_uuid:
            return True
        node = store.get_feature(cursor)
        if node is None:
            return False
        cursor = node.parent_uuid
    return False


def restructure_feature(
    feature_uuid: str,
    new_parent_uuid: str | None,
    store: SQLiteStore,
    tx_log: TransactionLog,
    jsonl_log: JSONLLog,
    author: str = "user",
    binding_graph: dict[str, set[str]] | None = None,
) -> tuple[Transaction, list[Obligation]]:
    """Move *feature_uuid* under *new_parent_uuid* (None → root).

    Validates that the move does not create a cycle.  Emits cascade
    obligations for the moved feature, every descendant, and 1-hop
    binding-graph neighbours.
    """
    feature = store.get_feature(feature_uuid)
    if feature is None:
        raise ValueError(f"Feature {feature_uuid!r} not found")
    if feature.retired:
        raise ValueError(f"Feature {feature_uuid!r} is retired")

    if new_parent_uuid == feature.parent_uuid:
        raise ValueError(
            f"Feature {feature_uuid!r} already has parent {new_parent_uuid!r}; nothing to restructure"
        )

    if new_parent_uuid is not None:
        new_parent = store.get_feature(new_parent_uuid)
        if new_parent is None:
            raise ValueError(f"New parent feature {new_parent_uuid!r} not found")
        if new_parent.retired:
            raise ValueError(f"New parent feature {new_parent_uuid!r} is retired")
        if _would_create_cycle(feature_uuid, new_parent_uuid, store):
            raise ValueError(
                f"Restructuring would create a cycle: {new_parent_uuid!r} is a descendant of {feature_uuid!r}"
            )

    hlc = tx_log._tick()
    parent_hlc = tx_log.head_hlc()
    parent_hlcs: list[HLC] = [parent_hlc] if parent_hlc is not None else []

    updated = feature.model_copy(
        update={"parent_uuid": new_parent_uuid, "updated_at_hlc": hlc}
    )
    store.upsert_feature(updated)

    payload = {
        "feature_uuid": feature_uuid,
        "old_parent_uuid": feature.parent_uuid,
        "new_parent_uuid": new_parent_uuid,
    }
    tx = Transaction(
        hlc=hlc,
        parent_hlcs=parent_hlcs,
        kind=TransactionKind.RESTRUCTURE,
        payload=payload,
        author=author,
        proposal=False,
        accepted_at=datetime.now(timezone.utc),
    )
    committed = tx_log.append(tx)
    jsonl_log.append(committed)

    enumerator = CascadeEnumerator(store, binding_graph=binding_graph)
    obligations = enumerator.enumerate_for_restructure(
        feature_uuid=feature_uuid,
        new_parent_uuid=new_parent_uuid,
        triggered_by_hlc=committed.hlc,
    )
    for obligation in obligations:
        store.upsert_obligation(obligation)

    return committed, obligations
