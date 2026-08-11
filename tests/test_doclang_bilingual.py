"""A bilingual tree is a legitimate tree, not a tree half-broken.

The workspace setting says what codoc *originates* prose in. It does not say what
the tree IS in, because an author is allowed to be inconsistent on purpose:
describing intent in Chinese while reaching for the English term whenever that is
the word people actually use, and leaving a node they themselves rewrote in English
alone. These tests pin that distinction — the one an implementation is most likely
to get wrong by being helpful, enforcing one language everywhere and quietly
translating somebody's words.
"""
from __future__ import annotations

import pytest

from codoc import doclang as dl
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


ZH = dl.resolve("zh-Hans")
EN = dl.resolve("en")


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir(parents=True)
    return d


# ── detection: which language is this prose in? ──────────────────────────────

@pytest.mark.parametrize("text,expected", [
    # Plain cases in both directions.
    ("比较两次索引快照，找出新增和删除的代码块。", "zh-Hans"),
    ("Diffs the index snapshot and reports what changed.", "en"),
    # THE case this feature exists for: Chinese prose carrying English terms. By
    # character share this string is 84% Latin, and a symmetric floor called it
    # English — which is backwards, and would have flagged correct writing.
    ("使用 tree-sitter 解析 Python 与 TypeScript。", "zh-Hans"),
    ("把 LanceDB 索引里的 chunk 读出来，交给 Loop A 做 snapshot diff。", "zh-Hans"),
    # Citations are code and must not count as prose in the code's language.
    ("通过 [compute_changeset()](codoc:a.py#compute_changeset) 比较快照。", "zh-Hans"),
    ("`parse_tree` 与 `types_hash` 的适配层。", "zh-Hans"),
    # …but borrowing only runs one way. English prose naming one Chinese product is
    # still English.
    ("Renders the 微信 share card for the mobile web view.", "en"),
    # Kana and Hangul are decisive where Han is shared.
    ("インデックス差分を計算する。", "ja"),
    ("인덱스 스냅샷을 비교합니다.", "ko"),
])
def test_detect_prose_language_in_a_chinese_tree(text, expected):
    assert dl.detect_prose_language(text, ZH).code == expected


def test_no_signal_keeps_the_default():
    """An empty or evidence-free string must not be assigned a language — the caller
    would then act on a guess it could not distinguish from a measurement."""
    for text in ("", "   ", "123 456", "— · —"):
        assert dl.detect_prose_language(text, ZH).code == "zh-Hans"
        assert dl.detect_prose_language(text, EN).code == "en"


def test_detection_is_symmetric_about_the_default():
    """Chinese prose in an English tree reads as Chinese, exactly as English prose in
    a Chinese tree reads as English — the rule is about the text, not the setting."""
    assert dl.detect_prose_language("比较两次快照。", EN).code == "zh-Hans"
    assert dl.detect_prose_language("Diffs the snapshot.", ZH).code == "en"


def test_language_tag_for_is_the_display_tag():
    assert dl.language_tag_for("比较两次快照。", ZH) == "zh-Hans"
    assert dl.language_tag_for("Diffs the snapshot.", ZH) == "en"
    assert dl.language_tag_for("", ZH) == "zh-Hans"


def test_prose_letters_ignores_code():
    """The evidence count must not be inflated by identifiers, or a title that is one
    symbol would look like enough prose to judge."""
    assert dl.prose_letters("`compute_changeset`") == 0
    assert dl.prose_letters("[x](codoc:a.py#x)") == 0
    assert dl.prose_letters("比较快照") == 4


# ── the prompt directive ────────────────────────────────────────────────────

def test_directive_separates_originating_from_editing():
    """The two rules are different and both load-bearing: originate in the tree's
    language, but edit in the language already on the page."""
    d = dl.prompt_directive(ZH)
    assert "Prose you originate goes in" in d
    assert "Prose you EDIT stays in the language it is already written in" in d


def test_directive_blesses_mixed_technical_vocabulary():
    """Without this the model 'fixes' 使用 tree-sitter 解析 into something monolingual,
    which is a worse description in either language."""
    d = dl.prompt_directive(ZH)
    assert "Mixing is normal, not an error" in d
    assert "do not translate the terms" in d


def test_english_directive_is_still_empty():
    assert dl.prompt_directive(EN) == ""


# ── what the MCP tools advise ───────────────────────────────────────────────

def _mk(codoc_dir, title, description):
    with open_store(codoc_dir) as s:
        f = Feature(title=title, description=description)
        s.upsert_feature(f)
        return f.id


def test_an_amend_is_judged_against_the_node_not_the_repo(codoc_dir):
    """The heart of it. A node the author wrote in English, inside a Chinese tree:
    amending it in English is correct and must not be flagged, while translating it
    to Chinese is the unrequested rewrite and must be."""
    from codoc.mcp import tools

    fid = _mk(codoc_dir, "Snapshot diff",
              "Diffs the index snapshot each pass and reports what changed.")
    with open_store(codoc_dir) as s:
        keep_en = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                         description="Diffs the index snapshot and reports each change.")
        assert tools._language_advice(s, keep_en, ZH) is None

        translated = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                            description="比较两次索引快照，并报告发生的变化。")
        advice = tools._language_advice(s, translated, ZH)
        assert advice and "keep the author's language" in advice


