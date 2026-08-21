"""A file that does not parse is not evidence that its code was deleted.

tree-sitter recovers what it can from a damaged file and drops the rest, so the
chunks it yields are a lower bound on what the file contains. The index has no way
to say "I could not read this", and Loop A reads a chunk's absence as deletion —
which DETACHES the binding. A save in the middle of an edit would therefore strip
a feature's attribution, and the repaired file would return as an unbound addition
for the LLM pass to guess at again.
"""
from __future__ import annotations

import pathlib

from codoc.lang import get_adapter, parses_cleanly
from codoc.loop.diff import ChangeSet, ChunkRef, _hold_unparseable_removals

WHOLE = "def ok():\n    return 1\n\n\ndef half(a, b):\n    return a\n"
TRUNCATED = "def ok():\n    return 1\n\n\ndef half(a, b\n"


def test_a_clean_file_parses_cleanly() -> None:
    assert parses_cleanly("m.py", WHOLE) is True
    assert parses_cleanly("m.ts", "export function f() { return 1; }\n") is True


def test_a_half_typed_definition_does_not() -> None:
    assert parses_cleanly("m.py", TRUNCATED) is False
    assert parses_cleanly("m.ts", "export function f( {\n") is False


def test_a_file_no_adapter_reads_is_not_judged() -> None:
    # Nothing parsed it, so there is no parse to call clean — and that is a third
    # answer, not False: False is what a damaged file says.
    assert parses_cleanly("README.md", "# hello\n") is None


def test_removal_from_a_broken_file_is_held(tmp_path: pathlib.Path) -> None:
    (tmp_path / "m.py").write_text(TRUNCATED)
    cs = ChangeSet(removed=[ChunkRef("m.py", "m.py::half", "h1")])
    held = _hold_unparseable_removals(cs, str(tmp_path))
    assert [c.symbol_path for c in held] == ["m.py::half"]
    assert cs.removed == []


def test_removal_from_a_readable_file_stands(tmp_path: pathlib.Path) -> None:
    # `half` really is gone from a file that parses, so the detach is correct.
    (tmp_path / "m.py").write_text("def ok():\n    return 1\n")
    cs = ChangeSet(removed=[ChunkRef("m.py", "m.py::half", "h1")])
    assert _hold_unparseable_removals(cs, str(tmp_path)) == []
    assert [c.symbol_path for c in cs.removed] == ["m.py::half"]


def test_removal_from_a_deleted_file_stands(tmp_path: pathlib.Path) -> None:
    # The file is gone: every chunk in it is gone with it, for real.
    cs = ChangeSet(removed=[ChunkRef("gone.py", "gone.py::ok", "h1")])
    assert _hold_unparseable_removals(cs, str(tmp_path)) == []
    assert [c.symbol_path for c in cs.removed] == ["gone.py::ok"]


def test_only_removals_are_held(tmp_path: pathlib.Path) -> None:
    # An addition or a modification out of a damaged file is at worst a spurious
    # refresh, and the next clean pass corrects it. A removal destroys attribution
    # nothing recreates, which is the whole asymmetry.
    (tmp_path / "m.py").write_text(TRUNCATED)
    cs = ChangeSet(
        added=[ChunkRef("m.py", "m.py::new", "h1")],
        removed=[ChunkRef("m.py", "m.py::half", "h2")],
        modified=[ChunkRef("m.py", "m.py::ok", "h3")],
    )
    _hold_unparseable_removals(cs, str(tmp_path))
    assert [c.symbol_path for c in cs.added] == ["m.py::new"]
    assert [c.symbol_path for c in cs.modified] == ["m.py::ok"]
    assert cs.removed == []


def test_a_broken_file_does_not_hold_another_file_s_removals(tmp_path: pathlib.Path) -> None:
    (tmp_path / "broken.py").write_text(TRUNCATED)
    (tmp_path / "clean.py").write_text("x = 1\n")
    cs = ChangeSet(removed=[
        ChunkRef("broken.py", "broken.py::half", "h1"),
        ChunkRef("clean.py", "clean.py::gone", "h2"),
    ])
    held = _hold_unparseable_removals(cs, str(tmp_path))
    assert [c.file for c in held] == ["broken.py"]
    assert [c.file for c in cs.removed] == ["clean.py"]


def test_the_hold_is_reported(tmp_path: pathlib.Path, caplog) -> None:
    # A file that never parses — a templated .py, Python 2 — keeps its stale
    # bindings until it does, so the hold must not be silent.
    (tmp_path / "m.py").write_text(TRUNCATED)
    cs = ChangeSet(removed=[ChunkRef("m.py", "m.py::half", "h1")])
    with caplog.at_level("WARNING"):
        _hold_unparseable_removals(cs, str(tmp_path))
    assert "m.py" in caplog.text and "does not parse" in caplog.text


# --- files with no adapter that are readable anyway -------------------------

RULES = '[periods]\nmonth = "made"\n\n[merchants]\nunmatched = "stop"\n'


def test_removal_from_a_settings_file_that_parses_stands(tmp_path: pathlib.Path) -> None:
    """A deleted section is a deleted decision. The file has no tree-sitter adapter,
    which for a while was answered as "damaged" — so the binding was held forever and
    the tree kept citing a section that was gone."""
    (tmp_path / "rules.toml").write_text('[periods]\nmonth = "made"\n')
    cs = ChangeSet(removed=[ChunkRef("rules.toml", "rules.toml::merchants", "h1")])
    assert _hold_unparseable_removals(cs, str(tmp_path)) == []
    assert [c.symbol_path for c in cs.removed] == ["rules.toml::merchants"]


