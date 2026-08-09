"""Steering comments (`> …`), bold emphasis, and external links in tree.codoc.

A blockquote line inside a description is a note TO THE AGENT: collected into
``ParsedNode.comments``, excluded from the prose, and (in Loop B) turned into a
realize directive. ``**bold**`` spans and ``[label](https://…)`` links are
prose-level signals extracted for directive building.
"""
from __future__ import annotations

from codoc.codoc_file.parse import extract_bold, extract_links, parse_text


def _node(text: str, title: str = "Feat"):
    tree = parse_text(text)
    assert not tree.errors, tree.errors
    return next(n for n in tree.nodes if n.title == title)


# -- comments -----------------------------------------------------------------

def test_comment_collected_and_excluded_from_description():
    n = _node(
        "- Feat  ⟨f-0000aaaa⟩\n"
        "  Validates user input.\n"
        "  > also handle unicode emails\n"
    )
    assert n.description == "Validates user input."
    assert n.comments == ["also handle unicode emails"]


def test_contiguous_run_is_one_comment_two_runs_are_two():
    n = _node(
        "- Feat  ⟨f-0000aaaa⟩\n"
        "  prose\n"
        "  > first line\n"
        "  > second line\n"
        "\n"
        "  > another note\n"
    )
    assert n.comments == ["first line\nsecond line", "another note"]
    assert n.description == "prose"


def test_comment_only_edit_does_not_change_prose():
    plain = "- Feat  ⟨f-0000aaaa⟩\n  para one\n\n  para two\n"
    commented = (
        "- Feat  ⟨f-0000aaaa⟩\n  para one\n\n"
        "  > steer here\n\n"
        "  para two\n"
    )
    assert _node(commented).description == _node(plain).description == "para one\n\npara two"


def test_comment_before_prose_and_at_eof():
    n = _node(
        "- Feat  ⟨f-0000aaaa⟩\n"
        "  > do it first\n"
        "  prose\n"
        "  > and last\n"
    )
    assert n.description == "prose"
    assert n.comments == ["do it first", "and last"]


def test_comment_citing_a_feature_id_is_not_a_bad_marker():
    n = _node(
        "- Feat  ⟨f-0000aaaa⟩\n"
        "  prose\n"
        "  > merge this with ⟨f-0000bbbb⟩\n"
    )
    assert n.comments == ["merge this with ⟨f-0000bbbb⟩"]


def test_hash_line_separates_two_steering_runs():
    n = _node(
        "- Feat  ⟨f-0000aaaa⟩\n"
        "  prose\n"
        "  > one\n"
        "  # divider\n"
        "  > two\n"
    )
    assert n.comments == ["one", "two"]


def test_comment_outside_any_feature_is_ignored():
    tree = parse_text("> stray note\n- Feat  ⟨f-0000aaaa⟩\n  prose\n")
    assert tree.nodes[0].comments == []
    assert not tree.errors


# -- bold / links ---------------------------------------------------------------

def test_extract_bold_in_order_deduped():
    assert extract_bold("a **first** b **second** c **first**") == ["first", "second"]
    assert extract_bold("no bold here, * single * stars") == []
    assert extract_bold(None) == []


def test_extract_links_skips_codoc_refs():
    text = ("see [docs](https://example.com/spec) and [api](http://api.io/v2), "
            "but [code](codoc:src/a.py#fn) is a ref")
    links = extract_links(text)
    assert [(l.label, l.url) for l in links] == [
        ("docs", "https://example.com/spec"), ("api", "http://api.io/v2")]
    assert extract_links("") == []


def test_the_link_pattern_is_pinned_because_a_second_copy_mirrors_it():
    """A canary, not a tautology.

    The editor underlines a link so the author can see it registered as an instruction,
    and it finds one with its own regex — `consult-decorations.ts:CONSULT_RE`, a
    hand-transcribed copy of this pattern. Its parity test compares against another
    hand-transcribed copy, so changing THIS pattern breaks nothing over there: the two
    surfaces would quietly disagree about what counts as a Consult link, and the cue
    would stop matching prose the daemon still parses (the write-side/read-side signal
    drift documented in docs/solutions/logic-errors/).

    Nothing can import a Python regex into vitest, so the guard is here: if this
    assertion fails, update `CONSULT_RE` and `PY_LINK_RE`
    (vscode-codoc/src/test/authoring-cues.test.ts) in the same change.
    """
    from codoc.codoc_file.parse import _LINK_RE

    assert _LINK_RE.pattern == r"\[(?P<label>[^\]]*)\]\((?P<url>https?://[^)\s]+)\)"