def test_an_amend_of_a_chinese_node_in_english_is_flagged(codoc_dir):
    """The same rule in the other direction — the node's language wins either way."""
    from codoc.mcp import tools

    fid = _mk(codoc_dir, "索引快照差异", "比较两次索引快照，找出新增和删除的代码块。")
    with open_store(codoc_dir) as s:
        op = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                    description="Diffs the index snapshot and reports what changed.")
        assert tools._language_advice(s, op, ZH) is not None


def test_a_new_node_is_judged_against_the_repo_default(codoc_dir):
    """An ADD has nothing behind it, so the workspace setting is the only thing that
    can say what language it should be in."""
    from codoc.mcp import tools

    with open_store(codoc_dir) as s:
        english = NodeOp(kind=NodeOpKind.ADD_NODE, title="Snapshot diff",
                         description="Diffs the index snapshot each pass.")
        advice = tools._language_advice(s, english, ZH)
        assert advice and "this tree is authored in" in advice

        chinese = NodeOp(kind=NodeOpKind.ADD_NODE, title="索引快照差异",
                         description="比较两次索引快照，找出新增和删除的代码块。")
        assert tools._language_advice(s, chinese, ZH) is None


def test_chinese_prose_with_english_terms_is_never_flagged(codoc_dir):
    """Ordinary bilingual technical writing. If this warns, the feature is worse than
    not having it — the author is being corrected for writing correctly."""
    from codoc.mcp import tools

    with open_store(codoc_dir) as s:
        for text in (
            "使用 tree-sitter 解析 Python 与 TypeScript，产出统一的 chunk。",
            "把 LanceDB 里的 snapshot 读出来，交给 Loop A 做 diff。",
            "通过 [compute_changeset()](codoc:a.py#compute_changeset) 比较快照。",
        ):
            op = NodeOp(kind=NodeOpKind.ADD_NODE, title="变更集计算", description=text)
            assert tools._language_advice(s, op, ZH) is None, text


def test_a_fragment_is_never_judged(codoc_dir):
    """Below a few words there is no evidence, and a verdict drawn from none is noise
    the agent will learn to ignore — including on the real findings."""
    from codoc.mcp import tools

    with open_store(codoc_dir) as s:
        for text in ("", "ok", "parse_tree", "`compute_changeset`"):
            op = NodeOp(kind=NodeOpKind.ADD_NODE, title="x", description=text)
            assert tools._language_advice(s, op, ZH) is None, text


def test_an_english_tree_is_never_advised(codoc_dir):
    """The default path must stay silent: an English repo did not opt into any of this."""
    from codoc.mcp import tools

    fid = _mk(codoc_dir, "索引快照差异", "比较两次索引快照，找出新增和删除的代码块。")
    with open_store(codoc_dir) as s:
        # Even prose in another script: with an English default and an existing
        # Chinese node, the node's own language is what an amend follows.
        op = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                    description="比较两次快照，并报告变化。")
        assert tools._language_advice(s, op, EN) is None


# ── what the agent and the UI are handed ────────────────────────────────────

def test_mcp_rows_tag_only_the_exceptions(codoc_dir):
    """Presence is the signal: a monolingual tree carries no per-node tags, and a
    bilingual one tags exactly the rows that differ."""
    from codoc.mcp import tools

    cd = str(codoc_dir)
    _mk(codoc_dir, "索引快照差异", "比较两次索引快照，找出新增和删除的代码块。")
    _mk(codoc_dir, "Snapshot diff", "Diffs the index snapshot each pass and reports it.")
    dl.write_config(cd, doc_language="zh-Hans")

    rows = {r["title"]: r for r in tools.read_tree(cd)["features"]}
    assert "lang" not in rows["索引快照差异"]
    assert rows["Snapshot diff"]["lang"] == "en"


def test_sidecar_carries_the_tree_language_and_node_exceptions(codoc_dir):
    """The extension and the hub both render `lang` from this, so it is the contract
    that makes a bilingual tree display correctly in either home."""
    import json

    from codoc.codoc_file.render import write_sidecar

    zh_id = _mk(codoc_dir, "索引快照差异", "比较两次索引快照，找出新增和删除的代码块。")
    en_id = _mk(codoc_dir, "Snapshot diff", "Diffs the index snapshot each pass and reports it.")
    dl.write_config(codoc_dir, doc_language="zh-Hans")

    with open_store(codoc_dir) as s:
        write_sidecar(s, codoc_dir)
    sidecar = json.loads((codoc_dir / "tree.bindings.json").read_text(encoding="utf-8"))

    assert sidecar["doc_language"]["code"] == "zh-Hans"
    assert "lang" not in sidecar["features"][zh_id]
    assert sidecar["features"][en_id]["lang"] == "en"


def test_the_hold_gloss_follows_the_nodes_own_language(codoc_dir):
    """The gloss is a sentence rendered beside the node's prose, so a Chinese default
    captioning an English node in Chinese reads as a rendering bug."""
    from codoc.loop.edits import Directive
    from codoc.loop.phase import _hold_detail

    zh = Feature(title="索引快照差异", description="比较两次索引快照，找出新增和删除的代码块。")
    en = Feature(title="Snapshot diff", description="Diffs the index snapshot each pass.")
    by_id = {zh.id: zh, en.id: en}
    directives = [
        Directive(id="d-1", feature_id=zh.id, kind="amend", handed_off=True),
        Directive(id="d-2", feature_id=en.id, kind="amend", handed_off=True),
    ]
    detail = _hold_detail(directives, by_id, ZH)
    assert detail[zh.id]["intent"] == "更新代码以符合你新的意图"
    assert detail[en.id]["intent"] == "update the code to match your new intent"
