"""doc_render: store/tree.codoc → tree.doc.json, the inverse of doc_parse.

The load-bearing guarantee is round-trip fidelity: rendering a parsed tree to a PM
doc and parsing it back recovers the same titles, depths, and descriptions — so a
hub-rendered doc is indistinguishable from an editor-authored one."""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.doc_render import (
    _inline_runs, build_doc, build_doc_from_store, build_doc_from_text,
)
from codoc.codoc_file.doc_parse import _inline_text, parse_doc
from codoc.codoc_file.parse import extract_bold, parse_text
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


def test_store_features_emit_in_tree_preorder(tmp_path):
    # Regression: a child created AFTER a later root must still render UNDER its parent,
    # not in flat created_at order — otherwise the doc body desyncs from the left nav
    # (which walks the tree) and scroll-spy jumps. Creation order here is
    # [Root A, Root B, Child of A]; the doc must read [Root A, Child of A, Root B].
    with open_store(tmp_path) as s:
        root_a = Feature(title="Root A", description="a")
        s.upsert_feature(root_a)
        root_b = Feature(title="Root B", description="b")
        s.upsert_feature(root_b)
        child = Feature(title="Child of A", description="c", parent_id=root_a.id)
        s.upsert_feature(child)
        doc = build_doc_from_store(s)
    titles = [b["content"][0]["text"] for b in doc["content"] if b["type"] == "featureHeading"]
    assert titles == ["Root A", "Child of A", "Root B"]


def test_store_orphan_with_dangling_parent_still_projects(tmp_path):
    # A feature whose parent_id points outside the live set (e.g. a retired parent) is
    # treated as a de-facto root and stays in the projection — never silently dropped.
    with open_store(tmp_path) as s:
        orphan = Feature(title="Orphan", description="o", parent_id="f-nonexistent")
        s.upsert_feature(orphan)
        doc = build_doc_from_store(s)
    titles = [b["content"][0]["text"] for b in doc["content"] if b["type"] == "featureHeading"]
    assert titles == ["Orphan"]


def test_store_paragraphs_carry_owner_id(tmp_path):
    # Invariant I2: each projected description paragraph is anchored to its feature by
    # identity (ownerId=fid), so the webview never re-attributes prose to a heading
    # inserted above it. Two features, each with a distinct paragraph.
    with open_store(tmp_path) as s:
        a = Feature(title="A", description="First para.\n\nSecond para.")
        s.upsert_feature(a)
        b = Feature(title="B", description="Only para.")
        s.upsert_feature(b)
        doc = build_doc_from_store(s)
    paras = [blk for blk in doc["content"] if blk["type"] == "paragraph"]
    owners = [p["attrs"]["ownerId"] for p in paras]
    assert owners == [a.id, a.id, b.id]


def test_build_doc_from_text_omits_owner_id(tmp_path):
    # The frozen text path (build_doc) stays byte-identical to the pre-I2 shape: no
    # ownerId attr (only the store-fed projection anchors prose by identity).
    doc = build_doc_from_text("- Auth  ⟨f-1⟩\n    Login and sessions.\n")
    para = next(b for b in doc["content"] if b["type"] == "paragraph")
    assert "attrs" not in para


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


def test_store_zero_width_comment_on_empty_description_projects(tmp_path):
    """A (0,0)-anchored comment on a feature with NO description is a feature-level
    note; it must still project (FIX G) — previously it was dropped because no
    paragraph was emitted for empty prose. It rides a zero-width caret run."""
    with open_store(tmp_path) as s:
        f = Feature(title="Auth", description="")
        s.upsert_feature(f)
        c = CommentThread(feature_id=f.id, body="why no body?", anchor_start=0, anchor_end=0)
        s.upsert_comment(c)
        doc = build_doc_from_store(s)
    para = next((b for b in doc["content"] if b["type"] == "paragraph"), None)
    assert para is not None  # a paragraph was emitted to carry the feature-level note
    commented = [r for r in para["content"] if r.get("marks")]
    assert len(commented) == 1
    assert commented[0]["text"] == ""  # zero-width caret run
    assert commented[0]["marks"][0]["type"] == "comment"
    assert commented[0]["marks"][0]["attrs"]["threadId"] == c.id


def test_store_zero_width_mark_caret_in_description_projects(tmp_path):
    """A (0,0) zero-width mark caret inside a non-empty description projects as a
    zero-width run at offset 0 (FIX G), without losing the description text."""
    with open_store(tmp_path) as s:
        f = Feature(title="Auth", description="Login and sessions.")
        s.upsert_feature(f)
        s.upsert_mark(Mark(feature_id=f.id, kind=MarkKind.AMEND, anchor_start=0, anchor_end=0))
        doc = build_doc_from_store(s)
    para = next(b for b in doc["content"] if b["type"] == "paragraph")
    caret = [r for r in para["content"] if r.get("marks") and r["text"] == ""]
    assert len(caret) == 1
    assert caret[0]["marks"][0]["type"] == "amend"
    # the description text survives intact alongside the caret run
    plain = "".join(r["text"] for r in para["content"] if not r.get("marks"))
    assert plain == "Login and sessions."


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


