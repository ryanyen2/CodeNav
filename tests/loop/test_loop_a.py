"""Phase 2 — Loop A routing + apply logic (real store, mocked LLM)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.loop_a import _state_changeset, apply_changeset
from codoc.model.binding import Binding
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _propose(ops):
    def p(changes, subtree, all_titles, *, repo_name="codebase", config=None):
        return list(ops)
    return p


def _raising(*a, **k):
    raise AssertionError("LLM should not be called")


def _feature(store, **kw) -> Feature:
    f = Feature(**kw)
    store.upsert_feature(f)
    return f


def _bind(store, fid, file, symbol, fp="old"):
    store.upsert_binding(Binding(feature_id=fid, file=file, symbol_path=symbol, fingerprint=fp))


# -----------------------------------------------------------------------
def test_empty_changeset_noop(store):
    res = apply_changeset(ChangeSet(), store, propose=_raising)
    assert res.auto == {} and not res.llm_called and not res.proposed


def test_modified_bound_refreshes_without_llm(store):
    f = _feature(store, title="Foo")
    _bind(store, f.id, "a.py", "a.py::foo", fp="old")
    cs = ChangeSet(modified=[ChunkRef("a.py", "a.py::foo", "new", "def foo(): ...")])

    res = apply_changeset(cs, store, propose=_raising)

    assert not res.llm_called
    assert res.auto == {"refresh": 1}
    assert store.binding_at("a.py", "a.py::foo").fingerprint == "new"


def test_removed_binding_not_emptying_skips_llm(store):
    f = _feature(store, title="Foo")
    _bind(store, f.id, "a.py", "a.py::foo")
    _bind(store, f.id, "a.py", "a.py::bar")
    cs = ChangeSet(removed=[ChunkRef("a.py", "a.py::foo")])

    res = apply_changeset(cs, store, propose=_raising)

    assert not res.llm_called
    assert res.auto == {"detach": 1}
    assert store.binding_at("a.py", "a.py::foo") is None
    assert len(store.bindings_for_feature(f.id)) == 1


def test_removed_binding_empties_feature_triggers_llm_retire(store):
    f = _feature(store, title="Lonely")
    _bind(store, f.id, "a.py", "a.py::foo")
    cs = ChangeSet(removed=[ChunkRef("a.py", "a.py::foo")])

    ops = [NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id, rationale="all code gone")]
    res = apply_changeset(cs, store, propose=_propose(ops))

    assert res.llm_called
    assert res.auto == {"detach": 1}
    # structural retire is a pending proposal, NOT yet applied
    assert store.get_feature(f.id).retired is False
    assert len(store.pending_events()) == 1
    assert res.proposed[0].kind is NodeOpKind.RETIRE_NODE


def test_loop_a_does_not_duplicate_pending_add_proposal(store):
    """A chunk already claimed by a pending ADD_NODE (e.g. the agent reflected via
    MCP) is not re-proposed, and the LLM is not called for it (verification net)."""
    pending_add = Event(
        source="loop_a_agent", applied=False,
        op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Query cache", description="caches",
                  bindings=[("a.py", "a.py::cache")]),
    )
    store.append_event(pending_add)
    cs = ChangeSet(added=[ChunkRef("a.py", "a.py::cache", "h", "def cache(): ...")])

    res = apply_changeset(cs, store, propose=_raising)

    assert not res.llm_called          # agent already covered it → no LLM
    assert not res.proposed            # no duplicate proposal
    assert len(store.pending_events()) == 1  # the original agent proposal only


def test_loop_a_does_not_duplicate_pending_retire(store):
    """A feature emptied of code is not re-retired when a pending RETIRE already
    exists for it (e.g. the agent proposed it)."""
    f = _feature(store, title="Lonely")
    _bind(store, f.id, "a.py", "a.py::foo")
    store.append_event(Event(
        source="loop_a_agent", applied=False,
        op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id, rationale="code gone"),
    ))
    cs = ChangeSet(removed=[ChunkRef("a.py", "a.py::foo")])

    res = apply_changeset(cs, store, propose=_raising)

    assert not res.llm_called          # emptied feature already has a pending retire
    assert res.auto == {"detach": 1}   # the binding still detaches
    assert len(store.pending_events()) == 1


def test_added_unbound_attach_is_safe(store):
    f = _feature(store, title="Foo")
    _bind(store, f.id, "a.py", "a.py::foo")
    cs = ChangeSet(added=[ChunkRef("a.py", "a.py::bar", "fpbar", "def bar(): ...")])

    ops = [NodeOp(kind=NodeOpKind.ATTACH, feature_id=f.id, bindings=[("a.py", "a.py::bar")])]
    res = apply_changeset(cs, store, propose=_propose(ops))

    assert res.llm_called and not res.proposed
    got = store.binding_at("a.py", "a.py::bar")
    assert got is not None and got.feature_id == f.id and got.fingerprint == "fpbar"


def test_added_unbound_add_node_is_structural(store):
    cs = ChangeSet(added=[ChunkRef("new.py", "new.py::thing", "fp", "class Thing: ...")])
    ops = [NodeOp(kind=NodeOpKind.ADD_NODE, title="New thing", description="does a thing",
                  bindings=[("new.py", "new.py::thing")], rationale="no node fits")]

    res = apply_changeset(cs, store, propose=_propose(ops))

    assert res.proposed and res.proposed[0].kind is NodeOpKind.ADD_NODE
    assert store.list_features() == []                 # not created until accepted
    assert store.binding_at("new.py", "new.py::thing") is None
    assert len(store.pending_events()) == 1


def test_small_amend_autoapplies_large_amend_proposed(store):
    f = _feature(store, title="Foo", description="the quick brown fox jumps over")
    _bind(store, f.id, "a.py", "a.py::foo")

    # A fresh unbound add per sub-case forces the LLM pass; the LLM attaches it
    # (so the coverage net stays out of the way) and emits the amend under test.
    def _attach(sym):
        return NodeOp(kind=NodeOpKind.ATTACH, feature_id=f.id, bindings=[("a.py", sym)])

    cs_small = ChangeSet(added=[ChunkRef("a.py", "a.py::new1", "fp", "src")],
                         modified=[ChunkRef("a.py", "a.py::foo", "new", "src")])
    small = NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id,
                   description="the quick brown fox jumped over")  # ~1 char change
    res = apply_changeset(cs_small, store, propose=_propose([_attach("a.py::new1"), small]))
    assert not res.proposed
    assert store.get_feature(f.id).description == "the quick brown fox jumped over"

    cs_large = ChangeSet(added=[ChunkRef("a.py", "a.py::new2", "fp", "src")],
                         modified=[ChunkRef("a.py", "a.py::foo", "new", "src")])
    large = NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id,
                   description="completely different prose describing an entirely new responsibility set")
    res2 = apply_changeset(cs_large, store, propose=_propose([_attach("a.py::new2"), large]))
    assert res2.proposed and res2.proposed[0].kind is NodeOpKind.AMEND
    # unchanged because the large amend is only a proposal
    assert store.get_feature(f.id).description == "the quick brown fox jumped over"


# -----------------------------------------------------------------------
# Correspondence: move / rename relocate the binding deterministically (no LLM)
# -----------------------------------------------------------------------

def test_move_relocates_binding_without_llm(store):
    """Same content (tokens_hash) at a new file/symbol carries the attribution."""
    f = _feature(store, title="HTTP verbs")
    _bind(store, f.id, "api.py", "api.py::trace", fp="HASH_A")
    cs = ChangeSet(
        removed=[ChunkRef("api.py", "api.py::trace", "HASH_A", "", "TYPES_A")],
        added=[ChunkRef("utils.py", "utils.py::trace", "HASH_A", "def trace(): ...", "TYPES_A")],
    )
    res = apply_changeset(cs, store, propose=_raising)  # LLM must NOT run
    assert not res.llm_called
    assert store.binding_at("api.py", "api.py::trace") is None
    moved = store.binding_at("utils.py", "utils.py::trace")
    assert moved is not None and moved.feature_id == f.id


def test_rename_relocates_binding_same_file(store):
    """Same AST shape (types_hash) in the same file, different content = a rename."""
    f = _feature(store, title="Public API")
    _bind(store, f.id, "a.py", "a.py::options", fp="HASH_OLD")
    cs = ChangeSet(
        removed=[ChunkRef("a.py", "a.py::options", "HASH_OLD", "", "SHAPE_X")],
        added=[ChunkRef("a.py", "a.py::options_request", "HASH_NEW", "def options_request(): ...", "SHAPE_X")],
    )
    res = apply_changeset(cs, store, propose=_raising)
    assert not res.llm_called
    assert store.binding_at("a.py", "a.py::options") is None
    renamed = store.binding_at("a.py", "a.py::options_request")
    assert renamed is not None and renamed.feature_id == f.id


def test_rename_not_matched_across_files(store):
    """A types_hash match across DIFFERENT files is not a rename — fall through."""
    f = _feature(store, title="Owner")
    _bind(store, f.id, "a.py", "a.py::x", fp="H1")
    cs = ChangeSet(
        removed=[ChunkRef("a.py", "a.py::x", "H1", "", "SHAPE")],
        added=[ChunkRef("b.py", "b.py::y", "H2", "def y(): ...", "SHAPE")],
    )
    res = apply_changeset(cs, store, propose=_propose([]))  # no neighbor, no LLM op
    assert res.llm_called                                   # treated as a genuine add
    # not silently bound to f by a (wrong) relocation; coverage proposes a home
    assert store.binding_at("b.py", "b.py::y") is None
    assert res.proposed and res.proposed[0].kind is NodeOpKind.ADD_NODE


# -----------------------------------------------------------------------
# Coverage net: an added chunk the LLM ignored is never silently dropped
# -----------------------------------------------------------------------

def test_coverage_attaches_uncovered_add_to_neighbor(store):
    """LLM returns nothing for a new chunk → it lands with its graph-neighbor's feature."""
    f = _feature(store, title="Helpers")
    _bind(store, f.id, "util.py", "util.py::helper", fp="h")
    store.insert_edges([{
        "src_file": "new.py", "src_symbol": "new.py::caller", "dst_name": "helper",
        "dst_symbol": "util.py::helper", "dst_file": "util.py", "kind": "call", "internal": 1,
    }])
    cs = ChangeSet(added=[ChunkRef("new.py", "new.py::caller", "hh", "def caller(): helper()")])
    res = apply_changeset(cs, store, propose=_propose([]))
    bound = store.binding_at("new.py", "new.py::caller")
    assert bound is not None and bound.feature_id == f.id
    assert not res.proposed


