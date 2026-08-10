"""How the loop behaves when the tree is far behind the code.

This is the ordinary state of a real workspace, not an edge case: a bootstrap
that hit its file cap, an interrupted init, a branch that added a subsystem, a
repo opened for the first time. All three defects pinned here were found in one
such workspace — a 19-file package whose tree described 2 files — and all three
failed the same way, by producing something that looked like an answer.
"""
from __future__ import annotations

import pytest

from codoc.loop.apply import apply_op
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.loop_a import _added_batches, _COVERAGE_ATTACH_BUDGET, apply_changeset
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _feature(store, title, file="known.py", symbol="known.py::thing"):
    apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title=title, description="A feature.",
                    bindings=[(file, symbol)]),
             store, source="bootstrap", applied=True)
    return next(f.id for f in store.list_features() if f.title == title)


def _orphans(n, *, files=4):
    """`n` added chunks spread over `files` files, none of them bound."""
    return [ChunkRef(f"mod{i % files}.py", f"mod{i % files}.py::sym{i}", f"fp{i}",
                     f"def sym{i}(): ...")
            for i in range(n)]


class TestBatching:
    def test_a_large_added_set_is_split_across_calls(self, store):
        """One prompt holding hundreds of chunks does not get hundreds of ops
        back — it gets one or two umbrella ops, and everything else falls to the
        coverage net. Observed: 246 chunks in, one op out."""
        _feature(store, "Known")
        seen: list[int] = []

        def capture(changes, subtree, all_titles, *, repo_name="codebase", config=None):
            seen.append(len(changes["added"]))
            return []

        apply_changeset(ChangeSet(added=_orphans(120)), store, propose=capture)
        assert len(seen) > 1
        assert max(seen) <= 40   # batch size, plus whole-file slack

    def test_a_small_added_set_stays_one_call(self, store):
        _feature(store, "Known")
        seen: list[int] = []

        def capture(changes, subtree, all_titles, *, repo_name="codebase", config=None):
            seen.append(len(changes["added"]))
            return []

        apply_changeset(ChangeSet(added=_orphans(5)), store, propose=capture)
        assert len(seen) == 1

    def test_a_pass_with_nothing_added_still_asks(self, store):
        """A retire or an amend is triggered by removal or in-place change, so
        batching an empty added-list must not skip the call entirely."""
        fid = _feature(store, "Known")
        calls = []

        def capture(changes, subtree, all_titles, *, repo_name="codebase", config=None):
            calls.append(changes)
            return []

        cs = ChangeSet(removed=[ChunkRef("known.py", "known.py::thing", "fp")])
        apply_changeset(cs, store, propose=capture, allow_retire=True)
        assert len(calls) == 1
        assert calls[0]["removed"][0]["current_feature_id"] == fid

    def test_batches_never_split_a_file(self):
        added = [{"file": "big.py", "symbol_path": f"big.py::s{i}"} for i in range(60)]
        added += [{"file": "small.py", "symbol_path": "small.py::s"}]
        for batch in _added_batches(added, 25):
            files = {e["file"] for e in batch}
            assert len(files) == 1 or "big.py" not in files

    def test_later_batches_see_earlier_proposals(self, store):
        """Otherwise two calls mint near-duplicate nodes for one concern."""
        _feature(store, "Known")
        titles_seen: list[list[str]] = []

        def capture(changes, subtree, all_titles, *, repo_name="codebase", config=None):
            titles_seen.append([t["title"] for t in all_titles])
            return [NodeOp(kind=NodeOpKind.ADD_NODE, title="Freshly proposed",
                           description="d.",
                           bindings=[(c["file"], c["symbol_path"])
                                     for c in changes["added"][:1]])]

        apply_changeset(ChangeSet(added=_orphans(120)), store, propose=capture)
        assert "Freshly proposed" in titles_seen[-1]


