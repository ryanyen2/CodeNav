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


def test_ttl_constants_match_the_ts_side():
    """Cross-language parity guard: the same lease TTLs are hardcoded in
    vscode-codoc/src/state/activity-model.ts (ms) and codoc/loop/activity.py
    (seconds). A change to one side without the other silently desyncs the
    IDE's liveness verdicts from the daemon/hub — mirroring the repo's
    parse.py↔TS-parser parity-test convention for the lease constants."""
    import re
    from pathlib import Path

    import pytest

    from codoc.loop.activity import FEATURE_PHASE_TTL_SECONDS

    ts = Path(__file__).resolve().parents[2] / "vscode-codoc" / "src" / "state" / "activity-model.ts"
    if not ts.exists():
        pytest.skip("vscode-codoc sources not present in this checkout")
    src = ts.read_text()

    def ts_const(name: str) -> int:
        m = re.search(rf"export const {name} = ([\d_]+);", src)
        assert m, f"{name} not found in activity-model.ts"
        return int(m.group(1).replace("_", ""))

    assert ts_const("EPOCH_UI_TTL_MS") == EPOCH_UI_TTL_SECONDS * 1000
    assert ts_const("FEATURE_PHASE_TTL_MS") == FEATURE_PHASE_TTL_SECONDS * 1000