def test_coverage_proposes_for_isolated_add(store):
    """An added chunk with no neighbors and no LLM placement → pending proposal."""
    _feature(store, title="Something")  # exists so the tree isn't empty
    cs = ChangeSet(added=[ChunkRef("x.py", "x.py::lonely", "z", "def lonely(): pass")])
    res = apply_changeset(cs, store, propose=_propose([]))
    assert store.binding_at("x.py", "x.py::lonely") is None     # proposal, not applied
    assert res.proposed and res.proposed[0].kind is NodeOpKind.ADD_NODE


# ── WS2: duplicate prevention + convergence ─────────────────────────────────

def test_add_node_same_title_as_unbound_node_attaches_not_duplicates(store):
    """An LLM ADD_NODE whose title matches a live, still-unbound node binds into
    that node instead of minting a duplicate-titled sibling (the hand-added node
    vs re-proposed node desync the audit found)."""
    parent = _feature(store, title="Demo runtime entrypoint")
    empty = _feature(store, title="CLI argument parsing", parent_id=parent.id)  # no bindings
    cs = ChangeSet(added=[ChunkRef("main.py", "main.py::parse_args", "h", "def parse_args(): ...")])

    # LLM independently proposes the SAME title (it saw it in all_titles).
    res = apply_changeset(cs, store, propose=_propose([
        NodeOp(kind=NodeOpKind.ADD_NODE, title="CLI argument parsing",
               parent_id=parent.id, bindings=[("main.py", "main.py::parse_args")]),
    ]))

    # parse_args bound to the EXISTING empty node; no duplicate created.
    b = store.binding_at("main.py", "main.py::parse_args")
    assert b is not None and b.feature_id == empty.id
    titles = [f.title for f in store.list_features()]
    assert titles.count("CLI argument parsing") == 1
    assert not res.proposed


