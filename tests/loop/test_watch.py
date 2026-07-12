"""Phase 5 — watch routing + self-write guard (process_batch, loops injected)."""
from __future__ import annotations

import pytest

from codoc.codoc_file.render import tree_path
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
    seen = {}

    def fn(root_dir, codoc_dir, **kw):
        seen.update(kw)
        seen["called"] = True
        return result
    fn.seen = seen
    return fn


def _noop_render(codoc_dir):
    pass


def test_code_change_routes_to_loop_a(dirs):
    root, codoc_dir, tp = dirs
    a = _spy(LoopAResult(auto={"refresh": 2}))
    b = _spy(LoopBResult())
    state = WatchState(last_tree_hash=_hash(tp))

    out = process_batch([str(root + "/pkg/mod.py")], root, codoc_dir, state,
                        loop_a=a, loop_b=b, render=_noop_render)

    assert out and out[0] == "code→codoc"
    assert a.seen["called"] and a.seen["file_scope"] == {"pkg/mod.py"}
    assert "called" not in b.seen


def test_tree_edit_routes_to_loop_b(dirs):
    root, codoc_dir, tp = dirs
    a = _spy(LoopAResult())
    b = _spy(LoopBResult(accepted=1))
    state = WatchState(last_tree_hash="stale")  # differs from real hash → real user edit
    tp.write_text("# tree\n- Root edited  ⟨f-1⟩\n")

    out = process_batch([str(tp)], root, codoc_dir, state, loop_a=a, loop_b=b, render=_noop_render)

    assert out and out[0] == "codoc→code"
    assert b.seen["called"]
    assert "called" not in a.seen


def test_self_render_is_ignored(dirs):
    root, codoc_dir, tp = dirs
    a = _spy(LoopAResult())
    b = _spy(LoopBResult())
    # state hash matches the current file → this event is codoc's own re-render
    state = WatchState(last_tree_hash=_hash(tp))

    out = process_batch([str(tp)], root, codoc_dir, state, loop_a=a, loop_b=b, render=_noop_render)

    assert out is None
    assert "called" not in a.seen and "called" not in b.seen


def test_safe_process_batch_survives_a_failing_cycle(dirs):
    """F2: an exception in a cycle is logged and swallowed — the daemon lives."""
    from codoc.loop.watch import safe_process_batch

    root, codoc_dir, tp = dirs
    state = WatchState()
    logs: list[str] = []

    def boom(*a, **k):
        raise RuntimeError("LLM exploded")

    out = safe_process_batch([str(tp)], root, codoc_dir, state,
                             printer=logs.append, _process=boom)
    assert out is None
    assert any("cycle error" in m for m in logs)


def test_safe_process_batch_floors_status_after_a_failing_cycle(dirs):
    """WS1.6: a status write earlier in a crashed pass (e.g. TREE_DIRTY stamped
    before Loop B ran) must not outlive the pass — the exception handler floors
    it to the ground truth instead of leaving "applying tree edits…" stuck until
    the next SUCCESSFUL pass happens to call refresh_status."""
    from codoc.loop import status
    from codoc.loop.watch import safe_process_batch

    root, codoc_dir, tp = dirs
    status.write_status(codoc_dir, status.TREE_DIRTY, detail="applying tree edits")
    state = WatchState()

    def boom(*a, **k):
        raise RuntimeError("LLM exploded")

    safe_process_batch([str(tp)], root, codoc_dir, state,
                       printer=lambda *_a: None, _process=boom)

    import json
    healed = json.loads(status.status_path(codoc_dir).read_text())
    assert healed["state"] != status.TREE_DIRTY


def test_mcp_render_with_stale_hash_does_not_route_to_loop_b(dirs):
    """H2: a tree.codoc write that matches the store (e.g. an agent MCP reflection
    in another process) has no user ops → must NOT spawn Loop B, even though the
    daemon's hash is stale (it never saw the external write)."""
    root, codoc_dir, tp = dirs
    a = _spy(LoopAResult())
    b = _spy(LoopBResult())
    state = WatchState(last_tree_hash="stale")  # daemon didn't produce this write
    tp.write_text("# tree\n- Root  ⟨f-1⟩\n- Agent proposal  ⟨f-2⟩\n")

    out = process_batch([str(tp)], root, codoc_dir, state,
                        loop_a=a, loop_b=b, render=_noop_render,
                        has_user_edits=lambda _cd: False)  # store already matches

    assert out is None
    assert "called" not in b.seen and "called" not in a.seen


def test_skip_dir_and_noncode_filtered(dirs):
    root, codoc_dir, tp = dirs
    a = _spy(LoopAResult())
    b = _spy(LoopBResult())
    state = WatchState(last_tree_hash=_hash(tp))

    out = process_batch(
        [str(root + "/.codoc/lancedb/data.lance"), str(root + "/README.md"),
         str(root + "/__pycache__/x.pyc")],
        root, codoc_dir, state, loop_a=a, loop_b=b, render=_noop_render,
    )

    assert out is None
    assert "called" not in a.seen and "called" not in b.seen


