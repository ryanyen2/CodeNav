"""Phase 2 — Loop A routing + apply logic (real store, mocked LLM)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from codoc.loop.apply import apply_op, should_auto_apply
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.loop_a import _backfill_types_hashes, _state_changeset, apply_changeset
from codoc.model.binding import Binding
from codoc.model.event import ACTOR_HUMAN, Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _propose(ops):
    def p(changes, subtree, all_titles, *, repo_name="codebase", config=None, **_kw):
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


def test_backfill_types_hashes_fills_empty_only(store):
    """D4: a binding attributed WITHOUT an AST shape (legacy / MCP / propose bind)
    gets its ``types_hash`` backfilled from the live index, so rename detection
    works on the next edit. Idempotent, and never overwrites a known shape."""
    f = _feature(store, title="X")
    store.upsert_binding(Binding(feature_id=f.id, file="a.py", symbol_path="a.py::g",
                                 fingerprint="h", types_hash=""))        # no shape recorded
    store.upsert_binding(Binding(feature_id=f.id, file="b.py", symbol_path="b.py::k",
                                 fingerprint="h", types_hash="KNOWN"))   # already has one

    rows = [SimpleNamespace(file="a.py", symbol_path="a.py::g", types_hash="SHAPE"),
            SimpleNamespace(file="b.py", symbol_path="b.py::k", types_hash="OTHER")]

    assert _backfill_types_hashes(store, rows) == 1                       # only the empty one
    assert store.binding_at("a.py", "a.py::g").types_hash == "SHAPE"
    assert store.binding_at("b.py", "b.py::k").types_hash == "KNOWN"      # never overwritten
    assert _backfill_types_hashes(store, rows) == 0                       # idempotent


def test_cross_file_rename_carries_attribution_when_shape_unique(store):
    """D3: a GLOBALLY 1:1-unique AST-shape (types_hash) that moved to a DIFFERENT
    file under a new name is a cross-file rename — the attribution is carried
    deterministically (no LLM), recovering what would otherwise re-place as a new
    node and drop the binding."""
    f = _feature(store, title="Owner")
    _bind(store, f.id, "a.py", "a.py::x", fp="H1")
    cs = ChangeSet(
        removed=[ChunkRef("a.py", "a.py::x", "H1", "", "SHAPE")],
        added=[ChunkRef("b.py", "b.py::y", "H2", "def y(): ...", "SHAPE")],
    )
    res = apply_changeset(cs, store, propose=_raising)  # deterministic, no LLM
    assert not res.llm_called
    assert store.binding_at("a.py", "a.py::x") is None
    moved = store.binding_at("b.py", "b.py::y")
    assert moved is not None and moved.feature_id == f.id


def test_cross_file_rename_skipped_when_shape_ambiguous(store):
    """The cross-file rename gate is STRICT: when the same AST-shape is shared by
    more than one unmatched chunk it is NOT a rename (it could mis-pair unrelated
    symbols across the repo) — fall through to the LLM, never a blind relocation."""
    f = _feature(store, title="Owner")
    _bind(store, f.id, "a.py", "a.py::x", fp="H1")
    # Two added chunks share SHAPE → not globally 1:1-unique → no cross-file pair.
    cs = ChangeSet(
        removed=[ChunkRef("a.py", "a.py::x", "H1", "", "SHAPE")],
        added=[ChunkRef("b.py", "b.py::y", "H2", "def y(): ...", "SHAPE"),
               ChunkRef("c.py", "c.py::z", "H3", "def z(): ...", "SHAPE")],
    )
    res = apply_changeset(cs, store, propose=_propose([]))
    assert res.llm_called                                   # ambiguous → LLM decides
    assert store.binding_at("b.py", "b.py::y") is None      # not silently bound
    assert store.binding_at("c.py", "c.py::z") is None


def test_cross_file_rename_gate_counts_unbound_removed_too(store):
    """The uniqueness gate is judged over the FULL change set: an UNBOUND removed
    chunk sharing the shape makes the pairing ambiguous, so a bound removal is NOT
    mis-attributed to an added chunk that is really the rename of the unbound one.
    (Regression — counting only bound removals let this false-attribution through.)"""
    f = _feature(store, title="Owner")
    _bind(store, f.id, "a.py", "a.py::x", fp="H1")          # bound, shape SHAPE
    # b.py::y (unbound) also has SHAPE and is removed; c.py::z (added) has SHAPE.
    cs = ChangeSet(
        removed=[ChunkRef("a.py", "a.py::x", "H1", "", "SHAPE"),   # bound (feature f)
                 ChunkRef("b.py", "b.py::y", "H2", "", "SHAPE")],  # UNBOUND, same shape
        added=[ChunkRef("c.py", "c.py::z", "H3", "def z(): ...", "SHAPE")],
    )
    res = apply_changeset(cs, store, propose=_propose([]))
    # Two removed chunks carry SHAPE → ambiguous → NOT a deterministic rename;
    # f must NOT be silently attributed to c.py::z.
    bound = store.binding_at("c.py", "c.py::z")
    assert bound is None or bound.feature_id != f.id
    assert res.llm_called


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


def test_binding_less_add_deduped_by_title_parent(store):
    """D2 — feature-identity guard: a binding-LESS ADD_NODE whose (title, parent)
    already names a live feature is folded, not re-proposed as a duplicate theme
    parent (the UNIQUE binding constraint can't catch a binding-less duplicate)."""
    parent = _feature(store, title="Subsystems")
    _feature(store, title="Indexing", parent_id=parent.id)  # existing binding-less theme
    cs = ChangeSet(added=[ChunkRef("idx.py", "idx.py::run", "h", "def run(): ...")])

    # The org/LLM pass re-proposes the SAME theme parent (case-insensitive match).
    res = apply_changeset(cs, store, propose=_propose([
        NodeOp(kind=NodeOpKind.ADD_NODE, title="indexing", parent_id=parent.id),
    ]))

    assert res.auto.get("dedup_node") == 1
    # No second "Indexing" exists or is pending.
    assert [f.title for f in store.list_features()].count("Indexing") == 1
    pending_add_titles = [(e.op.title or "").lower() for e in store.pending_events()
                          if e.op.kind is NodeOpKind.ADD_NODE]
    assert "indexing" not in pending_add_titles


def test_binding_less_add_kept_when_parent_differs(store):
    """The identity key includes the PARENT: a same-titled node under a DIFFERENT
    parent is a distinct concept and is NOT folded."""
    p1 = _feature(store, title="Backend")
    p2 = _feature(store, title="Frontend")
    _feature(store, title="Caching", parent_id=p1.id)
    cs = ChangeSet(added=[ChunkRef("c.py", "c.py::cache", "h", "def cache(): ...")])

    res = apply_changeset(cs, store, propose=_propose([
        NodeOp(kind=NodeOpKind.ADD_NODE, title="Caching", parent_id=p2.id),  # different parent
    ]))

    assert res.auto.get("dedup_node") is None  # not folded — distinct (title, parent)
    assert any(e.op.kind is NodeOpKind.ADD_NODE and e.op.title == "Caching"
               for e in store.pending_events())


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
    apply_changeset(cs2, store, propose=_raising, adopt_placeholders=True)
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


# ── False-retire hardening (plan→implement window robustness) ────────────────

def test_gc_drops_stale_retire_when_feature_rebinds(store):
    """A pending RETIRE is dropped once the feature has bindings again: its premise
    ("lost its last binding") is now false. This is the core fix for a retire raised
    while a feature was momentarily empty mid-implementation."""
    f = _feature(store, title="Retriever")
    _bind(store, f.id, "r.py", "r.py::search", fp="h")   # code rebound
    store.append_event(Event(source="loop_a", applied=False,
                             op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id)))
    assert len(store.pending_events()) == 1

    res = apply_changeset(ChangeSet(), store, propose=_raising)  # even an empty pass GCs it

    assert store.pending_events() == []
    assert store.get_feature(f.id).retired is False
    assert res.auto.get("gc") == 1


