"""Hierarchical bootstrap — per-file pass + org pass (mocked LLM, no index)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from codoc.graph.query import build_graph
from codoc.loop.bootstrap_hier import (
    _apply_ops_with_local_ids,
    _ensure_file_coverage,
    _feature_coupling,
    _only_offered_features,
    _settings_readers,
    bootstrap_hier_from_chunks,
)
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import open_store


@dataclass
class FakeRow:
    file: str
    symbol_path: str
    source: str = ""
    language: str = "python"
    id: int = 0
    tokens_hash: str = "h"
    types_hash: str = "t"
    start_byte: int = 0
    end_byte: int = 0
    embedding: object = None


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _add(temp_id, title, bindings, parent_id=None):
    return NodeOp(
        kind=NodeOpKind.ADD_NODE,
        feature_id=temp_id,          # bootstrap_agent stashes the local id here
        parent_id=parent_id,
        title=title,
        description=f"{title} does things.",
        bindings=bindings,
    )


# ---------------------------------------------------------------------------
# Local-id resolution + apply
# ---------------------------------------------------------------------------

def test_local_id_nesting_within_one_call(store):
    """A child referencing a sibling's temp id nests under it after apply.

    This is the core fix: within a SINGLE call the LLM nests a new node under
    another new node via temp ids, which the old apply path could not do.
    """
    ops = [
        _add("n1", "Parent", [("a.py", "a.py::Parent")]),
        _add("n2", "Child", [("a.py", "a.py::Parent.child")], parent_id="n1"),
    ]
    _apply_ops_with_local_ids(ops, store, {}, source="bootstrap")

    feats = {f.title: f for f in store.list_features()}
    assert set(feats) == {"Parent", "Child"}
    assert feats["Child"].parent_id == feats["Parent"].id
    assert feats["Parent"].parent_id is None


def test_local_id_hallucinated_parent_falls_to_top_level(store):
    """An add_node whose parent_id resolves to nothing lands at top level (no crash)."""
    ops = [_add("n1", "Orphan", [("a.py", "a.py::x")], parent_id="does-not-exist")]
    _apply_ops_with_local_ids(ops, store, {}, source="bootstrap")
    feats = store.list_features()
    assert len(feats) == 1 and feats[0].parent_id is None


def test_move_node_nests_existing_feature_under_new_theme(store):
    """Org-style ops: a new theme (temp id) adopts an existing real feature."""
    # Seed an existing feature.
    _apply_ops_with_local_ids(
        [_add("n1", "Sessions", [("s.py", "s.py::Session")])], store, {}, source="bootstrap"
    )
    real_id = store.list_features()[0].id

    ops = [
        _add("t1", "HTTP lifecycle", []),
        NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=real_id, parent_id="t1"),
    ]
    _apply_ops_with_local_ids(ops, store, {}, source="bootstrap")

    theme = next(f for f in store.list_features() if f.title == "HTTP lifecycle")
    sessions = store.get_feature(real_id)
    assert theme.parent_id is None
    assert sessions.parent_id == theme.id


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def test_coverage_folds_uncovered_into_primary_same_file(store):
    """Uncovered chunks fold into the file's largest node — never a new junk node."""
    rows = [
        FakeRow("a.py", "a.py::Big"),
        FakeRow("a.py", "a.py::Big.m1"),
        FakeRow("a.py", "a.py::stray"),  # left uncovered by the model
    ]
    ops = [_add("n1", "Big thing", [("a.py", "a.py::Big"), ("a.py", "a.py::Big.m1")])]
    out = _ensure_file_coverage(ops, rows, "a.py")
    assert len(out) == 1  # no new node minted
    assert ("a.py", "a.py::stray") in out[0].bindings


def test_coverage_mints_one_node_when_model_returns_nothing(store):
    rows = [FakeRow("util.py", "util.py::a"), FakeRow("util.py", "util.py::b")]
    out = _ensure_file_coverage([], rows, "util.py")
    assert len(out) == 1
    assert out[0].kind is NodeOpKind.ADD_NODE
    covered = set(out[0].bindings)
    assert covered == {("util.py", "util.py::a"), ("util.py", "util.py::b")}


