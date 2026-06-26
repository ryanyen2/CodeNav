"""doc_render: store/tree.codoc → tree.doc.json, the inverse of doc_parse.

The load-bearing guarantee is round-trip fidelity: rendering a parsed tree to a PM
doc and parsing it back recovers the same titles, depths, and descriptions — so a
hub-rendered doc is indistinguishable from an editor-authored one."""
from __future__ import annotations

import json

from codoc.codoc_file.doc_render import build_doc, build_doc_from_store, build_doc_from_text
from codoc.codoc_file.doc_parse import parse_doc
from codoc.codoc_file.parse import parse_text
from codoc.model.annotation import CommentThread, Mark, MarkKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


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


# ── build_doc_from_store: identity + per-feature version + annotations (U2) ───

def test_store_heading_carries_local_id_and_version(tmp_path):
    with open_store(tmp_path) as s:
        f = Feature(title="Auth", description="Login and sessions.", local_id="lid-7")
        s.upsert_feature(f)
        doc = build_doc_from_store(s)
    head = next(b for b in doc["content"] if b["type"] == "featureHeading")
    assert head["attrs"]["fid"] == f.id
    assert head["attrs"]["localId"] == "lid-7"
    assert head["attrs"]["version"] == f.updated_at.to_str()
    assert head["attrs"]["level"] == 0


def test_store_nested_features_carry_level(tmp_path):
    with open_store(tmp_path) as s:
        parent = Feature(title="Parent", description="p")
        s.upsert_feature(parent)
        child = Feature(title="Child", description="c", parent_id=parent.id)
        s.upsert_feature(child)
        doc = build_doc_from_store(s)
    heads = {b["content"][0]["text"]: b for b in doc["content"] if b["type"] == "featureHeading"}
    assert heads["Parent"]["attrs"]["level"] == 0
    assert heads["Child"]["attrs"]["level"] == 1


def test_store_mark_projects_onto_inline_run(tmp_path):
    with open_store(tmp_path) as s:
        # normalized description == "Login and sessions." — anchor the word "Login" (0..5).
        f = Feature(title="Auth", description="Login and sessions.")
        s.upsert_feature(f)
        s.upsert_mark(Mark(feature_id=f.id, kind=MarkKind.INSERTION, anchor_start=0, anchor_end=5))
        doc = build_doc_from_store(s)
    para = next(b for b in doc["content"] if b["type"] == "paragraph")
    marked = [r for r in para["content"] if r.get("marks")]
    assert len(marked) == 1
    assert marked[0]["text"] == "Login"
    assert marked[0]["marks"][0]["type"] == "insertion"
    # the remainder of the paragraph is an unmarked text run
    plain = [r for r in para["content"] if not r.get("marks") and r["type"] == "text"]
    assert "".join(r["text"] for r in plain) == " and sessions."


def test_store_comment_projects_at_anchor(tmp_path):
    with open_store(tmp_path) as s:
        f = Feature(title="Auth", description="Login and sessions.")
        s.upsert_feature(f)
        c = CommentThread(feature_id=f.id, body="why?", anchor_start=6, anchor_end=18)
        s.upsert_comment(c)
        doc = build_doc_from_store(s)
    para = next(b for b in doc["content"] if b["type"] == "paragraph")
    commented = [r for r in para["content"] if r.get("marks")]
    assert len(commented) == 1
    assert commented[0]["text"] == "and sessions"
    assert commented[0]["marks"][0]["type"] == "comment"
    assert commented[0]["marks"][0]["attrs"]["threadId"] == c.id


def test_store_excludes_retired_features(tmp_path):
    with open_store(tmp_path) as s:
        keep = Feature(title="Keep", description="kept")
        s.upsert_feature(keep)
        gone = Feature(title="Gone", description="gone")
        s.upsert_feature(gone)
        s.retire_feature(gone.id)
        doc = build_doc_from_store(s)
    titles = [b["content"][0]["text"] for b in doc["content"] if b["type"] == "featureHeading"]
    assert titles == ["Keep"]


def test_store_projection_idempotent(tmp_path):
    """Covers AE1 — projecting twice with no store change is byte-identical."""
    with open_store(tmp_path) as s:
        f = Feature(title="Auth", description="Login and sessions.")
        s.upsert_feature(f)
        s.upsert_mark(Mark(feature_id=f.id, anchor_start=0, anchor_end=5))
        s.upsert_comment(CommentThread(feature_id=f.id, body="q", anchor_start=6, anchor_end=18))
        first = build_doc_from_store(s)
        second = build_doc_from_store(s)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
