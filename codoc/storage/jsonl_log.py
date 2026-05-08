"""Append-only JSONL audit log for committed (accepted) transactions.

Every accepted transaction appends one JSON line to ``.codoc/log.jsonl``.
The SQLite store is source of truth; this file is a rebuildable derivative
used for audit and portability.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from codoc.model.hlc import HLC
from codoc.model.transaction import Transaction

if TYPE_CHECKING:
    from codoc.storage.sqlite_store import SQLiteStore


class JSONLLog:
    """Append-only JSONL audit log.

    Each line is the JSON-serialised form of a :class:`~codoc.model.transaction.Transaction`.
    HLC values are stored as their canonical string form (via ``HLC.to_str()``).

    Usage::

        log = JSONLLog(".codoc/log.jsonl")
        log.append(tx)

        transactions = log.read_all()
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tx_to_dict(tx: Transaction) -> dict:
        """Serialise a Transaction to a plain dict suitable for JSON output.

        HLC objects are converted to their canonical string representation.
        ``datetime`` values are converted to ISO-8601 strings.
        ``TransactionKind`` enum values are stored as their string value.
        """
        d = tx.model_dump()
        # HLC fields: hlc, parent_hlcs items
        d["hlc"] = tx.hlc.to_str()
        d["parent_hlcs"] = [h.to_str() for h in tx.parent_hlcs]
        # kind is a StrEnum so model_dump already gives the .value string,
        # but be explicit for clarity.
        d["kind"] = tx.kind.value
        # datetime → ISO string
        if d.get("accepted_at") is not None:
            d["accepted_at"] = tx.accepted_at.isoformat()  # type: ignore[union-attr]
        return d

    @staticmethod
    def _dict_to_tx(d: dict) -> Transaction:
        """Deserialise a plain dict back into a Transaction."""
        from datetime import datetime

        d = dict(d)  # defensive copy
        d["hlc"] = HLC.from_str(d["hlc"])
        d["parent_hlcs"] = [HLC.from_str(s) for s in d["parent_hlcs"]]
        if d.get("accepted_at") is not None:
            d["accepted_at"] = datetime.fromisoformat(d["accepted_at"])
        return Transaction.model_validate(d)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, tx: Transaction) -> None:
        """Append one JSON line to the JSONL file.

        Creates the file (and any parent directories) if they do not exist.
        The line is flushed to disk immediately.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(self._tx_to_dict(tx), ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()

    def rebuild_sqlite(self, store: "SQLiteStore") -> int:
        """Re-populate *store* from this JSONL file.

        Reads every line and upserts the transaction into the SQLite store via
        :meth:`~codoc.storage.sqlite_store.SQLiteStore.write_transaction`.
        Lines that already exist (same HLC primary key) raise
        ``sqlite3.IntegrityError``; callers should open the store in a
        suitable state (e.g. empty DB) or use
        :meth:`~codoc.storage.sqlite_store.SQLiteStore.update_transaction` as
        appropriate.

        Returns the number of lines successfully processed.
        """
        import sqlite3 as _sqlite3

        if not self._path.exists():
            return 0

        count = 0
        with self._path.open("r", encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    d = json.loads(raw_line)
                    tx = self._dict_to_tx(d)
                    try:
                        store.write_transaction(tx)
                    except _sqlite3.IntegrityError:
                        # Already present — skip without error.
                        pass
                    count += 1
                except Exception as exc:
                    raise ValueError(
                        f"Failed to parse JSONL line {lineno} in {self._path}: {exc!r}"
                    ) from exc
        return count

    def read_all(self) -> list[Transaction]:
        """Read and return all transactions from the JSONL file.

        Returns an empty list if the file does not exist.
        """
        if not self._path.exists():
            return []

        transactions: list[Transaction] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    d = json.loads(raw_line)
                    transactions.append(self._dict_to_tx(d))
                except Exception as exc:
                    raise ValueError(
                        f"Failed to parse JSONL line {lineno} in {self._path}: {exc!r}"
                    ) from exc
        return transactions