# ---------------------------------------------------------------------------
# Two-phase orchestration
# ---------------------------------------------------------------------------

def test_one_file_call_per_file_with_scoped_chunks(store):
    """propose_file is called once per file, seeing only that file's chunks."""
    rows = [
        FakeRow("a.py", "a.py::af"),
        FakeRow("b.py", "b.py::bf"),
        FakeRow("b.py", "b.py::bg"),
    ]
    build_graph(store, rows)

    seen: list[tuple[str, set]] = []

    def propose_file(file, chunks, edges, existing_titles, *, repo_name, config, why=None, **_kw):
        seen.append((file, {c["symbol_path"] for c in chunks}))
        return [_add("n1", f"Feature {file}", [(file, c["symbol_path"]) for c in chunks])]

    bootstrap_hier_from_chunks(
        rows, store, propose_file=propose_file, propose_org=lambda *a, **k: [], organize=True
    )

    assert {f for f, _ in seen} == {"a.py", "b.py"}
    by_file = dict(seen)
    assert by_file["a.py"] == {"a.py::af"}              # no cross-file leakage
    assert by_file["b.py"] == {"b.py::bf", "b.py::bg"}


def test_org_pass_groups_top_level_features(store):
    rows = [FakeRow("a.py", "a.py::af"), FakeRow("b.py", "b.py::bf")]
    build_graph(store, rows)

    def propose_file(file, chunks, edges, existing_titles, *, repo_name, config, why=None, **_kw):
        return [_add("n1", f"Feat {file}", [(file, c["symbol_path"]) for c in chunks])]

    def propose_org(features, edges, *, repo_name, config, flows=None, **_kw):
        ops = [_add("t1", "Theme", [])]
        for f in features:
            ops.append(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=f["id"], parent_id="t1"))
        return ops

    bootstrap_hier_from_chunks(rows, store, propose_file=propose_file, propose_org=propose_org)

    top = store.children(None)
    assert len(top) == 1 and top[0].title == "Theme"
    assert {c.title for c in store.children(top[0].id)} == {"Feat a.py", "Feat b.py"}


def test_org_skipped_for_single_top_level_feature(store):
    rows = [FakeRow("a.py", "a.py::af")]
    build_graph(store, rows)
    called = []

    def propose_file(file, chunks, edges, existing_titles, *, repo_name, config, why=None, **_kw):
        return [_add("n1", "Only", [(file, c["symbol_path"]) for c in chunks])]

    def propose_org(features, edges, *, repo_name, config, flows=None, **_kw):
        called.append(True)
        return []

    res = bootstrap_hier_from_chunks(rows, store, propose_file=propose_file, propose_org=propose_org)
    assert not called, "org pass should not run with a single top-level feature"
    assert res.batches == 1


def test_feature_coupling_lines(store):
    """Cross-feature call edges aggregate into coupling lines for the org pass."""
    rows = [
        FakeRow("a.py", "a.py::caller", "def caller():\n    callee()\n"),
        FakeRow("b.py", "b.py::callee", "def callee():\n    return 1\n"),
    ]
    build_graph(store, rows)
    _apply_ops_with_local_ids(
        [_add("n1", "A", [("a.py", "a.py::caller")])], store, {}, source="bootstrap"
    )
    _apply_ops_with_local_ids(
        [_add("n2", "B", [("b.py", "b.py::callee")])], store, {}, source="bootstrap"
    )

    lines = _feature_coupling(store)
    assert any("(A)" in ln and "(B)" in ln for ln in lines), lines


def test_empty_repo(store):
    res = bootstrap_hier_from_chunks(
        [], store, propose_file=lambda *a, **k: [], propose_org=lambda *a, **k: []
    )
    assert res.chunks == 0 and res.features == 0 and res.batches == 0