def test_removal_from_a_half_saved_settings_file_is_held(tmp_path: pathlib.Path) -> None:
    """Mid-edit is mid-edit in any language: an unterminated string means the reader
    saw a keystroke, not a decision to remove a section."""
    (tmp_path / "rules.toml").write_text('[periods]\nmonth = "made\n')
    cs = ChangeSet(removed=[ChunkRef("rules.toml", "rules.toml::merchants", "h1")])
    held = _hold_unparseable_removals(cs, str(tmp_path))
    assert [c.symbol_path for c in held] == ["rules.toml::merchants"]
    assert cs.removed == []


# --- legal code the grammar cannot read ------------------------------------
#
# "Damaged" is a verdict that DESTROYS attribution, so it has to mean every reader
# failed. The bundled grammar predates a trailing comma inside a subscript -- which is
# what a formatter writes each time it splits a type across lines -- and reported those
# files as damaged: 311 of altair's 2998 addressable chunks sit in the two modules where
# that happens (`utils/schemapi.py`, `vegalite/v6/api.py`), and nothing bound to them
# could ever learn that code had been deleted. So the Python adapter asks the interpreter
# it is running on as well, and the two readers fail on opposite halves of the language.

FORMATTED_TYPE = (
    "def versions() -> Mapping[\n"
    "    Literal[\n"
    '        "vega-lite", "vega-embed",\n'
    "    ],\n"
    "    str,\n"
    "]:\n"
    "    return {}\n"
)

#: `print x` and `except E, e` -- the grammar reads these, the interpreter does not.
PYTHON_2 = 'def show(x):\n    print "value", x\n'


def test_a_formatted_type_annotation_is_not_damage() -> None:
    assert parses_cleanly("m.py", FORMATTED_TYPE) is True


def test_a_removal_from_such_a_file_is_honoured(tmp_path: pathlib.Path) -> None:
    """The consequence, and the reason this is worth fixing rather than noting.

    A held removal is not a delay: the file never starts parsing, so the binding is
    held forever and the tree goes on citing code that was deleted.
    """
    (tmp_path / "m.py").write_text(FORMATTED_TYPE)
    cs = ChangeSet(removed=[ChunkRef("m.py", "m.py::gone", "h1")])
    assert _hold_unparseable_removals(cs, str(tmp_path)) == []
    assert [c.symbol_path for c in cs.removed] == ["m.py::gone"]


def test_a_python_2_file_is_still_read_by_the_reader_that_can() -> None:
    """The other half of the language, and why this is a union and not a swap.

    The interpreter rejects Python 2 outright; the grammar still has a rule for it and
    yields its definitions. Asking only the interpreter would move the false verdict
    from one kind of repository to another.
    """
    assert parses_cleanly("m.py", PYTHON_2) is True
    assert get_adapter("python").extract_chunks("m.py", PYTHON_2)


def test_a_file_neither_reader_gets_through_is_still_damaged() -> None:
    # The verdict has to stay usable: a mid-edit save, a templated .py, and a merge
    # conflict left in the file are all cases where a removal must be held.
    assert parses_cleanly("m.py", "def f(a, b\n") is False
    assert parses_cleanly("m.py", "class {{ name }}(Base):\n    pass\n") is False
    assert parses_cleanly(
        "m.py", "def f():\n<<<<<<< HEAD\n    return 1\n=======\n"
                "    return 2\n>>>>>>> other\n") is False


def test_a_byte_order_mark_is_not_damage() -> None:
    # `ast.parse` refuses a BOM, and a decoded str is exactly where one still is -- so
    # without stripping it the second reader would report every BOM file damaged, and
    # a BOM file using formatted types would have no reader left.
    assert parses_cleanly("m.py", "\ufeff" + FORMATTED_TYPE) is True


def test_source_that_breaks_the_interpreter_other_than_by_syntax() -> None:
    # A null byte raises ValueError, not SyntaxError. The second reader is asked one
    # question -- did the parse get through -- so anything it raises is a No, and
    # nothing it raises reaches a caller.
    assert parses_cleanly("m.py", "def f():\n    pass\n\x00") is False


def test_typescript_says_which_readers_it_has() -> None:
    # No TypeScript in this process, so the grammar is the whole answer there. Stated
    # as a test because the asymmetry is easy to read as an oversight.
    assert get_adapter("typescript").reads_cleanly("export const a = 1;\n") is True
    assert get_adapter("typescript").reads_cleanly("export function f( {\n") is False


def test_a_notebook_cell_gets_both_readers_too(tmp_path: pathlib.Path) -> None:
    """A cell the grammar rejects used to be commented out whole.

    Which is the same defect one level down: every definition in a cell using a
    formatted type annotation left the tree, silently.
    """
    import json

    notebook = json.dumps({"cells": [
        {"cell_type": "code", "source": FORMATTED_TYPE},
    ], "nbformat": 4})
    assert parses_cleanly("nb.ipynb", notebook) is True
    addressed = {c.symbol_path for c
                 in get_adapter("notebook").extract_chunks("nb.ipynb", notebook)}
    assert "nb.ipynb::versions" in addressed


def test_a_notebook_that_will_not_open_is_still_damaged() -> None:
    # The sentinel the notebook adapter parses instead of a broken notebook has to
    # fail BOTH readers now, or a file codoc could not open would report itself whole
    # and retire every feature bound to it.
    assert parses_cleanly("nb.ipynb", '{"cells": [') is False
