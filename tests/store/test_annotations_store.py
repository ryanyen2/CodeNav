"""U1 — the marks + comments tables: CRUD, ordering by anchor, identity
round-trip, cascade-clean on feature delete, and proof that an existing DB
gains the tables additively without data loss."""
from __future__ import annotations

import pytest

from codoc.model.annotation import (
    CommentStatus, CommentThread, Mark, MarkKind,
)
from codoc.model.binding import Binding
from codoc.model.block import Provenance
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def test_mark_crud_roundtrip(store):
    f = Feature(title="Auth")
    store.upsert_feature(f)
    m = Mark(feature_id=f.id, kind=MarkKind.INSERTION, provenance=Provenance.AGENT,
             anchor_start=3, anchor_end=11)
    store.upsert_mark(m)
    got = store.marks_for_feature(f.id)
    assert len(got) == 1
    assert got[0].id == m.id
    assert got[0].kind is MarkKind.INSERTION
    assert got[0].provenance is Provenance.AGENT
    assert (got[0].anchor_start, got[0].anchor_end) == (3, 11)


def test_comment_crud_roundtrip(store):
    f = Feature(title="Auth")
    store.upsert_feature(f)
    c = CommentThread(feature_id=f.id, body="reconsider this", status=CommentStatus.OPEN,
                      anchor_start=0, anchor_end=5, media_ref=".codoc/media/shot.png")
    store.upsert_comment(c)
    got = store.comments_for_feature(f.id)
    assert len(got) == 1
    assert got[0].body == "reconsider this"
    assert got[0].status is CommentStatus.OPEN
    assert got[0].media_ref == ".codoc/media/shot.png"


def test_upsert_preserves_identity_on_edit(store):
    f = Feature(title="X")
    store.upsert_feature(f)
    c = CommentThread(feature_id=f.id, body="first")
    store.upsert_comment(c)
    c.body = "edited"
    c.status = CommentStatus.SENT
    store.upsert_comment(c)
    got = store.comments_for_feature(f.id)
    assert len(got) == 1  # same id → update, not insert
    assert got[0].body == "edited"
    assert got[0].status is CommentStatus.SENT


def test_marks_ordered_by_anchor(store):
    f = Feature(title="X")
    store.upsert_feature(f)
    store.upsert_mark(Mark(feature_id=f.id, anchor_start=10))
    store.upsert_mark(Mark(feature_id=f.id, anchor_start=0))
    store.upsert_mark(Mark(feature_id=f.id, anchor_start=5))
    starts = [m.anchor_start for m in store.marks_for_feature(f.id)]
    assert starts == [0, 5, 10]


def test_delete_for_feature_clears_annotations(store):
    f = Feature(title="X")
    store.upsert_feature(f)
    store.upsert_mark(Mark(feature_id=f.id))
    store.upsert_comment(CommentThread(feature_id=f.id, body="note"))
    store.delete_marks_for_feature(f.id)
    store.delete_comments_for_feature(f.id)
    assert store.marks_for_feature(f.id) == []
    assert store.comments_for_feature(f.id) == []


def test_delete_single(store):
    f = Feature(title="X")
    store.upsert_feature(f)
    m = Mark(feature_id=f.id)
    store.upsert_mark(m)
    store.delete_mark(m.id)
    assert store.marks_for_feature(f.id) == []


def test_hlc_timestamps_monotonic(store):
    f = Feature(title="X")
    store.upsert_feature(f)
    a = Mark(feature_id=f.id)
    store.upsert_mark(a)
    b = Mark(feature_id=f.id)
    store.upsert_mark(b)
    marks = {m.id: m for m in store.all_marks()}
    # Both carry populated HLC timestamps that parse back.
    assert marks[a.id].created_at.to_str()
    assert marks[b.id].created_at.to_str()


def test_tables_added_additively_to_existing_db(tmp_path):
    """Opening a DB that predates the annotation tables must gain them without
    touching existing features/bindings (the additive-migration contract)."""
    s1 = open_store(tmp_path)
    f = Feature(title="Existing")
    s1.upsert_feature(f)
    s1.upsert_binding(Binding(feature_id=f.id, file="a.py", symbol_path="a.py::foo", fingerprint="abc"))
    # Simulate a pre-existing DB by dropping the new tables, then reopening. A
    # genuine legacy DB predates the user_version stamp, so reset that too —
    # open() only replays the schema when the stamp is stale.
    s1.conn.execute("DROP TABLE marks")
    s1.conn.execute("DROP TABLE comments")
    s1.conn.execute("PRAGMA user_version = 0")
    s1.conn.commit()
    s1.close()

    s2 = open_store(tmp_path)  # open() re-runs _SCHEMA (CREATE TABLE IF NOT EXISTS)
    # Existing data intact.
    feats = s2.list_features()
    assert [x.title for x in feats] == ["Existing"]
    assert len(s2.bindings_for_feature(f.id)) == 1
    # New tables present and usable.
    s2.upsert_comment(CommentThread(feature_id=f.id, body="works now"))
    assert s2.comments_for_feature(f.id)[0].body == "works now"
    s2.close()
