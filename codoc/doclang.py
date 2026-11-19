"""The tree's authoring language: language profiles, and text helpers that work in any script.

Not ``codoc/index/lang``, which holds the programming languages of the code.
This module is about the language titles, descriptions and directives are
written in. The two are independent: a Python repository can have a Mandarin
tree.

**Profiles** (:class:`DocLanguage`, :func:`resolve`,
:func:`workspace_doc_language`, :func:`prompt_directive`): which language the
workspace writes in, and how a prompt asks a model to write it. The setting is
``doc_language`` in the tracked ``.codoc/config.json``, so every contributor's
daemon writes the same language; ``CODOC_DOC_LANGUAGE`` overrides it for one
process.

**Script helpers** (:func:`norm_key`, :func:`char_budget`,
:func:`detect_prose_language`): these read only the text they are given. A
tree can hold English and Chinese nodes side by side, so one per-repository
setting would be wrong for some of them.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from codoc.calculus.scripts import HANGUL, LOGOGRAPHIC, SPACED, UNSPACED_ALPHA, ScriptClass, script_of
from codoc.workspace.config import read_config

# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------
# The table lives in calculus, which `ingest` also cuts text by.


def script_mix(text: str) -> dict[str, float]:
    """Fraction of the *letters* in ``text`` belonging to each script class.

    Digits, punctuation, and whitespace are excluded: they appear in every script
    and would dilute the signal toward SPACED.
    """
    counts: dict[str, int] = {}
    total = 0
    for ch in text or "":
        if not ch.isalpha():
            continue
        name = script_of(ch).name
        counts[name] = counts.get(name, 0) + 1
        total += 1
    if not total:
        return {}
    return {k: v / total for k, v in counts.items()}


_BY_NAME = {c.name: c for c in (SPACED, LOGOGRAPHIC, UNSPACED_ALPHA, HANGUL)}


def chars_per_word(text: str) -> float:
    """Mean characters per word for ``text``, blended across its scripts.

    Blended rather than winner-take-all because the mixed case is the normal one:
    a Chinese description citing ``parse_tree`` is most of the way to LOGOGRAPHIC
    but not all of it, and a threshold derived from this number should move
    proportionally rather than snap.
    """
    mix = script_mix(text)
    if not mix:
        return SPACED.chars_per_word
    return sum(_BY_NAME[name].chars_per_word * frac for name, frac in mix.items())


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def norm_key(text: str | None) -> str:
    """The identity key for a title: NFKC + casefold + collapsed whitespace.

    What it folds together is what codoc considers "the same title". NFKC
    matters for CJK input: an IME emits full-width forms (``，`` ``（`` ``ａ``)
    that look identical to their ASCII twins in the tree, so without it two
    titles a person cannot tell apart count as different.

    ``casefold`` rather than ``lower`` because it is the correct case-insensitive
    fold for non-English text (German ``ß``, Greek final sigma) and identical to
    ``lower`` for ASCII.

    Simplified and Traditional Han are not folded: 简体/繁體 are a
    localization choice, not a spelling variant, and a repo that authors in one
    never sees the other. Folding them would need a conversion table and would
    merge two legitimately distinct trees in a repo that hosts both.
    """
    s = unicodedata.normalize("NFKC", (text or "").strip())
    return re.sub(r"\s+", " ", s).casefold()


# ---------------------------------------------------------------------------
# Script-scaled budgets
# ---------------------------------------------------------------------------

def char_budget(base_chars: int, text: str) -> int:
    """A character count written for Latin prose, rescaled for ``text``'s script.

    Prompt budgets are capped in characters but paid for in tokens, and the two
    diverge by roughly the same factor as word length: 400 characters of English
    is ~100 tokens, 400 characters of Chinese is several times that. Scaling the
    cap down keeps the information, and the bill, roughly constant. A minimum
    scales the same way: two English words are 12 characters, two Chinese
    words 4.
    """
    return round(base_chars * chars_per_word(text) / SPACED.chars_per_word)


# ---------------------------------------------------------------------------
# Language profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocLanguage:
    """How to ask a model to write this language, and what to expect back."""

    code: str            # BCP-47-ish tag as configured: "en", "zh-Hans", "ja"…
    name: str            # what the prompt calls it, endonym included
    script: ScriptClass
    title_rule: str      # the shape of a good title, in this language's units
    prose_rule: str      # register + punctuation notes; "" when nothing to add

    @property
    def is_default(self) -> bool:
        """English, the language every prompt is written in. The directive is
        empty for it, so an English repo's prompts carry no extra section."""
        return self.code == "en"


