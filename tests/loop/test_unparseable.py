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

from codoc.lang import parses_cleanly
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
    # Nothing parsed it, so there is no parse to call clean — and the caller must
    # not read that as "damaged".
    assert parses_cleanly("README.md", "# hello\n") is False


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