def test_allow_retire_false_suppresses_llm_retire(store):
    """The twitchy temporal pass (allow_retire=False) never surfaces a RETIRE, even
    when the LLM proposes one for an emptied feature."""
    f = _feature(store, title="Lonely")
    _bind(store, f.id, "a.py", "a.py::foo")
    cs = ChangeSet(removed=[ChunkRef("a.py", "a.py::foo")])
    ops = [NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id, rationale="all code gone")]

    res = apply_changeset(cs, store, propose=_propose(ops), allow_retire=False)

    assert not res.proposed
    assert store.get_feature(f.id).retired is False
    assert store.pending_events() == []
    assert res.auto == {"detach": 1}   # the binding still detaches


def test_unrealized_placeholder_not_retire_candidate(store):
    """A realized=False plan placeholder that loses a transient binding is excluded
    from the emptied set → never retire-proposed (it reverts to awaiting-impl)."""
    plan = _feature(store, title="Future thing", realized=False)
    _bind(store, plan.id, "a.py", "a.py::stub")
    cs = ChangeSet(removed=[ChunkRef("a.py", "a.py::stub")])

    res = apply_changeset(cs, store, propose=_raising)  # placeholder excluded → no LLM

    assert not res.llm_called
    assert res.auto == {"detach": 1}
    assert store.get_feature(plan.id).retired is False


