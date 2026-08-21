"""Warrant — the citation from a stated why back to the evidence for it.

The point under test is not that a warrant appears; it is that a warrant can only ever
be a QUOTATION of something codoc actually had. The two rules that make it worth
believing are that an id the model invented resolves to nothing, and that the quote
comes from the index rather than from the reply.
"""
from __future__ import annotations

import pytest

from codoc.agent.tree_update import _coerce_op
from codoc.loop.warrant import MAX_WARRANTS, as_rows, index_evidence, resolve
from codoc.loop.why import stamp_ids

_BLOCK = {
    "commits": [
        {"files": ["a.py"], "subject": "Retry only on timeout",
         "why": "The server can duplicate a non-idempotent post.", "sha": "1a2b3c4d"},
        {"files": ["b.py"], "subject": "Move the write behind a rename"},
    ],
    "directives": [
        {"feature_id": "f-1", "asked": "Author asked: make the queue crash-safe"},
    ],
    "prior": [
        {"feature_id": "f-1", "recorded": ["the old sentence said one attempt", "older"]},
    ],
}


def _changes(**over) -> dict:
    import copy

    block = stamp_ids(copy.deepcopy(_BLOCK))
    out = {"why_evidence": block}
    out.update(over)
    return out


class TestIds:
    def test_stamp_ids_numbers_each_source_separately(self):
        import copy

        block = stamp_ids(copy.deepcopy(_BLOCK))
        assert [e["id"] for e in block["commits"]] == ["c1", "c2"]
        assert [e["id"] for e in block["directives"]] == ["d1"]
        assert [e["id"] for e in block["prior"]] == ["p1"]

    def test_stamp_ids_tolerates_an_absent_source(self):
        assert stamp_ids({}) == {}

    def test_gather_stamps_ids_and_shas_on_the_real_block(self, tmp_path):
        """The ids have to be on the block the PROMPT is built from, not bolted on
        afterwards — an id the model can see must be one the resolver can find."""
        from codoc.loop.why import clear_cache, gather_why_evidence
        from tests.loop.test_why import _commit, _git  # noqa: PLC0415 — one git helper

        clear_cache()
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "t@example.com")
        _git(tmp_path, "config", "user.name", "Tester")
        _commit(tmp_path, "client.py", "x = 1\n",
                "Retry only on timeout\n\nThe server can duplicate a post.")
        try:
            block = gather_why_evidence(root_dir=tmp_path, files={"client.py"})
        finally:
            clear_cache()
        assert block["commits"][0]["id"] == "c1"
        assert len(block["commits"][0]["sha"]) == 8
        assert index_evidence({"why_evidence": block})["c1"].ref \
            == block["commits"][0]["sha"]


class TestIndex:
    def test_every_source_is_citable(self):
        index = index_evidence(_changes())
        assert set(index) == {"c1", "c2", "d1", "p1"}
        assert index["c1"].kind == "commit"
        assert index["d1"].kind == "directive"
        assert index["p1"].kind == "prior"

    def test_a_commit_warrant_carries_the_sha_as_its_reference(self):
        assert index_evidence(_changes())["c1"].ref == "1a2b3c4d"

    def test_a_commit_quote_joins_the_subject_and_the_reason(self):
        quote = index_evidence(_changes())["c1"].quote
        assert quote.startswith("Retry only on timeout — ")
        assert "duplicate a non-idempotent post" in quote

    def test_a_commit_with_no_body_quotes_its_subject_alone(self):
        assert index_evidence(_changes())["c2"].quote == "Move the write behind a rename"

    def test_prior_quotes_the_newest_note(self):
        assert index_evidence(_changes())["p1"].quote == "the old sentence said one attempt"

    def test_author_intent_is_citable_and_ranked_as_intent(self):
        index = index_evidence(_changes(
            author_intent=[{"id": "a1", "asked": "add a retry guard to fan-out"}]))
        assert index["a1"].kind == "intent"
        assert index["a1"].quote == "add a retry guard to fan-out"

    def test_bare_string_intent_is_uncitable_but_does_not_raise(self):
        """The pre-warrant shape. Loop B still consumes plain strings, so an older
        caller must lose the ability to cite, not the ability to run."""
        index = index_evidence(_changes(author_intent=["add a retry guard"]))
        assert not [k for k in index if k.startswith("a")]

    def test_an_entry_with_nothing_to_quote_is_not_offered(self):
        index = index_evidence({"why_evidence": stamp_ids(
            {"directives": [{"feature_id": "f-1", "asked": ""}]})})
        assert index == {}

    def test_no_evidence_indexes_to_nothing(self):
        assert index_evidence({}) == {}
        assert index_evidence(None) == {}

    def test_a_long_quote_is_capped(self):
        index = index_evidence({"why_evidence": stamp_ids(
            {"commits": [{"subject": "x" * 900, "sha": "aaaaaaaa"}]})})
        assert len(index["c1"].quote) < 300
        assert index["c1"].quote.endswith("…")


