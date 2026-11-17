"""Doc language: the natural language codoc authors its prose in.

Not ``codoc/index/lang``, which is the *programming* languages of the code
under analysis. This module is the *authoring* language of the tree: what
titles, descriptions and directives are written in. The two are independent; a
Python repo can have a Mandarin tree.

The doc language is a property of the workspace, kept in the tracked
``.codoc/config.json`` (``doc_language``) so that every contributor's daemon
writes the same language. ``CODOC_DOC_LANGUAGE`` overrides it for one process.

Two halves:

**Profiles** (:class:`DocLanguage`, :func:`resolve`,
:func:`workspace_doc_language`, :func:`prompt_directive`): how to ask a model
to write a language. Configuration-driven.

**Script helpers** (:func:`norm_key`, :func:`char_budget`,
:func:`detect_prose_language`): these read nothing but the text in front of
them. A tree can hold English and Chinese nodes side by side, so one per-repo
setting would be the wrong answer for some of them.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from codoc.workspace.config import read_config

# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------
# A constant written in characters means something different in a script that
# needs two characters to say a word instead of six, and a string's script is
# the first evidence of its language. A script class carries the word length.


@dataclass(frozen=True)
class ScriptClass:
    name: str
    # Mean characters per word, INCLUDING the separator for spaced scripts. This
    # is the number that ports a character budget across scripts.
    chars_per_word: float


# Latin, Cyrillic, Greek, Arabic, Hebrew, Devanagari, and any other script that
# delimits words with whitespace and averages a Latin-ish word length.
SPACED = ScriptClass("spaced", chars_per_word=6.0)
# Han + kana: word boundaries are unmarked and a "word" is one or two characters.
LOGOGRAPHIC = ScriptClass("logographic", chars_per_word=2.0)
# Thai, Khmer, Lao, Myanmar: unspaced like Han, but alphabetic, so a word is
# several characters.
UNSPACED_ALPHA = ScriptClass("unspaced_alpha", chars_per_word=5.0)
# Hangul is spaced, but a syllable block packs what Latin spends 2–3 characters
# on, so it needs its own word length.
HANGUL = ScriptClass("hangul", chars_per_word=3.5)

# Codepoint ranges per non-default script class. Order matters only for reading;
# the ranges are disjoint. Anything unlisted is SPACED, the common case.
_RANGES: tuple[tuple[int, int, ScriptClass], ...] = (
    (0x3040, 0x30FF, LOGOGRAPHIC),    # hiragana + katakana
    (0x31F0, 0x31FF, LOGOGRAPHIC),    # katakana phonetic extensions
    (0x3400, 0x4DBF, LOGOGRAPHIC),    # CJK unified ext A
    (0x4E00, 0x9FFF, LOGOGRAPHIC),    # CJK unified
    (0xF900, 0xFAFF, LOGOGRAPHIC),    # CJK compatibility ideographs
    (0x20000, 0x3134F, LOGOGRAPHIC),  # CJK unified ext B…G
    (0x1100, 0x11FF, HANGUL),         # jamo
    (0x3130, 0x318F, HANGUL),         # compatibility jamo
    (0xA960, 0xA97F, HANGUL),         # jamo extended A
    (0xAC00, 0xD7AF, HANGUL),         # syllables
    (0x0E00, 0x0E7F, UNSPACED_ALPHA),  # thai
    (0x0E80, 0x0EFF, UNSPACED_ALPHA),  # lao
    (0x1000, 0x109F, UNSPACED_ALPHA),  # myanmar
    (0x1780, 0x17FF, UNSPACED_ALPHA),  # khmer
)


def script_of(ch: str) -> ScriptClass:
    """The script class of a single character (SPACED for anything unlisted)."""
    cp = ord(ch)
    for lo, hi, cls in _RANGES:
        if lo <= cp <= hi:
            return cls
    return SPACED


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

    Simplified vs Traditional Han is deliberately NOT folded: 简体/繁體 are a
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
    """A character cap written for Latin prose, rescaled for ``text``'s script.

    Prompt budgets are capped in characters but paid for in tokens, and the two
    diverge by roughly the same factor as word length: 400 characters of English
    is ~100 tokens, 400 characters of Chinese is several times that. Scaling the
    cap down keeps the *information* budget, and the bill, roughly constant
    instead of multiplying prompt size on a CJK repo.
    """
    ratio = chars_per_word(text) / SPACED.chars_per_word
    return max(40, round(base_chars * ratio))


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


_CJK_PUNCT = (
    "Use this language's own punctuation (。，；：、「」《》) rather than ASCII "
    "punctuation. Put a space on each side of Latin text embedded in a sentence "
    "(so `使用 parse_tree 解析`), which is the convention readers expect and "
    "keeps identifiers selectable."
)

# Register. With no instruction, a model asked for "documentation" in Chinese writes
# 书面语, and the result is prose a native reader finds HARDER to read than the English
# it came from: nominalized, 被-heavy, and shaped around English clause order.
#
# The prompt lists concrete DON'Ts because an abstract "write plainly" loses to the specific
# constraints elsewhere in a prompt. Naming the exact constructions (the light verbs, the
# calqued connective frames, the transplanted subordinate stacks) changes the output,
# because each one is a thing the model can check its own sentence against.
_ZH_HANS_REGISTER = (
    " Register: plain explanatory 白话 — the voice of an engineer walking a colleague "
    "through their own code — never formal 书面语, and never 翻译腔. Concretely: lead "
    "with verbs rather than nominalizations (「先解析，再判断」, not "
    "「进行解析之后作出判断」); drop the light verbs 进行/作出/实现 wherever a bare verb "
    "says it; use 被 only when the doer is genuinely unknown (「规则会去掉页眉」, not "
    "「页眉会被规则移除」); never calque English connective frames — 「通过…的方式」"
    "「在…的情况下」「这就是…的原因」 read as translation artifacts, so state the thought "
    "the way Chinese states it (「提交前想知道改动的影响，就用它看一眼」, not "
    "「这就是你在提交之前查看规则变更会带来什么影响的方式」); and break a long English "
    "sentence at its clause boundaries with 。 and ， instead of carrying its whole "
    "subordinate structure into one Chinese sentence. English library and API terms "
    "embedded in the prose stay in English."
)
_ZH_HANT_REGISTER = (
    " Register: plain explanatory 白話 — the voice of an engineer walking a colleague "
    "through their own code — never formal 書面語, and never 翻譯腔. Concretely: lead "
    "with verbs rather than nominalizations (「先解析，再判斷」, not "
    "「進行解析之後作出判斷」); drop the light verbs 進行/作出/實現 wherever a bare verb "
    "says it; use 被 only when the doer is genuinely unknown (「程式會略過空行」, not "
    "「空行會被程式略過」); never calque English connective frames — 「通過…的方式」"
    "「在…的情況下」「這就是…的原因」 read as translation artifacts, so state the thought "
    "the way Chinese states it; and break a long English sentence at its clause "
    "boundaries with 。 and ， instead of carrying its whole subordinate structure into "
    "one sentence. English library and API terms embedded in the prose stay in English."
)

# A title is where the calques show first: an English modifier stack ("transfer-aware
# dedup") transliterates into a noun pile no one would say. Chinese titles are most
# naturally verb-object.
_ZH_TITLE = (
    "a 4–12 character phrase, most naturally verb-object (「读取银行对账单」) or a plain "
    "noun phrase (「脚注识别」) — never an English-ordered stack of modifiers "
    "(not 「转账感知去重」; say 「去重时排除转账」), no trailing punctuation"
)
_ZH_HANT_TITLE = (
    "a 4–12 character phrase, most naturally verb-object (「讀取銀行對帳單」) or a plain "
    "noun phrase (「腳註識別」) — never an English-ordered stack of modifiers "
    "(not 「轉帳感知去重」; say 「去重時排除轉帳」), no trailing punctuation"
)

_PROFILES: dict[str, DocLanguage] = {
    "en": DocLanguage(
        code="en", name="English", script=SPACED,
        title_rule="a 3–6 word sentence-case noun phrase",
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
        title_rule="a 4–16 character noun phrase (体言止め), no trailing punctuation",
        prose_rule=_CJK_PUNCT + " Write descriptions in である/だ体, not です・ます体.",
    ),
    "ko": DocLanguage(
        code="ko", name="Korean / 한국어", script=HANGUL,
        title_rule="a 2–5 word noun phrase, no trailing punctuation",
        prose_rule="Write descriptions in the plain declarative (해라체/-다) form.",
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

    Unknown tags resolve rather than raise, because refusing an unlisted language
    would make "supports other languages" false in practice: a model writes
    perfectly good Norwegian from the tag alone, and there is nothing codoc needs
    to know about Norwegian that the generic spaced-script rules don't cover.
    """
    key = code.strip().casefold()
    name = _GENERIC_NAMES.get(key) or _GENERIC_NAMES.get(key.split("-")[0]) or code
    script = UNSPACED_ALPHA if key.split("-")[0] in ("th", "km", "lo", "my") else SPACED
    return DocLanguage(
        code=code, name=name, script=script,
        title_rule=("a short noun phrase of about 3–6 words"
                    if script is SPACED else
                    "a short noun phrase, no trailing punctuation"),
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
# The workspace setting says what language codoc AUTHORS in. It does not say what
# language the tree IS in, because an author is allowed to be inconsistent on
# purpose: describing intent in Chinese while a revision they typed themselves is
# in English, and reaching for the English term whenever that is the word people
# actually use. Both are ordinary technical writing, not drift to be corrected.
#
# So the setting governs prose codoc *originates* (a new node, a fresh
# description), and everything that touches prose already on the page follows the
# page. That is why this is detection rather than a stored per-feature column: a
# column records what the language was when someone last set it, goes stale the
# moment the author rewrites the node in the other language, and needs a migration
# to add. Detection is always current and costs a codepoint scan.

# Spans that stay in the code's language no matter what the prose is: inline code,
# markdown link labels + targets, and bare `codoc:` refs. Stripped before judging
# what language a description is in, because a well-cited Chinese description carries two
# long identifiers against a dozen Han characters, so measuring the raw string
# would call the correct answer wrong.
_CODEISH = re.compile(r"`[^`]*`|\[[^\]]*\]\([^)]*\)|\bcodoc:[^\s)]+|https?://\S+")

#: Share of a script's letters needed to call prose "in" that script.
SCRIPT_FLOOR = 0.30

#: The floor for a script that is not spaced, far lower on purpose. Borrowing runs
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


# Kana and Hangul are DECISIVE where Han is not: Han is shared by Chinese and
# Japanese, but only Japanese uses kana and only Korean uses Hangul. So a script
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

    Empty for English by design (see :attr:`DocLanguage.is_default`).

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

    if for_code_agent:
        opening = (
            f"The intent text below is written in **{lang.name}** — read it as the "
            f"authoritative statement of what to build. Anything you write BACK to "
            f"the feature tree (a `codoc_reflect` title, description, or rationale) "
            f"must also be in {lang.name}."
        )
    else:
        opening = (
            f"This tree is authored in **{lang.name}**. The instructions and examples "
            f"in this prompt are in English for your benefit only — never copy their "
            f"language."
        )

    bullets = [
        "**Never translate code.** Identifiers, symbol paths, file paths, type "
        "names, `codoc:` link targets, and anything inside backticks stay EXACTLY "
        "as they appear in the code, in their original language. An inline "
        "citation must be copied verbatim — a translated symbol names a binding "
        "that does not exist.",
    ]
    if for_code_agent:
        bullets.append(
            "**The source code is not translated either.** Write identifiers, "
            "comments, and docstrings in whatever language the surrounding files "
            "already use — read a neighbouring file and match it. The tree's "
            "language says nothing about the code's."
        )
    else:
        bullets += [
            f"**Prose you originate goes in {lang.name}** — a new node's title and "
            f"description, and any rationale.",
            "**Prose you EDIT stays in the language it is already written in.** When "
            "you amend a description, match the language of the text you are "
            "amending, even where that is not the tree's language: an author who "
            "wrote that node in another language chose to, and an amend that "
            "translates it is an unrequested rewrite of their words. Judge from the "
            "existing `description` in this prompt, not from this instruction.",
            "**Mixing is normal, not an error.** Technical terms, library and API "
            "names, and established jargon belong in whatever form readers of this "
            f"code actually use — usually the original English. {lang.name} prose "
            "carrying English terms is correct writing; do not translate the terms "
            "to make a description look monolingual, and do not switch the whole "
            "sentence to English because it contains one.",
            f"A title is {lang.title_rule} when you are writing a new one.",
            "Field names, enum values, and every other part of the JSON envelope "
            "stay in English. Only the human-readable values change language.",
        ]
    if lang.prose_rule:
        bullets.append(lang.prose_rule)
    if not for_code_agent:
        bullets.append(
            "Do not include a double-quote character (`\"`) in any value — these "
            "are JSON strings. Where the language wants quotation marks, use the "
            "language's own (「」, 《》, «», „“), which are safe."
        )

    return "\n".join([
        "## Authoring language (overrides any language shown in examples)",
        "",
        opening,
        "",
        *(f"- {b}" for b in bullets),
    ])
