"""BDD userflows — code DEPENDENCIES drive placement and impact.

codoc's code graph (call / import / inherit edges) is a first-class input to both
loops:

* **Placement** — when the LLM doesn't place a new chunk, the coverage net binds
  it to the feature its call/import neighbours already belong to, instead of
  dropping it or minting a junk node. The dependency *is* what positions it.
* **Impact** — when a symbol changes, the upstream features that call/import it
  are surfaced (``LoopAResult.impacted``) so a reviewer sees the blast radius.

These scenarios assert both behaviours deterministically (the graph edges are real
store rows; only the LLM pass is injected).
"""
from __future__ import annotations

from codoc.model.event import NodeOp, NodeOpKind

from tests.bdd.world import chunk, propose_nothing


def test_new_caller_lands_with_the_feature_it_calls(world):
    """A new function with no obvious home, but which calls an existing feature's
    code, is positioned WITH that feature via its dependency edge."""
    helpers = world.given_feature("String helpers", binds=[("util.py", "util.py::slugify")])
    world.given_call_edge("api.py::handler", "util.py::slugify")  # the new code depends on it

    # The model returns nothing for the new chunk; the dependency net must place it.
    world.when_code_changes(
        added=[chunk("api.py", "api.py::handler", tok="t", src="def handler(): return slugify(...)")],
        propose=propose_nothing,
    )

    world.then_owner_is("api.py", "api.py::handler", helpers, note="placed by its call dependency")
    world.then_proposal_count(0)
    world.then_status("in_sync")


def test_placement_follows_the_strongest_dependency(world):
    """When a new chunk depends on two features, it lands with the one it has the
    most edges to — the dominant dependency decides the position."""
    weak = world.given_feature("Formatting", binds=[("fmt.py", "fmt.py::indent")])
    strong = world.given_feature("Parsing",
                                 binds=[("parse.py", "parse.py::tokenize"), ("parse.py", "parse.py::lex")])
    world.given_call_edge("app.py::compile", "fmt.py::indent")      # 1 edge → Formatting
    world.given_call_edge("app.py::compile", "parse.py::tokenize")  # 2 edges → Parsing
    world.given_call_edge("app.py::compile", "parse.py::lex")

    world.when_code_changes(
        added=[chunk("app.py", "app.py::compile", tok="t", src="def compile(): ...")],
        propose=propose_nothing,
    )

    world.then_owner_is("app.py", "app.py::compile", strong, note="strongest dependency wins")
    world.then_proposal_count(0)


def test_import_dependency_also_positions_new_code(world):
    """An import edge counts as a dependency for placement, not just calls."""
    models = world.given_feature("Data models", binds=[("models.py", "models.py::User")])
    world.given_call_edge("views.py::profile", "models.py::User", kind="import")

    world.when_code_changes(
        added=[chunk("views.py", "views.py::profile", tok="t", src="from models import User")],
        propose=propose_nothing,
    )

    world.then_owner_is("views.py", "views.py::profile", models, note="placed by its import dependency")


def test_changing_a_symbol_surfaces_its_upstream_dependents(world):
    """Editing a shared helper flags the features that depend on it, so a reviewer
    sees what might be affected (impact propagation)."""
    core = world.given_feature("Core compute", binds=[("core.py", "core.py::compute", "old")])
    caller = world.given_feature("Reporting", binds=[("app.py", "app.py::report")])
    world.given_call_edge("app.py::report", "core.py::compute")  # Reporting depends on Core

    world.when_code_changes(
        modified=[chunk("core.py", "core.py::compute", tok="new", src="def compute(): ...  # changed")],
    )

    # The change itself auto-refreshes in place…
    world.then_owner_is("core.py", "core.py::compute", core)
    assert world.last_a.auto.get("refresh") == 1
    # …and the dependent feature is flagged as impacted for review.
    world.then_impacted_includes(caller)


def test_isolated_new_code_with_no_dependency_is_proposed_not_forced(world):
    """The contrast case: a new chunk with NO dependency edges and no LLM placement
    is surfaced as a proposal rather than force-attached anywhere."""
    world.given_feature("Unrelated", binds=[("x.py", "x.py::thing")])

    world.when_code_changes(
        added=[chunk("solo.py", "solo.py::standalone", tok="t", src="def standalone(): pass")],
        propose=propose_nothing,
    )

    world.then_unbound("solo.py", "solo.py::standalone")
    world.then_proposed_add("standalone")
    world.then_status("code_drift")