def test_every_chunk_bound_after_bootstrap(store):
    """No chunk is left unattributed across the whole two-phase run."""
    rows = [
        FakeRow("a.py", "a.py::Big"),
        FakeRow("a.py", "a.py::Big.m"),
        FakeRow("b.py", "b.py::fn"),
    ]
    build_graph(store, rows)

    def propose_file(file, chunks, edges, existing_titles, *, repo_name, config, why=None, **_kw):
        # Cover only the first chunk; coverage net must catch the rest.
        first = chunks[0]
        return [_add("n1", f"F {file}", [(file, first["symbol_path"])])]

    bootstrap_hier_from_chunks(
        rows, store, propose_file=propose_file, propose_org=lambda *a, **k: []
    )

    bound = {(b.file, b.symbol_path) for b in store.all_bindings()}
    assert bound == {(r.file, r.symbol_path) for r in rows}


def _rows(pairs):
    return [FakeRow(file=f, symbol_path=sym) for f, sym in pairs]


class TestPerFileTolerance:
    """One bad sample must not cost the whole bootstrap.

    Nineteen files' worth of calls were paid for and thrown away because file
    seven's description contained a quotation mark. The rest of the tree was
    fine and the user never saw it.
    """

    def test_one_failing_file_does_not_sink_the_others(self, store):
        rows = _rows([("a.py", "a.py::one"), ("b.py", "b.py::two"),
                      ("c.py", "c.py::three")])

        def propose_file(file, chunks, edges, existing_titles, *, repo_name, config,
                         why=None, **_kw):
            if file == "b.py":
                raise ValueError("Expecting ',' delimiter: line 3 column 3")
            return [_add("n1", f"Feature for {file}",
                         [(file, c["symbol_path"]) for c in chunks])]

        res = bootstrap_hier_from_chunks(rows, store, propose_file=propose_file,
                                         propose_org=lambda *a, **k: [], organize=False)
        titles = {f.title for f in store.list_features()}
        assert "Feature for a.py" in titles
        assert "Feature for c.py" in titles
        assert res.skipped == ["b.py"]

    def test_the_failed_file_is_still_represented(self, store):
        """`_ensure_file_coverage` gives it a node named after the file with no
        description — a visible, fillable gap rather than a silent hole."""
        rows = _rows([("a.py", "a.py::one"), ("b.py", "b.py::two")])

        def propose_file(file, chunks, edges, existing_titles, *, repo_name, config,
                         why=None, **_kw):
            if file == "b.py":
                raise ValueError("bad json")
            return [_add("n1", "A feature", [("a.py", "a.py::one")])]

        bootstrap_hier_from_chunks(rows, store, propose_file=propose_file,
                                   propose_org=lambda *a, **k: [], organize=False)
        assert store.binding_at("b.py", "b.py::two") is not None

    def test_every_file_failing_is_a_broken_setup_and_raises(self, store):
        """No key, no CLI, unreachable endpoint — handing back a tree of empty
        filename nodes and calling it success would be worse than failing."""
        rows = _rows([("a.py", "a.py::one"), ("b.py", "b.py::two")])

        def propose_file(file, chunks, edges, existing_titles, *, repo_name, config,
                         why=None, **_kw):
            raise RuntimeError("no LLM configured")

        with pytest.raises(RuntimeError, match="every bootstrap call failed"):
            bootstrap_hier_from_chunks(rows, store, propose_file=propose_file,
                                       propose_org=lambda *a, **k: [], organize=False)


def test_a_file_is_shown_in_its_own_order_not_alphabetically(store):
    """The prompt reads the chunk list in order, and a split file is cut along it.

    By name, a module's constants land between its classes and `__enter__` comes
    before `__init__` — so a pass sees a scatter of the file rather than a region
    of it.
    """
    rows = [
        FakeRow("m.py", "m.py::Store.write", start_byte=300),
        FakeRow("m.py", "m.py::Store.__init__", start_byte=200),
        FakeRow("m.py", "m.py::Store", start_byte=100),
        FakeRow("m.py", "m.py::open_store", start_byte=400),
    ]
    build_graph(store, rows)
    seen: list[list[str]] = []

    def propose_file(file, chunks, edges, existing_titles, *, repo_name, config,
                     why=None, **_kw):
        seen.append([c["symbol_path"] for c in chunks])
        return [_add("n1", "A feature", [(file, c["symbol_path"]) for c in chunks])]

    bootstrap_hier_from_chunks(rows, store, propose_file=propose_file,
                               propose_org=lambda *a, **k: [], organize=False)

    assert seen == [["m.py::Store", "m.py::Store.__init__",
                     "m.py::Store.write", "m.py::open_store"]]