def test_amend_on_change_runs_llm_only_when_enabled(store):
    """A pure in-place modification to a realized feature's code triggers the LLM
    (for a possible description amend) only when amend_on_change=True."""
    f = _feature(store, title="Engine", description="Runs the token generation loop.")
    _bind(store, f.id, "engine.py", "engine.py::run", fp="old")

    # Off (the temporal pass): modified-only → REFRESH, no LLM.
    cs0 = ChangeSet(modified=[ChunkRef("engine.py", "engine.py::run", "new", "def run(): ...")])
    res0 = apply_changeset(cs0, store, propose=_raising, amend_on_change=False)
    assert not res0.llm_called and res0.auto == {"refresh": 1}

    # On (the authoritative pass): the LLM runs and can propose an amend.
    cs1 = ChangeSet(modified=[ChunkRef("engine.py", "engine.py::run", "new2", "def run(): ... retrieval ...")])
    amend = NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id,
                   description="Runs the token generation loop with a retrieval-augmented branch.")
    res1 = apply_changeset(cs1, store, propose=_propose([amend]), amend_on_change=True)
    assert res1.llm_called
    # the amend actually lands — proposed (large edit) or auto-applied (small)
    assert res1.proposed or store.get_feature(f.id).description != "Runs the token generation loop."


def test_gc_preserves_retire_driven_by_in_flight_removal(store):
    """Symmetric to the rebind case: a pending RETIRE whose feature's ONLY binding is
    being removed THIS pass must NOT be GC'd — the removal genuinely empties it, and
    the dedup path (not GC) handles suppressing a duplicate retire."""
    f = _feature(store, title="Lonely")
    _bind(store, f.id, "a.py", "a.py::foo")
    store.append_event(Event(source="loop_a", applied=False,
                             op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id)))
    cs = ChangeSet(removed=[ChunkRef("a.py", "a.py::foo")])

    res = apply_changeset(cs, store, propose=_raising)  # binding in removed_keys → retire survives GC

    assert res.auto.get("gc") is None                 # not GC'd
    assert len(store.pending_events()) == 1            # the retire is still pending
    assert not res.llm_called                          # dedup path suppressed a duplicate


def test_llm_add_titled_after_a_pseudo_symbol_is_retitled_from_the_file(store):
    """34513d1 guarded the deterministic coverage net against `__module__` titles;
    this is the same guard at the LLM door — a model handed `x.py::__module__`
    happily names the node after the symbol, and every Python file has one."""
    cs = ChangeSet(added=[ChunkRef("livehub/github.py",
                                   "livehub/github.py::__module__", "fp", "import base64")])
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title="__module__", description="module-level code",
                bindings=[("livehub/github.py", "livehub/github.py::__module__")])

    res = apply_changeset(cs, store, propose=_propose([op]))

    titles = {e.op.title for e in store.pending_events()} | {f.title for f in store.list_features()}
    assert "__module__" not in titles
    assert res.llm_called


