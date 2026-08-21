"""A rename moves the binding AND the prose that cites it.

Relocation was already deterministic on the binding side. The description was not: a
sentence saying ``[`loads`](codoc:m.py#loads)`` went on saying it after the function
became ``load_json``, so the tree held a link that resolved to nothing and named a
symbol the codebase no longer had. The dead-ref registry reported it; nothing fixed it.

Two halves, tested apart: the text surgery (`repoint_refs`, pure) and the Loop A pass
that decides what to repoint and writes it without claiming the paragraph.
"""
from __future__ import annotations

import pytest

from codoc.codoc_file.parse import extract_refs, repoint_refs
from codoc.loop import prose
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.loop_a import apply_changeset
from codoc.model.binding import Binding
from codoc.model.event import ACTOR_HUMAN, ACTOR_LOOP, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store

# `m.py::loads` became `m.py::load_json`, and nothing else in the file survived to
# resolve `#loads` against.
RENAMED = {("m.py", "m.py::loads"): ("m.py", "m.py::load_json")}
AFTER = {"m.py": ["m.py::load_json"]}


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _raising(*a, **k):
    raise AssertionError("a repoint needs no LLM: the new address is known")


def _feature(store, *, writer: str = "", role: str = "", **kw) -> Feature:
    f = Feature(**kw)
    store.upsert_feature(f)
    if writer:
        store.set_feature_writer(f.id, writer, role)
    return f


def _bind(store, fid, file, symbol, fp="HASH", shape="SHAPE"):
    store.upsert_binding(Binding(feature_id=fid, file=file, symbol_path=symbol,
                                 fingerprint=fp, types_hash=shape))


def _renamed_loads() -> ChangeSet:
    """`m.py::loads` renamed to `m.py::load_json` — same shape, same file."""
    return ChangeSet(
        removed=[ChunkRef("m.py", "m.py::loads", "H_OLD", "", "SHAPE_X")],
        added=[ChunkRef("m.py", "m.py::load_json", "H_NEW",
                        "def load_json(s): ...", "SHAPE_X")],
    )


def _amends(store, fid):
    return [e for e in store.events_for_feature(fid)
            if e.op.kind is NodeOpKind.AMEND]


# ── the surgery ─────────────────────────────────────────────────────────────

def test_a_citation_of_moved_code_points_at_where_it_moved() -> None:
    text = "Parsed by [`loads`](codoc:m.py#loads) before anything else."
    out, changed = repoint_refs(text, RENAMED, AFTER)
    assert extract_refs(out)[0].symbol == "load_json"
    assert len(changed) == 1


def test_a_label_that_is_the_symbol_name_is_rewritten_with_it() -> None:
    # The label is the address rendered as text, not a sentence. Leaving it says
    # `loads` about something now called `load_json`, which is the same wrongness
    # this pass exists to remove — one line further left.
    for label in ("loads", "`loads`", "**loads**", "m.py::loads"):
        out, _ = repoint_refs(f"Parsed by [{label}](codoc:m.py#loads).", RENAMED, AFTER)
        assert "loads]" not in out and "loads`]" not in out and "loads**]" not in out
        assert "load_json" in out


def test_a_citation_keeps_the_depth_the_author_wrote_it_at() -> None:
    # A sentence names code at whatever depth reads well in it. Answering `#send` with
    # `Session.dispatch` would rewrite that choice along with the address.
    moves = {("s.py", "s.py::Session.send"): ("s.py", "s.py::Session.dispatch")}
    live = {"s.py": ["s.py::Session.dispatch"]}
    out, changed = repoint_refs(
        "[send](codoc:s.py#send) and [Session.send](codoc:s.py#Session.send).",
        moves, live)
    assert out == "[dispatch](codoc:s.py#dispatch) and " \
                  "[Session.dispatch](codoc:s.py#Session.dispatch)."
    assert len(changed) == 2


def test_a_label_that_is_prose_is_left_exactly_as_written() -> None:
    # "the JSON reader" is still true whatever the function is called, and rewriting
    # it would be editing the author's sentence rather than repairing an address.
    out, _ = repoint_refs("Parsed by [the JSON reader](codoc:m.py#loads) first.",
                          RENAMED, AFTER)
    assert "[the JSON reader](codoc:m.py#load_json)" in out


def test_a_citation_of_something_that_did_not_move_is_untouched() -> None:
    text = "Kept by [`dumps`](codoc:m.py#dumps)."
    assert repoint_refs(text, RENAMED, {"m.py": ["m.py::dumps"]}) == (text, [])


