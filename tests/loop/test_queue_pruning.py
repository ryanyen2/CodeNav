"""P-6 — the realize queue never outlives the feature it names.

A command-driven retire supersedes that feature's queued directives, but that write
happens outside the transaction that applied the retire and ledgered its command. A crash
in between leaves the retire applied, the command permanently ledgered (so nothing
replays) and the directive queued forever — the agent is eventually asked to implement
prose for a feature that is no longer in the tree. The invariant is therefore re-derived
once per pass instead of being maintained only at the call site.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codoc.codoc_file.render import write_tree
from codoc.loop import edits as edits_channel
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.loop.status import AWAITING_IMPL, status_path
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    codoc_dir = tmp_path / ".codoc"; codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def _seed(codoc_dir, **kw):
    with open_store(codoc_dir) as s:
        f = Feature(title="Feat", description="seed.", **kw)
        s.upsert_feature(f)
        write_tree(s, codoc_dir)
    return f


def _queue(codoc_dir, fid, *, kind="amend", handed_off=False, ts=1, did=None):
    did = did or f"d-{'0' * 6}{'1' if kind == 'amend' else '2'}"
    edits_channel.write_manifest(codoc_dir, [edits_channel.Directive(
        id=did, feature_id=fid, kind=kind, caused_by="c-1",
        text=f"Implement {kind} for {fid}", baseline="seed.",
        handed_off=handed_off, ts=ts)])
    return did


def _trigger(codoc_dir, did):
    """Write the agent trigger the way build_realize_prompt does — the id marker is what
    `_in_flight_directive_ids` reads, so a hand-crafted file has to carry it."""
    realize_path(codoc_dir).write_text(f"## 1. Do the thing \u27e8{did}\u27e9\n")


def _retire(codoc_dir, fid):
    with open_store(codoc_dir) as s:
        s.retire_feature(fid)


def test_a_directive_left_by_a_torn_retire_is_dropped_on_the_next_pass(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir)
    _queue(codoc_dir, f.id)          # the directive the supersede never got to drop
    _retire(codoc_dir, f.id)         # …because the process died right here

    run_loop_b(root, codoc_dir, dry_run=False)

    assert edits_channel.read_manifest(codoc_dir) == []
    assert not realize_path(codoc_dir).exists()


def test_a_retire_directive_for_a_retired_feature_survives(repo):
    """The destructive `~`: the whole point of a retire directive is to ask the agent to
    delete the code for a feature that IS retired."""
    root, codoc_dir = repo
    f = _seed(codoc_dir)
    did = _queue(codoc_dir, f.id, kind="retire", handed_off=True)
    _trigger(codoc_dir, did)
    _retire(codoc_dir, f.id)

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.kind for d in edits_channel.read_manifest(codoc_dir)] == ["retire"]


def test_a_directive_for_a_deleted_feature_is_dropped(repo):
    root, codoc_dir = repo
    _seed(codoc_dir)
    _queue(codoc_dir, "f-never-existed")

    run_loop_b(root, codoc_dir, dry_run=False)

    assert edits_channel.read_manifest(codoc_dir) == []


def test_a_live_feature_keeps_its_queued_directive(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir)
    _queue(codoc_dir, f.id)

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.feature_id for d in edits_channel.read_manifest(codoc_dir)] == [f.id]


def test_an_in_flight_directive_is_protected_even_for_a_retired_feature(repo):
    """INV8: the agent may be mid-implementation and its reflect call cites the directive
    id, so anything already in realize.md is left alone (same carve-out as supersede)."""
    root, codoc_dir = repo
    f = _seed(codoc_dir)
    did = _queue(codoc_dir, f.id, handed_off=True)
    _trigger(codoc_dir, did)
    _retire(codoc_dir, f.id)

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.id for d in edits_channel.read_manifest(codoc_dir)] == [did]


# ── the queue closes on evidence of the work, not on a file being deleted ────────
# Completion used to have exactly ONE signal: someone removing realize.md. So an agent
# could implement a directive, reflect it, and attach the code, and the queue would
# still read awaiting_impl forever — the status bar reporting work "to implement" that
# demonstrably exists, and every feature it named wearing a "sent, awaiting the agent"
# badge. The only repair was a hand-run `rm` in the right directory.

def _implement(codoc_dir, fid, did):
    """What the agent does when it carries a directive out: change the code and reflect
    it, citing the directive. That citation is the proof the queue now reads."""
    from codoc.loop.apply import apply_op
    from codoc.model.event import NodeOp, NodeOpKind

    with open_store(codoc_dir) as s:
        apply_op(NodeOp(kind=NodeOpKind.ATTACH, feature_id=fid,
                        bindings=[("mod.py", "mod.py::thing")]),
                 s, source="loop_a_agent", applied=True, caused_by=did)


def test_an_implemented_directive_closes_itself_without_anyone_deleting_realize_md(repo):
    import json

    root, codoc_dir = repo
    f = _seed(codoc_dir)
    did = _queue(codoc_dir, f.id, handed_off=True)
    _trigger(codoc_dir, did)
    _implement(codoc_dir, f.id, did)          # agent did the work and said so

    run_loop_b(root, codoc_dir, dry_run=False)

    assert edits_channel.read_manifest(codoc_dir) == []
    assert not realize_path(codoc_dir).exists()   # the trigger goes with it
    state = json.loads(status_path(codoc_dir).read_text())["state"]
    assert state != AWAITING_IMPL                 # …and the status bar stops lying


def test_the_outcome_is_still_recorded_when_the_ledger_closes_the_queue(repo):
    """"What happened to my edit?" has to stay answerable however the entry left."""
    import json

    root, codoc_dir = repo
    f = _seed(codoc_dir)
    did = _queue(codoc_dir, f.id, handed_off=True)
    _trigger(codoc_dir, did)
    _implement(codoc_dir, f.id, did)

    run_loop_b(root, codoc_dir, dry_run=False)

    log = Path(codoc_dir) / "realized.jsonl"
    assert log.exists()
    assert did in {json.loads(line)["id"] for line in log.read_text().splitlines()}


def test_a_handed_off_directive_nobody_has_implemented_yet_survives(repo):
    """The in-flight protection still holds — this closes it only once the reflect call
    the protection was waiting for has actually happened."""
    root, codoc_dir = repo
    f = _seed(codoc_dir)
    did = _queue(codoc_dir, f.id, handed_off=True)
    _trigger(codoc_dir, did)

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.id for d in edits_channel.read_manifest(codoc_dir)] == [did]
    assert realize_path(codoc_dir).exists()


def test_an_unrelated_reflection_does_not_close_the_queue(repo):
    """Only a citation of THIS directive counts. Loop A reflects constantly; work that
    merely happened nearby is not evidence that the queued intent was carried out."""
    from codoc.loop.apply import apply_op
    from codoc.model.event import NodeOp, NodeOpKind

    root, codoc_dir = repo
    f = _seed(codoc_dir)
    did = _queue(codoc_dir, f.id, handed_off=True)
    _trigger(codoc_dir, did)
    with open_store(codoc_dir) as s:
        apply_op(NodeOp(kind=NodeOpKind.ATTACH, feature_id=f.id,
                        bindings=[("other.py", "other.py::x")]),
                 s, source="loop_a", applied=True, caused_by="d-somethingelse")

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.id for d in edits_channel.read_manifest(codoc_dir)] == [did]


def test_a_held_draft_is_never_closed_by_the_ledger(repo):
    """A draft was never handed to anyone, so nothing can have implemented it — even if
    an event happens to carry its id, it is the author's to send or withdraw."""
    root, codoc_dir = repo
    f = _seed(codoc_dir)
    did = _queue(codoc_dir, f.id, handed_off=False)
    _implement(codoc_dir, f.id, did)

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.id for d in edits_channel.read_manifest(codoc_dir)] == [did]


