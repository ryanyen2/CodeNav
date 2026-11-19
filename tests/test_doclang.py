"""The doc-language surface: script inference, the lexical helpers built on it,
profile resolution, and the workspace setting.

Each helper is pinned twice: once for a non-Latin script, and once to show that
the English answer, which other parts of the loop are tuned against, holds.
"""
from __future__ import annotations

import json

import pytest

from codoc import doclang as dl
from codoc.workspace import config


# ── script classification ───────────────────────────────────────────────────

def test_script_mix_ignores_punctuation_and_digits():
    """Digits and punctuation belong to every script, so counting them would bias
    every measurement toward SPACED — the exact bias being corrected."""
    assert dl.script_mix("索引 123!!! 快照") == {"logographic": 1.0}


def test_chars_per_word_blends_a_mixed_string():
    """A Chinese description citing an identifier is the normal case, and its
    thresholds should move proportionally rather than snap to one script."""
    assert dl.chars_per_word("Index snapshot diff") == dl.SPACED.chars_per_word
    assert dl.chars_per_word("索引快照差异") == dl.LOGOGRAPHIC.chars_per_word
    mixed = dl.chars_per_word("使用 parse_tree 解析")
    assert dl.LOGOGRAPHIC.chars_per_word < mixed < dl.SPACED.chars_per_word


# ── normalization (the dedup key) ────────────────────────────────────────────

def test_norm_key_is_unchanged_for_ascii():
    assert dl.norm_key("  Index   Snapshot  Diff ") == "index snapshot diff"
    assert dl.norm_key(None) == ""


def test_norm_key_folds_full_width_forms():
    """The load-bearing case. An IME emits full-width punctuation and letters that
    are visually identical to their ASCII twins in the tree, so without NFKC two
    titles a person cannot tell apart become two nodes."""
    assert dl.norm_key("解析（快照）") == dl.norm_key("解析(快照)")
    assert dl.norm_key("Ｐａｒｓｅ") == dl.norm_key("Parse")
    assert dl.norm_key("索引，快照") == dl.norm_key("索引,快照")


def test_norm_key_does_not_fold_simplified_and_traditional():
    """A deliberate non-feature: 简体/繁體 is a localization choice, not a spelling
    variant, so folding them would merge two legitimately distinct trees."""
    assert dl.norm_key("程式碼") != dl.norm_key("代码")


# ── script-scaled budgets ───────────────────────────────────────────────────

def test_char_budget_is_identity_for_latin_and_scales_for_cjk():
    assert dl.char_budget(400, "an English commit subject line") == 400
    assert dl.char_budget(400, "中文提交说明") < 200
    assert dl.char_budget(400, "") == 400        # no letters ⇒ no rescale


def test_char_budget_scales_a_minimum_too():
    """The commit-subject minimum is two words: 12 Latin characters, 4 Han."""
    assert dl.char_budget(12, "fix bug") == 12
    assert dl.char_budget(12, "修复缓存过期竞态") == 4


# ── profiles ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tag,expected", [
    ("zh", "zh-Hans"),          # bare zh → the majority variant
    ("zh-Hans", "zh-Hans"),
    ("ZH_hans_CN", "zh-Hans"),  # over-specific locale, wrong case, underscores
    ("zh-TW", "zh-Hant"),
    ("zh-Hant-HK", "zh-Hant"),
    ("ja", "ja"),
    ("ko", "ko"),
    ("", "en"),
    (None, "en"),
])
def test_resolve_tags(tag, expected):
    assert dl.resolve(tag).code == expected


def test_unknown_tag_resolves_to_a_usable_generic_profile():
    """Refusing an unlisted language would make "supports other languages" false:
    a model writes fine Norwegian from the tag alone."""
    lang = dl.resolve("nb")
    assert lang.code == "nb"
    assert not lang.is_default
    assert lang.script is dl.SPACED
    assert dl.prompt_directive(lang)


def test_generic_profile_knows_thai_is_unspaced():
    assert dl.resolve("th").script is dl.UNSPACED_ALPHA


# ── the prompt directive ────────────────────────────────────────────────────