def test_a_citation_that_still_resolves_is_never_re_aimed() -> None:
    """The load-bearing guard on the surgery side.

    A ref carries a leaf, so `#loads` matches `m.py::Codec.loads` as well as
    `m.py::loads`. Renaming the METHOD must not re-aim a sentence that was pointing at
    the function and was never stale — that would be this pass corrupting prose it was
    written to repair.
    """
    text = "Parsed by [`loads`](codoc:m.py#loads)."
    moves = {("m.py", "m.py::Codec.loads"): ("m.py", "m.py::Codec.load_json")}
    live = {"m.py": ["m.py::loads", "m.py::Codec.load_json"]}
    assert repoint_refs(text, moves, live) == (text, [])


def test_an_ambiguous_match_is_left_as_a_dead_link() -> None:
    # Two relocations answer `#loads` and the author's shorthand no longer tells them
    # apart. A dead link the registry reports beats a live link to the wrong code.
    text = "Parsed by [`loads`](codoc:m.py#loads)."
    moves = {("m.py", "m.py::A.loads"): ("m.py", "m.py::A.load_json"),
             ("m.py", "m.py::B.loads"): ("m.py", "m.py::B.read_json")}
    live = {"m.py": ["m.py::A.load_json", "m.py::B.read_json"]}
    assert repoint_refs(text, moves, live) == (text, [])


def test_a_renamed_file_repoints_the_symbol_less_form() -> None:
    # A file rename arrives as its chunks relocating with their qualified names
    # unchanged — the only signal `[label](codoc:file.py)` can be repointed from.
    moves = {("m.py", "m.py::__module__"): ("reader.py", "reader.py::__module__"),
             ("m.py", "m.py::loads"): ("reader.py", "reader.py::loads")}
    out, changed = repoint_refs("See [m.py](codoc:m.py) and [the reader](codoc:m.py).",
                                moves, {"reader.py": ["reader.py::loads"]})
    assert out == "See [reader.py](codoc:reader.py) and [the reader](codoc:reader.py)."
    assert len(changed) == 2


def test_the_prose_around_a_citation_is_not_reflowed() -> None:
    # Surgery at the regex's own offsets, not a parse-and-render round trip: the
    # description is somebody's markdown, and this is entitled to change addresses
    # and nothing else.
    text = ("Line one.\n\n-   a  loose   list item citing [`loads`](codoc:m.py#loads)\n"
            "-   another\t(tabbed)\n")
    out, _ = repoint_refs(text, RENAMED, AFTER)
    assert out == text.replace("loads", "load_json")


# ── the Loop A pass ─────────────────────────────────────────────────────────

def test_loop_a_repoints_the_owner_of_the_renamed_code(store):
    f = _feature(store, title="JSON reading",
                 description="Parsed by [`loads`](codoc:m.py#loads) on the way in.")
    _bind(store, f.id, "m.py", "m.py::loads", fp="H_OLD", shape="SHAPE_X")

    res = apply_changeset(_renamed_loads(), store, propose=_raising)

    assert res.auto["relocate"] == 1 and res.auto["repoint"] == 1
    assert store.binding_at("m.py", "m.py::load_json").feature_id == f.id
    assert (store.get_feature(f.id).description
            == "Parsed by [`load_json`](codoc:m.py#load_json) on the way in.")


def test_the_registry_stops_reporting_the_link_it_repaired(store):
    """Repair and report have to agree, and they agree by sharing the predicate.

    The cross-reference registry is what tells the IDE and an agent that a link is
    dead; this pass decides what to repoint. They read the same `resolve_ref`, so a
    rename that used to leave one dead ref behind now leaves none — and a future
    divergence between the two callers fails here rather than in somebody's margin.
    """
    from codoc.codoc_file.render import _compute_registry

    f = _feature(store, title="JSON reading",
                 description="Parsed by [`loads`](codoc:m.py#loads).")
    _bind(store, f.id, "m.py", "m.py::loads", fp="H_OLD", shape="SHAPE_X")

    apply_changeset(_renamed_loads(), store, propose=_raising)

    assert [r for r in _compute_registry(store)["refs"] if not r["resolved"]] == []


def test_loop_a_repoints_a_feature_that_only_CITES_the_renamed_code(store):
    # A cross-reference is the whole reason a citation is an address rather than a
    # name, and a description citing code it does not bind is the case most likely to
    # rot unnoticed — nothing about that feature changed, so nothing would look at it.
    owner = _feature(store, title="JSON reading", description="No citations here.")
    _bind(store, owner.id, "m.py", "m.py::loads", fp="H_OLD", shape="SHAPE_X")
    other = _feature(store, title="Request bodies",
                     description="Decoded with [`loads`](codoc:m.py#loads).")

    apply_changeset(_renamed_loads(), store, propose=_raising)

    assert "load_json" in store.get_feature(other.id).description


