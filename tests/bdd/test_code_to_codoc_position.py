"""BDD userflows — code → codoc reflects in the CORRECT POSITION.

Each scenario edits the code (added / modified / removed / moved / renamed chunks)
and asserts where the change lands in the feature tree: which feature owns the
chunk, under which parent a new node sits, and that attribution is never silently
dropped or duplicated.

Loop A's single LLM pass is injected (``propose_*``) so the placement is
deterministic and assertable; the genuinely non-deterministic real-LLM placement
is exercised in ``test_e2e_userflows.py`` (which prints a report for manual
inspection rather than asserting exact positions).
"""
from __future__ import annotations

from codoc.model.event import NodeOp, NodeOpKind

from tests.bdd.world import chunk, propose_never, propose_nothing, propose_ops


# ── ADD ──────────────────────────────────────────────────────────────────────
def test_new_sibling_helper_attaches_to_the_owning_feature(world):
    """A new helper next to existing code lands in the SAME feature (no review)."""
    helpers = world.given_feature("Math helpers", binds=[("math.py", "math.py::add")])

    world.when_code_changes(
        added=[chunk("math.py", "math.py::subtract", tok="t1", src="def subtract(a, b): return a - b")],
        propose=propose_ops(NodeOp(kind=NodeOpKind.ATTACH, feature_id=helpers,
                                   bindings=[("math.py", "math.py::subtract")])),
    )

    world.then_owner_is("math.py", "math.py::subtract", helpers, note="attached, no review")
    world.then_proposal_count(0)
    world.then_status("in_sync")


def test_genuinely_new_feature_is_proposed_then_lands_under_its_parent_on_accept(world):
    """A distinct new responsibility is PROPOSED (not auto-applied); accepting it
    positions the node under the parent the model chose."""
    payments = world.given_feature("Payments")

    world.when_code_changes(
        added=[chunk("pay.py", "pay.py::refund", tok="t", src="def refund(order): ...")],
        propose=propose_ops(NodeOp(kind=NodeOpKind.ADD_NODE, title="Refund flow",
                                   description="Issues refunds for an order.", parent_id=payments,
                                   bindings=[("pay.py", "pay.py::refund")])),
    )

    # Structural ADD is a pending proposal — code stays unbound until accepted.
    eid = world.then_proposed_add("Refund flow")
    world.then_unbound("pay.py", "pay.py::refund")
    world.then_status("code_drift")

    # The human accepts; Loop B applies it at the proposed position.
    world.render()
    world.when_accept(eid)
    world.when_loop_b(dry_run=True)

    refund = world.then_feature_exists("Refund flow")
    world.then_parent_is(refund, payments)
    world.then_owner_is("pay.py", "pay.py::refund", refund, note="now bound after accept")


def test_added_code_with_no_home_is_proposed_not_dropped(world):
    """When the model can't place a new chunk and it has no graph neighbours, the
    coverage net surfaces a proposal rather than dropping the attribution."""
    world.given_feature("Existing thing", binds=[("a.py", "a.py::thing")])

    world.when_code_changes(
        added=[chunk("z.py", "z.py::orphan", tok="t", src="def orphan(): pass")],
        propose=propose_nothing,
    )

    world.then_unbound("z.py", "z.py::orphan")
    world.then_proposed_add("orphan")
    world.then_status("code_drift")


# ── MODIFY ───────────────────────────────────────────────────────────────────
def test_editing_a_bound_function_refreshes_in_place_without_review(world):
    """Changing a function body the tree already owns just refreshes its
    fingerprint — same feature, same position, no LLM, no proposal."""
    auth = world.given_feature("Authentication", binds=[("auth.py", "auth.py::login", "old")])

    world.when_code_changes(
        modified=[chunk("auth.py", "auth.py::login", tok="new", src="def login(): ...")],
        propose=propose_never,
    )

    world.then_owner_is("auth.py", "auth.py::login", auth, note="fingerprint refreshed")
    assert world.last_a.auto.get("refresh") == 1
    world.then_proposal_count(0)
    world.then_status("in_sync")


def test_small_description_refinement_applies_in_place_large_one_is_proposed(world):
    """When the model tightens a description: a small edit auto-applies in place; a
    wholesale rewrite is surfaced for review (the one similarity threshold)."""
    feat = world.given_feature("Color palette", description="the quick brown fox jumps over",
                               binds=[("colors.py", "colors.py::PALETTE", "old")])

    # A fresh unbound add forces the LLM pass; the model files it AND tweaks prose.
    small = NodeOp(kind=NodeOpKind.AMEND, feature_id=feat,
                   description="the quick brown fox jumped over")
    world.when_code_changes(
        added=[chunk("colors.py", "colors.py::ACCENT", tok="t", src="ACCENT = '#0af'")],
        modified=[chunk("colors.py", "colors.py::PALETTE", tok="new", src="PALETTE = {}")],
        propose=propose_ops(
            NodeOp(kind=NodeOpKind.ATTACH, feature_id=feat, bindings=[("colors.py", "colors.py::ACCENT")]),
            small),
        label="model files the new constant and tightens the description",
    )
    assert world.feature(feat).description == "the quick brown fox jumped over"
    world.then_proposal_count(0)

    # A wholesale rewrite of the same description is a proposal, not auto-applied.
    big = NodeOp(kind=NodeOpKind.AMEND, feature_id=feat,
                 description="completely different prose describing an unrelated responsibility")
    world.when_code_changes(
        added=[chunk("colors.py", "colors.py::SHADOW", tok="t2", src="SHADOW = 1")],
        modified=[chunk("colors.py", "colors.py::PALETTE", tok="new2", src="PALETTE = {}")],
        propose=propose_ops(
            NodeOp(kind=NodeOpKind.ATTACH, feature_id=feat, bindings=[("colors.py", "colors.py::SHADOW")]),
            big),
        label="model proposes a wholesale description rewrite",
    )
    world.then_proposal_count(1)
    assert world.feature(feat).description == "the quick brown fox jumped over", "big rewrite must not auto-apply"


