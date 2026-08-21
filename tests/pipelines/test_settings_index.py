"""The settings branch of the real indexer, end to end.

This is the one test that runs cocoindex and LanceDB rather than a stand-in, because
the claim being made is about the pipeline: a settings file the code reads arrives in
the index as named sections, a formatter pass does not wake the loops, and a changed
value does. Everything it needs is a temp directory and about a second.
"""
from __future__ import annotations

import pathlib

from codoc.pipelines.indexing.reader import read_all_chunks
from codoc.pipelines.indexing.runner import update_index

SUMMARY = '''"""Summarize a ledger."""
import tomllib
from pathlib import Path

RULES = Path(__file__).parent / "rules.toml"


def load_rules():
    """Read the tally rules from rules.toml."""
    return tomllib.loads(RULES.read_text())
'''

RULES = '''# How a tally lines up its summaries.
version = 2

# Three months rather than two, so a coincidence does not become a commitment.
[periods]
month = "made"

# An unmatched merchant stops the run.
[merchants]
unmatched = "stop"
'''


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "tally").mkdir()
    (tmp_path / "tally/summary.py").write_text(SUMMARY, encoding="utf-8")
    (tmp_path / "tally/rules.toml").write_text(RULES, encoding="utf-8")
    # Prominent, and nothing in the repo reads it.
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "t"\n', encoding="utf-8")
    # A settings file no source file names.
    (tmp_path / "tally/fixture.toml").write_text("[x]\ny = 1\n", encoding="utf-8")
    return tmp_path


def _index(root: pathlib.Path) -> dict[str, str]:
    """Run one pass and return symbol path → tokens_hash."""
    update_index(root, root / ".codoc")
    return {row.symbol_path: row.tokens_hash for row in read_all_chunks(root / ".codoc")}


def test_the_sections_of_a_settings_file_the_code_reads_are_indexed(tmp_path):
    rows = _index(_repo(tmp_path))
    assert "tally/rules.toml::periods" in rows
    assert "tally/rules.toml::merchants" in rows
    assert "tally/rules.toml::__module__" in rows
    assert "tally/summary.py::load_rules" in rows


def test_a_settings_file_nobody_reads_is_absent(tmp_path):
    rows = _index(_repo(tmp_path))
    assert not [path for path in rows if path.startswith(("pyproject", "tally/fixture"))]


def test_a_section_is_indexed_with_the_comment_that_explains_it(tmp_path):
    """The reason a value is what it is lives above the key, and that is the part a
    description most wants to quote."""
    root = _repo(tmp_path)
    update_index(root, root / ".codoc")
    rows = {row.symbol_path: row.source for row in read_all_chunks(root / ".codoc")}
    assert "a coincidence does not become a commitment" in rows["tally/rules.toml::periods"]


def test_re_running_over_an_unchanged_repo_changes_nothing(tmp_path):
    root = _repo(tmp_path)
    assert _index(root) == _index(root)


def test_a_changed_value_moves_that_sections_identity_and_no_others(tmp_path):
    root = _repo(tmp_path)
    before = _index(root)
    (root / "tally/rules.toml").write_text(
        RULES.replace('month = "made"', 'month = "posted"'), encoding="utf-8")
    after = _index(root)
    assert [path for path in before if before[path] != after[path]] == [
        "tally/rules.toml::periods"]


def test_reflowing_a_comment_wakes_nothing(tmp_path):
    """Identity is the parsed pairs, so a formatter pass over a settings file must
    not read to the loops as a policy change."""
    root = _repo(tmp_path)
    before = _index(root)
    (root / "tally/rules.toml").write_text(
        RULES.replace(
            "# Three months rather than two, so a coincidence does not become a commitment.",
            "# Three months rather than two,\n# so a coincidence does not become a commitment."),
        encoding="utf-8")
    assert _index(root) == before
