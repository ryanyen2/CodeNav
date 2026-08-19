"""The doc-language surface: script inference, the lexical helpers built on it,
profile resolution, and the workspace setting.

The through-line of these tests is a single claim: **English behaviour must not
move.** Every helper here replaced a Latin-only regex or a hard-coded character
count that other parts of the loop were tuned against, so each one is pinned
twice — once for the new script and once to prove the old answer is unchanged.
"""
from __future__ import annotations

import json

import pytest

from codoc import doclang as dl


# ── script classification ───────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Index snapshot diff", dl.SPACED),
    ("索引快照差异", dl.LOGOGRAPHIC),
    ("インデックス差分", dl.LOGOGRAPHIC),
    ("인덱스 스냅샷", dl.HANGUL),
    ("การจัดทำดัชนี", dl.UNSPACED_ALPHA),
    ("Индекс моментальных снимков", dl.SPACED),
    ("", dl.SPACED),
    ("123 456 !!!", dl.SPACED),      # no letters at all → the safe default
])
def test_dominant_script(text, expected):
    assert dl.dominant_script(text) is expected


def test_hangul_is_not_treated_as_unspaced():
    """Korean puts spaces between words, so it must NOT get n-gram segmentation —
    lumping it in with Han because both are 'CJK' would shred findable words."""
    assert dl.HANGUL.ngram == 0
    assert not dl.has_cjk("인덱스 스냅샷")
    assert dl.terms("인덱스 스냅샷") == {"인덱스", "스냅샷"}


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


# ── segmentation ────────────────────────────────────────────────────────────

def test_terms_matches_the_old_latin_behaviour():
    """`intent._terms` fed a scoring function tuned on these exact outputs:
    camel/snake split, stopwords dropped, nothing shorter than three characters."""
    assert dl.terms("make the OllamaClient retry") == {"ollama", "client", "retry"}
    assert dl.terms("compute_changeset") == {"compute", "changeset"}
    assert dl.terms("the and for with") == set()


def test_terms_is_non_empty_for_cjk():
    """The whole bug: `[^A-Za-z0-9]+` split a Chinese prompt into nothing, so it
    scored zero against every symbol and the author's stated why went unused."""
    got = dl.terms("让 ollama 客户端在超时后重试")
    assert got
    assert "ollama" in got          # the identifier survives untranslated
    assert "客户" in got            # and the prose is segmented, not dropped


def test_terms_keeps_a_latin_identifier_inside_cjk_prose():
    """Prose and identifier must BOTH survive: the lexical heuristics exist to
    bridge authored prose and code symbols, so dropping either side defeats them."""
    got = dl.terms("解析 parse_tree 的输出")
    assert "parse" in got and "tree" in got
    assert any(dl.has_cjk(t) for t in got)


def test_terms_drops_lone_cjk_function_words():
    """A single particle matches nearly every sentence in a tree, so it cannot be
    allowed to act as a discriminator."""
    assert "的" not in dl.terms("解析 的 输出")
    assert "让" not in dl.terms("让 ollama 重试")


def test_tokens_is_more_lenient_than_terms():
    """`tokens` feeds a divergence check where under-counting shared meaning raises
    a false alarm, so it keeps content characters that `terms` drops for precision."""
    assert dl.tokens("添加") > dl.terms("添加")


def test_tokens_matches_the_old_latin_behaviour():
    assert dl.tokens("Add validation") == {"add", "validation"}


# ── script-scaled constants ─────────────────────────────────────────────────

def test_clause_chars_reproduces_the_latin_constant():
    """24 was `apply._MIN_PRESERVED_RUN`. If this drifts, every English amend gate
    silently re-tunes — so it is pinned exactly."""
    assert dl.clause_chars("a plain English description of some feature") == 24


def test_clause_chars_shrinks_for_logographic_text():
    """24 characters of Chinese is a whole sentence, so the old constant scored
    ~0 preserved for every real amend and pushed all of them into review."""
    assert dl.clause_chars("索引快照差异计算与比较") == 8


