"""Proposal A1 — the named ``lifecycle`` state machine on Feature.

``lifecycle`` (planned|active|retired) is the single authoritative field;
``retired``/``realized`` are derived read-only views kept for back-compat. These
tests pin: the bool↔lifecycle fold, the DB round-trip, the guarded
planned→active transition, and the additive migration of a pre-A1 row (no
``lifecycle`` column) by backfilling from the legacy bools.
"""
from __future__ import annotations

import sqlite3

from codoc.model.feature import Feature, Lifecycle
from codoc.store.db import Store, open_store


# ─── model: bools fold into one named state ──────────────────────────────────

def test_legacy_bools_fold_to_lifecycle():
    assert Feature(title="A").lifecycle is Lifecycle.ACTIVE
    assert Feature(title="A", realized=False).lifecycle is Lifecycle.PLANNED
    assert Feature(title="A", retired=True).lifecycle is Lifecycle.RETIRED
    # retired dominates a stale realized=False.
    assert Feature(title="A", retired=True, realized=False).lifecycle is Lifecycle.RETIRED


def test_derived_views_track_lifecycle():
    f = Feature(title="A", lifecycle=Lifecycle.PLANNED)
    assert f.realized is False and f.retired is False
    f.realize()
    assert f.lifecycle is Lifecycle.ACTIVE and f.realized is True
    # realize() is a no-op once active/retired.
    f.lifecycle = Lifecycle.RETIRED
    f.realize()
    assert f.lifecycle is Lifecycle.RETIRED


def test_explicit_lifecycle_wins_over_bools():
    f = Feature(title="A", lifecycle=Lifecycle.PLANNED, realized=True)
    assert f.lifecycle is Lifecycle.PLANNED


# ─── store: round-trip + transitions ─────────────────────────────────────────

def test_store_roundtrip_and_transitions(tmp_path):
    with open_store(tmp_path) as s:
        plan = Feature(title="Rate limiting", lifecycle=Lifecycle.PLANNED)
        s.upsert_feature(plan)
        assert s.get_feature(plan.id).lifecycle is Lifecycle.PLANNED

        # mark_realized promotes planned→active...
        s.mark_realized(plan.id)
        assert s.get_feature(plan.id).lifecycle is Lifecycle.ACTIVE

        # ...and never resurrects a retired feature.
        s.retire_feature(plan.id)
        assert s.get_feature(plan.id).lifecycle is Lifecycle.RETIRED
        s.mark_realized(plan.id)
        assert s.get_feature(plan.id).lifecycle is Lifecycle.RETIRED


def test_backcompat_bool_columns_stay_in_sync(tmp_path):
    """A pre-A1 reader still sees correct retired/realized columns."""
    with open_store(tmp_path) as s:
        f = Feature(title="A", lifecycle=Lifecycle.PLANNED)
        s.upsert_feature(f)
        row = s.conn.execute(
            "SELECT lifecycle, retired, realized FROM features WHERE id=?", (f.id,)
        ).fetchone()
        assert row["lifecycle"] == "planned"
        assert row["retired"] == 0 and row["realized"] == 0
        s.retire_feature(f.id)
        row = s.conn.execute(
            "SELECT lifecycle, retired, realized FROM features WHERE id=?", (f.id,)
        ).fetchone()
        assert row["lifecycle"] == "retired" and row["retired"] == 1


# ─── migration: a pre-A1 db (no lifecycle column) backfills from the bools ────

def test_migration_backfills_lifecycle_from_legacy_bools(tmp_path):
    db_path = tmp_path / "codoc.db"
    # Hand-build the PRE-A1 features table (no lifecycle column) + seed three rows.
    from codoc.model.hlc import HLC
    now = HLC.now().to_str()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE features (id TEXT PRIMARY KEY, title TEXT NOT NULL,"
        " description TEXT NOT NULL DEFAULT '', parent_id TEXT,"
        " retired INTEGER NOT NULL DEFAULT 0, realized INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    for fid, retired, realized in [("f-act", 0, 1), ("f-plan", 0, 0), ("f-ret", 1, 1)]:
        conn.execute(
            "INSERT INTO features (id, title, retired, realized, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (fid, fid, retired, realized, now, now),
        )
    conn.commit()
    conn.close()

    # Opening through the Store runs _migrate → adds + backfills lifecycle.
    s = Store(db_path).open()
    try:
        assert s.get_feature("f-act").lifecycle is Lifecycle.ACTIVE
        assert s.get_feature("f-plan").lifecycle is Lifecycle.PLANNED
        assert s.get_feature("f-ret").lifecycle is Lifecycle.RETIRED
    finally:
        s.close()