# ── REMOVE ───────────────────────────────────────────────────────────────────
def test_deleting_the_last_function_detaches_and_proposes_retire(world):
    """Removing the only code a feature owns detaches it and proposes retiring the
    now-empty feature (a judgement call, so a proposal — never auto-retired)."""
    lonely = world.given_feature("Legacy export", binds=[("legacy.py", "legacy.py::dump")])

    world.when_code_changes(
        removed=[chunk("legacy.py", "legacy.py::dump")],
        propose=propose_ops(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=lonely,
                                   rationale="all code removed")),
    )

    world.then_unbound("legacy.py", "legacy.py::dump")
    assert world.last_a.auto.get("detach") == 1
    world.then_retired(lonely, False)          # retire is only PROPOSED
    world.then_proposal_count(1)
    world.then_status("code_drift")


def test_deleting_one_of_several_functions_only_detaches(world):
    """Removing one chunk from a multi-chunk feature detaches just that chunk; the
    feature keeps its position and its other code — no LLM."""
    api = world.given_feature("HTTP verbs",
                              binds=[("api.py", "api.py::get"), ("api.py", "api.py::post")])

    world.when_code_changes(removed=[chunk("api.py", "api.py::get")], propose=propose_never)

    world.then_unbound("api.py", "api.py::get")
    world.then_owner_is("api.py", "api.py::post", api, note="surviving sibling untouched")
    world.then_proposal_count(0)
    world.then_status("in_sync")


# ── MOVE / RENAME (relocation carries attribution, no LLM, no duplicate) ──────
def test_moving_a_function_to_another_file_carries_its_feature(world):
    """Same code at a new file/symbol (identical tokens_hash) is a MOVE: the
    feature attribution follows it to the new position deterministically."""
    feat = world.given_feature("Tracing", binds=[("api.py", "api.py::trace", "HASH_A", "SHAPE_A")])

    world.when_code_changes(
        removed=[chunk("api.py", "api.py::trace", tok="HASH_A", types="SHAPE_A")],
        added=[chunk("utils.py", "utils.py::trace", tok="HASH_A", src="def trace(): ...", types="SHAPE_A")],
        propose=propose_never,
    )

    world.then_unbound("api.py", "api.py::trace")
    world.then_owner_is("utils.py", "utils.py::trace", feat, note="moved, attribution carried")
    world.then_title_count("Tracing", 1)
    world.then_status("in_sync")


def test_renaming_a_function_in_place_keeps_its_feature(world):
    """Same AST shape (types_hash) in the same file under a new name is a RENAME:
    the feature stays put and adopts the new symbol — no duplicate node."""
    feat = world.given_feature("Public API", binds=[("a.py", "a.py::options", "HASH_OLD", "SHAPE_X")])

    world.when_code_changes(
        removed=[chunk("a.py", "a.py::options", tok="HASH_OLD", types="SHAPE_X")],
        added=[chunk("a.py", "a.py::options_request", tok="HASH_NEW",
                     src="def options_request(): ...", types="SHAPE_X")],
        propose=propose_never,
    )

    world.then_unbound("a.py", "a.py::options")
    world.then_owner_is("a.py", "a.py::options_request", feat, note="renamed, attribution carried")
    world.then_title_count("Public API", 1)


# ── DEDUP / LIFECYCLE on placement ───────────────────────────────────────────
def test_reproposed_same_title_node_binds_into_the_existing_empty_node(world):
    """If the model re-proposes a node whose title already names a live, empty
    node (e.g. a hand-added placeholder), the new code binds INTO that node —
    no duplicate-titled sibling appears in the tree."""
    parent = world.given_feature("CLI")
    empty = world.given_feature("Argument parsing", parent=parent)  # exists, owns nothing

    world.when_code_changes(
        added=[chunk("main.py", "main.py::parse_args", tok="t", src="def parse_args(): ...")],
        propose=propose_ops(NodeOp(kind=NodeOpKind.ADD_NODE, title="Argument parsing",
                                   parent_id=parent, bindings=[("main.py", "main.py::parse_args")])),
    )

    world.then_owner_is("main.py", "main.py::parse_args", empty, note="bound into the existing node")
    world.then_title_count("Argument parsing", 1)
    world.then_proposal_count(0)


def test_unrealized_placeholder_adopts_the_code_that_names_it(world):
    """New code whose symbol a plan placeholder named in its description binds to
    THAT placeholder (flipping it realized) — not a fresh duplicate node."""
    plan = world.given_placeholder("Input validation",
                                   description="Add a validate_positive(x) helper in utils.py.")

    world.when_code_changes(
        added=[chunk("utils.py", "utils.py::validate_positive", tok="t",
                     src="def validate_positive(x): ...")],
        propose=propose_never,  # adopted deterministically, no LLM
    )

    world.then_owner_is("utils.py", "utils.py::validate_positive", plan, note="placeholder adopted it")
    world.then_realized(plan, True)
    world.then_proposal_count(0)
    world.then_status("in_sync")
