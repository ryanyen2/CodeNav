"""Agent epoch loop-control tests for the watch daemon (process_batch)."""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.render import tree_path
from codoc.loop.activity import activity_path
from codoc.loop.loop_a import LoopAResult
from codoc.loop.loop_b import LoopBResult
from codoc.loop.watch import WatchState, _hash, process_batch


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    codoc_dir = root / ".codoc"
    tp = tree_path(codoc_dir)
    tp.write_text("# tree\n- Root  ⟨f-1⟩\n")
    return str(root), str(codoc_dir), tp


def _spy(result):
    seen: dict = {}

    def fn(root_dir, codoc_dir, **kw):
        seen.update(kw)
        seen["called"] = True
        return result
    fn.seen = seen
    return fn


def _noop_render(codoc_dir):
    pass


def _write_activity(codoc_dir: str, *, epoch_id: str, origin: str, open: bool) -> str:
    """Write a minimal activity.json and return its path as a string."""
    data = {
        "version": 1,
        "epoch": {"id": epoch_id, "origin": origin, "open": open,
                  "started_at": "2026-05-25T00:00:00+00:00", "ended_at": None},
        "touched": {"src/mod.py": {"symbols": [], "feature_ids": [], "last": None, "mode": "write"}},
        "recent": [],
    }
    path = activity_path(codoc_dir)
    path.write_text(json.dumps(data))
    return str(path)


# ── Rising edge — epoch open ──────────────────────────────────────────────────

def test_epoch_open_sets_state(dirs):
    root, codoc_dir, tp = dirs
    ap = _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=True)
    a = _spy(LoopAResult())
    b = _spy(LoopBResult())
    state = WatchState(last_tree_hash=_hash(tp))

    out = process_batch([ap], root, codoc_dir, state, loop_a=a, loop_b=b, render=_noop_render)

    assert out is None  # activity-only batch → no loop run
    assert state.epoch_open is True
    assert state.epoch_origin == "interactive"
    assert "called" not in a.seen and "called" not in b.seen


def test_epoch_open_suppresses_loop_a_for_code_files(dirs):
    root, codoc_dir, tp = dirs
    ap = _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=True)

    # Step 1: open epoch.
    state = WatchState(last_tree_hash=_hash(tp))
    process_batch([ap], root, codoc_dir, state,
                  loop_a=_spy(LoopAResult()), loop_b=_spy(LoopBResult()), render=_noop_render)
    assert state.epoch_open is True

    # Step 2: code file change arrives while epoch is open → suppressed.
    a = _spy(LoopAResult())
    b = _spy(LoopBResult())
    out = process_batch(
        [str(root) + "/src/mod.py"],
        root, codoc_dir, state, loop_a=a, loop_b=b, render=_noop_render,
    )
    assert out is None
    assert "called" not in a.seen
    assert "src/mod.py" in state.suppressed_files


def test_epoch_open_accumulates_suppressed_files(dirs):
    root, codoc_dir, tp = dirs
    ap = _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=True)
    state = WatchState(last_tree_hash=_hash(tp))
    process_batch([ap], root, codoc_dir, state,
                  loop_a=_spy(LoopAResult()), loop_b=_spy(LoopBResult()), render=_noop_render)

    # Two separate batches of code changes during the epoch.
    process_batch([str(root) + "/a.py"], root, codoc_dir, state,
                  loop_a=_spy(LoopAResult()), loop_b=_spy(LoopBResult()), render=_noop_render)
    process_batch([str(root) + "/b.py"], root, codoc_dir, state,
                  loop_a=_spy(LoopAResult()), loop_b=_spy(LoopBResult()), render=_noop_render)

    assert state.suppressed_files == {"a.py", "b.py"}


