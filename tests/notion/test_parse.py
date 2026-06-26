"""U3 — parse_blocks: Notion block tree → ParsedTree, then diff_codoc.

Mirrors tests/codoc_file/test_doc_parse.py + test_roundtrip_idempotency.py: the
pure walk shape, plus the diff integration that the whole identity model rests on.
"""
from __future__ import annotations

from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import normalize_description
from codoc.model.event import NodeOpKind
from codoc.model.feature import Feature
from codoc.notion.parse import parse_blocks, rich_text_to_markdown
from codoc.store.db import open_store


# ── Notion block builders ────────────────────────────────────────────────────

def _rt(content, *, bold=False, link=None):
    item = {"type": "text", "text": {"content": content, "link": ({"url": link} if link else None)},
            "annotations": {"bold": bold}, "plain_text": content, "href": link}
    return item


def _toggle(block_id, title, *, children=None):
    return {"id": block_id, "type": "toggle", "has_children": bool(children),
            "toggle": {"rich_text": [_rt(title)]}, "children": children or []}


def _para(*runs):
    return {"type": "paragraph", "paragraph": {"rich_text": list(runs)}}


def _quote(text):
    return {"type": "quote", "quote": {"rich_text": [_rt(text)]}}


def _callout(text):
    return {"type": "callout", "callout": {"rich_text": [_rt(text)], "icon": None}}


# ── rich_text reconstruction ─────────────────────────────────────────────────

def test_rich_text_plain():
    assert rich_text_to_markdown([_rt("hello world")]) == "hello world"


def test_rich_text_bold_wraps():
    assert rich_text_to_markdown([_rt("focus", bold=True)]) == "**focus**"


def test_rich_text_https_link_becomes_markdown_link():
    runs = [_rt("see ", ), _rt("docs", link="https://example.com/x")]
    assert rich_text_to_markdown(runs) == "see [docs](https://example.com/x)"


def test_rich_text_codoc_citation_stays_literal():
    # codoc: citations render as literal text (scheme-safe), so they come back verbatim.
    runs = [_rt("binds [auth](codoc:auth.py#login) here")]
    assert rich_text_to_markdown(runs) == "binds [auth](codoc:auth.py#login) here"


# ── structure + identity ─────────────────────────────────────────────────────

def test_single_toggle_unknown_block_is_add_shaped():
    tree = parse_blocks([_toggle("blk-1", "Auth", children=[_para(_rt("Login and sessions."))])])
    assert len(tree.nodes) == 1
    n = tree.nodes[0]
    assert (n.title, n.description, n.parent_id, n.retired) == (
        "Auth", "Login and sessions.", None, False)
    # Unknown block → no fid, block id carried as local_id for mint-back.
    assert n.id is None
    assert n.local_id == "blk-1"


def test_known_block_resolves_to_fid_via_map():
    tree = parse_blocks([_toggle("blk-1", "Auth")], block_to_fid={"blk-1": "f-42"})
    n = tree.nodes[0]
    assert n.id == "f-42"
    assert n.local_id == ""  # identity comes from the authoritative map, not content


def test_nested_toggles_reconstruct_parent_chain():
    tree = parse_blocks([
        _toggle("p", "Parent", children=[
            _para(_rt("parent prose")),
            _toggle("c", "Child", children=[
                _toggle("g", "Grandchild"),
            ]),
        ]),
    ], block_to_fid={"p": "f-p", "c": "f-c", "g": "f-g"})
    by_title = {n.title: n for n in tree.nodes}
    assert by_title["Parent"].parent_id is None
    assert by_title["Child"].parent_id == "f-p"
    assert by_title["Grandchild"].parent_id == "f-c"


def test_quote_becomes_steering_comment():
    tree = parse_blocks([_toggle("b", "F", children=[
        _para(_rt("prose")), _quote("please add tests"),
    ])])
    assert tree.nodes[0].comments == ["please add tests"]


def test_two_identical_quotes_both_kept():
    # Content-keyed collapse is a Loop B concern; the parser keeps both runs.
    tree = parse_blocks([_toggle("b", "F", children=[_quote("same"), _quote("same")])])
    assert tree.nodes[0].comments == ["same", "same"]


def test_callout_is_skipped_as_proposal():
    tree = parse_blocks([_toggle("b", "F", children=[_callout("ADD proposal ⟨e-1⟩")])])
    assert tree.nodes[0].description == ""  # callout content is not prose


def test_description_is_normalized():
    tree = parse_blocks([_toggle("b", "F", children=[_para(_rt("Holds colors.   "))])])
    assert tree.nodes[0].description == "Holds colors."
    assert tree.nodes[0].description == normalize_description("Holds colors.   ")


def test_refs_extracted_from_description():
    tree = parse_blocks([_toggle("b", "F", children=[
        _para(_rt("binds [auth](codoc:auth.py#login)")),
    ])])
    refs = tree.nodes[0].refs
    assert len(refs) == 1 and refs[0].file == "auth.py" and refs[0].symbol == "login"


# ── diff_codoc integration (the identity model) ──────────────────────────────

def test_known_feature_amend(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        f = Feature(title="Auth", description="old prose")
        s.upsert_feature(f)
        blocks = [_toggle("blk-1", "Auth", children=[_para(_rt("new prose"))])]
        diff = diff_codoc(parse_blocks(blocks, {"blk-1": f.id}), s, has_local_ids=True)
        kinds = [op.kind for op in diff.user_ops]
        assert NodeOpKind.AMEND in kinds
        amend = next(op for op in diff.user_ops if op.kind == NodeOpKind.AMEND)
        assert amend.description == "new prose"


def test_unchanged_feature_is_noop(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        f = Feature(title="Auth", description="prose")
        s.upsert_feature(f)
        blocks = [_toggle("blk-1", "Auth", children=[_para(_rt("prose"))])]
        diff = diff_codoc(parse_blocks(blocks, {"blk-1": f.id}), s, has_local_ids=True)
        assert diff.is_empty()


def test_unknown_toggle_is_add_with_local_id(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        blocks = [_toggle("blk-new", "Brand new", children=[_para(_rt("fresh"))])]
        diff = diff_codoc(parse_blocks(blocks, {}), s, has_local_ids=True)
        adds = [op for op in diff.user_ops if op.kind == NodeOpKind.ADD_NODE]
        assert len(adds) == 1
        assert adds[0].title == "Brand new"
        assert adds[0].local_id == "blk-new"  # block id rides the ADD for mint-back


def test_move_detected_when_parent_changes(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        parent = Feature(title="Parent", description="p")
        s.upsert_feature(parent)
        child = Feature(title="Child", description="c", parent_id=parent.id)
        s.upsert_feature(child)
        # Re-parent the child to top level (drag it out of the parent toggle).
        blocks = [
            _toggle("blk-p", "Parent"),
            _toggle("blk-c", "Child", children=[_para(_rt("c"))]),
        ]
        diff = diff_codoc(
            parse_blocks(blocks, {"blk-p": parent.id, "blk-c": child.id}),
            s, has_local_ids=True)
        moves = [op for op in diff.user_ops if op.kind == NodeOpKind.MOVE_NODE]
        assert len(moves) == 1 and moves[0].parent_id is None