class TestCoverageNetBudget:
    def test_one_feature_cannot_absorb_a_whole_package(self, store):
        """The observed failure: 245 chunks across 16 files auto-attached to a
        single node, which then read as the feature that owns everything."""
        fid = _feature(store, "Known")
        added = _orphans(60)
        for a in added:   # every orphan calls the known symbol
            store.insert_edges([{
                "src_file": a.file, "src_symbol": a.symbol_path, "dst_name": "thing",
                "dst_symbol": "known.py::thing", "dst_file": "known.py",
                "kind": "call", "internal": 1,
            }])

        apply_changeset(ChangeSet(added=added), store,
                        propose=lambda *a, **k: [])

        absorbed = sum(1 for b in store.bindings_for_feature(fid) if b.file != "known.py")
        assert absorbed <= _COVERAGE_ATTACH_BUDGET

    def test_what_the_budget_rejects_becomes_a_proposal(self, store):
        """Rejected chunks must not vanish — an unplaced chunk shows as drift and
        gets asked about again; a silently dropped one never does."""
        fid = _feature(store, "Known")
        added = _orphans(60)
        for a in added:
            store.insert_edges([{
                "src_file": a.file, "src_symbol": a.symbol_path, "dst_name": "thing",
                "dst_symbol": "known.py::thing", "dst_file": "known.py",
                "kind": "call", "internal": 1,
            }])

        res = apply_changeset(ChangeSet(added=added), store, propose=lambda *a, **k: [])

        placed = {(b.file, b.symbol_path) for b in store.bindings_for_feature(fid)}
        proposed = {b for op in res.proposed for b in op.bindings}
        for a in added:
            assert (a.file, a.symbol_path) in placed or (a.file, a.symbol_path) in proposed


class TestFallbackTitles:
    def test_bulk_leftovers_group_per_file_not_per_symbol(self, store):
        """Symbol-named nodes ("HTTPDigestAuth.handle_401", "__module__") are the
        symbol index with extra steps; accepting them would bake the shape of the
        code into a document that exists to describe intent."""
        _feature(store, "Known")
        added = [ChunkRef("auth.py", f"auth.py::HTTPDigestAuth.m{i}", f"fp{i}")
                 for i in range(8)]
        res = apply_changeset(ChangeSet(added=added), store, propose=lambda *a, **k: [])

        adds = [op for op in res.proposed if op.kind is NodeOpKind.ADD_NODE]
        assert len(adds) == 1
        assert len(adds[0].bindings) == 8
        assert "::" not in (adds[0].title or "")
        assert "HTTPDigestAuth.m0" != adds[0].title

    def test_a_lone_orphan_keeps_its_symbol_name(self, store):
        """For one chunk the symbol is the more informative label, and it is the
        file's whole story anyway."""
        _feature(store, "Known")
        added = [ChunkRef("solo.py", "solo.py::standalone", "fp")]
        res = apply_changeset(ChangeSet(added=added), store, propose=lambda *a, **k: [])

        (add,) = [op for op in res.proposed if op.kind is NodeOpKind.ADD_NODE]
        assert add.title == "standalone"

    def test_module_level_orphans_are_named_after_their_file(self, store):
        """Every Python file has a `__module__` chunk, so naming nodes after the
        leaf symbol gave six files six nodes all called `__module__` —
        indistinguishable in the outline, and the filename that would have told
        them apart was exactly what got discarded."""
        _feature(store, "Known")
        added = [ChunkRef(f, f"{f}::__module__", f"fp{f}")
                 for f in ("auth.py", "certs.py", "api.py")]
        res = apply_changeset(ChangeSet(added=added), store, propose=lambda *a, **k: [])

        titles = [op.title for op in res.proposed if op.kind is NodeOpKind.ADD_NODE]
        assert "__module__" not in titles
        assert len(titles) == len(set(titles)) == 3

    def test_a_leaf_that_collides_with_an_existing_feature_is_qualified(self, store):
        """These ops go straight to apply_op and never pass the (title, parent)
        identity guard, so the collision check has to happen in the net."""
        _feature(store, "Standalone")
        added = [ChunkRef("solo.py", "solo.py::Standalone", "fp")]
        res = apply_changeset(ChangeSet(added=added), store, propose=lambda *a, **k: [])

        (add,) = [op for op in res.proposed if op.kind is NodeOpKind.ADD_NODE]
        assert (add.title or "").lower() != "standalone"
