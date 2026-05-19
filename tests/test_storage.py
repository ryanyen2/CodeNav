"""Tests for SQLiteStore and JSONLLog round-trip."""

from __future__ import annotations

import uuid as _uuid
from pathlib import Path

import pytest

from codoc.core.log import TransactionLog
from codoc.model.anchor import Anchor
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.model.transaction import Transaction, TransactionKind
from codoc.storage.jsonl_log import JSONLLog
from codoc.storage.sqlite_store import SQLiteStore


def test_feature_round_trip(tmp_store: SQLiteStore, make_feature) -> None:
    feature = make_feature(slug="round-trip", intent="Test feature.")
    tmp_store.upsert_feature(feature)
    fetched = tmp_store.get_feature(feature.uuid)
    assert fetched is not None
    assert fetched.uuid == feature.uuid
    assert fetched.slug == feature.slug
    assert fetched.intent == feature.intent
    assert fetched.retired is False
    assert fetched.created_at_hlc == feature.created_at_hlc


def test_feature_upsert_updates_existing(tmp_store: SQLiteStore, make_feature) -> None:
    feature = make_feature(slug="orig")
    tmp_store.upsert_feature(feature)
    new_hlc = feature.updated_at_hlc.advance()
    updated = feature.model_copy(update={"slug": "renamed", "updated_at_hlc": new_hlc})
    tmp_store.upsert_feature(updated)
    fetched = tmp_store.get_feature(feature.uuid)
    assert fetched is not None
    assert fetched.slug == "renamed"


def test_list_features_root_filter(tmp_store: SQLiteStore, make_feature) -> None:
    parent = make_feature(slug="parent", uuid="parent-id")
    child = make_feature(slug="child", uuid="child-id", parent_uuid="parent-id")
    tmp_store.upsert_feature(parent)
    tmp_store.upsert_feature(child)
    roots = tmp_store.list_features(parent_uuid="")
    root_ids = {f.uuid for f in roots}
    assert "parent-id" in root_ids
    assert "child-id" not in root_ids


def test_binding_round_trip(tmp_store: SQLiteStore, make_binding) -> None:
    binding = make_binding(feature_uuid="feat-1")
    tmp_store.upsert_binding(binding)
    fetched = tmp_store.get_binding(binding.uuid)
    assert fetched is not None
    assert fetched.uuid == binding.uuid
    assert fetched.feature_uuid == binding.feature_uuid
    assert fetched.anchor.symbol_path == binding.anchor.symbol_path
    assert fetched.fingerprint == binding.fingerprint


def test_transaction_round_trip(tmp_store: SQLiteStore, hlc_now: HLC) -> None:
    tx = Transaction(
        hlc=hlc_now,
        parent_hlcs=[],
        kind=TransactionKind.AMEND,
        payload={"feature_uuid": "feat-1", "new_intent": "Updated."},
        author="user",
        proposal=False,
    )
    tmp_store.write_transaction(tx)
    fetched = tmp_store.get_transaction(hlc_now.to_str())
    assert fetched is not None
    assert fetched.hlc == hlc_now
    assert fetched.kind == TransactionKind.AMEND
    assert fetched.payload["feature_uuid"] == "feat-1"


def test_list_transactions_filter_by_proposal(tmp_store: SQLiteStore) -> None:
    h1 = HLC(logical_time=0, wall_clock=1, node_id="n")
    h2 = HLC(logical_time=0, wall_clock=2, node_id="n")
    proposal = Transaction(
        hlc=h1,
        parent_hlcs=[],
        kind=TransactionKind.INTRODUCE,
        payload={"feature_uuid": "f1"},
        author="reflective",
        proposal=True,
    )
    accepted = Transaction(
        hlc=h2,
        parent_hlcs=[],
        kind=TransactionKind.AMEND,
        payload={"feature_uuid": "f2"},
        author="user",
        proposal=False,
    )
    tmp_store.write_transaction(proposal)
    tmp_store.write_transaction(accepted)
    pending = tmp_store.list_transactions(proposal=True)
    assert len(pending) == 1
    assert pending[0].hlc == h1
    accepted_only = tmp_store.list_transactions(proposal=False)
    assert len(accepted_only) == 1
    assert accepted_only[0].hlc == h2


def test_jsonl_log_round_trip(tmp_path: Path) -> None:
    log_path = tmp_path / "log.jsonl"
    log = JSONLLog(str(log_path))
    h = HLC(logical_time=0, wall_clock=42, node_id="n")
    tx = Transaction(
        hlc=h,
        parent_hlcs=[],
        kind=TransactionKind.AMEND,
        payload={"feature_uuid": "f1", "new_intent": "Hello"},
        author="user",
        proposal=False,
    )
    log.append(tx)
    read = log.read_all()
    assert len(read) == 1
    assert read[0].hlc == h
    assert read[0].kind == TransactionKind.AMEND


def test_jsonl_to_sqlite_rebuild(tmp_path: Path) -> None:
    """Write SQLite, export to JSONL, rebuild a fresh SQLite, verify equivalence."""
    src_db = tmp_path / "src.db"
    dst_db = tmp_path / "dst.db"
    log_path = tmp_path / "log.jsonl"

    src_store = SQLiteStore(str(src_db))
    src_store.open()
    log = JSONLLog(str(log_path))

    txs = []
    for i in range(3):
        h = HLC(logical_time=0, wall_clock=i + 1, node_id="n")
        tx = Transaction(
            hlc=h,
            parent_hlcs=[],
            kind=TransactionKind.AMEND,
            payload={"feature_uuid": f"f-{i}", "new_intent": f"intent {i}"},
            author="user",
            proposal=False,
        )
        src_store.write_transaction(tx)
        log.append(tx)
        txs.append(tx)
    src_store.close()

    # Rebuild dst from JSONL.
    dst_store = SQLiteStore(str(dst_db))
    dst_store.open()
    count = log.rebuild_sqlite(dst_store)
    assert count == 3

    # Verify equivalence.
    for tx in txs:
        rebuilt = dst_store.get_transaction(tx.hlc.to_str())
        assert rebuilt is not None
        assert rebuilt.kind == tx.kind
        assert rebuilt.payload == tx.payload
        assert rebuilt.author == tx.author
    dst_store.close()


def test_obligation_round_trip(tmp_store: SQLiteStore, hlc_now: HLC) -> None:
    from codoc.model.obligation import Obligation, ObligationKind

    o = Obligation(
        uuid=str(_uuid.uuid4()),
        kind=ObligationKind.RECONCILE_PROSE,
        feature_uuid="feat-1",
        triggered_by_tx_hlc=hlc_now,
        context_hash="0" * 64,
        expected_output_schema="prose_patch",
        context={"foo": "bar"},
        status="pending",
    )
    tmp_store.upsert_obligation(o)
    fetched = tmp_store.get_obligation(o.uuid)
    assert fetched is not None
    assert fetched.kind == ObligationKind.RECONCILE_PROSE
    assert fetched.context == {"foo": "bar"}
    tmp_store.update_obligation_status(o.uuid, "resolved", result={"intent": "new"})
    refetched = tmp_store.get_obligation(o.uuid)
    assert refetched is not None
    assert refetched.status == "resolved"
    assert refetched.result == {"intent": "new"}
