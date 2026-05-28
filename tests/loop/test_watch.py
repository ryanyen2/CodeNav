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