def test_an_unparseable_llm_reply_does_not_sink_the_pass(monkeypatch):
    """A reply that is not valid JSON must degrade to "no ops", not raise.

    Per-op tolerance only helps after the response has parsed. A truncated reply
    raises out of parse_solution and takes the whole pass with it, discarding
    deterministic refresh/relocate/detach work that had already succeeded — seen
    once on a 158-commit altair replay.
    """
    from codoc.agent import tree_update

    def boom(*a, **k):
        raise ValueError("Expecting ',' delimiter: line 1 column 3752")

    monkeypatch.setattr(tree_update, "run_agent", boom)
    ops = tree_update.propose_tree_update(
        changes={"added": [], "removed": [], "modified": []},
        subtree=[], all_titles=[], repo_name="x",
    )
    assert ops == []


def test_model_may_not_reattribute_code_the_change_never_touched(store):
    """An ATTACH is a SAFE op, so it applies without review — which means a model
    reply could silently move any binding in the tree to another feature. Seen on
    altair: a pass about one reorganization also re-attributed two functions in a
    file the commit never opened.
    """
    from codoc.model.binding import Binding

    owner, thief = Feature(title="Owner"), Feature(title="Thief")
    store.upsert_feature(owner)
    store.upsert_feature(thief)
    store.upsert_binding(Binding(feature_id=owner.id, file="quiet.py",
                                 symbol_path="quiet.py::stays", fingerprint="h0"))
    cs = ChangeSet(added=[ChunkRef("loud.py", "loud.py::fresh", "h", "def fresh(): ...")])

    def propose(*a, **kwargs):
        # Answers about the touched file, and also grabs the untouched one.
        return [NodeOp(kind=NodeOpKind.ATTACH, feature_id=thief.id,
                       bindings=[("loud.py", "loud.py::fresh"),
                                 ("quiet.py", "quiet.py::stays")])]

    apply_changeset(cs, store, propose=propose)

    still = store.binding_at("quiet.py", "quiet.py::stays")
    assert still is not None and still.feature_id == owner.id
    moved = store.binding_at("loud.py", "loud.py::fresh")
    assert moved is not None and moved.feature_id == thief.id

# ---------------------------------------------------------------------------
# A settings section is a chunk like any other
# ---------------------------------------------------------------------------

def test_a_new_settings_section_reaches_the_pass_with_its_values(store):
    """The claim the whole config-file plan rests on, in its mechanical half.

    Which feature the section lands on is the model's judgment and cannot be pinned
    here. What can, and what the plan's failure was really about, is that the pass is
    SHOWN the decision: the comment above `[periods]` and `month = made` in the
    prompt, not a bare symbol path that leaves it describing the mechanism again.
    """
    from codoc.model.binding import Binding

    summaries = Feature(title="Monthly summaries",
                        description="The month threshold is read from rules.toml.")
    store.upsert_feature(summaries)
    store.upsert_binding(Binding(feature_id=summaries.id, file="tally/summary.py",
                                 symbol_path="tally/summary.py::summarise",
                                 fingerprint="h0"))
    section = ('# A month is lined up on the date the payment was made.\n'
               '[periods]\nmonth = "made"\n')
    cs = ChangeSet(added=[ChunkRef("tally/rules.toml", "tally/rules.toml::periods",
                                   "h1", section)])
    seen: list[dict] = []

    def propose(changes, *_a, **_kw):
        seen.append(changes)
        return [NodeOp(kind=NodeOpKind.ATTACH, feature_id=summaries.id,
                       bindings=[("tally/rules.toml", "tally/rules.toml::periods")])]

    apply_changeset(cs, store, propose=propose)

    added = seen[0]["added"]
    assert [a["symbol_path"] for a in added] == ["tally/rules.toml::periods"]
    assert added[0]["source"] == section
    bound = store.binding_at("tally/rules.toml", "tally/rules.toml::periods")
    assert bound is not None and bound.feature_id == summaries.id


# ── an amend that changes nothing is dropped, not applied and not proposed ───