# ``prose_rule`` is a list of ``- `` lines: the directive below appends them to its
# own bullets, and translate.txt puts them under its Register heading.
_CJK_PUNCT = (
    "- Use the language's own punctuation (。，；：、「」《》), not ASCII.\n"
    "- Put a space on each side of Latin text inside a sentence: `使用 parse_tree 解析`."
)

# Register. Asked for "documentation" in Chinese with nothing else, a model writes
# 书面语: nominalized, 被-heavy, in English clause order, and harder to read than the
# English it came from. Each line names one construction the model can check its own
# sentence against; a general "write plainly" does not change the output.
_ZH_HANS_REGISTER = (
    "\n- Plain spoken 白话, as an engineer explains their own code to a colleague. Not 书面语, not 翻译腔."
    "\n- Lead with the verb: 「先解析，再判断」, not 「进行解析之后作出判断」."
    "\n- Drop 进行、作出、实现 where a bare verb says it."
    "\n- Use 被 only when the doer is unknown: 「规则会去掉页眉」, not 「页眉会被规则移除」."
    "\n- No English connective frames (「通过…的方式」「在…的情况下」「这就是…的原因」): "
    "「提交前想知道改动的影响，就用它看一眼」, not 「这就是你在提交之前查看规则变更会带来什么影响的方式」."
    "\n- Break a long English sentence at its clauses, with 。 and ，."
)
_ZH_HANT_REGISTER = (
    "\n- Plain spoken 白話, as an engineer explains their own code to a colleague. Not 書面語, not 翻譯腔."
    "\n- Lead with the verb: 「先解析，再判斷」, not 「進行解析之後作出判斷」."
    "\n- Drop 進行、作出、實現 where a bare verb says it."
    "\n- Use 被 only when the doer is unknown: 「程式會略過空行」, not 「空行會被程式略過」."
    "\n- No English connective frames (「透過…的方式」「在…的情況下」「這就是…的原因」)."
    "\n- Break a long English sentence at its clauses, with 。 and ，."
)

# Calques show first in titles: an English modifier stack ("transfer-aware dedup")
# becomes a noun pile no one would say. Chinese titles are most naturally verb-object.
_ZH_TITLE = (
    "a phrase of 4 to 12 characters, verb-object (「读取银行对账单」) or a plain noun phrase "
    "(「脚注识别」), with no trailing punctuation. Not an English-ordered stack of modifiers: "
    "「去重时排除转账」, not 「转账感知去重」"
)
_ZH_HANT_TITLE = (
    "a phrase of 4 to 12 characters, verb-object (「讀取銀行對帳單」) or a plain noun phrase "
    "(「腳註識別」), with no trailing punctuation. Not an English-ordered stack of modifiers: "
    "「去重時排除轉帳」, not 「轉帳感知去重」"
)

_PROFILES: dict[str, DocLanguage] = {
    "en": DocLanguage(
        code="en", name="English", script=SPACED,
        title_rule="a sentence-case noun phrase of 3 to 6 words",
        prose_rule="",
    ),
    "zh-hans": DocLanguage(
        code="zh-Hans", name="Simplified Chinese / 简体中文", script=LOGOGRAPHIC,
        title_rule=_ZH_TITLE,
        prose_rule=_CJK_PUNCT + _ZH_HANS_REGISTER,
    ),
    "zh-hant": DocLanguage(
        code="zh-Hant", name="Traditional Chinese / 繁體中文", script=LOGOGRAPHIC,
        title_rule=_ZH_HANT_TITLE,
        prose_rule=_CJK_PUNCT + _ZH_HANT_REGISTER,
    ),
    "ja": DocLanguage(
        code="ja", name="Japanese / 日本語", script=LOGOGRAPHIC,
        title_rule="a noun phrase of 4 to 16 characters (体言止め), with no trailing punctuation",
        prose_rule=_CJK_PUNCT + "\n- Write である/だ体, not です・ます体.",
    ),
    "ko": DocLanguage(
        code="ko", name="Korean / 한국어", script=HANGUL,
        title_rule="a noun phrase of 2 to 5 words, with no trailing punctuation",
        prose_rule="- Write the plain declarative form (해라체, -다).",
    ),
}