# ── a plan ADD closes on its placeholder realizing, citation or no citation ──────
# /codoc:plan sessions implement accepted nodes and bind them via codoc_attach /
# codoc_reflect without ever reading realize.md — they never learn the ⟨d-…⟩ ids the
# daemon minted when the user clicked Accept. Requiring the citation therefore wedged
# every plan session's queue at awaiting_impl forever. The placeholder's guarded
# planned→active transition names the directive's own feature and can only mean the
# code it asked for arrived, so it is accepted as evidence in its own right.

def _bind(codoc_dir, fid, *, caused_by=""):
    """A post-implementation attach — what a plan session does after writing code.
    Deliberately cites nothing by default: the session never saw the queue."""
    from codoc.loop.apply import apply_op
    from codoc.model.event import NodeOp, NodeOpKind

    with open_store(codoc_dir) as s:
        apply_op(NodeOp(kind=NodeOpKind.ATTACH, feature_id=fid,
                        bindings=[("mod.py", "mod.py::thing")]),
                 s, source="loop_a_agent", applied=True, caused_by=caused_by)


def test_a_realized_plan_placeholder_closes_its_directive_without_a_citation(repo):
    import json

    root, codoc_dir = repo
    f = _seed(codoc_dir, realized=False)
    did = _queue(codoc_dir, f.id, kind="add_node", handed_off=True)
    _trigger(codoc_dir, did)
    _bind(codoc_dir, f.id)               # implement + attach, no caused_by

    run_loop_b(root, codoc_dir, dry_run=False)

    assert edits_channel.read_manifest(codoc_dir) == []
    assert not realize_path(codoc_dir).exists()
    state = json.loads(status_path(codoc_dir).read_text())["state"]
    assert state != AWAITING_IMPL


def test_an_unimplemented_plan_placeholder_keeps_its_directive(repo):
    """Accepting a plan is not implementing it: until code is bound, the node stays
    planned and the queue keeps asking."""
    root, codoc_dir = repo
    f = _seed(codoc_dir, realized=False)
    did = _queue(codoc_dir, f.id, kind="add_node", handed_off=True)
    _trigger(codoc_dir, did)

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.id for d in edits_channel.read_manifest(codoc_dir)] == [did]
    assert realize_path(codoc_dir).exists()


def test_pre_declared_binds_do_not_close_a_plan_directive(repo):
    """plan_add may pre-bind the symbols the agent INTENDS to write. Those rows ride
    in on the accepted ADD itself and leave the placeholder planned (apply keeps
    realized=False), so the directive stays queued until a real post-implementation
    attach performs the lifecycle transition."""
    from codoc.model.binding import Binding

    root, codoc_dir = repo
    f = _seed(codoc_dir, realized=False)
    with open_store(codoc_dir) as s:
        s.upsert_binding(Binding(feature_id=f.id, file="mod.py",
                                 symbol_path="mod.py::thing", fingerprint=""))
    did = _queue(codoc_dir, f.id, kind="add_node", handed_off=True)
    _trigger(codoc_dir, did)

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.id for d in edits_channel.read_manifest(codoc_dir)] == [did]


def test_a_realized_placeholder_does_not_close_an_amend_directive(repo):
    """The structural evidence is scoped to ADDs. An amend's ask is a specific prose→
    code alignment; the feature merely being realized says nothing about it."""
    root, codoc_dir = repo
    f = _seed(codoc_dir, realized=False)
    did = _queue(codoc_dir, f.id, kind="amend", handed_off=True)
    _trigger(codoc_dir, did)
    _bind(codoc_dir, f.id)

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.id for d in edits_channel.read_manifest(codoc_dir)] == [did]