def test_chunks_with_no_recorded_position_keep_a_stable_order(store):
    """An unindexed row carries 0 for every chunk, so the name breaks the tie."""
    rows = _rows([("m.py", "m.py::beta"), ("m.py", "m.py::alpha")])
    build_graph(store, rows)
    seen: list[list[str]] = []

    def propose_file(file, chunks, edges, existing_titles, *, repo_name, config,
                     why=None, **_kw):
        seen.append([c["symbol_path"] for c in chunks])
        return [_add("n1", "A feature", [(file, c["symbol_path"]) for c in chunks])]

    bootstrap_hier_from_chunks(rows, store, propose_file=propose_file,
                               propose_org=lambda *a, **k: [], organize=False)

    assert seen == [["m.py::alpha", "m.py::beta"]]


class TestACrowdedFileIsDescribedOverSeveralCalls:
    """One call cannot name a feature set it is only shown a fraction of.

    A file whose definitions do not fit one prompt at full allowance used to be
    spent down until they did — 60 characters of each method, and a call asked to
    name coherent features over evidence it could barely read. It is now split
    across several calls instead (`loop/payload.py`), and what has to hold is
    that the file still comes out described ONCE: no symbol named twice, no
    symbol lost, and each call able to see what the ones before it already named.
    """

    @staticmethod
    def _crowded(file: str, count: int) -> list[FakeRow]:
        """One file's worth of real definitions, too many for a single pass.

        The way to ask for a split is to hand over a file that does not fit —
        the criterion is the budget itself, so there is no knob here and no
        constant copied out of `payload.py` to drift from it.
        """
        rows = []
        for i in range(count):
            body = "\n".join(f"    step_{j}(value)" for j in range(60))
            nxt = f"func_{(i + 1) % count:03d}"
            src = f"def func_{i:03d}(value):\n    {nxt}(value)\n{body}\n"
            rows.append(FakeRow(file=file, symbol_path=f"{file}::func_{i:03d}",
                                source=src))
        return rows

    def test_it_becomes_several_calls_that_between_them_see_every_symbol(self, store):
        rows = self._crowded("wide.py", 200)
        build_graph(store, rows)
        seen: list[list[str]] = []

        def propose_file(file, chunks, edges, existing_titles, *, repo_name, config,
                         why=None, **_kw):
            seen.append([c["symbol_path"] for c in chunks])
            return [_add(f"n{len(seen)}", f"Part {len(seen)}",
                         [(file, c["symbol_path"]) for c in chunks])]

        bootstrap_hier_from_chunks(rows, store, propose_file=propose_file,
                                   propose_org=lambda *a, **k: [], organize=False)

        assert len(seen) > 1                                  # it did split
        flat = [sym for call in seen for sym in call]
        assert sorted(flat) == sorted(r.symbol_path for r in rows)
        assert len(flat) == len(set(flat))                    # nothing described twice

    def test_each_call_is_told_what_the_ones_before_it_named(self, store):
        """The passes are slices of one namespace, so a blind one duplicates.

        Avoiding that duplicate is exactly what a single whole-file call bought,
        which is why the groups run in sequence rather than concurrently.
        """
        rows = self._crowded("wide.py", 200)
        build_graph(store, rows)
        titles_seen: list[list[str]] = []

        def propose_file(file, chunks, edges, existing_titles, *, repo_name, config,
                         why=None, **_kw):
            titles_seen.append(list(existing_titles))
            n = len(titles_seen)
            return [_add(f"n{n}", f"Part {n}",
                         [(file, c["symbol_path"]) for c in chunks])]

        bootstrap_hier_from_chunks(rows, store, propose_file=propose_file,
                                   propose_org=lambda *a, **k: [], organize=False)

        assert len(titles_seen) > 1
        assert "Part 1" not in titles_seen[0]
        for i, titles in enumerate(titles_seen[1:], start=1):
            assert f"Part {i}" in titles

    def test_a_call_is_shown_the_edges_about_its_own_symbols(self, store):
        """An edge about a symbol this call is not naming is unusable context —
        and on a split file there are hundreds of them."""
        rows = self._crowded("wide.py", 200)
        build_graph(store, rows)
        offered: list[tuple[set[str], set[str]]] = []

        def propose_file(file, chunks, edges, existing_titles, *, repo_name, config,
                         why=None, **_kw):
            offered.append(({c["symbol_path"] for c in chunks},
                            {e["symbol"] for e in edges}))
            return [_add(f"n{len(offered)}", f"Part {len(offered)}",
                         [(file, c["symbol_path"]) for c in chunks])]

        bootstrap_hier_from_chunks(rows, store, propose_file=propose_file,
                                   propose_org=lambda *a, **k: [], organize=False)

        assert len(offered) > 1
        assert any(edges for _syms, edges in offered)   # there ARE edges to scope
        for syms, edges in offered:
            assert edges <= syms

    def test_one_failing_part_costs_that_slice_and_not_the_file(self, store):
        rows = self._crowded("wide.py", 200)
        build_graph(store, rows)
        attempts: list[str] = []

        def propose_file(file, chunks, edges, existing_titles, *, repo_name, config,
                         why=None, **_kw):
            attempts.append(chunks[0]["symbol_path"])
            if len(attempts) <= 2:                # both tries at the first part
                raise ValueError("bad json")
            return [_add(f"n{len(attempts)}", f"Part {len(attempts)}",
                         [(file, c["symbol_path"]) for c in chunks])]

        res = bootstrap_hier_from_chunks(rows, store, propose_file=propose_file,
                                         propose_org=lambda *a, **k: [], organize=False)

        titles = {f.title for f in store.list_features()}
        assert {t for t in titles if t.startswith("Part ")}   # the rest still ran
        # The failed slice falls to the coverage net like anything else left out,
        # so the file is still wholly bound…
        bound = {(b.file, b.symbol_path) for b in store.all_bindings()}
        assert bound == {(r.file, r.symbol_path) for r in rows}
        # …and it is reported once, as the one file it is.
        assert res.skipped == ["wide.py"]

