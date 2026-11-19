"""close_epoch: the locked read-modify-write that closes a stale epoch.

The daemon closes a dead epoch in activity.json itself, so every reader (IDE,
hub, autorealize) stops treating the session as live. It must not overwrite a
fresh SessionStart that took the one epoch slot between the daemon's staleness
check and its write, so close_epoch holds the activity FileLock across the read,
the identity check, the staleness re-check and the write.
"""
from __future__ import annotations

import json

from codoc.workspace.epoch import EPOCH_STALE_SECONDS, activity_path, close_epoch


def _write(codoc_dir, *, open: bool, epoch_id: str) -> None:
    data = {
        "version": 1,
        "epoch": {"id": epoch_id, "open": open, "started_at": "2026-07-11T00:00:00+00:00", "ended_at": None},
        "touched": ["src/a.py"],
    }
    activity_path(codoc_dir).write_text(json.dumps(data))


def test_closes_matching_stale_epoch(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True, epoch_id="ep-dead")

    assert close_epoch(codoc_dir, "ep-dead") is True

    healed = json.loads(activity_path(codoc_dir).read_text())
    assert healed["epoch"]["open"] is False
    assert healed["epoch"]["ended_at"]  # stamped
    # `touched` is preserved so the caller can still fold those files into Loop A.
    assert "src/a.py" in healed["touched"]


def test_refuses_when_a_newer_session_took_the_slot(tmp_path):
    """A SessionStart writes a fresh epoch before the close takes the lock.
    The id on disk does not match the dead id, so nothing changes and the
    live epoch is left intact."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True, epoch_id="ep-fresh")

    assert close_epoch(codoc_dir, "ep-dead") is False

    still = json.loads(activity_path(codoc_dir).read_text())
    assert still["epoch"]["id"] == "ep-fresh"
    assert still["epoch"]["open"] is True       # untouched


def test_refuses_when_same_id_but_freshly_rewritten(tmp_path):
    """Defence in depth: even if the same session re-registered (same id) between
    the caller's staleness stat and the lock, a fresh mtime under stale_after keeps
    it alive — the guard is not solely id-based."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True, epoch_id="ep-1")
    mtime = activity_path(codoc_dir).stat().st_mtime

    # `now` is only just past the write → within stale_after → treated as fresh.
    assert close_epoch(codoc_dir, "ep-1", now=mtime + 1, stale_after=EPOCH_STALE_SECONDS) is False
    assert json.loads(activity_path(codoc_dir).read_text())["epoch"]["open"] is True


def test_closes_same_id_when_confirmed_stale_under_lock(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True, epoch_id="ep-1")
    mtime = activity_path(codoc_dir).stat().st_mtime

    assert close_epoch(codoc_dir, "ep-1", now=mtime + EPOCH_STALE_SECONDS + 1,
                       stale_after=EPOCH_STALE_SECONDS) is True
    assert json.loads(activity_path(codoc_dir).read_text())["epoch"]["open"] is False


def test_noop_when_already_closed(tmp_path):
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


def test_every_epoch_a_turn_opens_has_an_id_its_stop_can_name(tmp_path):
    """A turn's first rising edge with no epoch on disk opens one with an id,
    so a stop that names its session closes it, and a stop of another session
    does not. A realize pass that runs no hooks opens its own, under its own
    id, and closes it when it ends."""
    from codoc.workspace.epoch import read_activity, reopen_epoch
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    reopen_epoch(codoc_dir, "realize-1")
    assert read_activity(codoc_dir)["epoch"]["id"] == "ep-realize-1"
    assert close_epoch(codoc_dir, "ep-abc") is False
    assert close_epoch(codoc_dir, "ep-realize-1") is True