# Display names for tags with no bespoke profile. These are spaced scripts whose
# title/prose rules are the English ones, so the generic profile fits them and the
# table only has to supply a name the model recognizes.
_GENERIC_NAMES: dict[str, str] = {
    "ar": "Arabic / العربية", "cs": "Czech / Čeština", "de": "German / Deutsch",
    "es": "Spanish / Español", "fi": "Finnish / Suomi", "fr": "French / Français",
    "he": "Hebrew / עברית", "hi": "Hindi / हिन्दी", "id": "Indonesian",
    "it": "Italian / Italiano", "nl": "Dutch / Nederlands", "pl": "Polish / Polski",
    "pt": "Portuguese / Português", "pt-br": "Brazilian Portuguese / Português do Brasil",
    "ru": "Russian / Русский", "sv": "Swedish / Svenska", "th": "Thai / ไทย",
    "tr": "Turkish / Türkçe", "uk": "Ukrainian / Українська", "vi": "Vietnamese / Tiếng Việt",
}

#: Env var that overrides the workspace setting (a one-off run in another
#: language, or a CI job that wants English output).
ENV_VAR = "CODOC_DOC_LANGUAGE"


def _generic(code: str) -> DocLanguage:
    """A usable profile for any tag without a bespoke one.

    Unknown tags resolve rather than raise: a model writes Norwegian well from
    the tag alone, and there is nothing codoc needs to know about Norwegian that
    the generic spaced-script rules don't cover.
    """
    key = code.strip().casefold()
    name = _GENERIC_NAMES.get(key) or _GENERIC_NAMES.get(key.split("-")[0]) or code
    script = UNSPACED_ALPHA if key.split("-")[0] in ("th", "km", "lo", "my") else SPACED
    return DocLanguage(
        code=code, name=name, script=script,
        title_rule=("a noun phrase of 3 to 6 words"
                    if script is SPACED else
                    "a short noun phrase, with no trailing punctuation"),
        prose_rule="",
    )


# A BCP 47 tag: subtags of letters or digits, 1–8 characters, joined by hyphens.
# The tree's language is written into a tracked file every clone reads, so the
# hub takes a tag and not a string.
_TAG = re.compile(r"^[A-Za-z0-9]{1,8}(-[A-Za-z0-9]{1,8}){0,4}$")


def is_language_tag(code: str | None) -> bool:
    return bool(_TAG.match((code or "").strip()))


def resolve(code: str | None) -> DocLanguage:
    """The profile for a language tag. Blank/unknown-shaped input → English.

    Tag matching is case-insensitive and falls back from ``zh-Hans-CN`` to
    ``zh-Hans`` to ``zh``, so an over-specific locale from an OS or editor
    setting still lands on the right profile.
    """
    raw = (code or "").strip()
    if not raw:
        return _PROFILES["en"]
    key = raw.casefold().replace("_", "-")
    if key in _PROFILES:
        return _PROFILES[key]
    # zh-Hans-CN → zh-hans; also maps bare "zh" to Simplified, the majority case.
    parts = key.split("-")
    for n in range(len(parts) - 1, 0, -1):
        if "-".join(parts[:n]) in _PROFILES:
            return _PROFILES["-".join(parts[:n])]
    if parts[0] == "zh":
        return _PROFILES["zh-hant"] if any(
            p in ("hant", "tw", "hk", "mo") for p in parts) else _PROFILES["zh-hans"]
    return _generic(raw)


