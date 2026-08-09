"""Phase 1 — data model + 3-table store."""
from __future__ import annotations

import pytest

from codoc.model.binding import Binding
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import Store, open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


# -- features ------------------------------------------------------------
def test_feature_roundtrip(store):
    f = Feature(title="Index snapshot diff", description="Diffs the index.")
    store.upsert_feature(f)
    got = store.get_feature(f.id)
    assert got is not None
    assert got.title == "Index snapshot diff"
    assert got.description == "Diffs the index."
    assert got.parent_id is None
    assert got.retired is False


def test_realized_defaults_true_and_roundtrips(store):
    f = Feature(title="Placeholder", realized=False)
    store.upsert_feature(f)
    assert store.get_feature(f.id).realized is False
    store.mark_realized(f.id)
    assert store.get_feature(f.id).realized is True
    # default for a plain feature is realized
    g = Feature(title="Plain")
    store.upsert_feature(g)
    assert store.get_feature(g.id).realized is True


def test_realized_column_migrates_on_legacy_db(tmp_path):
    """A pre-`realized` features table gains the column (default 1) on reopen."""
    import sqlite3

    from codoc.model.hlc import HLC

    now = HLC.now().to_str()
    db = tmp_path / "codoc.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE features (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            parent_id TEXT, retired INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO features VALUES ('f-legacy','Old','',NULL,0,?,?)", (now, now)
    )
    conn.commit()
    conn.close()

    s = Store(db).open()
    try:
        got = s.get_feature("f-legacy")
        assert got is not None and got.realized is True
    finally:
        s.close()


def test_feature_ids_are_prefixed_and_unique():
    a = Feature(title="A")
    b = Feature(title="B")
    assert a.id.startswith("f-")
    assert a.id != b.id


def test_upsert_feature_updates(store):
    f = Feature(title="Old title")
    store.upsert_feature(f)
    f.title = "New title"
    f.description = "now described"
    store.upsert_feature(f)
    got = store.get_feature(f.id)
    assert got.title == "New title"
    assert got.description == "now described"
    # still a single row
    assert len(store.list_features()) == 1


def test_children_and_retire(store):
    root = Feature(title="Root")
    child = Feature(title="Child", parent_id=root.id)
    store.upsert_feature(root)
    store.upsert_feature(child)

    assert [c.id for c in store.children(None)] == [root.id]
    assert [c.id for c in store.children(root.id)] == [child.id]

    store.retire_feature(child.id)
    assert store.children(root.id) == []
    assert store.get_feature(child.id).retired is True
    # retired excluded by default, included on request
    assert [f.id for f in store.list_features()] == [root.id]
    assert {f.id for f in store.list_features(include_retired=True)} == {root.id, child.id}


# -- bindings ------------------------------------------------------------
def test_binding_roundtrip_and_anchor_lookup(store):
    f = Feature(title="F")
    store.upsert_feature(f)
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::foo", fingerprint="h1")
    store.upsert_binding(b)

    got = store.binding_at("a.py", "a.py::foo")
    assert got is not None and got.feature_id == f.id and got.fingerprint == "h1"
    assert [x.id for x in store.bindings_for_feature(f.id)] == [got.id]


def test_binding_unique_anchor_rebinds(store):
    f1 = Feature(title="F1")
    f2 = Feature(title="F2")
    store.upsert_feature(f1)
    store.upsert_feature(f2)

    store.upsert_binding(Binding(feature_id=f1.id, file="a.py", symbol_path="a.py::foo", fingerprint="h1"))
    # same anchor, new owner + fingerprint → updates in place, no duplicate row
    store.upsert_binding(Binding(feature_id=f2.id, file="a.py", symbol_path="a.py::foo", fingerprint="h2"))

    assert len(store.all_bindings()) == 1
    got = store.binding_at("a.py", "a.py::foo")
    assert got.feature_id == f2.id
    assert got.fingerprint == "h2"
    assert store.bindings_for_feature(f1.id) == []


def test_delete_binding_and_bindings_in_files(store):
    f = Feature(title="F")
    store.upsert_feature(f)
    store.upsert_binding(Binding(feature_id=f.id, file="a.py", symbol_path="a.py::foo", fingerprint="h"))
    store.upsert_binding(Binding(feature_id=f.id, file="b.py", symbol_path="b.py::bar", fingerprint="h"))

    assert {b.file for b in store.bindings_in_files({"a.py"})} == {"a.py"}
    store.delete_binding("a.py", "a.py::foo")
    assert store.binding_at("a.py", "a.py::foo") is None
    assert len(store.all_bindings()) == 1


# -- events --------------------------------------------------------------
def test_event_proposal_lifecycle(store):
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title="New thing", description="desc", rationale="no node fits")
    e = Event(source="loop_a", op=op, applied=False)
    store.append_event(e)

    pending = store.pending_events()
    assert [p.id for p in pending] == [e.id]
    got = pending[0]
    assert got.op.kind is NodeOpKind.ADD_NODE
    assert got.op.title == "New thing"
    assert got.is_proposal is True

    store.mark_applied(e.id)
    assert store.pending_events() == []
    reloaded = store.get_event(e.id)
    assert reloaded.applied is True
    assert reloaded.accepted_at is not None


