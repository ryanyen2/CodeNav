"""`codoc_walkthrough` — validating a walkthrough against the store before drawing it.

The point of the validation is that a broken overlay reads to the user as a
broken TOOL, not as a wrong answer: a numbered chip on a feature that no longer
exists, or a highlight that lands nowhere. So a step naming a dead feature is
dropped and reported, and a quote that is not in the prose is reported and its
highlight (only) discarded.
"""
from __future__ import annotations

import pytest

from codoc.loop.ask import read_walkthrough
from codoc.mcp import tools
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    return str(cd)


def _seed(codoc_dir, **kw) -> Feature:
    s = open_store(codoc_dir)
    try:
        f = Feature(**kw)
        s.upsert_feature(f)
        return f
    finally:
        s.close()


def test_walkthrough_writes_the_overlay_and_numbers_the_steps(codoc_dir):
    a = _seed(codoc_dir, title="Strip furniture", description="Removes the running header.")
    b = _seed(codoc_dir, title="Join paragraphs", description="Undoes typeset line breaks.")
    res = tools.walkthrough(
        codoc_dir,
        question="how does a quote survive a page break?",
        answer="The header is gone before quotes are found.",
        steps=[
            {"feature_id": a.id, "group": "before the rules run", "note": "drops the header"},
            {"feature_id": b.id, "group": "the rules", "note": "rejoins the halves"},
        ],
    )
    assert res["ok"] and res["steps"] == 2
    assert res["labels"] == ["1a", "2a"]
    got = read_walkthrough(codoc_dir)
    assert got["question"] == "how does a quote survive a page break?"
    assert [s["feature_id"] for s in got["steps"]] == [a.id, b.id]


def test_a_step_naming_a_dead_feature_is_dropped_and_reported(codoc_dir):
    a = _seed(codoc_dir, title="Live one", description="here")
    res = tools.walkthrough(codoc_dir, question="q", steps=[
        {"feature_id": a.id, "note": "real"},
        {"feature_id": "f-deadbeef", "note": "gone"},
    ])
    assert res["ok"] and res["steps"] == 1
    assert res["dropped"] == [{"feature_id": "f-deadbeef", "why": "no such live feature"}]


def test_every_step_dead_is_an_error_not_an_empty_overlay(codoc_dir):
    _seed(codoc_dir, title="Live one")
    res = tools.walkthrough(codoc_dir, question="q", steps=[{"feature_id": "f-nope"}])
    assert res["ok"] is False
    assert "real f-ids" in res["error"]
    assert read_walkthrough(codoc_dir) is None


def test_a_title_resolves_as_well_as_an_id(codoc_dir):
    a = _seed(codoc_dir, title="Strip furniture", description="x")
    res = tools.walkthrough(codoc_dir, question="q",
                            steps=[{"feature_id": "strip furniture"}])
    assert res["ok"] and res["steps"] == 1
    assert read_walkthrough(codoc_dir)["steps"][0]["feature_id"] == a.id


def test_a_quote_present_in_the_prose_survives(codoc_dir):
    a = _seed(codoc_dir, title="Strip furniture",
              description="Runs first, so the running header is gone by then.")
    res = tools.walkthrough(codoc_dir, question="q", steps=[
        {"feature_id": a.id, "quote": "the running header is gone"},
    ])
    assert res["ok"] and "unresolved_quotes" not in res
    assert read_walkthrough(codoc_dir)["steps"][0]["quote"] == "the running header is gone"


def test_a_quote_matches_across_rewrapped_whitespace(codoc_dir):
    a = _seed(codoc_dir, title="T", description="Runs first,\nso the header\nis gone.")
    res = tools.walkthrough(codoc_dir, question="q", steps=[
        {"feature_id": a.id, "quote": "so the header is gone"},
    ])
    assert "unresolved_quotes" not in res


def test_a_quote_absent_from_the_prose_keeps_the_step_but_loses_the_highlight(codoc_dir):
    a = _seed(codoc_dir, title="Strip furniture", description="Removes the header.")
    res = tools.walkthrough(codoc_dir, question="q", steps=[
        {"feature_id": a.id, "note": "still useful", "quote": "words nobody wrote"},
    ])
    assert res["ok"] and res["steps"] == 1
    assert res["unresolved_quotes"] == [{"feature_id": a.id, "quote": "words nobody wrote"}]
    step = read_walkthrough(codoc_dir)["steps"][0]
    assert step["note"] == "still useful"
    assert "quote" not in step


def test_a_quote_may_come_from_the_title(codoc_dir):
    a = _seed(codoc_dir, title="Strip page furniture", description="Removes it.")
    res = tools.walkthrough(codoc_dir, question="q",
                            steps=[{"feature_id": a.id, "quote": "page furniture"}])
    assert "unresolved_quotes" not in res


def test_a_quote_straddling_a_paragraph_break_is_refused(codoc_dir):
    # The editor draws a highlight inside ONE block, so a quote spanning two
    # paragraphs is one it could not render — better caught here than on screen.
    a = _seed(codoc_dir, title="T", description="First paragraph ends.\n\nSecond begins.")
    res = tools.walkthrough(codoc_dir, question="q", steps=[
        {"feature_id": a.id, "quote": "First paragraph ends. Second begins."},
    ])
    assert res["unresolved_quotes"] == [
        {"feature_id": a.id, "quote": "First paragraph ends. Second begins."}]


def test_a_feature_listed_twice_keeps_only_its_first_stop(codoc_dir):
    a = _seed(codoc_dir, title="A", description="d")
    b = _seed(codoc_dir, title="B", description="d")
    res = tools.walkthrough(codoc_dir, question="q", steps=[
        {"feature_id": a.id, "note": "first visit"},
        {"feature_id": b.id},
        {"feature_id": a.id, "note": "second visit"},
    ])
    assert res["steps"] == 2
    assert res["dropped"] == [{"feature_id": a.id, "why": "already a stop on this path"}]
    steps = read_walkthrough(codoc_dir)["steps"]
    assert [s["feature_id"] for s in steps] == [a.id, b.id]
    assert steps[0]["note"] == "first visit"


def test_no_steps_is_an_error(codoc_dir):
    assert tools.walkthrough(codoc_dir, question="q", steps=[])["ok"] is False


def test_walkthrough_writes_nothing_to_the_store(codoc_dir):
    a = _seed(codoc_dir, title="A", description="d")
    with open_store(codoc_dir) as s:
        before = (len(s.list_features()), len(s.recent_events(50)), len(s.pending_events()))
    tools.walkthrough(codoc_dir, question="q", answer="a",
                      steps=[{"feature_id": a.id, "note": "n"}])
    with open_store(codoc_dir) as s:
        after = (len(s.list_features()), len(s.recent_events(50)), len(s.pending_events()))
    assert before == after


def test_clear_and_read_tools(codoc_dir):
    a = _seed(codoc_dir, title="A", description="d")
    tools.walkthrough(codoc_dir, question="q", steps=[{"feature_id": a.id}])
    assert tools.read_walkthrough_tool(codoc_dir)["walkthrough"]["question"] == "q"
    assert tools.clear_walkthrough_tool(codoc_dir) == {"ok": True, "cleared": True}
    assert tools.read_walkthrough_tool(codoc_dir)["walkthrough"] is None
    assert tools.clear_walkthrough_tool(codoc_dir) == {"ok": True, "cleared": False}
