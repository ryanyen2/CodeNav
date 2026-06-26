"""doc_render: store/tree.codoc → tree.doc.json, the inverse of doc_parse.

The load-bearing guarantee is round-trip fidelity: rendering a parsed tree to a PM
doc and parsing it back recovers the same titles, depths, and descriptions — so a
hub-rendered doc is indistinguishable from an editor-authored one."""
from __future__ import annotations

from codoc.codoc_file.doc_render import build_doc, build_doc_from_text
from codoc.codoc_file.doc_parse import parse_doc
from codoc.codoc_file.parse import parse_text


def test_heading_carries_fid_and_level():
    text = "- Parent  ⟨f-1⟩\n    - Child  ⟨f-2⟩\n"
    doc = build_doc_from_text(text)
    heads = [b for b in doc["content"] if b["type"] == "featureHeading"]
    assert [h["attrs"]["fid"] for h in heads] == ["f-1", "f-2"]
    assert heads[0]["attrs"]["level"] == 0
    assert heads[1]["attrs"]["level"] == 1   # nested under the parent


def test_codoc_ref_becomes_codeRef_atom():
    text = "- Feature  ⟨f-1⟩\n    Calls [run()](codoc:agent.py#Agent.run) in a loop.\n"
    doc = build_doc_from_text(text)
    para = next(b for b in doc["content"] if b["type"] == "paragraph")
    kinds = [r["type"] for r in para["content"]]
    assert "codeRef" in kinds
    ref = next(r for r in para["content"] if r["type"] == "codeRef")
    assert ref["attrs"] == {"label": "run()", "file": "agent.py", "symbol": "Agent.run"}


def test_round_trip_recovers_titles_and_descriptions():
    text = (
        "- Auth  ⟨f-1⟩\n"
        "    Login and sessions. Uses [hash()](codoc:crypto.py#hash).\n"
        "\n"
        "    A second paragraph of detail.\n"
        "- Billing  ⟨f-2⟩\n"
        "    Charges and invoices.\n"
    )
    original = parse_text(text)
    reparsed = parse_doc(build_doc(original))
    got = {n.id: (n.title, n.description) for n in reparsed.nodes}
    for n in original.nodes:
        assert got[n.id][0] == n.title
        assert got[n.id][1] == n.description   # normalized both sides → byte-identical


def test_empty_tree_renders_empty_doc():
    doc = build_doc_from_text("")
    assert doc == {"type": "doc", "content": []}
