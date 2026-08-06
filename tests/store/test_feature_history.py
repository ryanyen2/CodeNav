"""Blame substrate: the events feature_id column, its migration backfill, the
per-feature history query, and the ADD pre-mint that makes creation findable."""
from __future__ import annotations

import sqlite3

import pytest

from codoc.loop.apply import apply_op
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def test_events_for_feature_returns_only_that_features_applied_events(store):
    f = Feature(title="Mine")
    store.upsert_feature(f)
    g = Feature(title="Other")
    store.upsert_feature(g)

    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id, description="v1"),
             store, source="user", applied=True)
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=g.id, description="other"),
             store, source="user", applied=True)
    # A pending proposal on f must NOT appear in blame (it hasn't happened yet).
    apply_op(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id),
             store, source="loop_a", applied=False)

    events = store.events_for_feature(f.id)
    assert [e.op.kind for e in events] == [NodeOpKind.AMEND]
    assert events[0].op.description == "v1"


def test_history_carries_provenance(store):
    f = Feature(title="Mine")
    store.upsert_feature(f)
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id, description="v2",
                    rationale="user asked for idempotent retries"),
             store, source="loop_a_agent", applied=True,
             actor="claude-code", mode="auto", caused_by="d-1234")

    (e,) = store.events_for_feature(f.id)
    assert (e.actor, e.mode, e.caused_by) == ("claude-code", "auto", "d-1234")
    assert e.op.rationale == "user asked for idempotent retries"


def test_applied_add_premints_id_so_creation_is_findable(store):
    ev = apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Fresh",
                         description="a new thing"),
                  store, source="user", applied=True)
    fid = ev.op.feature_id
    assert fid  # minted before the event was recorded
    assert store.get_feature(fid) is not None
    events = store.events_for_feature(fid)
    assert [e.op.kind for e in events] == [NodeOpKind.ADD_NODE]


def test_pending_add_stays_bare(store):
    ev = apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Maybe"),
                  store, source="loop_a", applied=False)
    assert ev.op.feature_id is None


def test_migration_backfills_feature_id_from_op_json(tmp_path):
    """A v2 DB (no feature_id column) must gain the column, backfilled from the
    op payload, on open."""
    db_path = tmp_path / "codoc.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE features (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            parent_id TEXT, retired INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE events (
            id TEXT PRIMARY KEY, at TEXT NOT NULL, source TEXT NOT NULL,
            op_json TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 1, accepted_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO events (id, at, source, op_json) VALUES (?, ?, ?, ?)",
        ("e-old1", "0001", "user",
         '{"kind": "amend", "feature_id": "f-legacy", "description": "old"}'),
    )
    conn.commit()
    conn.close()

    with open_store(tmp_path) as store:
        row = store.conn.execute(
            "SELECT feature_id FROM events WHERE id='e-old1'").fetchone()
        assert row["feature_id"] == "f-legacy"


def test_mcp_feature_history(tmp_path):
    from codoc.mcp import tools

    with open_store(tmp_path) as store:
        f = Feature(title="Traced")
        store.upsert_feature(f)
        apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id,
                        description="new prose", rationale="why"),
                 store, source="user", applied=True, actor="human", mode="pen")
        fid = f.id

    out = tools.feature_history(str(tmp_path), f"⟨{fid}⟩")
    assert out["ok"] and out["title"] == "Traced"
    (entry,) = out["events"]
    assert entry["kind"] == "amend"
    assert entry["actor"] == "human"
    assert entry["description"] == "new prose"
    assert entry["at"].startswith("20")  # ISO wall-clock

    missing = tools.feature_history(str(tmp_path), "f-nope")
    assert missing["ok"] is False


def test_migration_survives_corrupt_op_json(tmp_path):
    """One torn/corrupt historical op_json row must NOT brick the v3 backfill
    (json_extract raises on malformed JSON without the json_valid guard) —
    a bricked migration means every codoc command dies on store open."""
    conn = sqlite3.connect(tmp_path / "codoc.db")
    conn.executescript(
        """
        CREATE TABLE features (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            parent_id TEXT, retired INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE events (
            id TEXT PRIMARY KEY, at TEXT NOT NULL, source TEXT NOT NULL,
            op_json TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 1, accepted_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO events (id, at, source, op_json) VALUES (?, ?, ?, ?)",
                 ("e-good", "0001", "user", '{"kind": "amend", "feature_id": "f-1"}'))
    conn.execute("INSERT INTO events (id, at, source, op_json) VALUES (?, ?, ?, ?)",
                 ("e-torn", "0002", "user", '{"kind": "amend", TRUNCATED-BY-KILL'))
    conn.commit()
    conn.close()

    with open_store(tmp_path) as store:  # must not raise
        rows = {r["id"]: r["feature_id"] for r in
                store.conn.execute("SELECT id, feature_id FROM events")}
    assert rows == {"e-good": "f-1", "e-torn": ""}
