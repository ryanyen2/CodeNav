"""close_epoch: the locked read-modify-write stale-epoch heal (WS1, review #13/#14).

The daemon's step-0 recovery must close a dead epoch in activity.json *itself* so
every reader (IDE, hub, autorealize) stops believing the session is live — but it
must NOT clobber a fresh SessionStart that raced into the single epoch slot in the
window between the daemon's staleness stat and its write. close_epoch holds the
activity FileLock across read + identity check + staleness re-check + write, so
the "never clobber a genuinely fresh epoch" guarantee is enforced, not narrated.
"""
from __future__ import annotations

import json

from codoc.loop.activity import EPOCH_UI_TTL_SECONDS, activity_path, close_epoch


def _write(codoc_dir, *, open: bool, epoch_id: str, features: dict | None = None) -> None:
    data = {
        "version": 1,
        "epoch": {"id": epoch_id, "origin": "interactive", "open": open,
                  "started_at": "2026-07-11T00:00:00+00:00", "ended_at": None},
        "touched": {"src/a.py": {"mode": "write", "symbols": [], "feature_ids": []}},
        "recent": [],
        "features": features or {},
    }
    activity_path(codoc_dir).write_text(json.dumps(data))


def test_closes_matching_stale_epoch(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True, epoch_id="ep-dead",
           features={"f-1": {"phase": "editing", "at": "2026-07-11T00:00:00+00:00"}})

    assert close_epoch(codoc_dir, "ep-dead") is True

    healed = json.loads(activity_path(codoc_dir).read_text())
    assert healed["epoch"]["open"] is False
    assert healed["epoch"]["ended_at"]  # stamped
    assert healed["features"] == {}
    # `touched` is preserved so the caller can still fold those files into Loop A.
    assert "src/a.py" in healed["touched"]


def test_refuses_when_a_newer_session_took_the_slot(tmp_path):
    """The race #14 describes: a SessionStart writes a fresh epoch before the heal
    acquires the lock. The on-disk id no longer matches the dead id → no-op, and
    the live epoch is left fully intact."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True, epoch_id="ep-fresh",
           features={"f-9": {"phase": "editing", "at": "2026-07-11T00:00:00+00:00"}})

    assert close_epoch(codoc_dir, "ep-dead") is False

    still = json.loads(activity_path(codoc_dir).read_text())
    assert still["epoch"]["id"] == "ep-fresh"
    assert still["epoch"]["open"] is True       # untouched
    assert still["features"] == {"f-9": {"phase": "editing", "at": "2026-07-11T00:00:00+00:00"}}


def test_refuses_when_same_id_but_freshly_rewritten(tmp_path):
    """Defence in depth: even if the same session re-registered (same id) between
    the caller's staleness stat and the lock, a fresh mtime under stale_after keeps
    it alive — the guard is not solely id-based."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True, epoch_id="ep-1")
    mtime = activity_path(codoc_dir).stat().st_mtime

    # `now` is only just past the write → within stale_after → treated as fresh.
    assert close_epoch(codoc_dir, "ep-1", now=mtime + 1, stale_after=EPOCH_UI_TTL_SECONDS) is False
    assert json.loads(activity_path(codoc_dir).read_text())["epoch"]["open"] is True


def test_closes_same_id_when_confirmed_stale_under_lock(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True, epoch_id="ep-1")
    mtime = activity_path(codoc_dir).stat().st_mtime

    assert close_epoch(codoc_dir, "ep-1", now=mtime + EPOCH_UI_TTL_SECONDS + 1,
                       stale_after=EPOCH_UI_TTL_SECONDS) is True
    assert json.loads(activity_path(codoc_dir).read_text())["epoch"]["open"] is False


def test_noop_when_already_closed_and_cleared(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=False, epoch_id="ep-1")
    assert close_epoch(codoc_dir, "ep-1") is False


def test_empty_expected_id_matches_any_open_epoch(tmp_path):
    """Legacy fallback: a caller that cannot identify the epoch (empty id) still
    closes whatever open epoch is present — strictly no worse than the old heal."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True, epoch_id="ep-anything")
    assert close_epoch(codoc_dir, "") is True
    assert json.loads(activity_path(codoc_dir).read_text())["epoch"]["open"] is False