def _human_feature(store, description: str) -> str:
    """A feature whose prose a PERSON wrote, recorded the way the ledger records it."""
    apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Edit queue", description=description),
             store, source="human", applied=True, actor=ACTOR_HUMAN)
    fid = next(f.id for f in store.list_features() if f.title == "Edit queue")
    _bind(store, fid, "a.py", "a.py::foo")
    return fid


def _pass(store, fid, ops, *, n=[0]):
    """One Loop A pass whose LLM returns *ops*.

    A fresh unbound add per pass is what forces the LLM door open; the pass attaches
    it so the coverage net stays out of the way of what is under test.
    """
    n[0] += 1
    sym = f"a.py::new{n[0]}"
    cs = ChangeSet(added=[ChunkRef("a.py", sym, "fp", "src")],
                   modified=[ChunkRef("a.py", "a.py::foo", "new", "def foo(): ...")])
    attach = NodeOp(kind=NodeOpKind.ATTACH, feature_id=fid, bindings=[("a.py", sym)])
    return apply_changeset(cs, store, propose=_propose([attach, *ops]))


HUMAN_PROSE = (
    "Holds the queue of edits waiting to be implemented. Readers tolerate a "
    "missing file, since an empty queue and no queue mean the same thing."
)
# Keeps the first sentence and rewrites the second: too much of the author's
# wording gone to auto-apply over them, little enough to auto-apply over the loop.
MID_BAND_REWRITE = (
    "Holds the queue of edits waiting to be implemented. Readers tolerate a "
    "missing file, which the loader treats as an empty queue."
)


def test_an_amend_restating_the_stored_prose_is_dropped(store):
    fid = _human_feature(store, HUMAN_PROSE)
    same = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description=HUMAN_PROSE)

    res = _pass(store, fid, [same])

    assert res.restated == 1
    assert "amend" not in res.auto, "not applied"
    assert res.proposed == [], "and not put to a person for a verdict on nothing"


def test_the_author_keeps_the_paragraph_a_restatement_would_have_taken(store):
    """The consequence that makes this a correctness fix rather than tidiness.

    `apply_op` stamps `feature_writers` with whoever wrote last, so applying a
    restatement moves a human-written node to the loop — and the amend gate then
    judges the next rewrite by the machine bar. One op that changed no words is
    otherwise enough to unlock somebody's prose.
    """
    fid = _human_feature(store, HUMAN_PROSE)
    rewrite = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description=MID_BAND_REWRITE)
    assert should_auto_apply(rewrite, store) is False, "the author's node, before"

    _pass(store, fid, [NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                              description=HUMAN_PROSE)])

    assert store.feature_writer_info(fid) == ("human", ACTOR_HUMAN)
    assert should_auto_apply(rewrite, store) is False, "still theirs"


def test_the_timeline_gains_no_moment_from_a_restatement(store):
    """The scrubber and the per-span blame read applied events. A change a reader can
    open and find nothing in is how a diff stops being worth reading."""
    fid = _human_feature(store, HUMAN_PROSE)

    _pass(store, fid, [NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                              description=HUMAN_PROSE + "\n")])

    # The attach and refresh this pass also makes are binding maintenance; neither
    # claims the prose changed, and neither is what the scrubber renders as a revision.
    prose = [e for e in store.events_for_feature(fid, limit=999)
             if e.op.description is not None]
    assert [e.actor for e in prose] == [ACTOR_HUMAN], "only the author ever wrote here"


def test_a_real_amend_in_the_same_batch_still_lands(store):
    """The drop is per op. A pass that restates one description and repairs another
    must still perform the repair."""
    fid = _human_feature(store, HUMAN_PROSE)
    repaired = HUMAN_PROSE.replace("an empty queue", "an empty queue on disk")

    res = _pass(store, fid, [
        NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description=HUMAN_PROSE),
        NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description=repaired),
    ])

    assert res.restated == 1
    assert res.auto.get("amend") == 1
    assert store.get_feature(fid).description == repaired


def test_the_drop_is_reported_rather_than_silent(store):
    fid = _human_feature(store, HUMAN_PROSE)
    res = _pass(store, fid, [NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                                    description=HUMAN_PROSE)])

    assert "changed nothing" in res.summary()