# ---------------------------------------------------------------------------
# Phase 1b — the decisions a settings file holds
# ---------------------------------------------------------------------------

_READS_IT = 'def summarise():\n    load("rules.toml")\n'
_PERIODS = '# Which date a summary lines up on.\n[periods]\nmonth = "made"\n'


def _settings_rows():
    """A repo whose summary code reads `tally/rules.toml`, which is indexed."""
    return [
        FakeRow("tally/summary.py", "tally/summary.py::summarise", _READS_IT),
        FakeRow("tally/other.py", "tally/other.py::unrelated", "def unrelated():\n    pass\n"),
        FakeRow("tally/rules.toml", "tally/rules.toml::periods", _PERIODS,
                language="toml"),
    ]


def _code_only(file, chunks, edges, existing_titles, *, repo_name, config, why=None, **_kw):
    return [_add("n1", f"Feat {file}", [(file, c["symbol_path"]) for c in chunks])]


def test_a_settings_file_is_described_after_every_code_file(store):
    """Phase 1b attaches a section to the feature that reads it, and names the value.

    The whole point of indexing the file: the reader of the summary feature asked
    which date a month is lined up on, and before this pass the answer was in a file
    the tree could not see.
    """
    rows = _settings_rows()
    build_graph(store, rows)
    seen: list[tuple[str, list[dict], list[dict]]] = []

    def propose_settings(file, sections, readers, **_kw):
        seen.append((file, sections, readers))
        fid = readers[0]["feature_id"]
        return [
            NodeOp(kind=NodeOpKind.ATTACH, feature_id=fid,
                   bindings=[(file, "tally/rules.toml::periods")]),
            NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                   description="Lines a summary up on the date the payment was made."),
        ]

    bootstrap_hier_from_chunks(rows, store, propose_file=_code_only,
                               propose_org=lambda *a, **k: [],
                               propose_settings=propose_settings)

    assert len(seen) == 1
    file, sections, readers = seen[0]
    assert file == "tally/rules.toml"
    # The values and the comment above them, verbatim — a section shown as a
    # `symbol_path` alone would leave the pass exactly where the old prose was.
    assert sections == [{"symbol_path": "tally/rules.toml::periods", "source": _PERIODS}]
    # Only the feature whose code names the file, and it arrives with the prose an
    # amend has to keep.
    assert [r["title"] for r in readers] == ["Feat tally/summary.py"]
    assert readers[0]["reads_it_in"] == ["tally/summary.py::summarise"]
    assert readers[0]["description"]

    owner = store.binding_at("tally/rules.toml", "tally/rules.toml::periods")
    assert owner is not None
    summary = next(f for f in store.list_features() if f.title == "Feat tally/summary.py")
    assert owner.feature_id == summary.id
    assert "payment was made" in summary.description


