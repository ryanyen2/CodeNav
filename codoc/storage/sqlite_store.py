"""SQLite WAL store — primary persistence layer for codoc.

All mutations are wrapped in sqlite3's implicit transaction management.
WAL mode is set on connection open for better read concurrency.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from codoc.model.binding import Binding
from codoc.model.constraint import Constraint
from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.model.obligation import Obligation
from codoc.model.transaction import Transaction

if TYPE_CHECKING:
    pass

_DDL = """\
CREATE TABLE IF NOT EXISTS transactions (
    hlc TEXT PRIMARY KEY,
    parent_hlcs TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    author TEXT NOT NULL,
    proposal INTEGER NOT NULL DEFAULT 0,
    accepted_at TEXT,
    label TEXT
);
CREATE INDEX IF NOT EXISTS idx_tx_proposal ON transactions(proposal);
CREATE INDEX IF NOT EXISTS idx_tx_kind ON transactions(kind);

CREATE TABLE IF NOT EXISTS features (
    uuid TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    parent_uuid TEXT,
    intent TEXT NOT NULL DEFAULT '',
    retired INTEGER NOT NULL DEFAULT 0,
    created_at_hlc TEXT NOT NULL,
    updated_at_hlc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_features_slug ON features(slug);
CREATE INDEX IF NOT EXISTS idx_features_parent ON features(parent_uuid);

CREATE TABLE IF NOT EXISTS bindings (
    uuid TEXT PRIMARY KEY,
    feature_uuid TEXT NOT NULL,
    anchor_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    fingerprint_at_hlc TEXT NOT NULL,
    parent_symbol TEXT
);
CREATE INDEX IF NOT EXISTS idx_bindings_feature ON bindings(feature_uuid);

CREATE TABLE IF NOT EXISTS constraints (
    uuid TEXT PRIMARY KEY,
    feature_uuid TEXT NOT NULL,
    rule TEXT NOT NULL,
    instated_at_hlc TEXT NOT NULL,
    lifted_at_hlc TEXT
);
CREATE INDEX IF NOT EXISTS idx_constraints_feature ON constraints(feature_uuid);

CREATE TABLE IF NOT EXISTS obligations (
    uuid TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    feature_uuid TEXT NOT NULL,
    triggered_by_tx_hlc TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    expected_output_schema TEXT NOT NULL,
    context_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_obligations_feature ON obligations(feature_uuid);
CREATE INDEX IF NOT EXISTS idx_obligations_status ON obligations(status);

CREATE TABLE IF NOT EXISTS chunk_fingerprints (
    id TEXT PRIMARY KEY,
    file TEXT NOT NULL,
    symbol_path TEXT,
    fingerprint TEXT NOT NULL,
    last_seen_commit TEXT NOT NULL
);
"""


def _tx_to_row(tx: Transaction) -> dict:
    return {
        "hlc": tx.hlc.to_str(),
        "parent_hlcs": json.dumps([h.to_str() for h in tx.parent_hlcs]),
        "kind": tx.kind.value,
        "payload": json.dumps(tx.payload),
        "author": tx.author,
        "proposal": 1 if tx.proposal else 0,
        "accepted_at": tx.accepted_at.isoformat() if tx.accepted_at is not None else None,
        "label": tx.label,
    }


def _row_to_tx(row: sqlite3.Row) -> Transaction:
    from datetime import datetime

    raw = dict(row)
    raw["hlc"] = HLC.from_str(raw["hlc"])
    raw["parent_hlcs"] = [HLC.from_str(s) for s in json.loads(raw["parent_hlcs"])]
    raw["payload"] = json.loads(raw["payload"])
    raw["proposal"] = bool(raw["proposal"])
    if raw["accepted_at"] is not None:
        raw["accepted_at"] = datetime.fromisoformat(raw["accepted_at"])
    return Transaction.model_validate(raw)


def _feature_to_row(f: Feature) -> dict:
    return {
        "uuid": f.uuid,
        "slug": f.slug,
        "parent_uuid": f.parent_uuid,
        "intent": f.intent,
        "retired": 1 if f.retired else 0,
        "created_at_hlc": f.created_at_hlc.to_str(),
        "updated_at_hlc": f.updated_at_hlc.to_str(),
    }


def _row_to_feature(row: sqlite3.Row) -> Feature:
    raw = dict(row)
    raw["retired"] = bool(raw["retired"])
    raw["created_at_hlc"] = HLC.from_str(raw["created_at_hlc"])
    raw["updated_at_hlc"] = HLC.from_str(raw["updated_at_hlc"])
    return Feature.model_validate(raw)


def _binding_to_row(b: Binding) -> dict:
    return {
        "uuid": b.uuid,
        "feature_uuid": b.feature_uuid,
        "anchor_json": json.dumps(b.anchor.model_dump()),
        "fingerprint": b.fingerprint,
        "fingerprint_at_hlc": b.fingerprint_at_hlc.to_str(),
        "parent_symbol": b.parent_symbol,
    }


def _row_to_binding(row: sqlite3.Row) -> Binding:
    from codoc.model.anchor import Anchor

    raw = dict(row)
    raw["anchor"] = Anchor.model_validate(json.loads(raw.pop("anchor_json")))
    raw["fingerprint_at_hlc"] = HLC.from_str(raw["fingerprint_at_hlc"])
    return Binding.model_validate(raw)


def _constraint_to_row(c: Constraint) -> dict:
    return {
        "uuid": c.uuid,
        "feature_uuid": c.feature_uuid,
        "rule": c.rule,
        "instated_at_hlc": c.instated_at_hlc.to_str(),
        "lifted_at_hlc": c.lifted_at_hlc.to_str() if c.lifted_at_hlc is not None else None,
    }


def _row_to_constraint(row: sqlite3.Row) -> Constraint:
    raw = dict(row)
    raw["instated_at_hlc"] = HLC.from_str(raw["instated_at_hlc"])
    if raw["lifted_at_hlc"] is not None:
        raw["lifted_at_hlc"] = HLC.from_str(raw["lifted_at_hlc"])
    return Constraint.model_validate(raw)


def _obligation_to_row(o: Obligation) -> dict:
    return {
        "uuid": o.uuid,
        "kind": o.kind.value,
        "feature_uuid": o.feature_uuid,
        "triggered_by_tx_hlc": o.triggered_by_tx_hlc.to_str(),
        "context_hash": o.context_hash,
        "expected_output_schema": o.expected_output_schema,
        "context_json": json.dumps(o.context),
        "status": o.status,
        "result_json": json.dumps(o.result) if o.result is not None else None,
    }


def _row_to_obligation(row: sqlite3.Row) -> Obligation:
    raw = dict(row)
    raw["triggered_by_tx_hlc"] = HLC.from_str(raw["triggered_by_tx_hlc"])
    raw["context"] = json.loads(raw.pop("context_json"))
    result_json = raw.pop("result_json")
    raw["result"] = json.loads(result_json) if result_json is not None else None
    return Obligation.model_validate(raw)


class SQLiteStore:
    """SQLite WAL store for all codoc entities.

    Usage::

        store = SQLiteStore(".codoc/codoc.db")
        store.open()
        # ... use store ...
        store.close()

    Or as a context manager::

        with SQLiteStore(".codoc/codoc.db") as store:
            store.write_transaction(tx)
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the database connection, enable WAL mode, and create tables."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "SQLiteStore":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteStore is not open. Call open() first.")
        return self._conn

    # ------------------------------------------------------------------
    # Transaction CRUD
    # ------------------------------------------------------------------

    def write_transaction(self, tx: Transaction) -> None:
        """Insert a transaction record. Raises IntegrityError if HLC already exists."""
        row = _tx_to_row(tx)
        self._db.execute(
            """
            INSERT INTO transactions
                (hlc, parent_hlcs, kind, payload, author, proposal, accepted_at, label)
            VALUES
                (:hlc, :parent_hlcs, :kind, :payload, :author, :proposal, :accepted_at, :label)
            """,
            row,
        )
        self._db.commit()

    def update_transaction(self, hlc_str: str, updates: dict) -> None:
        """Update mutable fields of an existing transaction.

        Allowed keys: ``proposal``, ``accepted_at``, ``payload``, ``label``.
        """
        allowed = {"proposal", "accepted_at", "payload", "label"}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return
        # Coerce types to match storage format.
        if "proposal" in filtered:
            filtered["proposal"] = 1 if filtered["proposal"] else 0
        if "payload" in filtered and isinstance(filtered["payload"], dict):
            filtered["payload"] = json.dumps(filtered["payload"])
        set_clause = ", ".join(f"{k} = :{k}" for k in filtered)
        filtered["_hlc"] = hlc_str
        self._db.execute(
            f"UPDATE transactions SET {set_clause} WHERE hlc = :_hlc",
            filtered,
        )
        self._db.commit()

    def delete_transaction(self, hlc_str: str) -> None:
        """Hard-delete a transaction row. Only used for proposal rollback."""
        self._db.execute("DELETE FROM transactions WHERE hlc = ?", (hlc_str,))
        self._db.commit()

    def get_transaction(self, hlc_str: str) -> Transaction | None:
        row = self._db.execute(
            "SELECT * FROM transactions WHERE hlc = ?", (hlc_str,)
        ).fetchone()
        return _row_to_tx(row) if row else None

    def list_transactions(
        self,
        proposal: bool | None = None,
        feature_uuid: str | None = None,
        limit: int = 100,
    ) -> list[Transaction]:
        """List transactions with optional filters.

        Args:
            proposal: If True, return only proposals; if False, only accepted;
                      if None, return all.
            feature_uuid: Filter by ``payload.feature_uuid`` OR
                          ``payload.affected_feature_uuid``.
            limit: Maximum rows to return. 0 means no limit.
        """
        clauses: list[str] = []
        params: list = []

        if proposal is not None:
            clauses.append("proposal = ?")
            params.append(1 if proposal else 0)

        if feature_uuid is not None:
            clauses.append(
                "(json_extract(payload, '$.feature_uuid') = ? "
                "OR json_extract(payload, '$.affected_feature_uuid') = ?)"
            )
            params.extend([feature_uuid, feature_uuid])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_clause = f"LIMIT {limit}" if limit > 0 else ""

        rows = self._db.execute(
            f"SELECT * FROM transactions {where} ORDER BY hlc ASC {limit_clause}",
            params,
        ).fetchall()
        return [_row_to_tx(r) for r in rows]

    # ------------------------------------------------------------------
    # Feature CRUD
    # ------------------------------------------------------------------

    def upsert_feature(self, feature: Feature) -> None:
        row = _feature_to_row(feature)
        self._db.execute(
            """
            INSERT INTO features
                (uuid, slug, parent_uuid, intent, retired, created_at_hlc, updated_at_hlc)
            VALUES
                (:uuid, :slug, :parent_uuid, :intent, :retired, :created_at_hlc, :updated_at_hlc)
            ON CONFLICT(uuid) DO UPDATE SET
                slug           = excluded.slug,
                parent_uuid    = excluded.parent_uuid,
                intent         = excluded.intent,
                retired        = excluded.retired,
                updated_at_hlc = excluded.updated_at_hlc
            """,
            row,
        )
        self._db.commit()

    def get_feature(self, uuid: str) -> Feature | None:
        row = self._db.execute(
            "SELECT * FROM features WHERE uuid = ?", (uuid,)
        ).fetchone()
        return _row_to_feature(row) if row else None

    def list_features(self, parent_uuid: str | None = None) -> list[Feature]:
        """Return features.

        Args:
            parent_uuid: If None, return all features (no filter).
                         If empty string ``""``, return root features
                         (those with parent_uuid IS NULL).
                         Otherwise, return children of the given UUID.
        """
        if parent_uuid is None:
            rows = self._db.execute("SELECT * FROM features ORDER BY slug ASC").fetchall()
        elif parent_uuid == "":
            rows = self._db.execute(
                "SELECT * FROM features WHERE parent_uuid IS NULL ORDER BY slug ASC"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM features WHERE parent_uuid = ? ORDER BY slug ASC",
                (parent_uuid,),
            ).fetchall()
        return [_row_to_feature(r) for r in rows]

    def delete_feature(self, uuid: str) -> None:
        """Hard-delete a feature. Only for proposal rollback; accepted features use retired flag."""
        self._db.execute("DELETE FROM features WHERE uuid = ?", (uuid,))
        self._db.commit()

    # ------------------------------------------------------------------
    # Binding CRUD
    # ------------------------------------------------------------------

    def upsert_binding(self, binding: Binding) -> None:
        row = _binding_to_row(binding)
        self._db.execute(
            """
            INSERT INTO bindings
                (uuid, feature_uuid, anchor_json, fingerprint, fingerprint_at_hlc, parent_symbol)
            VALUES
                (:uuid, :feature_uuid, :anchor_json, :fingerprint, :fingerprint_at_hlc, :parent_symbol)
            ON CONFLICT(uuid) DO UPDATE SET
                feature_uuid       = excluded.feature_uuid,
                anchor_json        = excluded.anchor_json,
                fingerprint        = excluded.fingerprint,
                fingerprint_at_hlc = excluded.fingerprint_at_hlc,
                parent_symbol      = excluded.parent_symbol
            """,
            row,
        )
        self._db.commit()

    def get_binding(self, uuid: str) -> Binding | None:
        row = self._db.execute(
            "SELECT * FROM bindings WHERE uuid = ?", (uuid,)
        ).fetchone()
        return _row_to_binding(row) if row else None

    def list_bindings(self, feature_uuid: str) -> list[Binding]:
        rows = self._db.execute(
            "SELECT * FROM bindings WHERE feature_uuid = ?", (feature_uuid,)
        ).fetchall()
        return [_row_to_binding(r) for r in rows]

    def delete_binding(self, uuid: str) -> None:
        self._db.execute("DELETE FROM bindings WHERE uuid = ?", (uuid,))
        self._db.commit()

    def get_all_bindings(self) -> list[Binding]:
        rows = self._db.execute("SELECT * FROM bindings").fetchall()
        return [_row_to_binding(r) for r in rows]

    # ------------------------------------------------------------------
    # Constraint CRUD
    # ------------------------------------------------------------------

    def upsert_constraint(self, constraint: Constraint) -> None:
        row = _constraint_to_row(constraint)
        self._db.execute(
            """
            INSERT INTO constraints
                (uuid, feature_uuid, rule, instated_at_hlc, lifted_at_hlc)
            VALUES
                (:uuid, :feature_uuid, :rule, :instated_at_hlc, :lifted_at_hlc)
            ON CONFLICT(uuid) DO UPDATE SET
                feature_uuid   = excluded.feature_uuid,
                rule           = excluded.rule,
                instated_at_hlc = excluded.instated_at_hlc,
                lifted_at_hlc  = excluded.lifted_at_hlc
            """,
            row,
        )
        self._db.commit()

    def get_constraint(self, uuid: str) -> Constraint | None:
        row = self._db.execute(
            "SELECT * FROM constraints WHERE uuid = ?", (uuid,)
        ).fetchone()
        return _row_to_constraint(row) if row else None

    def list_constraints(self, feature_uuid: str) -> list[Constraint]:
        rows = self._db.execute(
            "SELECT * FROM constraints WHERE feature_uuid = ?", (feature_uuid,)
        ).fetchall()
        return [_row_to_constraint(r) for r in rows]

    # ------------------------------------------------------------------
    # Obligation CRUD
    # ------------------------------------------------------------------

    def upsert_obligation(self, obligation: Obligation) -> None:
        row = _obligation_to_row(obligation)
        self._db.execute(
            """
            INSERT INTO obligations
                (uuid, kind, feature_uuid, triggered_by_tx_hlc, context_hash,
                 expected_output_schema, context_json, status, result_json)
            VALUES
                (:uuid, :kind, :feature_uuid, :triggered_by_tx_hlc, :context_hash,
                 :expected_output_schema, :context_json, :status, :result_json)
            ON CONFLICT(uuid) DO UPDATE SET
                kind                  = excluded.kind,
                feature_uuid          = excluded.feature_uuid,
                triggered_by_tx_hlc   = excluded.triggered_by_tx_hlc,
                context_hash          = excluded.context_hash,
                expected_output_schema = excluded.expected_output_schema,
                context_json          = excluded.context_json,
                status                = excluded.status,
                result_json           = excluded.result_json
            """,
            row,
        )
        self._db.commit()

    def get_obligation(self, uuid: str) -> Obligation | None:
        row = self._db.execute(
            "SELECT * FROM obligations WHERE uuid = ?", (uuid,)
        ).fetchone()
        return _row_to_obligation(row) if row else None

    def list_obligations(
        self,
        feature_uuid: str | None = None,
        status: str | None = None,
    ) -> list[Obligation]:
        clauses: list[str] = []
        params: list = []
        if feature_uuid is not None:
            clauses.append("feature_uuid = ?")
            params.append(feature_uuid)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.execute(
            f"SELECT * FROM obligations {where} ORDER BY triggered_by_tx_hlc ASC",
            params,
        ).fetchall()
        return [_row_to_obligation(r) for r in rows]

    def update_obligation_status(
        self, uuid: str, status: str, result: dict | None = None
    ) -> None:
        self._db.execute(
            "UPDATE obligations SET status = ?, result_json = ? WHERE uuid = ?",
            (status, json.dumps(result) if result is not None else None, uuid),
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # Chunk fingerprint cache
    # ------------------------------------------------------------------

    def upsert_chunk_fingerprint(
        self,
        key: str,
        file: str,
        symbol_path: str | None,
        fingerprint: str,
        commit: str,
    ) -> None:
        self._db.execute(
            """
            INSERT INTO chunk_fingerprints (id, file, symbol_path, fingerprint, last_seen_commit)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                file             = excluded.file,
                symbol_path      = excluded.symbol_path,
                fingerprint      = excluded.fingerprint,
                last_seen_commit = excluded.last_seen_commit
            """,
            (key, file, symbol_path, fingerprint, commit),
        )
        self._db.commit()

    def get_chunk_fingerprint(self, key: str) -> str | None:
        row = self._db.execute(
            "SELECT fingerprint FROM chunk_fingerprints WHERE id = ?", (key,)
        ).fetchone()
        return row["fingerprint"] if row else None

    def get_all_chunk_fingerprints(self) -> dict[str, str]:
        rows = self._db.execute("SELECT id, fingerprint FROM chunk_fingerprints").fetchall()
        return {r["id"]: r["fingerprint"] for r in rows}