def workspace_doc_language(codoc_dir: str | Path | None) -> DocLanguage:
    """The doc language in force: env var > workspace config > English.

    The env var is checked first, so a one-off run can override the committed
    workspace setting: an operator who exports the var means it for this run.
    A stale export left in the shell overrides the setting too.
    """
    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        return resolve(env)
    if codoc_dir is None:
        return _PROFILES["en"]
    return resolve(read_config(codoc_dir).get("doc_language"))


# ---------------------------------------------------------------------------
# Per-node language: observed, never imposed
# ---------------------------------------------------------------------------
# The workspace setting says what language codoc writes new prose in. It does not
# say what language the tree is in, because an author is allowed to be
# inconsistent on purpose: describing intent in Chinese while a revision they typed themselves is
# in English, and reaching for the English term whenever that is the word people
# actually use. Both are ordinary technical writing, not drift to be corrected.
#
# So the setting governs prose codoc originates (a new node, a fresh
# description), and everything that touches prose already on the page follows the
# page. That is why this is detection rather than a stored per-feature column: a
# column records what the language was when someone last set it, goes stale the
# moment the author rewrites the node in the other language, and needs a migration
# to add. Detection is always current and costs a codepoint scan.

# Spans that stay in the code's language no matter what the prose is: inline code,
# markdown link labels + targets, and bare `codoc:` refs. Stripped before judging
# what language a description is in, because a well-cited Chinese description carries two
# long identifiers against a dozen Han characters, and the raw string would
# measure as English.
_CODEISH = re.compile(r"`[^`]*`|\[[^\]]*\]\([^)]*\)|\bcodoc:[^\s)]+|https?://\S+")

#: Share of a script's letters needed to call prose "in" that script.
SCRIPT_FLOOR = 0.30

#: The floor for a script that is not spaced, much lower. Borrowing runs
#: one way: Chinese technical prose is full of English terms, while English
#: technical prose almost never reaches for 使用 or 。. And the two sides are not
#: measured on the same scale: a Han content word is one or two
#: characters where the English term beside it is ten, so character share
#: systematically overweights the Latin. "使用 tree-sitter 解析 Python 与
#: TypeScript。" is 16% Han characters and unmistakably a Chinese sentence; a
#: symmetric floor would call it English.
UNSPACED_FLOOR = 0.10
#: …but not from a single stray character: an English description citing one
#: product name in Han is still English.
_MIN_UNSPACED_LETTERS = 2


def strip_code_spans(text: str) -> str:
    """``text`` with the spans that are always code blanked out."""
    return _CODEISH.sub(" ", text or "")


def prose_script(text: str) -> ScriptClass | None:
    """The script ``text``'s *prose* is written in, or None when there is no signal.

    None means "draw no conclusion", as for an empty description or a run of digits.
    Callers must treat it as "keep whatever you would have done anyway".

    A non-spaced script wins on a much smaller share than a spaced one (see
    :data:`UNSPACED_FLOOR`), because a mixed CJK/Latin string is nearly always CJK
    prose quoting Latin terms rather than the reverse.
    """
    stripped = strip_code_spans(text)
    mix = script_mix(stripped)
    if not mix:
        return None
    letters = sum(1 for ch in stripped if ch.isalpha())
    for name, frac in sorted(mix.items(), key=lambda kv: -kv[1]):
        script = _BY_NAME[name]
        if script is SPACED:
            continue
        if frac >= UNSPACED_FLOOR and frac * letters >= _MIN_UNSPACED_LETTERS:
            return script
    name, frac = max(mix.items(), key=lambda kv: kv[1])
    return _BY_NAME[name] if frac >= SCRIPT_FLOOR else None


# Kana and Hangul name a language where Han does not: Han is shared by Chinese
# and Japanese, but only Japanese uses kana and only Korean uses Hangul. So a script
# class of LOGOGRAPHIC cannot name a language on its own, while these two can.
_KANA = ((0x3040, 0x30FF), (0x31F0, 0x31FF))


