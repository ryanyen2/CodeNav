"""The orientation pass: reading what a project says about itself.

Bootstrap used to start at the first symbol of the first file. Everything before
that — the README, the module docstring, the comment block above the first
definition — was never read by anything, and that is exactly where an author
writes what the code cannot say: what a rule gives up, which order matters, what
the program is deliberately not for.

The measured consequence, on the study's own codebase: a generated tree answered
7 of 12 questions about the program's intent; a hand-written description answered
12. The difference was not the model. It was that nothing had shown it the prose.
"""
from __future__ import annotations

import textwrap

import pytest

from codoc.agent.bootstrap_agent import format_brief
from codoc.loop.bootstrap_hier import _project_prose


class _Row:
    def __init__(self, file: str):
        self.file = file
        self.symbol_path = f"{file}::thing"
        self.source = "def thing(): pass"
        self.tokens_hash = "t"
        self.types_hash = "y"


# ── what gets read ───────────────────────────────────────────────────────────

def test_the_readme_is_read(tmp_path):
    (tmp_path / "README.md").write_text("# thing\n\nIt converts one format to another.")
    readme, _ = _project_prose(str(tmp_path), [])
    assert "converts one format to another" in readme


def test_the_opening_of_a_file_is_read_including_what_comes_before_the_code(tmp_path):
    # The whole point. Chunks start at the first symbol, so a comment explaining
    # why a threshold is what it is has never reached any prompt.
    (tmp_path / "rules.py").write_text(textwrap.dedent('''
        """What this module is for."""

        # Three pages, because under that there is no pattern and a coincidence
        # would be taken for a running header.
        MIN_PAGES = 3


        def strip(doc):
            return doc
    ''').lstrip())
    _, headers = _project_prose(str(tmp_path), [_Row("rules.py")])
    assert len(headers) == 1
    opening = headers[0]["opening"]
    assert "What this module is for" in opening
    assert "a coincidence" in opening, "the comment above the first symbol is the evidence"
    assert "def strip" not in opening, "it stops at the first definition"


def test_each_file_is_read_once_however_many_chunks_it_has(tmp_path):
    (tmp_path / "a.py").write_text('"""A."""\n\ndef one(): pass\ndef two(): pass\n')
    _, headers = _project_prose(str(tmp_path), [_Row("a.py"), _Row("a.py"), _Row("a.py")])
    assert len(headers) == 1


def test_a_project_with_no_prose_is_not_an_error(tmp_path):
    readme, headers = _project_prose(str(tmp_path), [])
    assert readme == ""
    assert headers == []


def test_no_root_means_nothing_to_read(tmp_path):
    # Bootstrap can run without a root, and must not crash trying to open files.
    readme, headers = _project_prose(None, [_Row("a.py")])
    assert readme == "" and headers == []


def test_a_missing_file_is_skipped_rather_than_fatal(tmp_path):
    (tmp_path / "there.py").write_text('"""Here."""\n\ndef f(): pass\n')
    _, headers = _project_prose(str(tmp_path), [_Row("gone.py"), _Row("there.py")])
    assert [h["file"] for h in headers] == ["there.py"]


def test_a_very_long_readme_is_capped(tmp_path):
    (tmp_path / "README.md").write_text("x" * 200_000)
    readme, _ = _project_prose(str(tmp_path), [])
    assert 0 < len(readme) < 60_000, "a repo that ships a book must not eat the context"


# ── how the brief reaches the file pass ──────────────────────────────────────

def test_an_empty_brief_says_so_rather_than_pretending():
    # The file prompt has to cope with both, and a blank block would read as an
    # instruction to describe nothing.
    assert "own terms" in format_brief(None)
    assert "own terms" in format_brief({})


def test_a_decision_carries_its_reason_and_its_cost():
    # The most valuable thing in the brief and the easiest to lose in rendering.
    out = format_brief({"decisions": [
        {"choice": "The hyphen is dropped", "because": "the typesetter put it there",
         "gave_up": "real compounds split across lines"},
    ]})
    assert "The hyphen is dropped" in out
    assert "because the typesetter put it there" in out
    assert "real compounds" in out


def test_a_decision_with_no_reason_is_still_reported():
    out = format_brief({"decisions": [{"choice": "Rounding happens once"}]})
    assert "Rounding happens once" in out
    assert "because" not in out, "no reason must not become an invented one"


def test_ordering_says_what_goes_wrong_without_it():
    out = format_brief({"ordering": [
        {"before": "furniture removal", "then": "heading detection",
         "otherwise": "a running header becomes a heading on every page"},
    ]})
    assert "furniture removal runs before heading detection" in out
    assert "every page" in out


def test_vocabulary_is_rendered_as_terms_a_reader_can_look_up():
    out = format_brief({"vocabulary": [{"term": "furniture", "means": "the repeated header"}]})
    assert "furniture" in out and "repeated header" in out


def test_a_field_with_nothing_in_it_is_left_out_entirely():
    out = format_brief({"purpose": "It converts things."})
    assert "It converts things." in out
    assert "Deliberately not in scope" not in out
    assert "Order that matters" not in out


def test_a_malformed_entry_does_not_break_the_render():
    # The brief comes from a model. A missing key must drop one line, not the
    # whole bootstrap.
    out = format_brief({
        "decisions": [{}, {"choice": "Kept"}],
        "ordering": [{}, {"before": "a", "then": "b"}],
        "vocabulary": [{}, {"term": "x", "means": "y"}],
    })
    assert "Kept" in out and "a runs before b" in out and "x" in out
