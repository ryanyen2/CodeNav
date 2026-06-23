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
from datetime import datetime, timezone
from pathlib import Path

from codoc.model.binding import Binding
from codoc.model.block import Block, BlockLifecycle, Provenance
from codoc.model.event import Event, NodeOp
from codoc.model.feature import Feature, Lifecycle
from codoc.model.hlc import HLC

_SCHEMA = """
CREATE TABLE IF NOT EXISTS features (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    parent_id   TEXT,
    -- `lifecycle` (planned|active|retired) is the authoritative named state
    -- (Proposal A1). `retired`/`realized` are kept in sync as derived columns so
    -- a reader from before A1 still sees correct values.
    lifecycle   TEXT NOT NULL DEFAULT 'active',
    retired     INTEGER NOT NULL DEFAULT 0,
    realized    INTEGER NOT NULL DEFAULT 1,
    -- The webview's client-side node id for a hand-authored feature (KTD8), so a
    -- minted fid matches back to the exact in-progress node. '' for code-derived nodes.
    local_id    TEXT NOT NULL DEFAULT '',
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

-- Typed-media blocks on a feature (diagram / image / latex / url / …). Prose is
-- NOT here — it is the implicit block-zero backed by features.description, so an
-- existing feature owns zero block rows and loads unchanged. Blocks carry a
-- STABLE id (KTD8) that survives host edits; `ord` is the position within the
-- feature, so a "move" is an ord update, not a delete+create. Binding stays
-- feature-level (KTD1) — there is deliberately no (file, symbol_path) column here.
CREATE TABLE IF NOT EXISTS blocks (
    id          TEXT PRIMARY KEY,
    feature_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    lifecycle   TEXT NOT NULL DEFAULT 'persistent',
    provenance  TEXT NOT NULL DEFAULT 'human',
    ord         INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blocks_feature ON blocks(feature_id);

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
            cols.add("realized")
        if "lifecycle" not in cols:
            # Proposal A1: add the authoritative named state column, then backfill
            # it from the legacy bool pair so existing rows get a correct lifecycle
            # without a data-loss window (retired dominates; unrealized → planned).
            self.conn.execute(
                "ALTER TABLE features ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'active'"
            )
            self.conn.execute(
                "UPDATE features SET lifecycle = CASE"
                " WHEN retired=1 THEN 'retired'"
                " WHEN realized=0 THEN 'planned'"
                " ELSE 'active' END"
            )
        if "local_id" not in cols:
            # Additive: hand-authored-node id for minted-fid reconciliation. '' for
            # every pre-existing row (they were never authored through the webview's
            # localId path), which is the correct "no client id" sentinel.
            self.conn.execute(
                "ALTER TABLE features ADD COLUMN local_id TEXT NOT NULL DEFAULT ''"
            )
            cols.add("local_id")
        bcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(bindings)")}
        if "types_hash" not in bcols:
            # Default '' ⇒ pre-existing bindings have no recorded AST shape; rename
            # detection degrades gracefully for them until they are next refreshed.
            self.conn.execute(
                "ALTER TABLE bindings ADD COLUMN types_hash TEXT NOT NULL DEFAULT ''"
            )
        ecols = {r["name"] for r in self.conn.execute("PRAGMA table_info(events)")}
        # Change-ledger provenance. Default '' ⇒ legacy events have unknown
        # actor/mode and no causality link; readers treat '' as "render as today".
        for col in ("actor", "mode", "caused_by"):
            if col not in ecols:
                self.conn.execute(
                    f"ALTER TABLE events ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
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
            INSERT INTO features (id, title, description, parent_id, lifecycle, retired, realized, local_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                parent_id=excluded.parent_id,
                lifecycle=excluded.lifecycle,
                retired=excluded.retired,
                realized=excluded.realized,
                -- keep a known local_id if a re-upsert (refresh/move) carries none
                local_id=CASE WHEN excluded.local_id != '' THEN excluded.local_id ELSE features.local_id END,
                updated_at=excluded.updated_at
            """,
            (
                f.id,
                f.title,
                f.description,
                f.parent_id,
                f.lifecycle.value,
                # retired/realized are derived (computed) views of lifecycle, kept
                # in the row for pre-A1 back-compat readers.
                int(f.retired),
                int(f.realized),
                f.local_id,
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
        # lifecycle is authoritative; retired=1 kept in sync for back-compat readers.
        self.conn.execute(
            "UPDATE features SET lifecycle='retired', retired=1, updated_at=? WHERE id=?",
            (HLC.now().to_str(), feature_id),
        )
        self.conn.commit()

    def unretire_feature(self, feature_id: str) -> None:
        """Bring a retired feature back to ``active`` — the undo of a soft delete.
        Guarded to ``retired`` rows so it only ever resurrects a tombstone (a
        planned/active feature is untouched). Used when a deleted node reappears in
        the doc (the human pressed undo / re-added it)."""
        self.conn.execute(
            "UPDATE features SET lifecycle='active', retired=0, realized=1, updated_at=?"
            " WHERE id=? AND lifecycle='retired'",
            (HLC.now().to_str(), feature_id),
        )
        self.conn.commit()

    def mark_realized(self, feature_id: str) -> None:
        """Promote a plan placeholder to ``active`` (code now binds to it) — the
        named planned→active lifecycle transition. Guarded to ``planned`` rows so
        it can never resurrect a retired feature; ``realized=1`` is kept in sync."""
        self.conn.execute(
            "UPDATE features SET lifecycle='active', realized=1, updated_at=?"
            " WHERE id=? AND lifecycle='planned'",
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

    def backfill_types_hash(self, file: str, symbol_path: str, types_hash: str) -> bool:
        """D4: record a binding's AST-shape hash when it was attributed WITHOUT one
        (a legacy row, or an MCP/propose bind that carried no hash). Only fills an
        EMPTY ``types_hash`` and only with a non-empty value, so it never overwrites
        a known shape — pure index maintenance, no event. Returns True if a row was
        filled. Without this, a binding that never got a ``types_hash`` would
        silently disable rename detection forever."""
        if not types_hash:
            return False
        cur = self.conn.execute(
            "UPDATE bindings SET types_hash=? WHERE file=? AND symbol_path=? AND types_hash=''",
            (types_hash, file, symbol_path),
        )
        self.conn.commit()
        return cur.rowcount > 0

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

    def bound_feature_ids(self) -> set[str]:
        """Feature ids that own at least one binding — one indexed query, so the
        loops can test "is unbound" over many features without a per-feature
        ``bindings_for_feature`` round-trip."""
        rows = self.conn.execute("SELECT DISTINCT feature_id FROM bindings").fetchall()
        return {r["feature_id"] for r in rows}

    # -- blocks (typed media on a feature) --------------------------------
    def upsert_block(self, b: Block) -> None:
        """Insert or update a block by its stable id (KTD8). Re-upserting the same
        id (e.g. after a move = ``ord`` change, or a content edit) keeps identity."""
        self.conn.execute(
            """
            INSERT INTO blocks (id, feature_id, kind, content, lifecycle, provenance, ord, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                feature_id=excluded.feature_id,
                kind=excluded.kind,
                content=excluded.content,
                lifecycle=excluded.lifecycle,
                provenance=excluded.provenance,
                ord=excluded.ord,
                updated_at=excluded.updated_at
            """,
            (
                b.id, b.feature_id, b.kind, b.content, b.lifecycle.value,
                b.provenance.value, b.ord, b.created_at.to_str(), b.updated_at.to_str(),
            ),
        )
        self.conn.commit()

    def get_block(self, block_id: str) -> Block | None:
        row = self.conn.execute("SELECT * FROM blocks WHERE id=?", (block_id,)).fetchone()
        return _row_to_block(row) if row else None

    def blocks_for_feature(self, feature_id: str) -> list[Block]:
        rows = self.conn.execute(
            "SELECT * FROM blocks WHERE feature_id=? ORDER BY ord, created_at", (feature_id,)
        ).fetchall()
        return [_row_to_block(r) for r in rows]

    def blocks_for_features(self, feature_ids: set[str]) -> list[Block]:
        if not feature_ids:
            return []
        placeholders = ",".join("?" * len(feature_ids))
        rows = self.conn.execute(
            f"SELECT * FROM blocks WHERE feature_id IN ({placeholders}) ORDER BY feature_id, ord",
            tuple(feature_ids),
        ).fetchall()
        return [_row_to_block(r) for r in rows]

    def all_blocks(self) -> list[Block]:
        return [_row_to_block(r) for r in self.conn.execute("SELECT * FROM blocks").fetchall()]

    def delete_block(self, block_id: str) -> None:
        self.conn.execute("DELETE FROM blocks WHERE id=?", (block_id,))
        self.conn.commit()

    def delete_blocks_for_feature(self, feature_id: str) -> None:
        self.conn.execute("DELETE FROM blocks WHERE feature_id=?", (feature_id,))
        self.conn.commit()

    # -- events -----------------------------------------------------------
    def append_event(self, e: Event) -> None:
        self.conn.execute(
            "INSERT INTO events (id, at, source, op_json, applied, accepted_at, actor, mode, caused_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e.id,
                e.at.to_str(),
                e.source,
                e.op.model_dump_json(),
                int(e.applied),
                e.accepted_at.isoformat() if e.accepted_at else None,
                e.actor,
                e.mode,
                e.caused_by,
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
            (datetime.now(timezone.utc).isoformat(), event_id),
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
    keys = r.keys()
    # Prefer the authoritative lifecycle column; fall back to the legacy bool pair
    # for a row written before the A1 migration ran (tolerant read).
    if "lifecycle" in keys and r["lifecycle"]:
        lifecycle = Lifecycle(r["lifecycle"])
    else:
        from codoc.model.feature import _lifecycle_from_bools
        lifecycle = _lifecycle_from_bools(bool(r["retired"]), bool(r["realized"]))
    return Feature(
        id=r["id"],
        title=r["title"],
        description=r["description"],
        parent_id=r["parent_id"],
        lifecycle=lifecycle,
        local_id=(r["local_id"] if "local_id" in keys else ""),
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


def _row_to_block(r: sqlite3.Row) -> Block:
    return Block(
        id=r["id"],
        feature_id=r["feature_id"],
        kind=r["kind"],
        content=r["content"],
        lifecycle=BlockLifecycle(r["lifecycle"]),
        provenance=Provenance(r["provenance"]),
        ord=r["ord"],
        created_at=HLC.from_str(r["created_at"]),
        updated_at=HLC.from_str(r["updated_at"]),
    )


def _row_to_event(r: sqlite3.Row) -> Event:
    keys = r.keys()
    return Event(
        id=r["id"],
        at=HLC.from_str(r["at"]),
        source=r["source"],
        op=NodeOp.model_validate_json(r["op_json"]),
        applied=bool(r["applied"]),
        accepted_at=datetime.fromisoformat(r["accepted_at"]) if r["accepted_at"] else None,
        actor=(r["actor"] if "actor" in keys else ""),
        mode=(r["mode"] if "mode" in keys else ""),
        caused_by=(r["caused_by"] if "caused_by" in keys else ""),
    )


def open_store(codoc_dir: str | Path = ".codoc") -> Store:
    """Open (creating if needed) the store at ``<codoc_dir>/codoc.db``."""
    return Store(Path(codoc_dir) / "codoc.db").open()
