"""The codoc store — three tables, nothing more.

``features`` (the tree), ``bindings`` (code attribution, one row per
``(file, symbol_path)``), and ``events`` (the append-only change log; a pending
proposal is a row with ``applied=0``). SQLite in WAL mode at
``<codoc_dir>/codoc.db``.

The ``UNIQUE(file, symbol_path)`` constraint on ``bindings`` is what structurally
forbids duplicate attribution — a chunk binds to at most one feature — so no
dedup pass is needed anywhere downstream.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from codoc.model.binding import Binding
from codoc.model.event import Event, NodeOp
from codoc.model.feature import Feature
from codoc.model.hlc import HLC

_SCHEMA = """
CREATE TABLE IF NOT EXISTS features (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    parent_id   TEXT,
    retired     INTEGER NOT NULL DEFAULT 0,
    realized    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_features_parent ON features(parent_id);

CREATE TABLE IF NOT EXISTS bindings (
    id          TEXT PRIMARY KEY,
    feature_id  TEXT NOT NULL,
    file        TEXT NOT NULL,
    symbol_path TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    types_hash  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    UNIQUE(file, symbol_path)
);
CREATE INDEX IF NOT EXISTS idx_bindings_feature ON bindings(feature_id);
CREATE INDEX IF NOT EXISTS idx_bindings_anchor  ON bindings(file, symbol_path);

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    at          TEXT NOT NULL,
    source      TEXT NOT NULL,
    op_json     TEXT NOT NULL,
    applied     INTEGER NOT NULL DEFAULT 1,
    accepted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_applied ON events(applied);

CREATE TABLE IF NOT EXISTS code_edges (
    src_file    TEXT NOT NULL,
    src_symbol  TEXT NOT NULL,
    dst_name    TEXT NOT NULL,
    dst_symbol  TEXT,
    dst_file    TEXT,
    kind        TEXT NOT NULL,
    internal    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (src_symbol, dst_name, kind)
);
CREATE INDEX IF NOT EXISTS idx_edges_src_symbol ON code_edges(src_symbol);
CREATE INDEX IF NOT EXISTS idx_edges_dst_symbol ON code_edges(dst_symbol);
CREATE INDEX IF NOT EXISTS idx_edges_src_file   ON code_edges(src_file);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle --------------------------------------------------------
    def open(self) -> "Store":
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()
        return self

    def _migrate(self) -> None:
        """Idempotent additive migrations. ``CREATE TABLE IF NOT EXISTS`` never
        alters an existing table, so new columns are added here PRAGMA-guarded."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(features)")}
        if "realized" not in cols:
            # Default 1 ⇒ every pre-existing node is realized (preserves behavior).
            self.conn.execute(
                "ALTER TABLE features ADD COLUMN realized INTEGER NOT NULL DEFAULT 1"
            )
        bcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(bindings)")}
        if "types_hash" not in bcols:
            # Default '' ⇒ pre-existing bindings have no recorded AST shape; rename
            # detection degrades gracefully for them until they are next refreshed.
            self.conn.execute(
                "ALTER TABLE bindings ADD COLUMN types_hash TEXT NOT NULL DEFAULT ''"
            )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Store":
        return self if self._conn else self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Store is not open(); call .open() first")
        return self._conn

    # -- features ---------------------------------------------------------
    def upsert_feature(self, f: Feature) -> None:
        self.conn.execute(
            """
            INSERT INTO features (id, title, description, parent_id, retired, realized, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                parent_id=excluded.parent_id,
                retired=excluded.retired,
                realized=excluded.realized,
                updated_at=excluded.updated_at
            """,
            (
                f.id,
                f.title,
                f.description,
                f.parent_id,
                int(f.retired),
                int(f.realized),
                f.created_at.to_str(),
                f.updated_at.to_str(),
            ),
        )
        self.conn.commit()

    def get_feature(self, feature_id: str) -> Feature | None:
        row = self.conn.execute("SELECT * FROM features WHERE id=?", (feature_id,)).fetchone()
        return _row_to_feature(row) if row else None

    def list_features(self, *, include_retired: bool = False) -> list[Feature]:
        sql = "SELECT * FROM features"
        if not include_retired:
            sql += " WHERE retired=0"
        sql += " ORDER BY created_at"
        return [_row_to_feature(r) for r in self.conn.execute(sql).fetchall()]

    def children(self, parent_id: str | None) -> list[Feature]:
        if parent_id is None:
            rows = self.conn.execute(
                "SELECT * FROM features WHERE parent_id IS NULL AND retired=0 ORDER BY created_at"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM features WHERE parent_id=? AND retired=0 ORDER BY created_at",
                (parent_id,),
            ).fetchall()
        return [_row_to_feature(r) for r in rows]

    def retire_feature(self, feature_id: str) -> None:
        self.conn.execute(
            "UPDATE features SET retired=1, updated_at=? WHERE id=?",
            (HLC.now().to_str(), feature_id),
        )
        self.conn.commit()

    def mark_realized(self, feature_id: str) -> None:
        """Flip a plan placeholder to realized (code now binds to it)."""
        self.conn.execute(
            "UPDATE features SET realized=1, updated_at=? WHERE id=?",
            (HLC.now().to_str(), feature_id),
        )
        self.conn.commit()

    # -- bindings ---------------------------------------------------------
    def upsert_binding(self, b: Binding) -> None:
        """Bind ``(file, symbol_path)`` to a feature. Re-binding the same anchor
        updates the owning feature + fingerprint (keeps the original row id)."""
        self.conn.execute(
            """
            INSERT INTO bindings (id, feature_id, file, symbol_path, fingerprint, types_hash, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file, symbol_path) DO UPDATE SET
                feature_id=excluded.feature_id,
                fingerprint=excluded.fingerprint,
                -- keep a known shape if a re-bind (mcp/propose/accept) carries none
                types_hash=CASE WHEN excluded.types_hash != ''
                           THEN excluded.types_hash ELSE bindings.types_hash END,
                updated_at=excluded.updated_at
            """,
            (b.id, b.feature_id, b.file, b.symbol_path, b.fingerprint,
             b.types_hash, b.updated_at.to_str()),
        )
        self.conn.commit()

    def delete_binding(self, file: str, symbol_path: str) -> None:
        self.conn.execute(
            "DELETE FROM bindings WHERE file=? AND symbol_path=?", (file, symbol_path)
        )
        self.conn.commit()

    def bindings_for_feature(self, feature_id: str) -> list[Binding]:
        rows = self.conn.execute(
            "SELECT * FROM bindings WHERE feature_id=? ORDER BY file, symbol_path", (feature_id,)
        ).fetchall()
        return [_row_to_binding(r) for r in rows]

    def binding_at(self, file: str, symbol_path: str) -> Binding | None:
        row = self.conn.execute(
            "SELECT * FROM bindings WHERE file=? AND symbol_path=?", (file, symbol_path)
        ).fetchone()
        return _row_to_binding(row) if row else None

    def bindings_in_files(self, files: set[str]) -> list[Binding]:
        if not files:
            return []
        placeholders = ",".join("?" * len(files))
        rows = self.conn.execute(
            f"SELECT * FROM bindings WHERE file IN ({placeholders})", tuple(files)
        ).fetchall()
        return [_row_to_binding(r) for r in rows]

    def all_bindings(self) -> list[Binding]:
        rows = self.conn.execute("SELECT * FROM bindings").fetchall()
        return [_row_to_binding(r) for r in rows]

    # -- events -----------------------------------------------------------
    def append_event(self, e: Event) -> None:
        self.conn.execute(
            "INSERT INTO events (id, at, source, op_json, applied, accepted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                e.id,
                e.at.to_str(),
                e.source,
                e.op.model_dump_json(),
                int(e.applied),
                e.accepted_at.isoformat() if e.accepted_at else None,
            ),
        )
        self.conn.commit()

    def get_event(self, event_id: str) -> Event | None:
        row = self.conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def pending_events(self) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE applied=0 ORDER BY at"
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    def recent_events(self, limit: int = 20) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    def mark_applied(self, event_id: str) -> None:
        self.conn.execute(
            "UPDATE events SET applied=1, accepted_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), event_id),
        )
        self.conn.commit()

    def delete_event(self, event_id: str) -> None:
        self.conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        self.conn.commit()

    # -- code_edges (derived graph cache) ---------------------------------
    def insert_edges(self, edges: list[dict]) -> None:
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO code_edges
                (src_file, src_symbol, dst_name, dst_symbol, dst_file, kind, internal)
            VALUES
                (:src_file, :src_symbol, :dst_name, :dst_symbol, :dst_file, :kind, :internal)
            """,
            edges,
        )
        self.conn.commit()

    def delete_edges_from_files(self, files: set[str]) -> None:
        if not files:
            return
        placeholders = ",".join("?" * len(files))
        self.conn.execute(
            f"DELETE FROM code_edges WHERE src_file IN ({placeholders})", tuple(files)
        )
        self.conn.commit()

    def drop_all_edges(self) -> None:
        self.conn.execute("DELETE FROM code_edges")
        self.conn.commit()

    def edges_out(self, symbol: str, *, internal_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM code_edges WHERE src_symbol=?"
        if internal_only:
            sql += " AND internal=1"
        return self.conn.execute(sql, (symbol,)).fetchall()

    def edges_in(self, symbol: str, *, internal_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM code_edges WHERE dst_symbol=?"
        if internal_only:
            sql += " AND internal=1"
        return self.conn.execute(sql, (symbol,)).fetchall()

    def all_edges(self, *, internal_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM code_edges"
        if internal_only:
            sql += " WHERE internal=1"
        return self.conn.execute(sql).fetchall()


# ---------------------------------------------------------------------------
# Row ↔ model
# ---------------------------------------------------------------------------
def _row_to_feature(r: sqlite3.Row) -> Feature:
    return Feature(
        id=r["id"],
        title=r["title"],
        description=r["description"],
        parent_id=r["parent_id"],
        retired=bool(r["retired"]),
        realized=bool(r["realized"]),
        created_at=HLC.from_str(r["created_at"]),
        updated_at=HLC.from_str(r["updated_at"]),
    )


def _row_to_binding(r: sqlite3.Row) -> Binding:
    keys = r.keys()
    return Binding(
        id=r["id"],
        feature_id=r["feature_id"],
        file=r["file"],
        symbol_path=r["symbol_path"],
        fingerprint=r["fingerprint"],
        types_hash=(r["types_hash"] if "types_hash" in keys else ""),
        updated_at=HLC.from_str(r["updated_at"]),
    )


def _row_to_event(r: sqlite3.Row) -> Event:
    return Event(
        id=r["id"],
        at=HLC.from_str(r["at"]),
        source=r["source"],
        op=NodeOp.model_validate_json(r["op_json"]),
        applied=bool(r["applied"]),
        accepted_at=datetime.fromisoformat(r["accepted_at"]) if r["accepted_at"] else None,
    )


def open_store(codoc_dir: str | Path = ".codoc") -> Store:
    """Open (creating if needed) the store at ``<codoc_dir>/codoc.db``."""
    return Store(Path(codoc_dir) / "codoc.db").open()