def test_loop_a_leaves_a_citation_of_deleted_code_alone(store):
    # Nothing here knows where a deleted symbol went, and inventing an address is
    # worse than a dead link the registry already reports.
    f = _feature(store, title="Reading",
                 description="Parsed by [`loads`](codoc:m.py#loads).")
    _bind(store, f.id, "m.py", "m.py::loads", fp="H_OLD", shape="SHAPE_X")
    cs = ChangeSet(removed=[ChunkRef("m.py", "m.py::loads", "H_OLD", "", "SHAPE_X")])

    apply_changeset(cs, store, propose=lambda *a, **k: [])

    assert "codoc:m.py#loads" in store.get_feature(f.id).description


def test_a_repoint_does_not_take_the_paragraph_over(store):
    """The load-bearing one: an address repair must not make the loop the author.

    `feature_writers` decides how strict the amend gate is over the NEXT rewrite of
    this prose. If repointing stamped the loop there, a mechanical link fix would
    quietly relax the protection on somebody's paragraph — the same laundering
    `restates_current` refuses on the other side.
    """
    f = _feature(store, title="JSON reading", writer="human", role=ACTOR_HUMAN,
                 description="Parsed by [`loads`](codoc:m.py#loads) on the way in.")
    _bind(store, f.id, "m.py", "m.py::loads", fp="H_OLD", shape="SHAPE_X")

    apply_changeset(_renamed_loads(), store, propose=_raising)

    assert "load_json" in store.get_feature(f.id).description   # it did repoint
    assert store.feature_writer_info(f.id) == ("human", ACTOR_HUMAN)


def test_a_repoint_is_not_counted_in_the_prose_scorecard(store):
    # The words were already scored when they were written. Counting them again would
    # inflate the denominator with unchanged text, and against whoever repointed it
    # rather than whoever wrote it.
    f = _feature(store, title="JSON reading",
                 description="Parsed by [`loads`](codoc:m.py#loads) on the way in.")
    _bind(store, f.id, "m.py", "m.py::loads", fp="H_OLD", shape="SHAPE_X")
    before = prose.defect_rate(store)["checked"]

    apply_changeset(_renamed_loads(), store, propose=_raising)

    assert prose.defect_rate(store)["checked"] == before


def test_a_repoint_records_what_it_displaced(store):
    # The timeline reconstructs backwards from the live document, so an amend that
    # does not record the text it replaced is a permanent hole in the scrubber.
    f = _feature(store, title="JSON reading",
                 description="Parsed by [`loads`](codoc:m.py#loads).")
    _bind(store, f.id, "m.py", "m.py::loads", fp="H_OLD", shape="SHAPE_X")

    apply_changeset(_renamed_loads(), store, propose=_raising)

    amends = _amends(store, f.id)
    assert len(amends) == 1
    assert amends[0].op.prev_description == "Parsed by [`loads`](codoc:m.py#loads)."
    assert "repointed 1 citation(s)" in (amends[0].op.rationale or "")
    assert amends[0].actor == ACTOR_LOOP


def test_no_rename_means_no_amend_at_all(store):
    # A pass with nothing to repoint must not write. An AMEND per feature per pass
    # would flood the ledger the timeline reads and mark every node as touched.
    f = _feature(store, title="JSON reading",
                 description="Parsed by [`loads`](codoc:m.py#loads).")
    _bind(store, f.id, "m.py", "m.py::loads", fp="H_OLD", shape="SHAPE_X")
    cs = ChangeSet(modified=[ChunkRef("m.py", "m.py::loads", "H_NEW",
                                     "def loads(s): ...", "SHAPE_X")])

    res = apply_changeset(cs, store, propose=_raising)

    assert "repoint" not in res.auto
    assert not _amends(store, f.id)


def test_a_retired_feature_is_not_repointed(store):
    f = _feature(store, title="Old reading", retired=True,
                 description="Parsed by [`loads`](codoc:m.py#loads).")
    live = _feature(store, title="JSON reading", description="No citations.")
    _bind(store, live.id, "m.py", "m.py::loads", fp="H_OLD", shape="SHAPE_X")

    apply_changeset(_renamed_loads(), store, propose=_raising)

    assert "codoc:m.py#loads" in store.get_feature(f.id).description
