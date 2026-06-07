"""BDD userflows — PARTIAL accept / reject of proposals.

When code drift raises several proposals at once, the human reviews them one by
one: accept some, reject others. These scenarios assert that exactly the accepted
proposals land (in the right position), the rejected ones vanish without trace,
the store converges, and — crucially — only the accepted edits that *request* code
queue a realize directive for the live session (the "dependency" that triggers
implementation).

Verdicts flow through ``.codoc/inbox.json`` (the IDE's Accept/Reject) and are
drained by Loop B, exactly as in production.
"""
from __future__ import annotations

from codoc.model.event import NodeOp, NodeOpKind

from tests.bdd.world import chunk, propose_ops


def test_accept_two_reject_one_lands_only_the_accepted_nodes(world):
    """Three new-feature proposals; the user keeps Alpha + Gamma, drops Beta."""
    app = world.given_feature("App")

    world.when_code_changes(
        added=[chunk("a.py", "a.py::alpha", tok="1", src="def alpha(): ..."),
               chunk("b.py", "b.py::beta", tok="2", src="def beta(): ..."),
               chunk("c.py", "c.py::gamma", tok="3", src="def gamma(): ...")],
        propose=propose_ops(
            NodeOp(kind=NodeOpKind.ADD_NODE, title="Alpha", parent_id=app, bindings=[("a.py", "a.py::alpha")]),
            NodeOp(kind=NodeOpKind.ADD_NODE, title="Beta", parent_id=app, bindings=[("b.py", "b.py::beta")]),
            NodeOp(kind=NodeOpKind.ADD_NODE, title="Gamma", parent_id=app, bindings=[("c.py", "c.py::gamma")]),
        ),
    )
    world.then_proposal_count(3)
    world.then_status("code_drift")

    # WHEN the human accepts Alpha + Gamma and rejects Beta.
    world.render()
    world.when_accept(world.pending_add_id("Alpha"))
    world.when_reject(world.pending_add_id("Beta"))
    world.when_accept(world.pending_add_id("Gamma"))
    res = world.when_loop_b(dry_run=True)

    # THEN only the accepted features exist, each at its proposed position.
    alpha = world.then_feature_exists("Alpha")
    world.then_parent_is(alpha, app)
    world.then_owner_is("a.py", "a.py::alpha", alpha)
    gamma = world.then_feature_exists("Gamma")
    world.then_owner_is("c.py", "c.py::gamma", gamma)

    # Beta left no trace: no feature, code unbound, proposal gone, store converged.
    assert [f for f in world.features() if f.title == "Beta"] == []
    world.then_unbound("b.py", "b.py::beta")
    world.then_proposal_count(0)
    world.then_status("in_sync")
    assert (res.accepted, res.rejected) == (2, 1)


def test_only_accepted_imperative_edits_trigger_a_realize_directive(world):
    """Accepting a documentation proposal records intent silently; accepting a
    proposal that *requests* code queues exactly one realize directive."""
    documents = world.given_pending_add(
        "Request logging", binds=[("log.py", "log.py::emit")],
        description="Writes one structured line per request.")          # descriptive → no code work
    requests = world.given_pending_add(
        "Dark mode", description="Add a light/dark theme toggle to settings.")  # imperative → code work

    world.when_accept(documents)
    world.when_accept(requests)
    res = world.when_loop_b(dry_run=False)

    # Both became real features…
    world.then_feature_exists("Request logging")
    world.then_feature_exists("Dark mode")
    # …but only the imperative one is queued for the live session to implement.
    world.then_directive_mentions("NEW FEATURE", "Dark mode")
    assert "Request logging" not in "\n".join(res.directives)
    assert res.queued is True
    world.then_status("awaiting_impl")
    assert "Dark mode" in world.realize_text()


def test_rejecting_every_proposal_queues_no_work_and_converges(world):
    """A clean sweep of rejections leaves the tree untouched and back in sync."""
    e1 = world.given_pending_add("Throwaway one", description="Add a thing.")
    e2 = world.given_pending_add("Throwaway two", description="Add another thing.")

    world.when_reject(e1)
    world.when_reject(e2)
    res = world.when_loop_b(dry_run=False)

    assert world.features() == []
    world.then_no_directives()
    assert res.queued is False
    world.then_proposal_count(0)
    world.then_status("in_sync")


def test_accept_a_retire_while_rejecting_a_doc_add(world):
    """Mixed verdicts of different kinds. Accepting an auto-raised RETIRE on bound
    code is DETACH-ONLY: the feature is untracked (retired=True) but NO code-removal
    directive is queued — a false auto-retire can never destroy live code on accept
    (only a human `~` edit removes code). The unrelated documentation ADD is rejected."""
    deprecated = world.given_feature("Deprecated API", binds=[("old.py", "old.py::legacy")])
    retire = world.given_pending(
        NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=deprecated, rationale="superseded"),
        label='propose RETIRE "Deprecated API"')
    doc_add = world.given_pending_add("Notes", description="Documents an existing module.")

    world.when_accept(retire)
    world.when_reject(doc_add)
    res = world.when_loop_b(dry_run=True)

    world.then_retired(deprecated, True)        # untracked on accept …
    world.then_no_directives()                  # … but no "remove this code" directive
    assert [f for f in world.features() if f.title == "Notes"] == []
    assert (res.accepted, res.rejected) == (1, 1)
    world.then_proposal_count(0)
