"""codoc.pipelines.intentional.split — SPLIT transaction handler (Phase 2).

Splits one feature into two children, retiring the original.  Emits a single
SPLIT meta-transaction (per Q9) and a list of pending Obligations enumerated
by CascadeEnumerator for the cascade reconciliation pass.
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


def split_feature(
    feature_uuid: str,
    child_a_slug: str,
    child_a_intent: str,
    child_a_binding_uuids: list[str],
    child_b_slug: str,
    child_b_intent: str,
    child_b_binding_uuids: list[str],
    store: SQLiteStore,
    tx_log: TransactionLog,
    jsonl_log: JSONLLog,
    author: str = "user",
    binding_graph: dict[str, set[str]] | None = None,
) -> tuple[Transaction, list[Obligation]]:
    """Split *feature_uuid* into two new children.

    - Creates two new features with fresh UUIDs.
    - Reattributes ``child_a_binding_uuids`` to child A and
      ``child_b_binding_uuids`` to child B.
    - Marks the original feature as retired.
    - Emits a single SPLIT meta-transaction (atomic accept per Q9).
    - Returns the cascade obligations enumerated by CascadeEnumerator (caller
      writes them to storage as needed).
    """
    feature = store.get_feature(feature_uuid)
    if feature is None:
        raise ValueError(f"Feature {feature_uuid!r} not found")
    if feature.retired:
        raise ValueError(f"Feature {feature_uuid!r} is retired and cannot be split")

    if not child_a_slug or not child_b_slug:
        raise ValueError("Both child slugs must be non-empty")
    if child_a_slug == child_b_slug:
        raise ValueError("Child slugs must differ")

    all_binding_uuids = set(child_a_binding_uuids) | set(child_b_binding_uuids)
    overlap = set(child_a_binding_uuids) & set(child_b_binding_uuids)
    if overlap:
        raise ValueError(
            f"Bindings {sorted(overlap)} cannot be assigned to both children"
        )

    existing_bindings = {b.uuid: b for b in store.list_bindings(feature_uuid)}
    for buid in all_binding_uuids:
        if buid not in existing_bindings:
            raise ValueError(
                f"Binding {buid!r} is not attached to feature {feature_uuid!r}"
            )

    hlc = tx_log._tick()
    parent_hlc = tx_log.head_hlc()
    parent_hlcs: list[HLC] = [parent_hlc] if parent_hlc is not None else []

    child_a_uuid = _new_uuid()
    child_b_uuid = _new_uuid()

    child_a = Feature(
        uuid=child_a_uuid,
        slug=child_a_slug,
        parent_uuid=feature.parent_uuid,
        intent=child_a_intent,
        retired=False,
        created_at_hlc=hlc,
        updated_at_hlc=hlc,
    )
    child_b = Feature(
        uuid=child_b_uuid,
        slug=child_b_slug,
        parent_uuid=feature.parent_uuid,
        intent=child_b_intent,
        retired=False,
        created_at_hlc=hlc,
        updated_at_hlc=hlc,
    )
    store.upsert_feature(child_a)
    store.upsert_feature(child_b)

    for buid in child_a_binding_uuids:
        binding = existing_bindings[buid]
        store.upsert_binding(binding.model_copy(update={"feature_uuid": child_a_uuid}))
    for buid in child_b_binding_uuids:
        binding = existing_bindings[buid]
        store.upsert_binding(binding.model_copy(update={"feature_uuid": child_b_uuid}))

    retired_feature = feature.model_copy(update={"retired": True, "updated_at_hlc": hlc})
    store.upsert_feature(retired_feature)

    payload = {
        "feature_uuid": feature_uuid,
        "child_a": {
            "uuid": child_a_uuid,
            "slug": child_a_slug,
            "intent": child_a_intent,
            "binding_uuids": list(child_a_binding_uuids),
        },
        "child_b": {
            "uuid": child_b_uuid,
            "slug": child_b_slug,
            "intent": child_b_intent,
            "binding_uuids": list(child_b_binding_uuids),
        },
        "child_kinds": [
            TransactionKind.INTRODUCE.value,
            TransactionKind.INTRODUCE.value,
            TransactionKind.RETIRE_REFLECTIVE.value,
        ],
    }
    tx = Transaction(
        hlc=hlc,
        parent_hlcs=parent_hlcs,
        kind=TransactionKind.SPLIT,
        payload=payload,
        author=author,
        proposal=False,
        accepted_at=datetime.now(timezone.utc),
    )
    committed = tx_log.append(tx)
    jsonl_log.append(committed)

    enumerator = CascadeEnumerator(store, binding_graph=binding_graph)
    obligations = enumerator.enumerate_for_split(
        feature_uuid=feature_uuid,
        child_a_uuid=child_a_uuid,
        child_b_uuid=child_b_uuid,
        triggered_by_hlc=committed.hlc,
    )
    for obligation in obligations:
        store.upsert_obligation(obligation)

    return committed, obligations