def test_render_called_and_hash_updated_after_cycle(dirs):
    root, codoc_dir, tp = dirs
    a = _spy(LoopAResult(auto={"detach": 1}))
    rendered = {}

    def render(cd):
        tp.write_text("# tree\n- Root  ⟨f-1⟩\n- Added  ⟨f-2⟩\n")  # simulate re-render
        rendered["done"] = True

    state = WatchState(last_tree_hash=_hash(tp))
    process_batch([str(root + "/m.py")], root, codoc_dir, state,
                  loop_a=a, loop_b=_spy(LoopBResult()), render=render)

    assert rendered["done"]
    assert state.last_tree_hash == _hash(tp)  # guard now matches the freshly rendered file


def test_edits_json_routes_to_loop_b(dirs):
    """A doc-ahead suggestion (edits.json write) wakes Loop B — payload intents
    are applied by the loop, so the daemon must watch the intent channel."""
    from codoc.loop.edits import edits_path
    root, codoc_dir, tp = dirs
    ep = edits_path(codoc_dir)
    ep.write_text('{"version":1,"edits":[],"intents":[{"id":"d-s","feature_id":"f-1","ts":0,"description":"Should X."}]}')
    a = _spy(LoopAResult())
    b = _spy(LoopBResult(user_edits=1))
    state = WatchState(last_tree_hash=_hash(tp))

    out = process_batch([str(ep)], root, codoc_dir, state, loop_a=a, loop_b=b, render=_noop_render)

    assert out and out[0] == "codoc→code"
    assert b.seen["called"]
    assert "called" not in a.seen


def test_loop_b_self_clear_of_edits_does_not_refire(dirs):
    """Loop B drains edits.json (clearing the annotation list); that clear is a
    watched-file event but must NOT re-trigger a second no-op Loop B pass — the
    "edits 1" then "edits 0" double-fire the daemon log showed. The self-write
    guard recognises the daemon's own write and ignores it."""
    from codoc.loop.edits import edits_path
    root, codoc_dir, tp = dirs
    ep = edits_path(codoc_dir)
    ep.write_text(
        '{"version":1,"edits":[{"feature_id":"f-1","fields":["description"],'
        '"actor":"human","mode":"pen"}],"intents":[]}'
    )
    a = _spy(LoopAResult())

    calls = {"n": 0}

    def b_drain(root_dir, codoc_dir, **kw):
        # Mimic drain_annotations: the only list is consumed → the file is removed.
        edits_path(codoc_dir).unlink()
        calls["n"] += 1
        return LoopBResult(user_edits=1)

    state = WatchState(last_tree_hash=_hash(tp))

    # Batch 1 — the genuine host edit. Routes to Loop B, which drains edits.json.
    out1 = process_batch([str(ep)], root, codoc_dir, state,
                         loop_a=a, loop_b=b_drain, render=_noop_render)
    assert out1 and out1[0] == "codoc→code"
    assert calls["n"] == 1

    # Batch 2 — the watch event from Loop B's OWN clear of edits.json. Ignored:
    # no second pass, nothing routed.
    out2 = process_batch([str(ep)], root, codoc_dir, state,
                         loop_a=a, loop_b=b_drain, render=_noop_render)
    assert out2 is None
    assert calls["n"] == 1  # Loop B not re-fired
    assert "called" not in a.seen


def test_host_ops_log_routes_to_loop_b_then_self_write_suppressed(dirs):
    """#2 — an IDE append to edits.host.jsonl routes to Loop B; the daemon's own
    consumption of that log (it's gone after the merge) is recognised as a self-write and
    NOT re-routed into a no-op pass."""
    from codoc.loop.edits import host_ops_path
    root, codoc_dir, tp = dirs
    hp = host_ops_path(codoc_dir)
    hp.write_text('{"fn":"appendCommand","arg":{"id":"c-1","kind":"add","payload":{"title":"A"}}}\n')
    a = _spy(LoopAResult())

    def b_consume(root_dir, codoc_dir, **kw):
        host_ops_path(codoc_dir).unlink(missing_ok=True)  # mimic merge consuming the log
        return LoopBResult(commands=1)

    state = WatchState(last_tree_hash=_hash(tp))

    out1 = process_batch([str(hp)], root, codoc_dir, state,
                         loop_a=a, loop_b=b_consume, render=_noop_render)
    assert out1 and out1[0] == "codoc→code"

    # Batch 2 — the watch event from the daemon's OWN consumption (log now gone). Ignored.
    out2 = process_batch([str(hp)], root, codoc_dir, state,
                         loop_a=a, loop_b=b_consume, render=_noop_render)
    assert out2 is None


def test_mixed_batch_runs_both_loops(dirs):
    """#6/P2 batch routing: a batch carrying BOTH a tree edit and a code change runs
    Loop B AND Loop A (was: an `elif` ran only Loop B, starving code→tree reflection)."""
    root, codoc_dir, tp = dirs
    a = _spy(LoopAResult(auto={"refresh": 1}))
    b = _spy(LoopBResult(accepted=1))
    state = WatchState(last_tree_hash="stale")  # tree edit is a real user edit
    tp.write_text("# tree\n- Root edited  ⟨f-1⟩\n")

    out = process_batch([str(tp), str(root + "/pkg/mod.py")], root, codoc_dir, state,
                        loop_a=a, loop_b=b, render=_noop_render)

    assert b.seen["called"]                       # Loop B ran (tree edit)
    assert a.seen["called"]                       # Loop A ALSO ran (code change)
    assert a.seen["file_scope"] == {"pkg/mod.py"}
    assert out and "codoc→code" in out[0] and "code→codoc" in out[0]