def _decisive_tag(text: str) -> str | None:
    """A language tag the script alone proves, or None (as for bare Han)."""
    for ch in text or "":
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _KANA):
            return "ja"
        if script_of(ch) is HANGUL:
            return "ko"
    return None


def prose_letters(text: str) -> int:
    """How many letters of real prose ``text`` carries, code spans excluded.

    Callers use it as an evidence threshold. A title that is nothing but an
    identifier, or a two-word fragment, cannot support a claim about what language
    it is in, and a check that makes one anyway will often be wrong.
    """
    return sum(1 for ch in strip_code_spans(text) if ch.isalpha())


def detect_prose_language(text: str, default: DocLanguage) -> DocLanguage:
    """The language ``text`` appears to be written in, falling back to ``default``.

    Resolution is by *script*, so it can tell Chinese from English but not French
    from Spanish. It returns ``default`` rather than guessing whenever the
    scripts agree, which is the case where a model reading the surrounding
    prose does better than a codepoint scan. Within one script, "match the writing
    you are editing" is the prompt's job, not this function's.
    """
    script = prose_script(text)
    if script is None:
        return default
    # Kana / Hangul name a language outright, and must be checked even when the
    # script class agrees with the default: Han is shared, so a Japanese sentence in
    # a Chinese tree classifies as LOGOGRAPHIC and would otherwise pass as Chinese.
    decisive = _decisive_tag(strip_code_spans(text))
    if decisive:
        return _PROFILES[decisive]
    if script is default.script:
        return default
    # A different script than the default. Prefer a profile whose own script
    # matches, so a Latin revision inside a Chinese tree resolves to English rather
    # than to some other spaced language the repo never mentioned.
    if script is SPACED:
        return _PROFILES["en"]
    for lang in _PROFILES.values():
        if lang.script is script:
            return lang
    return default


# ---------------------------------------------------------------------------
# The prompt directive
# ---------------------------------------------------------------------------

def prompt_directive(lang: DocLanguage, *, for_code_agent: bool = False) -> str:
    """The block that tells a model which language to author in.

    Empty for English (see :attr:`DocLanguage.is_default`).

    The identifier rule matters most. A model asked to write Chinese
    documentation will translate ``parse_tree`` to ``解析树`` inside a
    ``codoc:`` link, and the binding it names stops resolving. A translated
    tree that has lost its attachment to the code is worse than an English
    one. Prose is translated; anything that is an address is not.

    ``for_code_agent`` is for the realize prompt, whose reader writes source
    files rather than tree nodes. It needs the opposite default: the tree is in
    the doc language, but the code it is about to write is not automatically in
    it: identifier and comment language is a property of the repo it is editing.
    """
    if lang.is_default:
        return ""
    no_code = ("- Never translate code. Identifiers, symbol paths, file paths, type names, `codoc:` link "
               "targets and anything in backticks stay exactly as in the code. A translated symbol names "
               "a binding that does not exist.")
    if for_code_agent:
        lines = [
            f"The intent below is in {lang.name}. What you write back to the tree (a `codoc_reflect` "
            f"title, description or rationale) is in {lang.name} too.",
            "",
            no_code,
            "- Source code is not translated either. Identifiers, comments and docstrings follow the "
            "language the neighbouring files use.",
        ]
    else:
        lines = [
            f"Write in {lang.name}. This prompt's instructions and examples are in English; do not "
            "copy their language.",
            "",
            no_code,
            f"- New prose is in {lang.name}: a new node's title and description, and a rationale.",
            f"- An amend keeps the language of the text it changes, even when that is not {lang.name}. "
            "Read it from the existing description.",
            "- Technical terms, library and API names stay in the form readers of this code use, usually "
            "English. Do not translate them, and do not switch a sentence to English because it holds one.",
            f"- A new title is {lang.title_rule}.",
            "- JSON field names and enum values stay in English.",
        ]
    if lang.prose_rule:
        lines.append(lang.prose_rule)
    if not for_code_agent:
        lines.append("- No double-quote character (`\"`) in a value. Where the language wants quotation "
                     "marks, use its own (「」, 《》, «», „“).")
    return "\n".join(["## Authoring language", "", *lines])
