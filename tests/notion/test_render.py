"""U2 — render_blocks: store → Notion toggle-block tree."""
from __future__ import annotations

from codoc.model.feature import Feature
from codoc.notion.render import render_blocks, text_to_rich
from codoc.store.db import open_store


def _titles(blocks):
    return [b["toggle"]["rich_text"][0]["text"]["content"] for b in blocks]


# ── tokenizer ────────────────────────────────────────────────────────────────

def test_text_to_rich_plain():
    runs = text_to_rich("hello")
    assert runs == [{"type": "text", "text": {"content": "hello", "link": None},
                     "annotations": {"bold": False}}]


def test_text_to_rich_bold_run():
    runs = text_to_rich("a **b** c")
    assert [r["text"]["content"] for r in runs] == ["a ", "b", " c"]
    assert runs[1]["annotations"]["bold"] is True


def test_text_to_rich_https_link_run():
    runs = text_to_rich("see [docs](https://x.com)")
    assert runs[-1]["text"]["content"] == "docs"
    assert runs[-1]["text"]["link"] == {"url": "https://x.com"}


def test_text_to_rich_codoc_citation_stays_literal():
    runs = text_to_rich("binds [a](codoc:f.py#g)")
    assert len(runs) == 1
    assert runs[0]["text"]["content"] == "binds [a](codoc:f.py#g)"
    assert runs[0]["text"]["link"] is None


def test_empty_text_yields_one_empty_run():
    assert text_to_rich("") == [{"type": "text", "text": {"content": "", "link": None},
                                 "annotations": {"bold": False}}]


# ── tree shape ───────────────────────────────────────────────────────────────

def test_single_feature_renders_toggle_with_paragraph(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        s.upsert_feature(Feature(title="Auth", description="Login and sessions."))
        blocks = render_blocks(s)
    assert _titles(blocks) == ["Auth"]
    kids = blocks[0]["children"]
    assert kids[0]["type"] == "paragraph"
    assert kids[0]["paragraph"]["rich_text"][0]["text"]["content"] == "Login and sessions."


def test_nested_features_nest_as_toggle_children(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        p = Feature(title="Parent", description="p")
        s.upsert_feature(p)
        s.upsert_feature(Feature(title="Child", description="c", parent_id=p.id))
        blocks = render_blocks(s)
    assert _titles(blocks) == ["Parent"]
    child_toggles = [k for k in blocks[0]["children"] if k["type"] == "toggle"]
    assert _titles(child_toggles) == ["Child"]


def test_siblings_ordered_by_title(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        for t in ["Zebra", "alpha", "Mango"]:
            s.upsert_feature(Feature(title=t, description="x"))
        blocks = render_blocks(s)
    assert _titles(blocks) == ["alpha", "Mango", "Zebra"]


def test_multi_paragraph_description_splits(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        s.upsert_feature(Feature(title="F", description="First para.\n\nSecond para."))
        blocks = render_blocks(s)
    paras = [k for k in blocks[0]["children"] if k["type"] == "paragraph"]
    assert len(paras) == 2


def test_fid_to_block_stamps_existing_block_id(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        f = Feature(title="F", description="d")
        s.upsert_feature(f)
        blocks = render_blocks(s, fid_to_block={f.id: "blk-99"})
    assert blocks[0]["id"] == "blk-99"


def test_unmapped_feature_has_no_block_id(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        s.upsert_feature(Feature(title="F", description="d"))
        blocks = render_blocks(s)
    assert "id" not in blocks[0]
