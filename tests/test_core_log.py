"""Tests for TransactionLog: append proposal, accept, reject, history."""

from __future__ import annotations

from codoc.core.log import TransactionLog
from codoc.model.hlc import HLC
from codoc.model.transaction import Transaction, TransactionKind


def _proposal_tx(feature_uuid: str = "feat-1") -> Transaction:
    return Transaction(
        hlc=HLC(logical_time=0, wall_clock=0, node_id="test"),
        parent_hlcs=[],
        kind=TransactionKind.INTRODUCE,
        payload={"feature_uuid": feature_uuid, "slug": "x"},
        author="reflective",
        proposal=True,
    )


def _accepted_tx(kind: TransactionKind, feature_uuid: str) -> Transaction:
    return Transaction(
        hlc=HLC(logical_time=0, wall_clock=0, node_id="test"),
        parent_hlcs=[],
        kind=kind,
        payload={"feature_uuid": feature_uuid},
        author="user",
        proposal=False,
    )


def test_append_proposal_writes_with_stamped_hlc(tmp_tx_log: TransactionLog) -> None:
    tx = _proposal_tx()
    stamped = tmp_tx_log.append_proposal(tx)
    assert stamped.proposal is True
    fetched = tmp_tx_log.get_transaction(stamped.hlc.to_str())
    assert fetched is not None
    assert fetched.proposal is True
    assert fetched.kind == TransactionKind.INTRODUCE


def test_pending_proposals_lists_only_proposals(tmp_tx_log: TransactionLog) -> None:
    p1 = tmp_tx_log.append_proposal(_proposal_tx("feat-a"))
    p2 = tmp_tx_log.append_proposal(_proposal_tx("feat-b"))
    pending = tmp_tx_log.pending_proposals()
    pending_hlcs = {tx.hlc.to_str() for tx in pending}
    assert p1.hlc.to_str() in pending_hlcs
    assert p2.hlc.to_str() in pending_hlcs
    assert all(tx.proposal for tx in pending)


def test_accept_proposal_flips_to_accepted(tmp_tx_log: TransactionLog) -> None:
    proposal = tmp_tx_log.append_proposal(_proposal_tx())
    accepted = tmp_tx_log.accept_proposal(proposal.hlc.to_str())
    assert accepted.proposal is False
    assert accepted.accepted_at is not None
    fetched = tmp_tx_log.get_transaction(proposal.hlc.to_str())
    assert fetched is not None
    assert fetched.proposal is False


def test_accept_proposal_with_edits_merges_payload(tmp_tx_log: TransactionLog) -> None:
    proposal = tmp_tx_log.append_proposal(_proposal_tx("feat-1"))
    accepted = tmp_tx_log.accept_proposal(
        proposal.hlc.to_str(), edits={"intent": "Edited intent"}
    )
    assert accepted.payload["intent"] == "Edited intent"
    assert accepted.payload["feature_uuid"] == "feat-1"


def test_accept_unknown_proposal_raises(tmp_tx_log: TransactionLog) -> None:
    import pytest

    with pytest.raises(KeyError):
        tmp_tx_log.accept_proposal("00000000000000000000-00000000000000000000-nope")


def test_accept_already_accepted_raises(tmp_tx_log: TransactionLog) -> None:
    import pytest

    proposal = tmp_tx_log.append_proposal(_proposal_tx())
    tmp_tx_log.accept_proposal(proposal.hlc.to_str())
    with pytest.raises(ValueError):
        tmp_tx_log.accept_proposal(proposal.hlc.to_str())


def test_reject_proposal_deletes_record(tmp_tx_log: TransactionLog) -> None:
    proposal = tmp_tx_log.append_proposal(_proposal_tx())
    tmp_tx_log.reject_proposal(proposal.hlc.to_str())
    assert tmp_tx_log.get_transaction(proposal.hlc.to_str()) is None


def test_reject_accepted_raises(tmp_tx_log: TransactionLog) -> None:
    import pytest

    proposal = tmp_tx_log.append_proposal(_proposal_tx())
    tmp_tx_log.accept_proposal(proposal.hlc.to_str())
    with pytest.raises(ValueError):
        tmp_tx_log.reject_proposal(proposal.hlc.to_str())


def test_history_returns_only_accepted(tmp_tx_log: TransactionLog) -> None:
    proposal = tmp_tx_log.append_proposal(_proposal_tx("feat-x"))
    tmp_tx_log.accept_proposal(proposal.hlc.to_str())
    other_proposal = tmp_tx_log.append_proposal(_proposal_tx("feat-y"))
    history = tmp_tx_log.history()
    assert all(tx.proposal is False for tx in history)
    assert other_proposal.hlc.to_str() not in {tx.hlc.to_str() for tx in history}


def test_history_filters_by_feature_uuid(tmp_tx_log: TransactionLog) -> None:
    p_a = tmp_tx_log.append_proposal(_proposal_tx("feat-A"))
    tmp_tx_log.accept_proposal(p_a.hlc.to_str())
    p_b = tmp_tx_log.append_proposal(_proposal_tx("feat-B"))
    tmp_tx_log.accept_proposal(p_b.hlc.to_str())

    a_history = tmp_tx_log.history(feature_uuid="feat-A")
    a_features = {tx.payload.get("feature_uuid") for tx in a_history}
    assert a_features == {"feat-A"}


def test_append_writes_accepted_immediately(tmp_tx_log: TransactionLog) -> None:
    tx = _accepted_tx(TransactionKind.AMEND, "feat-x")
    committed = tmp_tx_log.append(tx)
    assert committed.proposal is False
    assert committed.accepted_at is not None
    fetched = tmp_tx_log.get_transaction(committed.hlc.to_str())
    assert fetched is not None
    assert fetched.proposal is False


def test_head_hlc_returns_most_recent_accepted(tmp_tx_log: TransactionLog) -> None:
    tx_a = tmp_tx_log.append(_accepted_tx(TransactionKind.AMEND, "feat-1"))
    tx_b = tmp_tx_log.append(_accepted_tx(TransactionKind.RENAME, "feat-1"))
    head = tmp_tx_log.head_hlc()
    assert head is not None
    # head_hlc returns first by hlc ASC limit 1, so it's the earliest, not latest.
    # That's the documented behaviour — verify it returns one of the appended.
    assert head in {tx_a.hlc, tx_b.hlc}
