"""codoc.pipelines.intentional.merge — MERGE transaction handler (Phase 2).

Merges multiple source features into a new target.  All bindings move to the
target; sources are retired.  Emits a single MERGE meta-transaction and the
cascade obligations enumerated by CascadeEnumerator.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone

from codoc.core.cascade import CascadeEnumerator
from codoc.core.log import TransactionLog
from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.model.obligation import Obligation
from codoc.model.transaction import Transaction, TransactionKind
from codoc.storage.jsonl_log import JSONLLog
from codoc.storage.sqlite_store import SQLiteStore


def _new_uuid() -> str:
    try:
        import uuid_utils  # type: ignore[import]
        return str(uuid_utils.uuid7())
    except ImportError:
        return str(_uuid.uuid4())


def merge_features(
    source_uuids: list[str],
    target_slug: str,
    target_intent: str,
    store: SQLiteStore,
    tx_log: TransactionLog,
    jsonl_log: JSONLLog,
    author: str = "user",
    binding_graph: dict[str, set[str]] | None = None,
) -> tuple[Transaction, list[Obligation]]:
    """Merge *source_uuids* into a fresh target feature.

    - Creates a new feature with the supplied slug/intent.
    - Reattributes every binding currently on any source feature to the target.
    - Retires every source feature.
    - Emits a single MERGE meta-transaction.
    """
    if not source_uuids:
        raise ValueError("source_uuids must contain at least one feature uuid")
    if len(set(source_uuids)) != len(source_uuids):
        raise ValueError("source_uuids must be unique")

    sources: list[Feature] = []
    for suid in source_uuids:
        feature = store.get_feature(suid)
        if feature is None:
            raise ValueError(f"Feature {suid!r} not found")
        if feature.retired:
            raise ValueError(f"Feature {suid!r} is already retired")
        sources.append(feature)

    if not target_slug:
        raise ValueError("target_slug must be non-empty")

    parent_uuids = {f.parent_uuid for f in sources}
    parent_uuid = next(iter(parent_uuids)) if len(parent_uuids) == 1 else None

    hlc = tx_log._tick()
    parent_hlc = tx_log.head_hlc()
    parent_hlcs: list[HLC] = [parent_hlc] if parent_hlc is not None else []

    target_uuid = _new_uuid()
    target = Feature(
        uuid=target_uuid,
        slug=target_slug,
        parent_uuid=parent_uuid,
        intent=target_intent,
        retired=False,
        created_at_hlc=hlc,
        updated_at_hlc=hlc,
    )
    store.upsert_feature(target)

    moved_binding_uuids: list[str] = []
    for source in sources:
        for binding in store.list_bindings(source.uuid):
            store.upsert_binding(
                binding.model_copy(update={"feature_uuid": target_uuid})
            )
            moved_binding_uuids.append(binding.uuid)
        retired = source.model_copy(update={"retired": True, "updated_at_hlc": hlc})
        store.upsert_feature(retired)

    payload = {
        "source_uuids": list(source_uuids),
        "target_uuid": target_uuid,
        "target_slug": target_slug,
        "target_intent": target_intent,
        "moved_binding_uuids": moved_binding_uuids,
        "child_kinds": [
            TransactionKind.INTRODUCE.value,
            *([TransactionKind.RETIRE_REFLECTIVE.value] * len(source_uuids)),
        ],
    }
    tx = Transaction(
        hlc=hlc,
        parent_hlcs=parent_hlcs,
        kind=TransactionKind.MERGE,
        payload=payload,
        author=author,
        proposal=False,
        accepted_at=datetime.now(timezone.utc),
    )
    committed = tx_log.append(tx)
    jsonl_log.append(committed)

    enumerator = CascadeEnumerator(store, binding_graph=binding_graph)
    obligations = enumerator.enumerate_for_merge(
        source_uuids=list(source_uuids),
        target_uuid=target_uuid,
        triggered_by_hlc=committed.hlc,
    )
    for obligation in obligations:
        store.upsert_obligation(obligation)

    return committed, obligations