def test_tree_write_during_epoch_does_not_spawn_loop_b(dirs):
    """An agent MCP reflection re-renders tree.codoc mid-epoch; the daemon must NOT
    route that to Loop B (it would spawn a nested coding agent)."""
    root, codoc_dir, tp = dirs
    ap = _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=True)
    state = WatchState(last_tree_hash=_hash(tp))
    process_batch([ap], root, codoc_dir, state,
                  loop_a=_spy(LoopAResult()), loop_b=_spy(LoopBResult()), render=_noop_render)

    # The MCP write changed tree.codoc out-of-band (hash no longer matches).
    tp.write_text("# tree\n- Root  ⟨f-1⟩\n- New from agent  ⟨f-2⟩\n")
    a = _spy(LoopAResult())
    b = _spy(LoopBResult())
    out = process_batch([str(tp)], root, codoc_dir, state,
                        loop_a=a, loop_b=b, render=_noop_render)

    assert out is None
    assert "called" not in b.seen  # Loop B suppressed during the epoch
    assert "called" not in a.seen


# ── Falling edge — interactive epoch close ────────────────────────────────────

def test_epoch_close_interactive_runs_one_scoped_loop_a(dirs):
    root, codoc_dir, tp = dirs
    ap = _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=True)
    state = WatchState(last_tree_hash=_hash(tp))

    # Open the epoch and accumulate a suppressed file.
    process_batch([ap], root, codoc_dir, state,
                  loop_a=_spy(LoopAResult()), loop_b=_spy(LoopBResult()), render=_noop_render)
    state.suppressed_files.add("suppressed.py")

    # Close the epoch.
    _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=False)
    a = _spy(LoopAResult(auto={"refresh": 1}))
    b = _spy(LoopBResult())
    out = process_batch([str(activity_path(codoc_dir))], root, codoc_dir, state,
                        loop_a=a, loop_b=b, render=_noop_render)

    assert out is not None
    assert out[0] == "agent→codoc"
    assert a.seen["called"]
    # file_scope should include both the epoch-touched file and the suppressed one.
    assert "suppressed.py" in a.seen["file_scope"]
    assert "src/mod.py" in a.seen["file_scope"]
    assert "called" not in b.seen
    assert state.epoch_open is False
    assert state.suppressed_files == set()


def test_epoch_close_interactive_no_realize_skips_loop_a(dirs):
    root, codoc_dir, tp = dirs
    ap = _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=True)
    state = WatchState(last_tree_hash=_hash(tp))
    process_batch([ap], root, codoc_dir, state,
                  loop_a=_spy(LoopAResult()), loop_b=_spy(LoopBResult()), render=_noop_render)

    _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=False)
    a = _spy(LoopAResult())
    out = process_batch([str(activity_path(codoc_dir))], root, codoc_dir, state,
                        no_realize=True, loop_a=a, loop_b=_spy(LoopBResult()), render=_noop_render)

    assert out is None
    assert "called" not in a.seen


# ── Falling edge — loop_b epoch close ────────────────────────────────────────

def test_epoch_close_loop_b_not_reconciled_by_daemon(dirs):
    root, codoc_dir, tp = dirs
    ap = _write_activity(codoc_dir, epoch_id="ep-2", origin="loop_b", open=True)
    state = WatchState(last_tree_hash=_hash(tp))

    # Track the epoch.
    process_batch([ap], root, codoc_dir, state,
                  loop_a=_spy(LoopAResult()), loop_b=_spy(LoopBResult()), render=_noop_render)
    assert state.epoch_origin == "loop_b"

    # Close it.
    _write_activity(codoc_dir, epoch_id="ep-2", origin="loop_b", open=False)
    a = _spy(LoopAResult())
    out = process_batch([str(activity_path(codoc_dir))], root, codoc_dir, state,
                        loop_a=a, loop_b=_spy(LoopBResult()), render=_noop_render)

    assert out is None  # daemon does NOT reconcile loop_b epochs
    assert "called" not in a.seen
    assert state.epoch_open is False


# ── Missed loop_b epoch (daemon was blocked) ──────────────────────────────────