def test_unrealized_placeholder_adopts_new_code(store):
    """A new unbound chunk an unrealized plan placeholder names in its description
    binds to that placeholder (flipping it realized), not a fresh node."""
    plan = _feature(store, title="Input validation helpers",
                    description="Add a validate_positive(x) helper in utils.py.",
                    realized=False)
    cs = ChangeSet(added=[ChunkRef("utils.py", "utils.py::validate_positive", "h",
                                   "def validate_positive(x): ...")])

    res = apply_changeset(cs, store, propose=_raising)  # adopted deterministically, no LLM

    b = store.binding_at("utils.py", "utils.py::validate_positive")
    assert b is not None and b.feature_id == plan.id
    assert store.get_feature(plan.id).realized is True       # first binding realized it
    assert not res.llm_called and not res.proposed
    assert res.auto.get("adopt") == 1


def test_sole_placeholder_adopts_only_with_flag(store):
    """Without a name match, the SOLE placeholder adopts new code only when
    adopt_placeholders=True (Loop B post-implement reflect)."""
    plan = _feature(store, title="Dark mode", description="Theme toggle.", realized=False)
    cs = ChangeSet(added=[ChunkRef("ui.py", "ui.py::persist_pref", "h", "def persist_pref(): ...")])

    # Default Loop A: no name match → does NOT adopt, surfaces a proposal.
    res = apply_changeset(cs, store, propose=_propose([]))
    assert store.binding_at("ui.py", "ui.py::persist_pref") is None
    assert res.proposed

    # Reflect as Loop B would: the sole live placeholder adopts the new code even
    # without a name match.
    cs2 = ChangeSet(added=[ChunkRef("ui2.py", "ui2.py::persist_pref2", "h", "def persist_pref2(): ...")])
    res2 = apply_changeset(cs2, store, propose=_raising, adopt_placeholders=True)
    b = store.binding_at("ui2.py", "ui2.py::persist_pref2")
    assert b is not None and b.feature_id == plan.id


