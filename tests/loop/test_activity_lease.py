"""epoch_alive: the lease-based liveness check (WS1.1).

`epoch.open=True` alone is not trustworthy — the `Stop`/`SessionEnd` hooks are
the only writers that clear it, and neither fires on a hard kill (Esc, SIGKILL,
closed window). `epoch_alive` additionally requires a fresh activity.json write
within the TTL, so a dead session self-heals instead of reading as active forever.
"""
from __future__ import annotations

import json

from codoc.loop.activity import EPOCH_UI_TTL_SECONDS, activity_path, epoch_alive


def _write(codoc_dir, *, open: bool, epoch_id: str = "ep-1") -> None:
    data = {
        "version": 1,
        "epoch": {"id": epoch_id, "origin": "interactive", "open": open,
                  "started_at": "2026-07-11T00:00:00+00:00", "ended_at": None},
        "touched": {}, "recent": [],
    }
    activity_path(codoc_dir).write_text(json.dumps(data))


def test_alive_when_open_and_fresh(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True)
    assert epoch_alive(codoc_dir) is True


def test_dead_when_closed(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=False)
    assert epoch_alive(codoc_dir) is False


def test_dead_when_stale_despite_open_flag(tmp_path):
    """The core lease behavior: open=True but no write in TTL seconds → dead."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True)
    mtime = activity_path(codoc_dir).stat().st_mtime
    far_future = mtime + EPOCH_UI_TTL_SECONDS + 1
    assert epoch_alive(codoc_dir, now=far_future) is False


def test_alive_just_inside_ttl(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _write(codoc_dir, open=True)
    mtime = activity_path(codoc_dir).stat().st_mtime
    assert epoch_alive(codoc_dir, now=mtime + EPOCH_UI_TTL_SECONDS - 1) is True


def test_dead_when_file_absent(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    assert epoch_alive(codoc_dir) is False
