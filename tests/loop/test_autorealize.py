"""The opt-in headless realize fallback (codoc/loop/autorealize.py)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from codoc.agent.hook import REALIZE_FILENAME
from codoc.loop import autorealize
from codoc.loop.activity import activity_path


@pytest.fixture
def codoc_dir(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    return str(cd)


def _queue(codoc_dir: str) -> None:
    (__import__("pathlib").Path(codoc_dir) / REALIZE_FILENAME).write_text(
        "### 1. NEW FEATURE: \"X\"\nIntent: do X\n")


def _epoch(codoc_dir: str, *, open_: bool) -> None:
    activity_path(codoc_dir).write_text(json.dumps({"epoch": {"id": "e1", "open": open_}}))


def test_no_spawn_without_a_queue(codoc_dir):
    assert autorealize.should_spawn(codoc_dir, in_flight=False) is False


def test_spawn_when_queued_and_no_epoch(codoc_dir):
    _queue(codoc_dir)
    assert autorealize.should_spawn(codoc_dir, in_flight=False) is True


def test_no_spawn_while_a_live_session_is_open(codoc_dir):
    _queue(codoc_dir)
    _epoch(codoc_dir, open_=True)
    assert autorealize.should_spawn(codoc_dir, in_flight=False) is False


def test_no_spawn_while_epoch_open_but_activity_quiet(codoc_dir):
    """A LIVE session renews activity.json only on Edit/Write/Read hook events —
    minutes of Bash/inference silence must NOT read as 'no session'. Spawn
    decisions use the daemon-grade 900s TTL, not the 90s UI TTL (WS1.1 tiering);
    otherwise a headless pass would race the live session on the same queue."""
    import os
    import time

    _queue(codoc_dir)
    _epoch(codoc_dir, open_=True)
    old = time.time() - 300   # > EPOCH_UI_TTL_SECONDS, < EPOCH_STALE_SECONDS
    os.utime(activity_path(codoc_dir), (old, old))
    assert autorealize.should_spawn(codoc_dir, in_flight=False) is False


def test_spawn_resumes_after_daemon_grade_ttl(codoc_dir):
    """A hard-killed session stops starving --auto-realize once the daemon-grade
    lease expires (~15 min), instead of forever."""
    import os
    import time

    from codoc.loop.watch import EPOCH_STALE_SECONDS

    _queue(codoc_dir)
    _epoch(codoc_dir, open_=True)
    old = time.time() - EPOCH_STALE_SECONDS - 1
    os.utime(activity_path(codoc_dir), (old, old))
    assert autorealize.should_spawn(codoc_dir, in_flight=False) is True


def test_no_spawn_while_realizing_lease_is_fresh(codoc_dir):
    """An interactive /codoc:sync renews status.json per directive even when its
    epoch looks activity-silent — a fresh realizing lease blocks the headless
    spawn (two agents must never race one queue)."""
    from codoc.loop import status

    _queue(codoc_dir)
    status.write_status(codoc_dir, status.REALIZING, detail="implementing 1/2")
    assert autorealize.should_spawn(codoc_dir, in_flight=False) is False


def test_spawn_resumes_after_realizing_lease_decays(codoc_dir):
    """A crashed pass's realizing lease decays on its own (nothing renews it),
    after which the headless fallback may pick the queue back up."""
    import os
    import time

    from codoc.loop import status

    _queue(codoc_dir)
    status.write_status(codoc_dir, status.REALIZING, detail="implementing 1/2")
    old = time.time() - status.REALIZING_LEASE_SECONDS - 1
    os.utime(status.status_path(codoc_dir), (old, old))
    assert autorealize.should_spawn(codoc_dir, in_flight=False) is True


def test_no_spawn_when_one_is_already_in_flight(codoc_dir):
    _queue(codoc_dir)
    assert autorealize.should_spawn(codoc_dir, in_flight=True) is False


def test_spawn_returns_none_when_claude_missing(codoc_dir):
    _queue(codoc_dir)
    with patch("codoc.loop.sdk_realize.sdk_available", return_value=False), \
         patch.object(autorealize, "find_claude", return_value=None):
        assert autorealize.spawn_realize(str(__import__("pathlib").Path(codoc_dir).parent), codoc_dir) is None


def test_spawn_launches_claude_and_sets_status(codoc_dir):
    _queue(codoc_dir)
    fake = object()
    with patch("codoc.loop.sdk_realize.sdk_available", return_value=False), \
         patch.object(autorealize, "find_claude", return_value="/usr/bin/claude"), \
         patch.object(autorealize.subprocess, "Popen", return_value=fake) as popen:
        proc = autorealize.spawn_realize("/repo", codoc_dir)
    assert proc is fake
    args, kwargs = popen.call_args
    assert args[0] == ["/usr/bin/claude", "-p", "/codoc:sync"]
    assert kwargs["cwd"] == "/repo"
    state = json.loads((__import__("pathlib").Path(codoc_dir) / "status.json").read_text())
    assert state["state"] == "realizing"


# ─── daemon driver: maybe_auto_realize ────────────────────────────────────────

def test_maybe_auto_realize_spawns_and_tracks(codoc_dir):
    from codoc.loop.watch import WatchState, maybe_auto_realize

    _queue(codoc_dir)
    state = WatchState()
    fake = object()
    with patch.object(autorealize, "spawn_realize", return_value=fake):
        maybe_auto_realize(state, "/repo", codoc_dir, printer=lambda *_: None)
    assert state.realize_proc is fake


def test_maybe_auto_realize_reaps_finished_pass(codoc_dir):
    from codoc.loop.watch import WatchState, maybe_auto_realize

    class _Done:
        def poll(self):
            return 0  # finished

    # No queue left → after reaping the finished proc, nothing new is spawned.
    state = WatchState(realize_proc=_Done())
    with patch.object(autorealize, "spawn_realize") as spawn:
        maybe_auto_realize(state, "/repo", codoc_dir, printer=lambda *_: None)
    assert state.realize_proc is None
    spawn.assert_not_called()


def test_maybe_auto_realize_reap_floors_stuck_realizing(codoc_dir):
    """A crashed headless child must not leave `realizing` on disk past its
    reap — the reap floors status.json immediately (WS1.5) instead of waiting
    out the 300s lease."""
    from codoc.loop import status
    from codoc.loop.watch import WatchState, maybe_auto_realize

    class _Dead:
        def poll(self):
            return 1  # crashed

    status.write_status(codoc_dir, status.REALIZING, detail="implementing (headless)")
    state = WatchState(realize_proc=_Dead())
    with patch.object(autorealize, "spawn_realize") as spawn:
        maybe_auto_realize(state, "/repo", codoc_dir, printer=lambda *_: None)

    assert state.realize_proc is None
    spawn.assert_not_called()  # no queue → nothing respawned
    data = json.loads((__import__("pathlib").Path(codoc_dir) / "status.json").read_text())
    assert data["state"] == "in_sync"


def test_maybe_auto_realize_does_not_stack(codoc_dir):
    from codoc.loop.watch import WatchState, maybe_auto_realize

    class _Live:
        def poll(self):
            return None  # still running

    _queue(codoc_dir)
    live = _Live()
    state = WatchState(realize_proc=live)
    with patch.object(autorealize, "spawn_realize") as spawn:
        maybe_auto_realize(state, "/repo", codoc_dir, printer=lambda *_: None)
    spawn.assert_not_called()      # an in-flight pass blocks a second one
    assert state.realize_proc is live