def test_missed_loop_b_epoch_suppresses_code_files(dirs):
    """When the daemon missed a loop_b epoch (was blocked), it must not double-
    reconcile the epoch's touched files."""
    root, codoc_dir, tp = dirs
    # Write a closed loop_b epoch with known touched files.
    _write_activity(codoc_dir, epoch_id="ep-missed", origin="loop_b", open=False)
    state = WatchState(last_tree_hash=_hash(tp))
    # last_epoch_id starts empty → this is a "missed" epoch.

    a = _spy(LoopAResult())
    b = _spy(LoopBResult())
    code_file = str(root) + "/src/mod.py"  # also in activity touched

    out = process_batch(
        [str(activity_path(codoc_dir)), code_file],
        root, codoc_dir, state, loop_a=a, loop_b=b, render=_noop_render,
    )

    # Code file was in the epoch → excluded → no Loop A.
    assert out is None
    assert "called" not in a.seen
    assert state.last_epoch_id == "ep-missed"


# ── Stale-epoch recovery (hard-killed agent) ─────────────────────────────────

def test_stale_epoch_recovers_and_reconciles_suppressed_files(dirs):
    """A hard-killed agent leaves the epoch open; after EPOCH_STALE_SECONDS the
    daemon recovers and reconciles the suppressed/touched files via Loop A."""
    root, codoc_dir, tp = dirs
    _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=True)
    state = WatchState(last_tree_hash=_hash(tp), epoch_open=True,
                       epoch_origin="interactive", last_epoch_id="ep-1")
    state.suppressed_files.add("half_written.py")

    a = _spy(LoopAResult(auto={"refresh": 1}))
    b = _spy(LoopBResult())
    # A new code change arrives "much later" — now() far past the activity mtime.
    out = process_batch(
        [str(root) + "/another.py"], root, codoc_dir, state,
        loop_a=a, loop_b=b, render=_noop_render, now=lambda: 9_999_999_999.0,
    )

    assert state.epoch_open is False           # recovered
    assert out is not None and out[0] == "code→codoc"
    assert a.seen["called"]
    scope = a.seen["file_scope"]
    assert "half_written.py" in scope and "another.py" in scope


def test_stale_epoch_recovery_heals_activity_json_file(dirs):
    """WS1.4: recovery must WRITE activity.json (not just mutate WatchState) so
    every other reader (the IDE, autorealize, a second hook invocation) also sees
    the epoch closed — previously only the daemon's in-memory state was fixed,
    leaving `epoch.open=true` and stuck feature phases in the file forever."""
    root, codoc_dir, tp = dirs
    _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=True)
    data = json.loads(activity_path(codoc_dir).read_text())
    data["features"] = {"f-1": {"phase": "editing", "at": "2026-07-11T00:00:00+00:00"}}
    activity_path(codoc_dir).write_text(json.dumps(data))

    state = WatchState(last_tree_hash=_hash(tp), epoch_open=True,
                       epoch_origin="interactive", last_epoch_id="ep-1")

    process_batch(
        [str(root) + "/another.py"], root, codoc_dir, state,
        loop_a=_spy(LoopAResult(auto={"refresh": 1})), loop_b=_spy(LoopBResult()),
        render=_noop_render, now=lambda: 9_999_999_999.0,
    )

    healed = json.loads(activity_path(codoc_dir).read_text())
    assert healed["epoch"]["open"] is False
    assert healed["features"] == {}


def test_stale_epoch_recovery_refreshes_status(dirs):
    """WS1.4: recovery also floors status.json — a stuck `realizing` written by
    the dead session must not survive the recovery."""
    root, codoc_dir, tp = dirs
    _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=True)
    from codoc.loop import status

    status.write_status(codoc_dir, status.REALIZING, detail="implementing")

    state = WatchState(last_tree_hash=_hash(tp), epoch_open=True,
                       epoch_origin="interactive", last_epoch_id="ep-1")

    process_batch(
        [str(root) + "/another.py"], root, codoc_dir, state,
        loop_a=_spy(LoopAResult(auto={"refresh": 1})), loop_b=_spy(LoopBResult()),
        render=_noop_render, now=lambda: 9_999_999_999.0,
    )

    healed_status = json.loads(status.status_path(codoc_dir).read_text())
    # No realize.md queued and no pending proposals in a fresh store → in_sync.
    assert healed_status["state"] == status.IN_SYNC