def test_english_directive_is_empty():
    """So an English repo's prompts stay byte-identical: no cache invalidation and
    no behaviour change from a feature it does not use."""
    assert dl.prompt_directive(dl.resolve("en")) == ""


def test_directive_names_the_language_and_protects_identifiers():
    d = dl.prompt_directive(dl.resolve("zh-Hans"))
    assert "简体中文" in d
    assert "Never translate code" in d
    # The title rule rides the directive. It now also STEERS the shape, because an
    # English modifier stack transliterates into a noun pile no one would say.
    assert "4 to 12 characters" in d
    assert "Not an English-ordered stack of modifiers" in d


def test_code_agent_directive_exempts_the_source_code():
    """The realize prompt's reader writes code, not tree nodes: the tree's language
    must not leak into identifiers and comments."""
    d = dl.prompt_directive(dl.resolve("zh-Hans"), for_code_agent=True)
    assert "Source code is not translated" in d
    assert "codoc_reflect" in d
    # It is not asked to emit JSON, so the JSON rules are absent.
    assert "JSON" not in d


def test_directive_offers_safe_quotation_marks():
    """The no-double-quote rule is a JSON constraint; a language whose quotation
    marks are distinct codepoints can have them."""
    assert "「」" in dl.prompt_directive(dl.resolve("ja"))


# ── the workspace setting ───────────────────────────────────────────────────

@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir(parents=True)
    return d


def test_defaults_to_english_with_no_config(codoc_dir):
    assert dl.workspace_doc_language(codoc_dir).code == "en"
    assert dl.workspace_doc_language(None).code == "en"


def test_reads_the_committed_setting(codoc_dir):
    config.write_config(codoc_dir, doc_language="zh-Hans")
    assert dl.workspace_doc_language(codoc_dir).code == "zh-Hans"


def test_env_var_overrides_the_committed_setting(codoc_dir, monkeypatch):
    config.write_config(codoc_dir, doc_language="zh-Hans")
    monkeypatch.setenv(dl.ENV_VAR, "ja")
    assert dl.workspace_doc_language(codoc_dir).code == "ja"


def test_blank_env_var_does_not_override(codoc_dir, monkeypatch):
    """An exported-but-empty variable is not a choice, and treating it as one would
    silently reset a committed tree to English."""
    config.write_config(codoc_dir, doc_language="zh-Hans")
    monkeypatch.setenv(dl.ENV_VAR, "   ")
    assert dl.workspace_doc_language(codoc_dir).code == "zh-Hans"


def test_write_config_merges_and_stays_readable(codoc_dir):
    config.write_config(codoc_dir, doc_language="zh-Hans")
    config.write_config(codoc_dir, some_other_setting=7)
    raw = config.config_path(codoc_dir).read_text(encoding="utf-8")
    assert json.loads(raw) == {"doc_language": "zh-Hans", "some_other_setting": 7}


def test_corrupt_config_degrades_to_the_default(codoc_dir):
    """Read on the daemon's hot path and from the CC hook, where raising would
    block the author's turn."""
    config.config_path(codoc_dir).write_text("{not json", encoding="utf-8")
    assert config.read_config(codoc_dir) == {}
    assert dl.workspace_doc_language(codoc_dir).code == "en"


def test_chinese_gets_a_register_rule_like_every_other_cjk_language():
    """Japanese names である/だ体 and Korean 해라체; Chinese names a register too.
    With no register instruction a model writes 书面语 for "documentation", and
    the result reads harder than the English it came from."""
    for tag in ("zh-Hans", "zh-Hant"):
        rule = dl.resolve(tag).prose_rule
        assert "白话" in rule or "白話" in rule
        assert "书面语" in rule or "書面語" in rule
        assert "翻译腔" in rule or "翻譯腔" in rule
        # The rules name constructions; a general "write plainly" does not change the output.
        assert "进行" in rule or "進行" in rule          # the light verbs to drop
        assert "被" in rule                              # the passive rule
    # …and the punctuation guidance every CJK language shares is still there.
    assert "。，；：、「」《》" in dl.resolve("zh-Hans").prose_rule
    assert "である" in dl.resolve("ja").prose_rule
