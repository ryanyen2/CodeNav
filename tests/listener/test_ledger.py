"""Tests for codoc.listener.ledger — LiveActivity in-memory tracker."""
from __future__ import annotations

import time

import pytest

from codoc.listener.ledger import LiveActivity, ActivityEntry, TTL_SECONDS


@pytest.fixture
def la() -> LiveActivity:
    return LiveActivity()


def test_record_returns_entry(la):
    entry = la.record("s1", "/abs/path/foo.py", "foo.py", "PostToolUse:Edit", "post")
    assert isinstance(entry, ActivityEntry)
    assert entry.session_id == "s1"
    assert entry.rel_path == "foo.py"
    assert entry.ended_at is not None  # phase="post" sets ended_at


def test_record_pre_phase_no_ended_at(la):
    entry = la.record("s1", "/abs/foo.py", "foo.py", "PreToolUse:Edit", "pre")
    assert entry.ended_at is None


def test_get_active_returns_recent(la):
    la.record("s1", "/abs/foo.py", "foo.py", "PostToolUse:Edit", "post")
    assert len(la.get_active()) == 1


def test_ttl_expiry(la, monkeypatch):
    """Entries older than TTL_SECONDS are excluded from get_active."""
    la.record("s1", "/abs/foo.py", "foo.py", "PostToolUse:Edit", "post")
    # Monkey-patch time.monotonic to return a future time.
    original = time.monotonic()

    def fake_monotonic():
        return original + TTL_SECONDS + 1

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    assert la.get_active() == []


def test_multi_session_isolation(la):
    """Two sessions on the same file produce separate entries."""
    la.record("s1", "/abs/foo.py", "foo.py", "PostToolUse:Edit", "post")
    la.record("s2", "/abs/foo.py", "foo.py", "PostToolUse:Write", "post")
    active = la.get_active()
    assert len(active) == 2
    session_ids = {e.session_id for e in active}
    assert session_ids == {"s1", "s2"}


def test_same_session_same_file_overwrites(la):
    """Re-recording same (session, file) replaces the previous entry."""
    la.record("s1", "/abs/foo.py", "foo.py", "PreToolUse:Read", "pre")
    la.record("s1", "/abs/foo.py", "foo.py", "PostToolUse:Edit", "post")
    assert len(la.get_active()) == 1
    assert la.get_active()[0].tool == "PostToolUse:Edit"


def test_clear_session(la):
    la.record("s1", "/abs/a.py", "a.py", "PostToolUse:Edit", "post")
    la.record("s1", "/abs/b.py", "b.py", "PostToolUse:Edit", "post")
    la.record("s2", "/abs/a.py", "a.py", "PostToolUse:Edit", "post")
    la.clear_session("s1")
    active = la.get_active()
    assert all(e.session_id == "s2" for e in active)
    assert len(active) == 1


def test_feature_uuids_stored(la):
    entry = la.record(
        "s1", "/abs/foo.py", "foo.py", "PostToolUse:Edit", "post",
        feature_uuids=["uuid-1", "uuid-2"],
        feature_slugs=["auth-login", "auth-session"],
    )
    assert entry.feature_uuids == ["uuid-1", "uuid-2"]
    assert entry.feature_slugs == ["auth-login", "auth-session"]


def test_max_entries_cap():
    """Hard cap evicts oldest when MAX_ENTRIES is exceeded."""
    from codoc.listener.ledger import MAX_ENTRIES
    la = LiveActivity()
    for i in range(MAX_ENTRIES + 5):
        la.record("s1", f"/abs/{i}.py", f"{i}.py", "PostToolUse:Edit", "post")
    assert len(la._entries) <= MAX_ENTRIES