def test_fresh_epoch_not_treated_as_stale(dirs):
    """An epoch whose activity.json was just written is NOT recovered."""
    root, codoc_dir, tp = dirs
    import os
    ap = _write_activity(codoc_dir, epoch_id="ep-1", origin="interactive", open=True)
    state = WatchState(last_tree_hash=_hash(tp), epoch_open=True,
                       epoch_origin="interactive", last_epoch_id="ep-1")

    a = _spy(LoopAResult())
    # now() just after the file's mtime → not stale.
    out = process_batch([str(root) + "/x.py"], root, codoc_dir, state,
                        loop_a=a, loop_b=_spy(LoopBResult()), render=_noop_render,
                        now=lambda: os.path.getmtime(ap) + 1.0)

    assert state.epoch_open is True            # still live
    assert out is None                         # code churn suppressed during epoch
    assert "x.py" in state.suppressed_files


# ── Pure activity churn → no-op ───────────────────────────────────────────────

def test_activity_churn_alone_is_noop(dirs):
    root, codoc_dir, tp = dirs
    # Write a valid open epoch; already-tracked.
    _write_activity(codoc_dir, epoch_id="ep-3", origin="interactive", open=True)
    state = WatchState(last_tree_hash=_hash(tp), epoch_open=True,
                       epoch_origin="interactive", last_epoch_id="ep-3")

    a = _spy(LoopAResult())
    b = _spy(LoopBResult())
    # activity.json re-written (a tool call appended to recent log), no transition.
    out = process_batch([str(activity_path(codoc_dir))], root, codoc_dir, state,
                        loop_a=a, loop_b=b, render=_noop_render)

    assert out is None
    assert "called" not in a.seen and "called" not in b.seen


# ── turns 2+: the hook's re-opened epoch registers as a rising edge ────────────

def test_hook_reopened_epoch_suppresses_the_next_turns_saves(dirs):
    """The full turn-2 cycle through the REAL hook writers: SessionStart opens,
    Stop closes (falling edge, scoped reconcile), a turn-2 tool call re-opens —
    and the daemon must treat that re-open as a rising edge and suppress the
    agent's subsequent saves instead of LLM-processing half-written code."""
    from codoc.agent.hook import handle_pre_tool, handle_session_start, handle_stop

    root, codoc_dir, tp = dirs
    payload = {"session_id": "sess-9", "cwd": root}
    state = WatchState(last_tree_hash=_hash(tp))
    ap = str(activity_path(codoc_dir))

    # Turn 1: open + close through the daemon.
    handle_session_start(payload, codoc_dir)
    process_batch([ap], root, codoc_dir, state,
                  loop_a=_spy(LoopAResult()), loop_b=_spy(LoopBResult()), render=_noop_render)
    assert state.epoch_open is True
    handle_stop(payload, codoc_dir)
    process_batch([ap], root, codoc_dir, state,
                  loop_a=_spy(LoopAResult()), loop_b=_spy(LoopBResult()), render=_noop_render)
    assert state.epoch_open is False

    # Turn 2 begins: a PreToolUse re-opens the epoch (before the Write lands).
    handle_pre_tool({**payload, "tool_name": "Write",
                     "tool_input": {"file_path": root + "/src/mod.py"}}, codoc_dir)
    a = _spy(LoopAResult())
    out = process_batch([ap, root + "/src/mod.py"], root, codoc_dir, state,
                        loop_a=a, loop_b=_spy(LoopBResult()), render=_noop_render)

    assert out is None
    assert state.epoch_open is True
    assert "called" not in a.seen                # no mid-turn LLM pass
    assert "src/mod.py" in state.suppressed_files
