"""Append-only transaction log.

The storage layer is injected at construction time as a duck-typed interface
(see the Protocol below for the expected surface).  This keeps ``log.py``
free of any import-time dependency on the concrete SQLite implementation.

Storage interface expected
--------------------------
``write_transaction(tx: Transaction) -> None``
``update_transaction(hlc_str: str, updates: dict) -> None``
``delete_transaction(hlc_str: str) -> None``
``get_transaction(hlc_str: str) -> Transaction | None``
``list_transactions(proposal: bool | None, feature_uuid: str | None, limit: int) -> list[Transaction]``
"""

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from codoc.model.hlc import HLC
from codoc.model.transaction import Transaction, TransactionKind


# ---------------------------------------------------------------------------
# Storage protocol (structural / duck-typed)
# ---------------------------------------------------------------------------


@runtime_checkable
class StorageProtocol(Protocol):
    def write_transaction(self, tx: Transaction) -> None: ...
    def update_transaction(self, hlc_str: str, updates: dict) -> None: ...
    def delete_transaction(self, hlc_str: str) -> None: ...
    def get_transaction(self, hlc_str: str) -> Transaction | None: ...
    def list_transactions(
        self,
        proposal: bool | None = None,
        feature_uuid: str | None = None,
        limit: int = 100,
    ) -> list[Transaction]: ...


# ---------------------------------------------------------------------------
# TransactionLog
# ---------------------------------------------------------------------------


class TransactionLog:
    """Append-only log of ``Transaction`` records backed by an injected storage."""

    def __init__(self, storage, node_id: str = "default") -> None:
        self._storage = storage
        self._node_id = node_id
        self._current_hlc: HLC = HLC(node_id=node_id)

    # ------------------------------------------------------------------
    # HLC management
    # ------------------------------------------------------------------

    def _tick(self, observed: HLC | None = None) -> HLC:
        """Advance the local HLC, optionally incorporating an observed remote HLC."""
        self._current_hlc = self._current_hlc.advance(observed)
        return self._current_hlc

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def append_proposal(self, tx: Transaction) -> Transaction:
        """Write a proposal (tx.proposal must be True) to storage.

        If the incoming transaction does not yet carry a valid HLC the log
        stamps it with the next local HLC tick.
        """
        hlc = self._tick(tx.hlc)
        stamped = tx.model_copy(update={"hlc": hlc, "proposal": True})
        self._storage.write_transaction(stamped)
        return stamped

    def accept_proposal(
        self, hlc_str: str, edits: dict | None = None
    ) -> Transaction:
        """Flip a proposal to accepted, optionally patching its payload.

        Parameters
        ----------
        hlc_str:
            The ``HLC.to_str()`` key identifying the proposal.
        edits:
            Optional dict merged into the transaction's payload before acceptance.
        """
        tx = self._storage.get_transaction(hlc_str)
        if tx is None:
            raise KeyError(f"No proposal found for HLC {hlc_str!r}")
        if not tx.proposal:
            raise ValueError(f"Transaction {hlc_str!r} is not a proposal")

        payload = dict(tx.payload)
        if edits:
            payload.update(edits)

        accepted_at = datetime.now(tz=timezone.utc)
        updates: dict = {
            "proposal": False,
            "accepted_at": accepted_at,
            "payload": payload,
        }
        self._storage.update_transaction(hlc_str, updates)

        accepted = tx.model_copy(
            update={"proposal": False, "accepted_at": accepted_at, "payload": payload}
        )
        return accepted

    def reject_proposal(self, hlc_str: str) -> None:
        """Hard-delete a proposal.  Only proposals may be deleted; accepted
        transactions are immutable."""
        tx = self._storage.get_transaction(hlc_str)
        if tx is None:
            raise KeyError(f"No proposal found for HLC {hlc_str!r}")
        if not tx.proposal:
            raise ValueError(
                f"Transaction {hlc_str!r} is accepted and immutable; only proposals may be rejected"
            )
        self._storage.delete_transaction(hlc_str)

    def append(self, tx: Transaction) -> Transaction:
        """Write an accepted transaction directly (intentional v1 transactions
        that do not require proposal review).

        The transaction is stamped with the next local HLC tick and
        ``proposal`` is forced to False.
        """
        hlc = self._tick(tx.hlc)
        accepted_at = tx.accepted_at or datetime.now(tz=timezone.utc)
        stamped = tx.model_copy(
            update={"hlc": hlc, "proposal": False, "accepted_at": accepted_at}
        )
        self._storage.write_transaction(stamped)
        return stamped

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_transaction(self, hlc_str: str) -> Transaction | None:
        """Look up a single transaction by its HLC string key."""
        return self._storage.get_transaction(hlc_str)

    def pending_proposals(self) -> list[Transaction]:
        """Return all proposals not yet accepted, ordered by HLC ascending."""
        return self._storage.list_transactions(proposal=True, limit=0)

    def history(
        self,
        feature_uuid: str | None = None,
        limit: int = 100,
    ) -> list[Transaction]:
        """Return recent accepted transactions.

        Parameters
        ----------
        feature_uuid:
            When provided, restrict results to transactions whose payload
            contains a matching ``feature_uuid`` key.
        limit:
            Maximum number of records to return.  0 means no limit (use
            sparingly on large logs).
        """
        return self._storage.list_transactions(
            proposal=False, feature_uuid=feature_uuid, limit=limit
        )

    def head_hlc(self) -> HLC | None:
        """Return the HLC of the most recent accepted transaction, or None."""
        txs = self._storage.list_transactions(proposal=False, limit=1)
        if not txs:
            return None
        return txs[0].hlc