# ── bold: the focus signal survives the projection ───────────────────────────
#
# `**…**` is the one piece of markdown in a description that MEANS something:
# `extract_bold` lifts it into a realize directive's `Focus:` line. The projection
# turns it into a `bold` mark so the author reads emphasis instead of asterisks, and
# `doc_parse._inline_text` writes it back — the editor's B button used to produce a
# mark both serializers threw away, so the signal was unreachable from the only human
# surface. The case list below is mirrored verbatim in the TS twin
# (vscode-codoc/src/test/doc-roundtrip.test.ts, "bold: `**…**` ↔ the bold mark");
# both sides must agree, or the daemon and the webview disagree about what the author
# wrote.

_BOLD_CASES: list[tuple[str, list[str]]] = [
    ("plain prose, no markers", []),
    ("a **focus** b", ["focus"]),
    ("**leading** span", ["leading"]),
    ("trailing **span**", ["span"]),
    ("**two** spans **here**", ["two", "here"]),
    ("**see [x](codoc:a.py#fn) now** tail", ["see [x](codoc:a.py#fn) now"]),
    ("[**x**](codoc:a.py#fn) label markers survive", []),
    ("**  ** whitespace-only marker pair", []),
    ("**a****b** touching pairs", ["a"]),
    ("unmatched ** marker", []),
    ("***tripled***", ["tripled"]),
    ("a **b*c** d", []),
    ("cite [y](codoc:g.py) then **focus**", ["focus"]),
]


def _projected_bold(runs: list[dict]) -> list[str]:
    """The text each maximal run of bold-marked inline nodes covers — what the author
    sees emphasized, in the same shape ``extract_bold`` returns."""
    out: list[str] = []
    cur = ""
    for r in runs:
        if r["type"] == "text":
            text = r.get("text") or ""
        else:
            a = r["attrs"]
            target = f"{a['file']}#{a['symbol']}" if a.get("symbol") else a["file"]
            text = f"[{a['label']}](codoc:{target})"
        if any(m["type"] == "bold" for m in r.get("marks") or []):
            cur += text
        elif cur:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


@pytest.mark.parametrize("text,expected", _BOLD_CASES)
def test_bold_projection_and_text_round_trip(text, expected):
    """THE property: text → doc → text is the identity. The daemon projects the stored
    description, the webview serializes it back to compare against what it adopted, and
    any drift there is a phantom AMEND on every projection."""
    runs = _inline_runs(text)
    assert _inline_text(runs) == text
    assert _projected_bold(runs) == expected


@pytest.mark.parametrize("text,_expected", _BOLD_CASES)
def test_bold_projection_is_a_fixpoint(text, _expected):
    """doc → text → doc: re-projecting what was just serialized changes nothing."""
    runs = _inline_runs(text)
    assert _inline_runs(_inline_text(runs)) == runs


@pytest.mark.parametrize("text,expected", _BOLD_CASES)
def test_bold_projection_matches_extract_bold(text, expected):
    """Parity with ``parse.extract_bold`` — the daemon's reader of the same signal. The
    author must see emphasized exactly what the agent is told to focus on.

    Two inputs diverge, both deliberately (see ``doc_render._bold_matches``): markers
    inside a citation LABEL stay label text, and the second of two touching pairs stays
    prose. Neither is reachable by any authoring gesture — TipTap merges adjacent bold
    into one span — and projecting them would corrupt the text on the way back."""
    if text in ("[**x**](codoc:a.py#fn) label markers survive", "**a****b** touching pairs"):
        assert _projected_bold(_inline_runs(text)) != extract_bold(text)
        return
    assert _projected_bold(_inline_runs(text)) == extract_bold(text)


def test_bold_survives_a_whole_feature_round_trip():
    text = "- Retry  ⟨f-1⟩\n    Keep the **retry budget** bounded, see [backoff](codoc:net.py#backoff).\n"
    tree = parse_doc(build_doc_from_text(text))
    assert tree.nodes[0].description == (
        "Keep the **retry budget** bounded, see [backoff](codoc:net.py#backoff)."
    )
    assert extract_bold(tree.nodes[0].description) == ["retry budget"]


def test_bold_projects_alongside_an_annotation_anchor(tmp_path):
    """An annotation anchors at a char offset into the description — which INCLUDES the
    `**` markers. The markers are dropped from the emitted runs but still occupy their
    offsets, so a comment written against the bolded words still lands on them."""
    with open_store(tmp_path) as s:
        f = Feature(title="Retry", description="a **focus** b")
        s.upsert_feature(f)
        # offsets 2..10 span `**focus**` in the stored description
        s.upsert_comment(CommentThread(feature_id=f.id, body="why?", anchor_start=2, anchor_end=10))
        doc = build_doc_from_store(s)
    para = next(b for b in doc["content"] if b["type"] == "paragraph")
    marked = next(r for r in para["content"] if r.get("marks"))
    assert marked["text"] == "focus"
    assert {m["type"] for m in marked["marks"]} == {"bold", "comment"}
    assert parse_doc(doc).nodes[0].description == "a **focus** b"