def test_a_settings_file_is_not_offered_to_the_per_file_pass(store):
    """It would be asked what the file is FOR, and answer with a Configuration node."""
    rows = _settings_rows()
    build_graph(store, rows)
    asked: list[str] = []

    def propose_file(file, chunks, edges, existing_titles, *, repo_name, config, why=None, **_kw):
        asked.append(file)
        return _code_only(file, chunks, edges, existing_titles,
                          repo_name=repo_name, config=config, why=why)

    bootstrap_hier_from_chunks(rows, store, propose_file=propose_file,
                              propose_org=lambda *a, **k: [],
                              propose_settings=lambda *a, **k: [])

    assert "tally/rules.toml" not in asked


def test_a_section_no_feature_reads_still_lands_in_the_tree(store):
    """A pass that returns nothing leaves a node named after the file — a visible,
    fillable gap rather than a section bound to nothing."""
    rows = _settings_rows()
    build_graph(store, rows)

    bootstrap_hier_from_chunks(rows, store, propose_file=_code_only,
                               propose_org=lambda *a, **k: [],
                               propose_settings=lambda *a, **k: [])

    owner = store.binding_at("tally/rules.toml", "tally/rules.toml::periods")
    assert owner is not None


def test_a_settings_pass_that_fails_costs_the_values_and_nothing_else(store):
    """One raised call must not lose the code files' tree — the same tolerance the
    per-file pass has, for the same reason: the user paid for the rest."""
    rows = _settings_rows()
    build_graph(store, rows)

    def boom(*_a, **_k):
        raise RuntimeError("no key")

    res = bootstrap_hier_from_chunks(rows, store, propose_file=_code_only,
                                     propose_org=lambda *a, **k: [],
                                     propose_settings=boom)

    assert {f.title for f in store.list_features()} >= {
        "Feat tally/summary.py", "Feat tally/other.py"}
    assert res.features >= 2
    assert store.binding_at("tally/rules.toml", "tally/rules.toml::periods") is not None


def test_only_features_that_name_the_file_are_offered(store):
    """Evidence, not proximity: a module in the same package that never mentions
    `rules.toml` is not a candidate, so the pass cannot amend prose about it."""
    rows = _settings_rows()
    build_graph(store, rows)
    _apply_ops_with_local_ids(
        [_add("n1", "Summaries", [("tally/summary.py", "tally/summary.py::summarise")]),
         _add("n2", "Other", [("tally/other.py", "tally/other.py::unrelated")])],
        store, {}, source="bootstrap")

    readers = _settings_readers("tally/rules.toml", rows, store)

    assert [r["title"] for r in readers] == ["Summaries"]


def test_an_amend_citing_a_feature_the_pass_never_saw_is_dropped():
    """A hallucinated id would rewrite the description of an unrelated feature, and
    nothing in the answer distinguishes it from a real one."""
    ops = [
        NodeOp(kind=NodeOpKind.AMEND, feature_id="f-real", description="kept"),
        NodeOp(kind=NodeOpKind.AMEND, feature_id="f-invented", description="dropped"),
        NodeOp(kind=NodeOpKind.ADD_NODE, feature_id="n1", title="Minted",
               description="A section nothing reads."),
    ]

    kept = _only_offered_features(ops, {"f-real"})

    assert [o.feature_id for o in kept] == ["f-real", "n1"]
