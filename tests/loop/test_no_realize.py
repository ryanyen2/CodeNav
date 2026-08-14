"""#6 — --dry / --no-realize (realize=False) must APPLY tree edits but queue no
realization.

The field bug: the watch daemon mapped --no-realize onto loop_b's dry_run, which
skips command application entirely — so every webview edit stayed queued forever and
the editor appeared frozen. The contract (CLAUDE.md): "--dry: apply tree edits, don't
queue realization." These tests pin realize=False:

  * authored commands ARE applied to the store and BOTH files re-rendered;
  * no realize.md is written (nothing handed to the agent);
  * a code-implying edit is recorded as a HELD draft (intent preserved);
  * steers are deferred (not drained); a code-implying verdict accept is deferred.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codoc.codoc_file.render import write_tree
from codoc.loop import inbox
from codoc.loop.edits import Command, Steer, append_command, append_steer, read_manifest, read_steers
from codoc.loop.filenames import DOC_FILENAME
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    codoc_dir = tmp_path / ".codoc"; codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def _seed(codoc_dir, *features: Feature) -> None:
    s = open_store(codoc_dir)
    for f in features:
        s.upsert_feature(f)
    write_tree(s, codoc_dir)
    s.close()


def test_no_realize_applies_add_command_and_renders_but_no_realize_md(dirs):
    """The frozen-editor fix: an authored add IS applied + tree.doc.json IS written under
    realize=False, but no realize.md is queued."""
    root, codoc_dir = dirs
    _seed(codoc_dir)
    append_command(codoc_dir, Command(
        id="c-1", kind="add", local_id="L-1",
        payload={"title": "Theme system", "description": "Light/dark."}))

    res = run_loop_b(root, codoc_dir, realize=False)

    assert res.commands == 1
    s = open_store(codoc_dir)
    assert any(f.title == "Theme system" for f in s.list_features())  # APPLIED
    s.close()
    assert (Path(codoc_dir) / DOC_FILENAME).exists()                  # projection rendered
    assert not realize_path(codoc_dir).exists()                       # nothing queued


def test_no_realize_records_code_implying_edit_as_held_draft(dirs):
    """A set_description on a bound feature applies to the store and is recorded as a HELD
    directive (intent preserved for a later hand-off), but writes no realize.md."""
    root, codoc_dir = dirs
    from codoc.model.binding import Binding
    f = Feature(title="Auth", description="old")
    _seed(codoc_dir, f)
    s = open_store(codoc_dir)
    s.upsert_binding(Binding(feature_id=f.id, file="auth.py", symbol_path="auth.py::login",
                             fingerprint="fp"))
    s.close()
    append_command(codoc_dir, Command(
        id="c-2", kind="set_description", feature_id=f.id,
        payload={"description": "Rework the login flow entirely."}))

    res = run_loop_b(root, codoc_dir, realize=False)

    assert res.commands == 1
    s = open_store(codoc_dir)
    assert s.get_feature(f.id).description == "Rework the login flow entirely."  # APPLIED
    s.close()
    assert not realize_path(codoc_dir).exists()                                  # not queued
    directives = read_manifest(codoc_dir)
    assert directives and all(not d.handed_off for d in directives)              # HELD


def test_no_realize_defers_steers(dirs):
    """A steer is a pure realize request → deferred (left in edits.json) under realize=False."""
    root, codoc_dir = dirs
    f = Feature(title="Auth", description="x")
    _seed(codoc_dir, f)
    append_steer(codoc_dir, Steer(feature_id=f.id, text="handle unicode emails", comment_id="t-1"))

    res = run_loop_b(root, codoc_dir, realize=False)

    assert res.steered == 0
    assert len(read_steers(codoc_dir)) == 1               # still queued for a real pass
    assert not realize_path(codoc_dir).exists()


def test_no_realize_defers_code_implying_plan_accept(dirs):
    """A code-implying accept (plan ADD) is deferred: the verdict stays in the inbox and the
    feature is NOT created — a later realize-mode pass processes it."""
    root, codoc_dir = dirs
    _seed(codoc_dir)
    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Theme", realized=False, description="plan"))
    s.append_event(e)
    s.close()
    inbox.append_verdict(codoc_dir, e.id, accept=True)

    res = run_loop_b(root, codoc_dir, realize=False)

    assert res.accepted == 0
    assert inbox.read_verdicts(codoc_dir)                 # verdict preserved
    s = open_store(codoc_dir)
    assert not any(f.title == "Theme" for f in s.list_features())
    s.close()

    # A subsequent realize-mode pass processes it (feature created, directive queued).
    res2 = run_loop_b(root, codoc_dir, realize=True)
    assert res2.accepted == 1
    s = open_store(codoc_dir)
    assert any(f.title == "Theme" for f in s.list_features())
    s.close()


# ── accepting a proposal without a readable index must keep its bindings ─────
def test_accepted_proposal_keeps_bindings_when_the_index_is_unreadable(dirs):
    """An unreadable or empty index means "no view", NOT "the index is empty".

    Loop B validates an accepted proposal's bindings against the index key set,
    because a model-proposed binding can name a symbol that does not exist. The
    first version of that check cached an empty set when the index could not be
    read, and an empty set fails every membership test — so accepting a proposal
    in a workspace whose index was missing landed the feature owning nothing,
    silently. There is no index in this fixture, which is exactly that case.
    """
    root, codoc_dir = dirs
    app = Feature(title="App")
    _seed(codoc_dir, app)

    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Alpha", parent_id=app.id,
                        bindings=[("a.py", "a.py::alpha")]))
    s.append_event(e)
    s.close()
    inbox.append_verdict(codoc_dir, e.id, accept=True)

    res = run_loop_b(root, codoc_dir, dry_run=False, realize=False)

    assert res.accepted == 1
    s = open_store(codoc_dir)
    alpha = next(f for f in s.list_features() if f.title == "Alpha")
    owned = [(b.file, b.symbol_path) for b in s.bindings_for_feature(alpha.id)]
    s.close()
    assert owned == [("a.py", "a.py::alpha")], "the accepted proposal's binding was dropped"
