"""U2b — parse_doc_file: tree.doc.json → ParsedTree (the single-writer input).

The webview persists its authored intent to tree.doc.json and no longer writes
tree.codoc; Loop B learns edits from here. parse_doc_file must produce the SAME
ParsedTree shape parse.py does (so diff_codoc is unchanged), with the matching
projection: baseline-aware inline text, blocksToDescriptionText normalization,
and level→parent derivation.
"""
from __future__ import annotations

import json
from pathlib import Path

from codoc.codoc_file.doc_parse import doc_path, parse_doc, parse_doc_file


def _heading(fid, title, *, level=0, retired=False):
    return {"type": "featureHeading",
            "attrs": {"fid": fid, "level": level, "retired": retired, "realized": True},
            "content": [{"type": "text", "text": title}]}


def _para(*runs):
    return {"type": "paragraph", "content": list(runs)}


def _text(t, marks=None):
    n = {"type": "text", "text": t}
    if marks:
        n["marks"] = marks
    return n


def _doc(*blocks):
    return {"type": "doc", "content": list(blocks)}


def _write(codoc_dir, doc, *, wrap=True):
    payload = {"version": 1, "doc": doc, "suggestions": [], "comments": []} if wrap else doc
    doc_path(codoc_dir).write_text(json.dumps(payload))


def test_single_feature(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    _write(str(cd), _doc(_heading("f-1", "Auth"), _para(_text("Login and sessions."))))
    tree = parse_doc_file(str(cd))
    assert len(tree.nodes) == 1
    n = tree.nodes[0]
    assert (n.id, n.title, n.description, n.parent_id, n.retired) == (
        "f-1", "Auth", "Login and sessions.", None, False)


def test_nesting_from_level():
    tree = parse_doc(_doc(
        _heading("f-1", "Parent", level=0),
        _heading("f-2", "Child", level=1),
        _heading("f-3", "Grandchild", level=2),
        _heading("f-4", "Sibling", level=1),
    ))
    by_id = {n.id: n for n in tree.nodes}
    assert by_id["f-2"].parent_id == "f-1"
    assert by_id["f-3"].parent_id == "f-2"
    assert by_id["f-4"].parent_id == "f-1"  # popped back to level 0's child


def test_retired_and_new_node():
    tree = parse_doc(_doc(
        _heading("f-1", "Live", retired=False),
        _heading(None, "Brand new"),   # fid:null → a hand-added node (ADD on diff)
        _heading("f-2", "Dead", retired=True),
    ))
    assert tree.nodes[0].retired is False
    assert tree.nodes[1].id is None
    assert tree.nodes[2].retired is True


def test_description_normalization_drops_empty_paragraphs():
    tree = parse_doc(_doc(
        _heading("f-1", "F"),
        _para(_text("P1.")),
        _para(),                 # empty paragraph (cosmetic) → dropped
        _para(_text("P2.")),
    ))
    assert tree.nodes[0].description == "P1.\n\nP2."  # one blank-line break, no extras


def test_baseline_excludes_insertion_marked_runs():
    """An agent-proposal insertion mark (U4) must not leak into the parsed baseline —
    the projection is baseline-aware, like inlineRunsToText."""
    ins = [{"type": "insertion", "attrs": {"changeId": "c1", "authorId": "claude-code"}}]
    tree = parse_doc(_doc(
        _heading("f-1", "Auth"),
        _para(_text("Login and sessions."), _text(" Plus OAuth.", ins)),
    ))
    assert tree.nodes[0].description == "Login and sessions."  # insertion excluded


def test_code_ref_projects_to_markdown_and_refs():
    tree = parse_doc(_doc(
        _heading("f-1", "F"),
        _para(_text("See "), {"type": "codeRef", "attrs": {"label": "parse", "file": "p.py", "symbol": "parse_text"}}, _text(".")),
    ))
    n = tree.nodes[0]
    assert n.description == "See [parse](codoc:p.py#parse_text)."
    assert [(r.file, r.symbol) for r in n.refs] == [("p.py", "parse_text")]


def test_accepts_bare_doc_without_wrapper(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    _write(str(cd), _doc(_heading("f-1", "Bare")), wrap=False)  # top-level is the doc
    tree = parse_doc_file(str(cd))
    assert tree is not None and tree.nodes[0].title == "Bare"


def test_missing_file_returns_none(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    assert parse_doc_file(str(cd)) is None  # no webview doc yet → fall back to text


def test_corrupt_file_returns_none(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    doc_path(str(cd)).write_text("{ not json")
    assert parse_doc_file(str(cd)) is None  # tolerant: degrade, don't crash
