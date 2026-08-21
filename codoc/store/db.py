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

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from codoc.model.annotation import (
    CommentReply, CommentScope, CommentStatus, CommentThread, Mark, MarkKind,
)
from codoc.model.binding import Binding
from codoc.model.block import Block, BlockLifecycle, Provenance
from codoc.model.event import ACTOR_HUMAN, Event, NodeOp
from codoc.model.feature import Feature, Lifecycle
from codoc.model.hlc import HLC
from codoc.model.voice import LessonAxis, LessonStatus, StyleLesson

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
    -- Sibling order key (codoc.model.rank): a base-62 fraction compared as a plain
    -- string, so ORDER BY needs no collation. Before this the tree had no order of
    -- its own — siblings came back in created_at order, so a reorder wrote a
    -- parent_id that had not changed and the next render put the node back.
    rank        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_features_parent ON features(parent_id);
-- The (retired, rank, created_at) covering index is created in _migrate, NOT here:
-- on a pre-rank database `CREATE TABLE IF NOT EXISTS` is a no-op, so this script
-- would try to index a column that the ALTER has not added yet and every open of
-- an existing workspace would fail. Same reason idx_events_feature lives there.

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
    accepted_at TEXT,
    feature_id  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_applied ON events(applied);
-- recent_events() / the sidecar changes-feed sort by `at` DESC on every render; the
-- events table is append-only and grows for the daemon's lifetime, so without this
-- index that sort degrades to a full scan (tens of ms once the table is large).
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);

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

