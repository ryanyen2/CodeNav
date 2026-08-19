"""Doc always wins (classify table rows 13 + 6).

A feature with pending doc-ahead intent — a live suggestion in ``edits.json`` or
a queued directive in ``realize.json`` — is being re-specified by the user. Code
drift on that feature must not fight the edit: intent-level proposals
(AMEND/RETIRE/MOVE) are deferred until the hold releases, while binding
maintenance (REFRESH/DETACH/ATTACH) keeps attribution correct throughout. Ops
produced while a realize queue is implemented carry ``caused_by=⟨directive id⟩``.
"""
from __future__ import annotations

import pytest

from codoc.loop import edits as edits_channel
from codoc.loop.loop_b import realize_path
from codoc.model.event import NodeOp, NodeOpKind

from .world import World, chunk, propose_never, propose_nothing, propose_ops


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    codoc = tmp_path / ".codoc"
    codoc.mkdir()
    return World(root, codoc)


def test_code_drift_on_held_feature_defers_amend_but_keeps_bindings(world):
    fid = world.given_feature("Request validation",
                              description="Validates incoming request headers.")
    world.given_binding(fid, "v.py", "v.py::check", tok="fp1")
    world.given_doc_suggestion(fid)

    # Code drifts: the bound chunk changes in place AND a new helper appears.
    # The LLM (injected) tries to amend the held feature's description and to
    # attach the helper to it.
    res = world.when_code_changes(
        added=[chunk("v.py", "v.py::sanitize", tok="fp2", src="def sanitize(): ...")],
        modified=[chunk("v.py", "v.py::check", tok="fp1b", src="def check(): ...")],
        propose=propose_ops(
            NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                   description="Validates and sanitizes incoming request headers."),
            NodeOp(kind=NodeOpKind.ATTACH, feature_id=fid,
                   bindings=[("v.py", "v.py::sanitize")]),
        ),
        label="code drifts under a live doc-ahead suggestion",
    )

    # The AMEND was deferred (the user's edit wins) …
    assert res.held_back == 1
    f = world.feature(fid)
    assert f.description == "Validates incoming request headers."
    assert not [e for e in world.proposals() if e.op.kind is NodeOpKind.AMEND]
    # … but binding maintenance still ran: refresh applied, helper attached.
    assert res.auto.get("refresh", 0) == 1
    world.then_owner_is("v.py", "v.py::sanitize", fid, note="bindings are not intent")


def test_held_feature_never_becomes_a_retire_candidate(world):
    fid = world.given_feature("Legacy export", description="Exports CSV.")
    world.given_binding(fid, "e.py", "e.py::export", tok="fp1")
    world.given_doc_suggestion(fid)

    # Its only chunk disappears — normally an `emptied` retire candidate for the
    # authoritative pass. Held ⇒ the LLM isn't even consulted (propose_never).
    world.when_code_changes(
        removed=[chunk("e.py", "e.py::export", tok="fp1")],
        propose=propose_never,
        label="the held feature loses its last binding",
    )
    world.then_unbound("e.py", "e.py::export")
    world.then_retired(fid, False)
    world.then_proposal_count(0)


def test_hold_releases_when_intent_clears(world):
    fid = world.given_feature("Legacy export", description="Exports CSV.")
    world.given_binding(fid, "e.py", "e.py::export", tok="fp1")
    world.given_doc_suggestion(fid)

    world.when_code_changes(removed=[chunk("e.py", "e.py::export", tok="fp1")],
                            propose=propose_never)
    world.then_retired(fid, False)

    # The user withdraws the suggestion (host removes the intent) → next
    # authority pass may surface the retire again.
    edits_channel._write_edits_file(world.codoc_dir, edits=[], intents=[])
    world.when_code_changes(
        added=[chunk("e.py", "e.py::other", tok="fp9", src="def other(): ...")],
        propose=propose_ops(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=fid,
                                   rationale="lost its last binding")),
        label="hold released — the retire may now be proposed",
    )
    assert [e for e in world.proposals() if e.op.kind is NodeOpKind.RETIRE_NODE]


def test_stale_intent_does_not_hold_forever(world):
    fid = world.given_feature("Request validation", description="Validates headers.")
    world.given_binding(fid, "v.py", "v.py::check", tok="fp1")
    world.given_doc_suggestion(fid, ts=1)  # ancient — beyond INTENT_STALE_MS

    res = world.when_code_changes(
        added=[chunk("v.py", "v.py::extra", tok="fp2", src="def extra(): ...")],
        propose=propose_ops(
            NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description="Validates more."),
            NodeOp(kind=NodeOpKind.ATTACH, feature_id=fid,
                   bindings=[("v.py", "v.py::extra")]),
        ),
        label="code drifts under an ABANDONED (stale) suggestion",
    )
    assert res.held_back == 0  # staleness backstop: the small amend applied
    assert world.feature(fid).description == "Validates more."


def test_realize_queue_stamps_caused_by_on_loop_a_ops(world):
    fid = world.given_feature("Rate limiting", description="Caps request rates.")
    world.given_binding(fid, "r.py", "r.py::limit", tok="fp1")

    # A directive for this feature is queued (as Loop B would after an
    # imperative edit) — manifest + realize.md exist while the agent implements.
    realize_path(world.codoc_dir).write_text("### 1. ⟨d-11aa22bb⟩ UPDATE FEATURE …")
    edits_channel.write_manifest(world.codoc_dir, [
        edits_channel.Directive(id="d-11aa22bb", feature_id=fid, kind="amend",
                                caused_by="d-sugg1"),
    ])

    # The implementing agent's code lands; the epoch-close Loop A pass reflects
    # it. The new helper goes to the feature that already describes `r.py` and the
    # modified bound chunk refreshes. Both are maintenance the directive caused,
    # which is what this is about.
    #
    # It used to become a coverage ADD proposal instead, titled after the symbol
    # with an empty description. A helper beside the function it supports, in a
    # file one feature already owns, is part of that feature; proposing a second
    # node for it gave a reviewer something with nothing in it to answer.
    world.when_code_changes(
        added=[chunk("r.py", "r.py::burst_window", tok="fp2", src="def burst_window(): ...")],
        modified=[chunk("r.py", "r.py::limit", tok="fp1b", src="def limit(): ...")],
        propose=propose_nothing,
        label="the realize implementation lands while the queue is open",
    )

    with world._store() as s:
        evs = s.recent_events(10)
        refresh = [e for e in evs if e.op.kind is NodeOpKind.REFRESH]
        attach = [e for e in evs if e.op.kind is NodeOpKind.ATTACH]
        assert refresh and refresh[0].caused_by == "d-11aa22bb", \
            "binding maintenance during the realize window carries the directive id"
        assert attach and attach[0].caused_by == "d-11aa22bb", \
            "and so does the new chunk it placed"
        assert attach[0].op.feature_id == fid, \
            "the helper belongs to the feature whose file it lives in"
    world.then_proposal_count(0)