def test_gc_drops_superseded_pending_add(store):
    """A pending ADD_NODE whose chunk is now bound elsewhere is GC'd so status can
    converge to in_sync."""
    owner = _feature(store, title="Owner")
    _bind(store, owner.id, "a.py", "a.py::foo", fp="h")
    # A stale pending ADD proposing a home for the already-bound chunk.
    stale = Event(source="loop_a", applied=False,
                  op=NodeOp(kind=NodeOpKind.ADD_NODE, title="foo",
                            bindings=[("a.py", "a.py::foo")]))
    store.append_event(stale)
    assert len(store.pending_events()) == 1

    # Any pass (even empty) GCs it.
    res = apply_changeset(ChangeSet(), store, propose=_raising)
    assert store.pending_events() == []
    assert res.auto.get("gc") == 1


# ── WS4: rename detection survives in the state-based path ───────────────────

def test_state_path_rename_carries_attribution_via_binding_types_hash(store):
    """A rename detected entirely from state (daemon was down): the old symbol
    left the index, but the binding stored its types_hash, so the state-based
    reconciler still recognises the rename and carries attribution — no LLM, no
    duplicate node, no dropped binding."""
    f = _feature(store, title="Auth login")
    store.upsert_binding(Binding(feature_id=f.id, file="auth.py",
                                 symbol_path="auth.py::login",
                                 fingerprint="tok_old", types_hash="shape1"))
    # Index now holds the RENAMED symbol: new name + new tokens, SAME AST shape.
    rows = [SimpleNamespace(file="auth.py", symbol_path="auth.py::signin",
                            tokens_hash="tok_new", types_hash="shape1",
                            source="def signin(): ...")]
    cs = _state_changeset(rows, store, None)
    # The removed side carries the binding's stored types_hash (the enabler).
    assert cs.removed and cs.removed[0].types_hash == "shape1"

    res = apply_changeset(cs, store, propose=_raising)  # deterministic — LLM must NOT run

    assert not res.llm_called
    assert store.binding_at("auth.py", "auth.py::login") is None       # old detached
    b = store.binding_at("auth.py", "auth.py::signin")
    assert b is not None and b.feature_id == f.id                      # attribution carried
    assert len(store.list_features()) == 1                             # no duplicate node
    assert b.types_hash == "shape1"                                    # shape recorded onward