def test_char_budget_is_identity_for_latin_and_scales_for_cjk():
    assert dl.char_budget(400, "an English commit subject line") == 400
    assert dl.char_budget(400, "中文提交说明") < 200
    assert dl.char_budget(400, "") == 400        # no letters ⇒ no rescale


def test_char_budget_never_collapses_to_nothing():
    assert dl.char_budget(10, "索引快照") >= 40


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


def test_non_english_profiles_use_a_multilingual_embedder():
    """The English default is monolingual, so leaving it in place would make
    semantic title dedup a coin flip rather than a conservative gate."""
    assert dl.resolve("en").embedder == "all-MiniLM-L6-v2"
    for tag in ("zh-Hans", "ja", "ko", "fr"):
        assert "multilingual" in dl.resolve(tag).embedder


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
    assert "4–12 character phrase" in d
    assert "never an English-ordered stack of modifiers" in d


def test_code_agent_directive_exempts_the_source_code():
    """The realize prompt's reader writes code, not tree nodes: the tree's language
    must not leak into identifiers and comments."""
    d = dl.prompt_directive(dl.resolve("zh-Hans"), for_code_agent=True)
    assert "source code is not translated" in d
    assert "codoc_reflect" in d
    # It is not asked to emit JSON, so the JSON-envelope rules are absent.
    assert "JSON envelope" not in d


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
    dl.write_config(codoc_dir, doc_language="zh-Hans")
    assert dl.workspace_doc_language(codoc_dir).code == "zh-Hans"


def test_env_var_overrides_the_committed_setting(codoc_dir, monkeypatch):
    dl.write_config(codoc_dir, doc_language="zh-Hans")
    monkeypatch.setenv(dl.ENV_VAR, "ja")
    assert dl.workspace_doc_language(codoc_dir).code == "ja"


def test_blank_env_var_does_not_override(codoc_dir, monkeypatch):
    """An exported-but-empty variable is not a choice, and treating it as one would
    silently reset a committed tree to English."""
    dl.write_config(codoc_dir, doc_language="zh-Hans")
    monkeypatch.setenv(dl.ENV_VAR, "   ")
    assert dl.workspace_doc_language(codoc_dir).code == "zh-Hans"


def test_write_config_merges_and_stays_readable(codoc_dir):
    dl.write_config(codoc_dir, doc_language="zh-Hans")
    dl.write_config(codoc_dir, some_other_setting=7)
    raw = dl.config_path(codoc_dir).read_text(encoding="utf-8")
    assert json.loads(raw) == {"doc_language": "zh-Hans", "some_other_setting": 7}


def test_corrupt_config_degrades_to_the_default(codoc_dir):
    """Read on the daemon's hot path and from the CC hook, where raising would
    block the author's turn."""
    dl.config_path(codoc_dir).write_text("{not json", encoding="utf-8")
    assert dl.read_config(codoc_dir) == {}
    assert dl.workspace_doc_language(codoc_dir).code == "en"


def test_chinese_gets_a_register_rule_like_every_other_cjk_language():
    """Japanese was told である/だ体 and Korean 해라체; Chinese was told only where to put
    its spaces. With no register instruction a model writes 书面语 for "documentation",
    and the result reads harder than the English it came from."""
    for tag in ("zh-Hans", "zh-Hant"):
        rule = dl.resolve(tag).prose_rule
        assert "白话" in rule or "白話" in rule
        assert "书面语" in rule or "書面語" in rule
        assert "翻译腔" in rule or "翻譯腔" in rule
        # The concrete DON'Ts are what actually move the output — an abstract "write
        # plainly" loses to the specific constraints elsewhere in the prompt.
        assert "进行" in rule or "進行" in rule          # the light verbs to drop
        assert "被" in rule                              # the passive rule
    # …and the punctuation guidance every CJK language shares is still there.
    assert "。，；：、「」《》" in dl.resolve("zh-Hans").prose_rule
    assert "である" in dl.resolve("ja").prose_rule