def test_event_op_bindings_roundtrip(store):
    op = NodeOp(
        kind=NodeOpKind.ATTACH,
        feature_id="f-123",
        bindings=[("a.py", "a.py::foo"), ("a.py", "a.py::bar")],
    )
    e = Event(source="loop_a", op=op, applied=True)
    store.append_event(e)
    got = store.get_event(e.id).op
    assert got.bindings == [("a.py", "a.py::foo"), ("a.py", "a.py::bar")]


def test_delete_event(store):
    e = Event(source="user", op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id="f-1"), applied=False)
    store.append_event(e)
    store.delete_event(e.id)
    assert store.get_event(e.id) is None
    assert store.pending_events() == []


def test_event_provenance_roundtrip(store):
    """Ledger fields (actor/mode/caused_by) persist and reload."""
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id="f-1", description="new prose")
    e = Event(source="user", op=op, applied=True,
              actor="human", mode="pen", caused_by="d-ab12cd34")
    store.append_event(e)
    got = store.get_event(e.id)
    assert (got.actor, got.mode, got.caused_by) == ("human", "pen", "d-ab12cd34")


def test_event_ledger_columns_migrate_on_legacy_db(tmp_path):
    """A pre-ledger events table gains actor/mode/caused_by (default '') on reopen."""
    import sqlite3

    from codoc.model.hlc import HLC

    db = tmp_path / "codoc.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE events (
            id TEXT PRIMARY KEY, at TEXT NOT NULL, source TEXT NOT NULL,
            op_json TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 1,
            accepted_at TEXT
        );
        """
    )
    op_json = NodeOp(kind=NodeOpKind.AMEND, feature_id="f-1").model_dump_json()
    conn.execute(
        "INSERT INTO events VALUES ('e-legacy',?, 'user', ?, 1, NULL)",
        (HLC.now().to_str(), op_json),
    )
    conn.commit()
    conn.close()

    s = Store(db).open()
    try:
        got = s.get_event("e-legacy")
        assert got is not None
        # Legacy rows carry no stored provenance; the model infers it from the
        # source on load ("user" → human/pen). caused_by stays unknown.
        assert (got.actor, got.mode, got.caused_by) == ("human", "pen", "")
        # New events on the migrated db carry full provenance.
        e = Event(source="user", op=NodeOp(kind=NodeOpKind.AMEND, feature_id="f-1"),
                  actor="human", mode="suggest")
        s.append_event(e)
        assert s.get_event(e.id).mode == "suggest"
    finally:
        s.close()


def test_concurrent_first_opens_of_a_legacy_db_all_succeed(tmp_path):
    """The daemon, the CLI and the MCP server share one store, so several processes can
    reach the migration at once on a workspace's first open after an upgrade. Nothing
    serializes them (executescript commits, and DDL auto-commits), so two openers both
    see "column missing" and both ALTER — and the loser used to raise `duplicate column
    name`, i.e. a failed open on a workspace that IS correctly migrated."""
    import sqlite3
    import threading

    from codoc.model.hlc import HLC

    now = HLC.now().to_str()
    db = tmp_path / "codoc.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE features (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            parent_id TEXT, retired INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO features (id,title,parent_id,created_at,updated_at)"
        " VALUES ('f-legacy','Legacy',NULL,?,?)", (now, now))
    conn.commit()
    conn.close()

    ready = threading.Barrier(4)
    errors: list[BaseException] = []

    def opener() -> None:
        try:
            ready.wait(timeout=5)
            s = Store(db).open()
            try:
                assert s.get_feature("f-legacy") is not None
            finally:
                s.close()
        except BaseException as exc:   # noqa: BLE001 — the assertion IS "nothing raised"
            errors.append(exc)

    threads = [threading.Thread(target=opener) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, errors
    # …and the workspace ends up migrated exactly once, with the rank backfill intact.
    s = Store(db).open()
    try:
        assert s.get_feature("f-legacy").rank
    finally:
        s.close()


def test_migrate_is_safe_to_run_twice_on_the_same_connection(tmp_path):
    """Every step is gated on the state it would change, so re-entering the migration —
    which is what a concurrent opener effectively does — writes nothing new."""
    s = open_store(tmp_path)
    try:
        f = Feature(title="Ranked", rank=s.rank_for_append(None))
        s.upsert_feature(f)
        before = s.get_feature(f.id)
        s._migrate()
        s._migrate()
        after = s.get_feature(f.id)
        assert (after.rank, after.lifecycle) == (before.rank, before.lifecycle)
    finally:
        s.close()


def test_store_reopen_persists(tmp_path):
    s = open_store(tmp_path)
    f = Feature(title="Persisted")
    s.upsert_feature(f)
    s.close()

    s2 = Store(tmp_path / "codoc.db").open()
    assert s2.get_feature(f.id).title == "Persisted"
    s2.close()