class TestResolve:
    def test_cited_ids_resolve_to_what_the_evidence_said(self):
        index = index_evidence(_changes())
        (w,) = resolve(index, ["c1"])
        assert w.ref == "1a2b3c4d"
        assert "duplicate a non-idempotent post" in w.quote

    def test_an_invented_id_is_dropped_rather_than_recorded(self):
        """The whole safety property: a fabricated citation leaves the op
        UNWARRANTED, never warranted by a source that does not exist."""
        assert resolve(index_evidence(_changes()), ["c9", "zz", "commit"]) == []

    def test_a_real_and_an_invented_citation_keeps_only_the_real_one(self):
        got = resolve(index_evidence(_changes()), ["c1", "c7"])
        assert [w.kind for w in got] == ["commit"]

    def test_ranked_by_directness_not_by_citation_order(self):
        index = index_evidence(_changes(
            author_intent=[{"id": "a1", "asked": "make the queue crash-safe"}]))
        got = resolve(index, ["p1", "c1", "a1", "d1"])
        assert [w.kind for w in got] == ["intent", "directive", "commit", "prior"]

    def test_duplicates_collapse(self):
        assert len(resolve(index_evidence(_changes()), ["c1", "c1", " C1 "])) == 1

    @pytest.mark.parametrize("cited", ["c1,d1", "c1, d1", "c1;d1", ["`c1`", '"d1"']])
    def test_liberal_about_the_wrapper_the_citation_arrives_in(self, cited):
        got = resolve(index_evidence(_changes()), cited)
        assert {w.kind for w in got} == {"commit", "directive"}

    def test_an_object_citation_is_read_by_its_id(self):
        (w,) = resolve(index_evidence(_changes()), [{"id": "d1"}])
        assert w.kind == "directive"

    def test_capped(self):
        index = index_evidence(_changes(
            author_intent=[{"id": "a1", "asked": "one"}, {"id": "a2", "asked": "two"}]))
        got = resolve(index, ["c1", "c2", "d1", "p1", "a1", "a2"])
        assert len(got) == MAX_WARRANTS

    def test_nothing_cited_and_nothing_indexed_are_both_empty(self):
        assert resolve({}, ["c1"]) == []
        assert resolve(index_evidence(_changes()), []) == []
        assert resolve(index_evidence(_changes()), None) == []


class TestOpCoercion:
    def test_an_amend_records_the_warrant_it_cited(self):
        op = _coerce_op(
            {"kind": "amend", "feature_id": "f-1",
             "description": "Retries only on a timeout, because the server can duplicate a post.",
             "warrant": ["c1"]},
            index_evidence(_changes()),
        )
        assert [w.kind for w in op.warrant] == ["commit"]
        assert op.warrant[0].ref == "1a2b3c4d"

    def test_an_op_that_writes_no_prose_keeps_no_warrant(self):
        """A citation on an attach has no claim to support."""
        op = _coerce_op(
            {"kind": "attach", "feature_id": "f-1",
             "bindings": [["a.py", "a.py::f"]], "warrant": ["c1"]},
            index_evidence(_changes()),
        )
        assert op.warrant == []

    def test_no_citation_is_the_ordinary_case_and_costs_nothing(self):
        op = _coerce_op({"kind": "amend", "feature_id": "f-1",
                         "description": "Computes the total on write."},
                        index_evidence(_changes()))
        assert op.warrant == []

    def test_coercion_still_works_with_no_index_at_all(self):
        op = _coerce_op({"kind": "amend", "feature_id": "f-1", "title": "A title",
                         "warrant": ["c1"]})
        assert op.warrant == []


class TestWire:
    def test_as_rows_omits_a_reference_it_does_not_have(self):
        index = index_evidence(_changes())
        rows = as_rows([index["c1"], index["p1"]])
        assert rows[0]["ref"] == "1a2b3c4d"
        assert rows[1]["kind"] == "prior"
        assert rows[1]["ref"] == "f-1"

    def test_as_rows_drops_a_quoteless_warrant(self):
        from codoc.model.event import Warrant

        assert as_rows([Warrant(kind="commit", ref="abc")]) == []

    def test_as_rows_of_nothing(self):
        assert as_rows(None) == []
        assert as_rows([]) == []