-- Tracked-change authorship marks on a feature's description span (R8). The
-- store-authoritative home for the webview's ProseMirror authorship ink, so the
-- store→doc projection (doc_render.build_doc_from_store) re-emits them instead of
-- the host holding them in tree.doc.json. Anchors are char offsets into the
-- normalized description.
CREATE TABLE IF NOT EXISTS marks (
    id           TEXT PRIMARY KEY,
    feature_id   TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'amend',
    provenance   TEXT NOT NULL DEFAULT 'human',
    anchor_start INTEGER NOT NULL DEFAULT 0,
    anchor_end   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_marks_feature ON marks(feature_id);

-- Inline comment threads anchored to a feature's description span (R9). The
-- durable home for what currently lives only in tree.doc.json DocFile.comments;
-- migrated in on first run (U8) so threads survive the host no longer writing the
-- doc file.
CREATE TABLE IF NOT EXISTS comments (
    id           TEXT PRIMARY KEY,
    feature_id   TEXT NOT NULL,
    body         TEXT NOT NULL DEFAULT '',
    author       TEXT NOT NULL DEFAULT 'human',
    status       TEXT NOT NULL DEFAULT 'open',
    anchor_start INTEGER NOT NULL DEFAULT 0,
    anchor_end   INTEGER NOT NULL DEFAULT 0,
    -- W8: what makes a comment a unit of requested WORK rather than a sticky note —
    -- the words it was anchored to (offsets alone go stale the moment the prose is
    -- rewritten), the code it names (the directive's `Edit only:` scope), whether it
    -- also asks for the prose to be updated, and the directive it produced.
    anchor_text  TEXT NOT NULL DEFAULT '',
    code_refs    TEXT NOT NULL DEFAULT '[]',
    scope        TEXT NOT NULL DEFAULT 'code',
    directive_id TEXT NOT NULL DEFAULT '',
    replies      TEXT NOT NULL DEFAULT '[]',
    media_ref    TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_feature ON comments(feature_id);

-- Applied identity-keyed command ledger (U3 / KTD8). The commands channel
-- (edits.json) is at-most-once-by-consumption, not idempotent-on-replay: a
-- write/drain interleaved with a crash can re-deliver. So each command carries a
-- stable id and the daemon records applied ids here; re-applying a recorded id is
-- a no-op. (The realize.md manifest tracks DIRECTIVES, not this channel — a
-- separate ledger is required.)
CREATE TABLE IF NOT EXISTS applied_commands (
    id          TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

-- Who wrote each feature last. An authored edit declares the text it is REPLACING
-- (Command.base_text) so the daemon can refuse to overwrite a feature that moved
-- under its author. But "moved" is not the same as "moved because of someone
-- else": an author typing faster than the projection round-trip sends several
-- commands against a store that has already absorbed the earlier ones, and the
-- base legitimately trails. Recording the last writer separates the two cases —
-- if the feature's current text came from this same editing session, this command
-- continues it; if it came from anyone else, the two genuinely disagree.
-- `role` is that writer's actor ("human" | agent id | "loop"), recorded so a
-- contended edit can be arbitrated by rank without re-deriving authorship from
-- the writer string. The writer is an opaque session tag; only the boundary
-- that performed the write knows what it was.
CREATE TABLE IF NOT EXISTS feature_writers (
    feature_id  TEXT PRIMARY KEY,
    writer      TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL
);

-- What codoc has learned about how this codebase's author writes, inferred from
-- their rewrites of prose codoc generated (codoc.model.voice, codoc.loop.voice).
-- Not derived state: the ledger holds the rewrites, but the LESSON drawn from one
-- is an LLM inference that costs a call, so re-deriving it on every pass would
-- re-bill the whole history. Hence a table, and hence `harvest_watermark` — the
-- newest event id already considered, so a harvest reads only what arrived since.
--
-- `status` gates injection (provisional until a second edit corroborates it), and
-- a RETIRED row is kept rather than deleted: a lesson the author told us to forget
-- would otherwise be re-inferred from the same untouched history on the next pass,
-- so the row is the record of the refusal.
CREATE TABLE IF NOT EXISTS style_lessons (
    id            TEXT PRIMARY KEY,
    axis          TEXT NOT NULL,
    instruction   TEXT NOT NULL,
    example_before TEXT NOT NULL DEFAULT '',
    example_after TEXT NOT NULL DEFAULT '',
    axis_detail   TEXT NOT NULL DEFAULT '',
    scope_path    TEXT NOT NULL DEFAULT '[]',
    scope_files   TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL DEFAULT 'provisional',
    evidence      INTEGER NOT NULL DEFAULT 1,
    sources       TEXT NOT NULL DEFAULT '[]',
    source_events TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lessons_status ON style_lessons(status);

-- Small key/value scratch for the store's own bookkeeping. One row today (the
-- voice harvest watermark) and deliberately not a column on style_lessons: the
-- watermark has to advance even on a harvest that produced NO lesson, or a
-- history of pure content edits would be re-read and re-billed forever.
CREATE TABLE IF NOT EXISTS store_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

# Stamped into ``PRAGMA user_version`` after the schema + migrations have run, so
# subsequent opens of an up-to-date DB skip the executescript + 4 PRAGMA
# table_info scans entirely (open_store runs several times per loop tick — the
# schema replay was measurable overhead on every one). Bump when _SCHEMA or
# _migrate changes; version 0 (never stamped) always takes the slow path.
#: Sibling order: the rank key first, then created_at as a deterministic tiebreak.
#: The tiebreak matters — two features can legitimately share a rank (a restore, a
#: hand-edited db, an import), and without it SQLite is free to return them in
#: either order, so the tree would shuffle between renders for no visible reason.
_ORDER_BY = " ORDER BY rank, created_at"

_SCHEMA_VERSION = 7

#: How many times :meth:`Store._ensure_schema` re-attempts a lock-contended schema pass.
#: Each step is idempotent, so a retry is free; the contention window is one peer's
#: migration (a handful of small DDL statements), so a few tries cover it comfortably.
_SCHEMA_ATTEMPTS = 5


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._suppress_commit = False  # set inside transaction() so the per-method
                                       # eager commits coalesce into one atomic commit

    def _commit(self) -> None:
        """The single commit funnel every mutator calls (in place of the connection's
        ``commit`` directly), so :meth:`transaction` can SUPPRESS the per-method eager
        commits and make several mutations land atomically. Outside a transaction this
        is a plain commit — identical to the prior behavior."""
        if not self._suppress_commit:
            self.conn.commit()

    # -- lifecycle --------------------------------------------------------
    def open(self) -> "Store":
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        # busy_timeout first: it is what makes every statement below WAIT for a peer's
        # write lock instead of failing outright.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._set_wal()
        self._ensure_schema()
        return self

    def _set_wal(self) -> None:
        """Put the database in WAL mode, tolerating a peer doing it at the same moment.

        Journal mode is a property of the FILE, not of this connection, and switching it
        is the one thing ``busy_timeout`` cannot wait for: another connection merely
        HAVING the database open blocks the change, and SQLite returns SQLITE_BUSY
        immediately rather than queueing. Several processes opening a fresh workspace
        together therefore raced, and the loser's open failed outright — on a database
        that a moment later was in exactly the mode it wanted.

        So skip the switch when the file is already WAL (the common case, and one cheap
        statement), and read a lock refusal as the peer having it in hand. Whoever wins
        sets the mode for everyone, including the connections that lost.
        """
        assert self._conn is not None
        mode = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() == "wal":
            return
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise

    def _ensure_schema(self) -> None:
        """Bring the schema up to ``_SCHEMA_VERSION``, tolerating concurrent openers.

        The daemon, the CLI and the MCP server share this store, so several processes can
        reach here at once on a workspace's first open after an upgrade. Nothing
        serializes them: ``executescript`` COMMITs before it runs and DDL auto-commits, so
        there is no outer transaction to hold. Every step is therefore written to be safe
        when a peer has already done it — ``_SCHEMA`` is all ``IF NOT EXISTS``, each ALTER
        goes through :meth:`_add_column`, every backfill is gated on the data it writes
        (see :meth:`_migrate`) — and the version stamp is the same value whoever lands
        last writes.

        Safe-to-repeat is not enough on its own, because SQLite can refuse rather than
        wait: ``busy_timeout`` covers a peer holding a write lock, but a lock UPGRADE
        (this connection already reading, now wanting to write) returns SQLITE_BUSY
        immediately — waiting would deadlock, so there is nothing for the timeout to do.
        That surfaced as a bare "database is locked" on a concurrent first open. Since
        every step is idempotent, retrying the whole thing is the honest answer: by the
        next attempt the peer has usually finished, and the version check then makes this
        a no-op.
        """
        assert self._conn is not None
        for attempt in range(_SCHEMA_ATTEMPTS):
            try:
                version = self._conn.execute("PRAGMA user_version").fetchone()[0]
                if version != _SCHEMA_VERSION:
                    self._conn.executescript(_SCHEMA)
                    self._migrate()
                    self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                self._conn.commit()
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == _SCHEMA_ATTEMPTS - 1:
                    raise
                self._conn.rollback()
                # Linear backoff: the contention window is one process's migration, which
                # is a handful of small DDL statements, not a long transaction.
                time.sleep(0.05 * (attempt + 1))

    def _add_column(self, table: str, column: str, decl: str) -> None:
        """Add a column unless it is already there — safe against a concurrent opener.

        The PRAGMA check and the ALTER are two statements, and Python's sqlite3 runs DDL
        in autocommit, so there is no transaction holding them together. The daemon, the
        CLI and the MCP server all open the same store, and two first-opens of a
        pre-upgrade workspace can interleave: both read "column missing", both ALTER, and
        the loser used to raise ``duplicate column name`` — a failed open, from a
        workspace that is in fact correctly migrated.

        Treating the duplicate as success is what makes the race benign: the two processes
        wanted the same column, so the loser's work is already done. It also covers the
        case where a crash landed the ALTER but not the stamp, which is the same state.
        """
        cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        if column in cols:
            return
        try:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    def _migrate(self) -> None:
        """Idempotent additive migrations. ``CREATE TABLE IF NOT EXISTS`` never
        alters an existing table, so new columns are added here PRAGMA-guarded.

        Two rules make this recovery-grade rather than merely idempotent, and both come
        from the same fact: sqlite3 auto-commits each DDL statement on its own, so a
        migration is a SEQUENCE of commits with no transaction around it.

        1. Every ``ALTER`` goes through :meth:`_add_column`, which tolerates a concurrent
           opener having added the same column.
        2. Every BACKFILL is gated on the DATA it would write, never on the presence of
           the column it writes into. A crash between the ALTER and its UPDATE leaves the
           column present and empty; a column-existence gate would then skip the backfill
           forever, and the workspace would carry a silently unpopulated index for the
           rest of its life. Re-checking the rows makes the torn state heal on the next
           open, which is also what makes the whole method safe to re-run.
        """
        self._add_column("features", "realized", "INTEGER NOT NULL DEFAULT 1")
        # Proposal A1: `lifecycle` is the authoritative named state; the legacy bool pair
        # is kept in sync as derived columns. Backfilled from that pair (retired
        # dominates; unrealized → planned), gated on DISAGREEMENT rather than on the
        # column being new — the only way the two can disagree is a torn migration, and
        # this heals it.
        self._add_column("features", "lifecycle", "TEXT NOT NULL DEFAULT 'active'")
        _LIFECYCLE_FROM_BOOLS = (
            "CASE WHEN retired=1 THEN 'retired'"
            " WHEN realized=0 THEN 'planned' ELSE 'active' END"
        )
        self.conn.execute(
            f"UPDATE features SET lifecycle = {_LIFECYCLE_FROM_BOOLS}"
            f" WHERE lifecycle <> {_LIFECYCLE_FROM_BOOLS}"
        )
        # Additive: hand-authored-node id for minted-fid reconciliation. '' for
        # every pre-existing row (they were never authored through the webview's
        # localId path), which is the correct "no client id" sentinel.
        self._add_column("features", "local_id", "TEXT NOT NULL DEFAULT ''")
        # Default '' ⇒ pre-existing bindings have no recorded AST shape; rename
        # detection degrades gracefully for them until they are next refreshed.
        self._add_column("bindings", "types_hash", "TEXT NOT NULL DEFAULT ''")
        # Change-ledger provenance. Default '' ⇒ legacy events have unknown
        # actor/mode and no causality link; readers treat '' as "render as today".
        for col in ("actor", "mode", "caused_by"):
            self._add_column("events", col, "TEXT NOT NULL DEFAULT ''")
        # W8: a comment becomes a unit of requested work. The defaults are exactly the
        # behaviour a pre-W8 thread had — no quoted anchor, no code targets, code-only
        # scope, no directive — so an existing workspace reads unchanged and the fields
        # only start meaning something once something writes them.
        for col, decl in (("anchor_text", "TEXT NOT NULL DEFAULT ''"),
                          ("code_refs", "TEXT NOT NULL DEFAULT '[]'"),
                          ("scope", "TEXT NOT NULL DEFAULT 'code'"),
                          ("directive_id", "TEXT NOT NULL DEFAULT ''"),
                          ("replies", "TEXT NOT NULL DEFAULT '[]'")):
            self._add_column("comments", col, decl)
        # Blame index (v3): the feature an event touched, lifted out of the op_json
        # payload so per-feature history is a single indexed lookup instead of a
        # full-scan JSON parse.
        self._add_column("events", "feature_id", "TEXT NOT NULL DEFAULT ''")
        # Gated on the ROWS, not the column (see the method docstring): a crash between
        # the ALTER above and this UPDATE left every event unindexed, and a
        # column-existence guard would have skipped the backfill for good — leaving
        # `codoc history` permanently empty for everything that predates the upgrade.
        # json_valid guard: json_extract RAISES on malformed JSON, so one torn/corrupt
        # historical row would otherwise brick the migration — and with it every store
        # open. A corrupt row keeps '' (it was never readable as an Event anyway).
        self.conn.execute(
            "UPDATE events SET feature_id ="
            " COALESCE(json_extract(op_json, '$.feature_id'), '')"
            " WHERE feature_id = '' AND json_valid(op_json)"
            " AND json_extract(op_json, '$.feature_id') IS NOT NULL"
        )
        # Outside any guard: a fresh DB gets the column from _SCHEMA (no ALTER),
        # so the index must be created unconditionally-idempotently here.
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_feature"
            " ON events(feature_id, at)"
        )
        self._add_column("features", "rank", "TEXT NOT NULL DEFAULT ''")
        # The backfill is gated on the DATA, not the column: sqlite3 auto-commits
        # the ALTER (DDL) on its own, separately from the UPDATEs below, so a
        # crash between them leaves the column present with every rank '' — and a
        # column-existence guard would then skip the backfill forever (every drag
        # silently appends-to-end once ranks are all-empty). Re-checking the rows
        # makes that torn state heal on the next open. Only the all-'' state
        # backfills: partial rank data means reorders already happened and must
        # not be recomputed from creation order.
        counts = self.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(rank=''),0) AS empty FROM features"
        ).fetchone()
        if counts["n"] and counts["empty"] == counts["n"]:
            # Sibling order keys. The backfill must reproduce EXACTLY the order the
            # tree is being rendered in today — created_at, per parent, ties broken
            # by rowid — or the first render after upgrading silently reshuffles
            # somebody's tree and every feature looks moved. rowid, not id: the old
            # ORDER BY created_at scans resolved same-millisecond ties by the
            # index's implicit rowid (insertion order), while id is a random uuid
            # fragment that would shuffle every bootstrap-minted sibling batch.
            # Ranks are assigned per parent group in that order, evenly spaced so
            # the first reorders need no relabelling.
            from codoc.model.rank import ordinal_keys

            groups: dict[object, list[str]] = {}
            for row in self.conn.execute(
                "SELECT id, parent_id FROM features ORDER BY created_at, rowid"
            ):
                groups.setdefault(row["parent_id"], []).append(row["id"])
            for ids in groups.values():
                keys = ordinal_keys(len(ids))
                self.conn.executemany(
                    "UPDATE features SET rank=? WHERE id=?", list(zip(keys, ids))
                )
        # Unconditionally idempotent, like idx_events_feature: a fresh database gets
        # `rank` from _SCHEMA and never enters the branch above, so the index has to
        # be created outside it.
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_features_retired_rank"
            " ON features(retired, rank, created_at)"
        )

        # Default '' ⇒ writers recorded before roles existed rank as non-human (see
        # model.event.outranks). Backfilling them as human would hand every
        # pre-existing row authority it was never granted, and the rows are mostly
        # loop_a's — exactly the ones a person's edit is supposed to win against.
        self._add_column("feature_writers", "role", "TEXT NOT NULL DEFAULT ''")

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
            INSERT INTO features (id, title, description, parent_id, lifecycle, retired, realized, local_id, rank, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                parent_id=excluded.parent_id,
                lifecycle=excluded.lifecycle,
                retired=excluded.retired,
                realized=excluded.realized,
                -- keep a known local_id if a re-upsert (refresh/move) carries none
                local_id=CASE WHEN excluded.local_id != '' THEN excluded.local_id ELSE features.local_id END,
                -- Same rule as local_id: a re-upsert that carries no rank (a refresh,
                -- a caller built from a pre-rank Feature) must not blank the order the
                -- tree is currently rendered in.
                rank=CASE WHEN excluded.rank != '' THEN excluded.rank ELSE features.rank END,
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
                f.rank,
                f.created_at.to_str(),
                f.updated_at.to_str(),
            ),
        )
        self._commit()

    def get_feature(self, feature_id: str) -> Feature | None:
        row = self.conn.execute("SELECT * FROM features WHERE id=?", (feature_id,)).fetchone()
        return _row_to_feature(row) if row else None

    def list_features(self, *, include_retired: bool = False) -> list[Feature]:
        sql = "SELECT * FROM features"
        if not include_retired:
            sql += " WHERE retired=0"
        sql += _ORDER_BY
        return [_row_to_feature(r) for r in self.conn.execute(sql).fetchall()]

    def children(self, parent_id: str | None) -> list[Feature]:
        if parent_id is None:
            rows = self.conn.execute(
                "SELECT * FROM features WHERE parent_id IS NULL AND retired=0" + _ORDER_BY
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM features WHERE parent_id=? AND retired=0" + _ORDER_BY,
                (parent_id,),
            ).fetchall()
        return [_row_to_feature(r) for r in rows]

    def _sibling_rank(self, parent_id: str | None, feature_id: str) -> str:
        """``feature_id``'s rank, but only if it really is a live child of
        ``parent_id``. A neighbour that was retired or reparented since the author
        saw it is not a position any more, and using its rank would place the node
        somewhere nobody asked for."""
        if not feature_id:
            return ""
        f = self.get_feature(feature_id)
        if f is None or f.retired or f.parent_id != parent_id:
            return ""
        return f.rank

    def _rank_preceding(self, parent_id: str | None, rank: str) -> str:
        """The rank of the live sibling immediately before ``rank``, or "" if none.

        What makes "before B" mean the gap above B rather than the top of the list.
        """
        if parent_id is None:
            row = self.conn.execute(
                "SELECT rank FROM features WHERE parent_id IS NULL AND retired = 0 "
                "AND rank < ? ORDER BY rank DESC LIMIT 1", (rank,)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT rank FROM features WHERE parent_id = ? AND retired = 0 "
                "AND rank < ? ORDER BY rank DESC LIMIT 1", (parent_id, rank)).fetchone()
        return row[0] if row else ""

    def rank_between(
        self, parent_id: str | None, after_id: str = "", before_id: str = ""
    ) -> str:
        """An order key placing a feature between two named siblings.

        Positions are given as NEIGHBOUR IDENTITIES, never as an index. An index
        is a re-derived guess: by the time the daemon applies it, Loop A may have
        added or retired a sibling and "third child" means something else.
        "After A, before B" still means what its author meant.

        Both ids empty means *no opinion about order* — a plain reparent, or a
        caller that predates ordering — and appends.

        `before_id` ALONE means immediately before that sibling, not first among
        all of them. It used to mean first, and the surface disagreed: the editor
        materializes a proposed node at the rank it would take (`plan-materialize.
        insertAt`, which places it exactly at its `beforeId`), so a plan drawn in
        the middle of the tree jumped to the top the moment it was accepted. The
        one promise the in-place proposal makes is that the node the reader judged
        is the node they get, and this broke it. It also gave every node proposed
        `before` the same anchor an identical rank, since they all resolved to the
        same "first".

        Placing a node first is still said the same way — by naming what it goes
        before, which is then the first node, whose own predecessor does not exist.
        """
        from codoc.model.rank import RankError, append_after, between

        after = self._sibling_rank(parent_id, after_id)
        before = self._sibling_rank(parent_id, before_id)
        if not after and before:
            after = self._rank_preceding(parent_id, before)
        if not after and not before:
            return self.rank_for_append(parent_id)
        if after and before and after >= before:
            # The two are no longer adjacent (something landed between them, or
            # one moved). "After A" is the more specific half of the intent —
            # it names where the author dropped the node — so keep it.
            before = ""
        try:
            return between(after, before)
        except RankError:
            return append_after(after)

    def rank_for_append(self, parent_id: str | None) -> str:
        """An order key placing a feature last among ``parent_id``'s children."""
        from codoc.model.rank import append_after

        if parent_id is None:
            row = self.conn.execute(
                "SELECT rank FROM features WHERE parent_id IS NULL AND retired=0"
                " ORDER BY rank DESC, created_at DESC LIMIT 1"
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT rank FROM features WHERE parent_id=? AND retired=0"
                " ORDER BY rank DESC, created_at DESC LIMIT 1",
                (parent_id,),
            ).fetchone()
        return append_after((row[0] if row else "") or "")

    def would_move_create_cycle(self, feature_id: str, new_parent_id: str | None) -> bool:
        """True if re-parenting ``feature_id`` under ``new_parent_id`` would form a cycle
        — i.e. ``new_parent_id`` is ``feature_id`` itself or one of its descendants.

        Walks UP the parent chain from ``new_parent_id``: reaching ``feature_id`` means
        the destination is inside the moved subtree. Moving to root (``None``) can never
        cycle. The ``seen`` guard bounds the walk so a PRE-EXISTING cycle (crash debris)
        can't spin forever — callers reject the move either way. A cycle is
        catastrophic: render/projection/sidecar all walk from the roots, so a cycle makes
        the whole subtree invisible AND unrecoverable while its features stay live+bound
        (Loop A reads their chunks as covered and never re-homes them)."""
        if not new_parent_id or not feature_id:
            return False
        seen: set[str] = set()
        cur: str | None = new_parent_id
        while cur:
            if cur == feature_id:
                return True
            if cur in seen:
                break  # pre-existing cycle — stop; the move is rejected regardless
            seen.add(cur)
            f = self.get_feature(cur)
            cur = f.parent_id if f else None
        return False

    def set_feature_writer(self, feature_id: str, writer: str, role: str = "") -> None:
        """Record who most recently wrote this feature, and in what role
        (see `feature_writers`)."""
        if not feature_id or not writer:
            return
        self.conn.execute(
            "INSERT INTO feature_writers (feature_id, writer, role, at) VALUES (?,?,?,?)"
            " ON CONFLICT(feature_id) DO UPDATE SET writer=excluded.writer,"
            " role=excluded.role, at=excluded.at",
            (feature_id, writer, role, datetime.now(timezone.utc).isoformat()),
        )
        self._commit()

    def feature_writer_info(self, feature_id: str) -> tuple[str, str]:
        """``(writer, role)`` for the last write — one lookup, since a resolver
        that asks "is this mine?" always also asks "and if not, whose?"."""
        row = self.conn.execute(
            "SELECT writer, role FROM feature_writers WHERE feature_id=?", (feature_id,)
        ).fetchone()
        if not row:
            return "", ""
        return (row[0] or ""), (row[1] or "")

    def feature_writer(self, feature_id: str) -> str:
        return self.feature_writer_info(feature_id)[0]

    def human_written_descriptions(self, limit: int = 2) -> list[str]:
        """The most recent descriptions a *person* wrote, newest first.

        These are shown to the describing model as the house voice to match.
        Samples beat any derived metric here: telling a model "the author writes
        short sentences at a high level of abstraction" produces its idea of
        that, while showing it two of their paragraphs produces theirs. Long
        enough to have a register (very short ones are titles in disguise), and
        capped hard — this is a style cue, not a corpus.
        """
        rows = self.conn.execute(
            "SELECT f.description FROM features f"
            " JOIN feature_writers w ON w.feature_id = f.id"
            " WHERE w.role = ? AND f.retired = 0 AND length(f.description) >= 60"
            " ORDER BY w.at DESC LIMIT ?",
            (ACTOR_HUMAN, max(0, limit)),
        ).fetchall()
        return [r[0] for r in rows if r and r[0]]

    # -- voice: what codoc has learned about how the author writes ---------
    #
    # See :mod:`codoc.model.voice` for why a lesson is provisional until
    # corroborated and why a retired one is kept. The store's only job here is
    # durability and the two reads the loop needs: everything (for ``codoc voice``
    # and for the harvest's dedup) and the injectable set (for the prompts).

    def upsert_lesson(self, lesson: StyleLesson) -> None:
        """Insert or update a lesson by its stable id.

        ``evidence`` and the source lists come from the caller rather than being
        incremented here, because deciding that a new inference is the SAME lesson
        as an existing one is a judgment about the two instructions, which lives in
        :mod:`codoc.loop.voice`. The store must not guess at it.
        """
        self.conn.execute(
            """
            INSERT INTO style_lessons (id, axis, instruction, example_before, example_after,
                                       axis_detail, scope_path, scope_files, status,
                                       evidence, sources, source_events,
                                       created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                axis=excluded.axis,
                instruction=excluded.instruction,
                example_before=excluded.example_before,
                example_after=excluded.example_after,
                axis_detail=excluded.axis_detail,
                scope_path=excluded.scope_path,
                scope_files=excluded.scope_files,
                status=excluded.status,
                evidence=excluded.evidence,
                sources=excluded.sources,
                source_events=excluded.source_events,
                updated_at=excluded.updated_at
            """,
            (
                lesson.id, lesson.axis.value, lesson.instruction,
                lesson.example_before, lesson.example_after, lesson.axis_detail,
                json.dumps(lesson.scope_path), json.dumps(lesson.scope_files),
                lesson.status.value, lesson.evidence,
                json.dumps(lesson.sources), json.dumps(lesson.source_events),
                lesson.created_at.to_str(), lesson.updated_at.to_str(),
            ),
        )
        self._commit()

    def all_lessons(self, *, include_retired: bool = True) -> list[StyleLesson]:
        """Every lesson, strongest evidence first.

        Retired ones are included by default because both callers that want the
        whole set — ``codoc voice`` and the harvest's dedup — need to see them: the
        first to show what was refused, the second to avoid re-learning it.
        """
        sql = "SELECT * FROM style_lessons"
        if not include_retired:
            sql += f" WHERE status <> '{LessonStatus.RETIRED.value}'"
        sql += " ORDER BY evidence DESC, updated_at DESC"
        return [_row_to_lesson(r) for r in self.conn.execute(sql).fetchall()]

    def injectable_lessons(self) -> list[StyleLesson]:
        """The lessons corroborated enough to shape prose (``status = active``)."""
        rows = self.conn.execute(
            "SELECT * FROM style_lessons WHERE status = ?"
            " ORDER BY evidence DESC, updated_at DESC",
            (LessonStatus.ACTIVE.value,),
        ).fetchall()
        return [_row_to_lesson(r) for r in rows]

    def get_lesson(self, lesson_id: str) -> StyleLesson | None:
        row = self.conn.execute(
            "SELECT * FROM style_lessons WHERE id=?", (lesson_id,)
        ).fetchone()
        return _row_to_lesson(row) if row else None

    def set_lesson_status(self, lesson_id: str, status: LessonStatus) -> bool:
        """Promote or retire one lesson by hand. Returns whether it existed.

        The author-facing half of PRELUDE's argument for keeping preferences in
        text: a learned instruction is only safe when the person it models can read
        it and say no.
        """
        cur = self.conn.execute(
            "UPDATE style_lessons SET status=?, updated_at=? WHERE id=?",
            (status.value, HLC.now().to_str(), lesson_id),
        )
        self._commit()
        return cur.rowcount > 0

    # -- store_meta: the store's own small bookkeeping ---------------------

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM store_meta WHERE key=?", (key,)
        ).fetchone()
        return (row[0] if row and row[0] is not None else default)

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO store_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._commit()

    def human_amend_events(
        self, *, since: str = "", limit: int = 40,
    ) -> list[tuple[str, Event]]:
        """Applied AMENDs a PERSON made, oldest first, after cursor ``since``.

        Returns ``(cursor, event)`` pairs. The harvest's input: each event is a human
        rewriting prose that was already on the page, and ``op.prev_description`` /
        ``op.prev_written_by`` say what it replaced and who had written it. Oldest
        first because the harvest advances a watermark through them and a partial run
        must leave a resumable position, which reverse order cannot.

        The cursor is the **insertion order**, not the HLC stamp, and that is
        load-bearing. ``HLC.now()`` reports the wall clock with ``logical_time`` fixed
        at zero, so every event Loop B applies inside one millisecond carries an
        IDENTICAL ``at`` — and a batch of drained commands is exactly that. Paging on
        ``at > since`` would then skip whatever shared the watermark's millisecond,
        losing those rewrites permanently, while ``at >= since`` would re-read them
        forever. Insertion order is total and has no ties, which is the only property
        a resumable cursor needs. Zero-padded so the value compares correctly as the
        string that ``store_meta`` holds.

        Filtered on ``actor`` rather than ``source``: ``source`` says which channel
        carried the edit (``loop_b`` carries both a person's typing and the loop's
        own maintenance), while ``actor`` says who authored it, which is the
        question here.
        """
        after = int(since) if since.strip().isdigit() else 0
        rows = self.conn.execute(
            "SELECT rowid AS _seq, * FROM events WHERE applied=1 AND actor=?"
            " AND rowid > ? ORDER BY rowid ASC LIMIT ?",
            (ACTOR_HUMAN, after, max(0, limit)),
        ).fetchall()
        return [(f"{r['_seq']:020d}", _row_to_event(r)) for r in rows]

    def human_comment_threads(
        self, *, since: str = "", limit: int = 20,
    ) -> list[tuple[str, "CommentThread"]]:
        """Comment threads a PERSON wrote, oldest first, after cursor ``since``.

        The second input to the style harvest, and the more direct of the two: a
        rewrite makes codoc infer the preference from a gap, while a note states it.

        Cursor semantics are :meth:`human_amend_events`'s, for the same reason — the
        insertion order, zero-padded so it compares as the string ``store_meta``
        holds. A thread's rowid survives its later edits (``upsert_comment`` is an
        ``ON CONFLICT DO UPDATE``, not a delete-and-reinsert), so the cursor stays
        valid across a reworded note and a resolution.

        Threads are taken as they ARRIVE rather than once they resolve. A note is a
        stated preference the moment it is typed; waiting for its directive to land
        would delay every lesson by a build, and a ``code``-scope note never rewrites
        prose at all, so waiting for a resolution would learn nothing from it ever.
        """
        after = int(since) if since.strip().isdigit() else 0
        rows = self.conn.execute(
            "SELECT rowid AS _seq, * FROM comments WHERE author=? AND rowid > ?"
            " ORDER BY rowid ASC LIMIT ?",
            (Provenance.HUMAN.value, after, max(0, limit)),
        ).fetchall()
        return [(f"{r['_seq']:020d}", _row_to_comment(r)) for r in rows]

    def _next_version(self, feature_id: str) -> str:
        """The next version stamp for a feature — strictly after its current one.

        ``HLC.now()`` is the raw wall clock, so a backwards clock adjustment (an NTP
        correction, a laptop waking up elsewhere) can stamp a change with a version
        LOWER than the change it followed. Everything downstream asks "is this
        newer?" — above all the webview's per-feature adopt gate — and would then
        refuse the update indefinitely, leaving the editor showing a feature the
        store has already retired. ``advance()`` is monotonic per feature by
        construction, so the answer stays truthful whatever the clock does.
        """
        row = self.conn.execute(
            "SELECT updated_at FROM features WHERE id=?", (feature_id,)
        ).fetchone()
        current = HLC.from_str(row[0]) if row and row[0] else None
        return (current.advance() if current else HLC.now()).to_str()

    def retire_feature(self, feature_id: str) -> None:
        # lifecycle is authoritative; retired=1 kept in sync for back-compat readers.
        self.conn.execute(
            "UPDATE features SET lifecycle='retired', retired=1, updated_at=? WHERE id=?",
            (self._next_version(feature_id), feature_id),
        )
        self._commit()

    def unretire_feature(self, feature_id: str) -> None:
        """Bring a retired feature back to ``active`` — the undo of a soft delete.
        Guarded to ``retired`` rows so it only ever resurrects a tombstone (a
        planned/active feature is untouched). Used when a deleted node reappears in
        the doc (the human pressed undo / re-added it)."""
        self.conn.execute(
            "UPDATE features SET lifecycle='active', retired=0, realized=1, updated_at=?"
            " WHERE id=? AND lifecycle='retired'",
            (self._next_version(feature_id), feature_id),
        )
        self._commit()

    def mark_realized(self, feature_id: str) -> None:
        """Promote a plan placeholder to ``active`` (code now binds to it) — the
        named planned→active lifecycle transition. Guarded to ``planned`` rows so
        it can never resurrect a retired feature; ``realized=1`` is kept in sync."""
        self.conn.execute(
            "UPDATE features SET lifecycle='active', realized=1, updated_at=?"
            " WHERE id=? AND lifecycle='planned'",
            (self._next_version(feature_id), feature_id),
        )
        self._commit()

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
        self._commit()

    def delete_binding(self, file: str, symbol_path: str) -> None:
        self.conn.execute(
            "DELETE FROM bindings WHERE file=? AND symbol_path=?", (file, symbol_path)
        )
        self._commit()

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
        self._commit()
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

    def bindings_by_feature(self) -> dict[str, list[Binding]]:
        """All bindings grouped by ``feature_id``, each group sorted by
        ``(file, symbol_path)`` to match :meth:`bindings_for_feature`.

        One bulk read for the whole-tree render / read paths, which would
        otherwise issue a ``bindings_for_feature`` query per feature."""
        grouped: dict[str, list[Binding]] = {}
        for b in self.all_bindings():
            grouped.setdefault(b.feature_id, []).append(b)
        for group in grouped.values():
            group.sort(key=lambda b: (b.file, b.symbol_path))
        return grouped

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
        self._commit()

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
        self._commit()

    def delete_blocks_for_feature(self, feature_id: str) -> None:
        self.conn.execute("DELETE FROM blocks WHERE feature_id=?", (feature_id,))
        self._commit()

    # -- marks (tracked-change authorship spans, R8) ----------------------
    def upsert_mark(self, m: Mark) -> None:
        """Insert or update an authorship mark by its stable id."""
        self.conn.execute(
            """
            INSERT INTO marks (id, feature_id, kind, provenance, anchor_start, anchor_end, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                feature_id=excluded.feature_id,
                kind=excluded.kind,
                provenance=excluded.provenance,
                anchor_start=excluded.anchor_start,
                anchor_end=excluded.anchor_end,
                updated_at=excluded.updated_at
            """,
            (
                m.id, m.feature_id, m.kind.value, m.provenance.value,
                m.anchor_start, m.anchor_end, m.created_at.to_str(), m.updated_at.to_str(),
            ),
        )
        self._commit()

    def marks_for_feature(self, feature_id: str) -> list[Mark]:
        rows = self.conn.execute(
            "SELECT * FROM marks WHERE feature_id=? ORDER BY anchor_start, created_at", (feature_id,)
        ).fetchall()
        return [_row_to_mark(r) for r in rows]

    def all_marks(self) -> list[Mark]:
        return [_row_to_mark(r) for r in self.conn.execute("SELECT * FROM marks").fetchall()]

    def delete_mark(self, mark_id: str) -> None:
        self.conn.execute("DELETE FROM marks WHERE id=?", (mark_id,))
        self._commit()

    def delete_marks_for_feature(self, feature_id: str) -> None:
        self.conn.execute("DELETE FROM marks WHERE feature_id=?", (feature_id,))
        self._commit()

    # -- comments (inline steering threads, R9) ---------------------------
    def upsert_comment(self, c: CommentThread) -> None:
        """Insert or update a comment thread by its stable id."""
        self.conn.execute(
            """
            INSERT INTO comments (id, feature_id, body, author, status, anchor_start, anchor_end,
                                  anchor_text, code_refs, scope, directive_id, replies,
                                  media_ref, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                feature_id=excluded.feature_id,
                body=excluded.body,
                author=excluded.author,
                status=excluded.status,
                anchor_start=excluded.anchor_start,
                anchor_end=excluded.anchor_end,
                anchor_text=excluded.anchor_text,
                code_refs=excluded.code_refs,
                scope=excluded.scope,
                -- Never cleared by a later write: a re-sent steer (the author edited
                -- their note) carries no directive id, and blanking the one already
                -- minted would orphan the thread from the work it caused.
                directive_id=CASE WHEN excluded.directive_id != '' THEN excluded.directive_id
                                  ELSE comments.directive_id END,
                -- Same rule as directive_id: a re-sent steer carries no replies, and
                -- blanking them would erase the answers the thread already got.
                replies=CASE WHEN excluded.replies != '[]' THEN excluded.replies
                             ELSE comments.replies END,
                media_ref=excluded.media_ref,
                updated_at=excluded.updated_at
            """,
            (
                c.id, c.feature_id, c.body, c.author.value, c.status.value,
                c.anchor_start, c.anchor_end, c.anchor_text,
                json.dumps(c.code_refs, ensure_ascii=False), c.scope.value, c.directive_id,
                json.dumps([r.model_dump(mode="json") for r in c.replies], ensure_ascii=False),
                c.media_ref, c.created_at.to_str(), c.updated_at.to_str(),
            ),
        )
        self._commit()

    def comments_for_feature(self, feature_id: str) -> list[CommentThread]:
        rows = self.conn.execute(
            "SELECT * FROM comments WHERE feature_id=? ORDER BY anchor_start, created_at", (feature_id,)
        ).fetchall()
        return [_row_to_comment(r) for r in rows]

    def all_comments(self) -> list[CommentThread]:
        return [_row_to_comment(r) for r in self.conn.execute("SELECT * FROM comments").fetchall()]

    def delete_comment(self, comment_id: str) -> None:
        self.conn.execute("DELETE FROM comments WHERE id=?", (comment_id,))
        self._commit()

    def delete_comments_for_feature(self, feature_id: str) -> None:
        self.conn.execute("DELETE FROM comments WHERE feature_id=?", (feature_id,))
        self._commit()

    # -- events -----------------------------------------------------------
    def append_event(self, e: Event) -> None:
        self.conn.execute(
            "INSERT INTO events (id, at, source, op_json, applied, accepted_at, actor, mode, caused_by, feature_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                e.op.feature_id or "",
            ),
        )
        self._commit()

    def get_event(self, event_id: str) -> Event | None:
        row = self.conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def pending_events(self) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE applied=0 ORDER BY at"
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    def applied_event_for_cause(self, cause_id: str) -> Event | None:
        """The applied event that cites ``cause_id`` as its ``caused_by`` — the durable
        trace of a verdict. Accepting a proposal applies a NEW event (fresh id) stamped
        ``caused_by=<proposal id>``, and the proposal row itself is deleted on drain —
        so this lookup is the only way left to recover the outcome, and the feature id
        an accepted ADD minted (the applied event's op carries it). ``None`` means no
        applied event cites the proposal: it was rejected, withdrawn, or superseded —
        either way it will never be applied."""
        row = self.conn.execute(
            "SELECT * FROM events WHERE applied=1 AND caused_by=? ORDER BY at LIMIT 1",
            (cause_id,),
        ).fetchone()
        return _row_to_event(row) if row else None

    def recent_events(self, limit: int = 20) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    def events_for_feature(self, feature_id: str, limit: int = 50) -> list[Event]:
        """The applied change history of one feature, newest first — the blame
        substrate: who (actor) changed what (op) how (mode) and why (caused_by /
        rationale), via the indexed ``feature_id`` column."""
        rows = self.conn.execute(
            "SELECT * FROM events WHERE feature_id=? AND applied=1"
            " ORDER BY at DESC LIMIT ?",
            (feature_id, limit),
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    def implemented_directive_ids(self, ids: set[str]) -> set[str]:
        """Which of ``ids`` some APPLIED event cites as its ``caused_by``.

        The ledger's own evidence that a queued directive was carried out: the agent
        implements it and reflects the result with ``caused_by=<directive id>``, so the
        id turns up on an applied event. Used to close the realize queue from what
        actually happened rather than from a file being deleted afterwards — see
        ``loop_b._prune_implemented_directives``.
        """
        if not ids:
            return set()
        out: set[str] = set()
        # Chunked so a large queue can't exceed SQLite's variable limit.
        batch = list(ids)
        for i in range(0, len(batch), 400):
            part = batch[i:i + 400]
            marks = ",".join("?" * len(part))
            rows = self.conn.execute(
                f"SELECT DISTINCT caused_by FROM events"
                f" WHERE applied=1 AND caused_by IN ({marks})",
                part,
            ).fetchall()
            out.update(r[0] for r in rows if r[0])
        return out

    def mark_applied(self, event_id: str) -> None:
        self.conn.execute(
            "UPDATE events SET applied=1, accepted_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), event_id),
        )
        self._commit()

    def delete_event(self, event_id: str) -> None:
        self.conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        self._commit()

    # -- applied-command ledger (idempotency, U3 / KTD8) ------------------
    def command_applied(self, cmd_id: str) -> bool:
        """True if the identity-keyed command ``cmd_id`` has already been applied —
        the at-least-once → exactly-once guard for the commands channel."""
        row = self.conn.execute(
            "SELECT 1 FROM applied_commands WHERE id=?", (cmd_id,)
        ).fetchone()
        return row is not None

    def mark_command_applied(self, cmd_id: str) -> None:
        """Record ``cmd_id`` as applied (HLC-stamped). Idempotent — re-stamping an
        already-recorded id is a no-op (INSERT OR IGNORE keeps the first stamp)."""
        self.conn.execute(
            "INSERT OR IGNORE INTO applied_commands (id, applied_at) VALUES (?, ?)",
            (cmd_id, HLC.now().to_str()),
        )
        self._commit()

    def try_claim_command(self, cmd_id: str) -> bool:
        """Atomically claim ``cmd_id`` for application — ``INSERT OR IGNORE`` into
        the ledger, returning True ONLY when the row was newly inserted (this caller
        won the claim). A re-sent / crash-replayed id loses the claim (returns False)
        so it is skipped, never double-applied.

        Crash-consistency (KTD8): the claim is written WITHOUT committing, so the
        caller can wrap claim + ``apply_op``'s mutation in one transaction (see
        ``Store.transaction``) — both commit together or roll back together. If the
        process dies after the claim but before that commit, SQLite rolls back the
        uncommitted claim, so the command is re-delivered and re-applied (no silent
        drop). ``rowcount`` is 1 for a fresh insert, 0 when the id already existed."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO applied_commands (id, applied_at) VALUES (?, ?)",
            (cmd_id, HLC.now().to_str()),
        )
        return cur.rowcount == 1

    @contextmanager
    def transaction(self):
        """A single atomic unit around several mutations (claim + apply): commit on
        clean exit, roll back on any exception. The store's methods commit eagerly via
        :meth:`_commit`; this SUPPRESSES those inner commits for the duration — they
        all land (or none do) on this context's single commit.

        Used by Loop B's command apply so a crash between the ledger claim and the
        ``apply_op`` store mutation leaves NEITHER (the command is re-delivered),
        never the claim alone (which would drop the command's effect). Not reentrant —
        the suppress flag is a bool, and Loop B never nests these."""
        self._suppress_commit = True
        try:
            yield self
        except BaseException:
            self._suppress_commit = False
            self.conn.rollback()
            raise
        else:
            self._suppress_commit = False
            self.conn.commit()

    def feature_by_local_id(self, local_id: str) -> Feature | None:
        """The LIVE (non-retired) feature owning ``local_id`` — the webview's
        client-side node id (KTD8). Used to fold a re-emitted ``add`` (same localId,
        possibly a changed title) onto the feature it already minted, instead of
        minting a second one. Empty ``local_id`` never matches (most rows carry '')."""
        if not local_id:
            return None
        row = self.conn.execute(
            "SELECT * FROM features WHERE local_id=? AND retired=0 ORDER BY created_at LIMIT 1",
            (local_id,),
        ).fetchone()
        return _row_to_feature(row) if row else None

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
        self._commit()

    def delete_edges_from_files(self, files: set[str]) -> None:
        if not files:
            return
        placeholders = ",".join("?" * len(files))
        self.conn.execute(
            f"DELETE FROM code_edges WHERE src_file IN ({placeholders})", tuple(files)
        )
        self._commit()

    def drop_all_edges(self) -> None:
        self.conn.execute("DELETE FROM code_edges")
        self._commit()

    def has_edges(self) -> bool:
        """Whether ANY graph edge exists — the cheap emptiness probe the reconcile
        pass uses to decide if a never-built graph needs a one-time full build."""
        return self.conn.execute("SELECT 1 FROM code_edges LIMIT 1").fetchone() is not None

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
        rank=(r["rank"] if "rank" in keys else ""),
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


def _row_to_mark(r: sqlite3.Row) -> Mark:
    return Mark(
        id=r["id"],
        feature_id=r["feature_id"],
        kind=MarkKind(r["kind"]),
        provenance=Provenance(r["provenance"]),
        anchor_start=r["anchor_start"],
        anchor_end=r["anchor_end"],
        created_at=HLC.from_str(r["created_at"]),
        updated_at=HLC.from_str(r["updated_at"]),
    )


def _json_list(raw: object) -> list:
    """A JSON list column as a list, tolerating anything else.

    A lesson's scope and source lists are advisory context for retrieval, so a row
    written by a different version — or hand-edited — must degrade to "no scope
    recorded" rather than take down every read of the table.
    """
    if not raw:
        return []
    try:
        value = json.loads(raw if isinstance(raw, str) else str(raw))
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _row_to_lesson(r: sqlite3.Row) -> StyleLesson:
    keys = r.keys()
    return StyleLesson(
        id=r["id"],
        axis=LessonAxis(r["axis"]),
        instruction=r["instruction"],
        example_before=r["example_before"],
        example_after=r["example_after"],
        # Presence-checked: added after the table shipped, so a connection opened
        # before the migration ran can still read the row.
        axis_detail=(r["axis_detail"] if "axis_detail" in keys else "") or "",
        scope_path=[str(p) for p in _json_list(r["scope_path"])],
        scope_files=[str(f) for f in _json_list(r["scope_files"])],
        status=LessonStatus(r["status"]),
        evidence=r["evidence"],
        sources=[str(s) for s in _json_list(r["sources"])],
        source_events=[str(s) for s in _json_list(r["source_events"])],
        created_at=HLC.from_str(r["created_at"]),
        updated_at=HLC.from_str(r["updated_at"]),
    )


def _row_to_comment(r: sqlite3.Row) -> CommentThread:
    keys = r.keys()
    return CommentThread(
        id=r["id"],
        feature_id=r["feature_id"],
        body=r["body"],
        author=Provenance(r["author"]),
        status=CommentStatus(r["status"]),
        anchor_start=r["anchor_start"],
        anchor_end=r["anchor_end"],
        # Presence-checked per column: a db written before these existed is migrated on
        # open, but a row read through a connection that predates the migration (or a
        # hand-built fixture) must still parse rather than raise.
        anchor_text=(r["anchor_text"] if "anchor_text" in keys else ""),
        code_refs=_json_list(r["code_refs"]) if "code_refs" in keys else [],
        scope=CommentScope(r["scope"]) if "scope" in keys and r["scope"] else CommentScope.CODE,
        directive_id=(r["directive_id"] if "directive_id" in keys else ""),
        replies=_replies(r["replies"]) if "replies" in keys else [],
        media_ref=r["media_ref"],
        created_at=HLC.from_str(r["created_at"]),
        updated_at=HLC.from_str(r["updated_at"]),
    )


def _replies(raw) -> list[CommentReply]:
    """A JSON column read as replies; anything unreadable reads as none.

    Never raises: a corrupt cell costs the thread its answers, which is a degraded read
    rather than a failed one for the whole tree."""
    try:
        rows = json.loads(raw) if isinstance(raw, str) and raw else []
    except (ValueError, TypeError):
        return []
    if not isinstance(rows, list):
        return []
    out: list[CommentReply] = []
    for row in rows:
        if isinstance(row, dict):
            try:
                out.append(CommentReply.model_validate(row))
            except Exception:  # noqa: BLE001 — one bad reply must not lose the rest
                continue
    return out


def _json_list(raw) -> list[str]:
    """A JSON string column read as a list of strings; anything else reads as empty.

    Never raises: a corrupt cell costs the comment its code targets (the directive falls
    back to the feature's own bindings), which is a degraded answer rather than a failed
    read of the whole tree."""
    try:
        val = json.loads(raw) if isinstance(raw, str) and raw else []
    except (ValueError, TypeError):
        return []
    return [str(x) for x in val] if isinstance(val, list) else []


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
