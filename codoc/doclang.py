"""Doc language — the natural language codoc AUTHORS its prose in.

Two different things share the word "language" in this repo, and confusing them
will waste an afternoon:

* ``codoc/lang/`` — **programming** languages (tree-sitter adapters). What the
  code under analysis is written in.
* this module — the **authoring** language of the feature tree: what the titles,
  descriptions, and realize directives are written in. Orthogonal to the above;
  a Python repo can have a Mandarin tree.

A repo's doc language is a durable property of the *workspace*, not a preference
of whoever's shell happens to launch the daemon. If it lived only in an env var,
a contributor running ``codoc watch`` without it would start appending English
descriptions into a Chinese tree — silent corruption of authored intent, which is
the one thing codoc is supposed to protect. So it is persisted in
``.codoc/config.json`` (tracked in git, unlike the rest of ``.codoc/``) and an
env var only *overrides* it.

Two halves, deliberately kept apart:

**The profile half** (:class:`DocLanguage`, :func:`resolve`,
:func:`workspace_doc_language`) — cold paths only: assembling prompts, choosing
the embedder, reporting state to the IDE/MCP. Configuration-driven.

**The script half** (:func:`norm_key`, :func:`terms`, :func:`tokens`,
:func:`clause_chars`, :func:`char_budget`) — hot paths, and deliberately
*configuration-free*: each function infers what it needs from the text in front
of it. That is not laziness about plumbing. A tree mid-migration holds English
and Chinese nodes side by side, so a single per-repo setting would be the wrong
answer for half of them; and inferring per-string means the lexical heuristics
improve on a Chinese tree nobody remembered to configure. It also keeps
``norm_key`` free of file IO, which matters because it runs per feature per pass.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from codoc.loop.filenames import CONFIG_FILENAME

# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------
# Every lexical heuristic in the loop was written for a script that puts spaces
# between words and needs ~6 characters to say one. Both assumptions are local to
# Latin. What the heuristics actually want to know is two things — "can I find
# word boundaries?" and "how much text is one word?" — so that is what a script
# class carries, rather than a language name.


@dataclass(frozen=True)
class ScriptClass:
    name: str
    # Character n-gram width for segmentation, or 0 when whitespace/punctuation
    # already marks word boundaries.
    ngram: int
    # Mean characters per word, INCLUDING the separator for spaced scripts. This
    # is the number that ports a "how long is a clause" or "how big is a prompt
    # budget" constant across scripts.
    chars_per_word: float


# Latin, Cyrillic, Greek, Arabic, Hebrew, Devanagari … — anything that delimits
# words with whitespace and averages a Latin-ish word length.
SPACED = ScriptClass("spaced", ngram=0, chars_per_word=6.0)
# Han + kana: word boundaries are unmarked and a "word" is one or two characters.
LOGOGRAPHIC = ScriptClass("logographic", ngram=2, chars_per_word=2.0)
# Thai, Khmer, Lao, Myanmar: unspaced like Han, but alphabetic — a word is
# several characters, so bigrams would shred it. Wider n-grams behave.
UNSPACED_ALPHA = ScriptClass("unspaced_alpha", ngram=4, chars_per_word=5.0)
# Hangul is spaced (word boundaries are findable) but a syllable block packs what
# Latin spends 2–3 characters on, so it needs its own word length.
HANGUL = ScriptClass("hangul", ngram=0, chars_per_word=3.5)

# Codepoint ranges per non-default script class. Order matters only for reading;
# the ranges are disjoint. Anything unlisted is SPACED, which is both the common
# case and the safe default (it reproduces today's behavior exactly).
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
    and would dilute the signal toward SPACED, which is exactly the bias that
    made the Latin-shaped heuristics misread CJK prose in the first place.
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


def dominant_script(text: str) -> ScriptClass:
    """The script class most of ``text``'s letters are in; SPACED when empty.

    Used where one answer is needed for the whole string (which segmenter to
    run). Where a *blended* answer is meaningful, :func:`chars_per_word` weights
    the mix instead of picking a winner.
    """
    mix = script_mix(text)
    if not mix:
        return SPACED
    return _BY_NAME[max(mix.items(), key=lambda kv: kv[1])[0]]


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


def has_cjk(text: str) -> bool:
    """Whether ``text`` contains any character from an unspaced script.

    The name says CJK because that is what callers mean; Hangul is excluded
    precisely because it *is* space-delimited and needs no special segmentation.
    """
    return any(script_of(ch) in (LOGOGRAPHIC, UNSPACED_ALPHA)
               for ch in text or "" if ch.isalpha())


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def norm_key(text: str | None) -> str:
    """The identity key for a title: NFKC + casefold + collapsed whitespace.

    The two dedup gates (``loop_a``/``loop_b`` ``_norm_title``) key on this, so
    what it folds together is what codoc considers "the same title". NFKC is the
    load-bearing addition for CJK input: an IME emits full-width forms (``，``
    ``（`` ``ａ``) that are visually identical to their ASCII twins in the tree,
    so without NFKC two titles a person cannot tell apart get two nodes — the
    duplicate-node failure the single-LLM-pass design exists to prevent, arriving
    through the keyboard instead of the model.

    ``casefold`` rather than ``lower`` because it is the correct case-insensitive
    fold for non-English text (German ``ß``, Greek final sigma) and identical to
    ``lower`` for ASCII, so no existing key changes.

    Simplified vs Traditional Han is deliberately NOT folded: 简体/繁體 are a
    localization choice, not a spelling variant, and a repo that authors in one
    never sees the other. Folding them would need a conversion table and would
    silently merge two legitimately distinct trees in a repo that hosts both.
    """
    s = unicodedata.normalize("NFKC", (text or "").strip())
    return re.sub(r"\s+", " ", s).casefold()


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------
# ``\w`` is Unicode-aware in Python 3, so ``[^\W_]`` is "letter or digit, not
# underscore" in every script. This replaces the old ``[A-Za-z0-9]`` /
# ``[a-z0-9]`` classes, which silently dropped every non-ASCII character — the
# bug that made a Chinese prompt produce zero terms.
_WORDISH = re.compile(r"[^\W_]+", re.UNICODE)

# Words too common to signal that a prompt is about a particular change. Moved
# here from ``loop.intent`` so both lexical consumers share one list.
_STOP_SPACED = frozenset("""
the a an and or but if then this that these those with without for from into
you your we our it its is are was were be been being do does did done make
made add adds added fix fixes fixed use uses used can could should would will
please just also now new old code file files test tests run running why what
how when where which who all any some more most other another
""".split())

# The logographic equivalent: characters that carry grammar rather than content
# (particles, copulas, common prepositions and demonstratives). They matter more
# here than English stopwords do, because in unigram mode a single such character
# matches almost any sentence.
_STOP_LOGOGRAPHIC = frozenset(
    # particles, copulas, prepositions, demonstratives, pronouns, modals
    "的了是在和与对及或把被就都也很更中上下不无有为以之其这那此個个我你他她它們们"
    "並并於于從从會会能將将可要還还只再又"
    # light verbs — the counterparts of the make/add/use/fix/run the Latin list
    # drops. As a lone character these state that something was done, not what.
    "让使令用做加改修跑"
)


def _ngrams(run: str, n: int) -> set[str]:
    if len(run) <= n:
        return {run} if run else set()
    return {run[i:i + n] for i in range(len(run) - n + 1)}


def _segment(text: str, *, unigrams: bool) -> set[str]:
    """Split ``text`` into comparable units, per-run by script.

    Per *run* and not per string: a Chinese description citing ``parse_tree``
    must yield both Chinese n-grams and the Latin identifier, since the whole
    point of the lexical heuristics is to bridge authored prose and code symbols.
    Splitting on the dominant script alone would throw one side away.
    """
    out: set[str] = set()
    for word in _WORDISH.findall(text or ""):
        # A "word" here may be a mixed run (``解析parse_tree``); walk it and
        # group maximal same-script stretches.
        start = 0
        cur = script_of(word[0])
        for i in range(1, len(word) + 1):
            nxt = script_of(word[i]) if i < len(word) else None
            if nxt is cur and i < len(word):
                continue
            run = word[start:i]
            if cur.ngram:
                out |= _ngrams(run, cur.ngram)
                if unigrams:
                    out |= {c for c in run if c not in _STOP_LOGOGRAPHIC}
            else:
                out.add(run)
            start, cur = i, nxt
    return out


def terms(text: str) -> set[str]:
    """Content terms of a prompt, title, or symbol path — for *matching*.

    camelCase and snake_case are split first: symbols are the bridge between a
    prompt and a change, and an author who wrote "make the ollama client retry"
    shares ``ollama`` and ``client`` with ``OllamaClient.complete`` only once both
    sides are broken into words.

    Unspaced scripts contribute n-grams but NOT single characters, because this
    set is used for precision (does this prompt name this symbol?) and one Han
    character matches nearly everything. Compare :func:`tokens`.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text or "")
    raw = _segment(spaced, unigrams=False)
    out: set[str] = set()
    for t in raw:
        low = t.casefold()
        if has_cjk(t):
            # n-grams are already short by construction, so no length floor — but
            # a one-character run reaches here whole (there is no bigram to take),
            # and a lone particle would match every sentence in the tree.
            if len(t) > 1 or t not in _STOP_LOGOGRAPHIC:
                out.add(t)
        elif len(low) >= _min_term_chars(t) and low not in _STOP_SPACED:
            out.add(low)
    return out


def _min_term_chars(token: str) -> int:
    """Shortest token worth matching on, in ``token``'s script.

    The original floor — longer than two characters — is "more than half a Latin
    word", and it was doing two jobs: dropping abbreviations too generic to
    identify anything, and dropping fragments. Both are proportional to word
    length, so a Hangul syllable block (which packs what Latin spends 2–3
    characters on) needs a lower one, or every two-syllable Korean word — most of
    them — is discarded as too short.
    """
    return 3 if dominant_script(token) is SPACED else 2


def tokens(text: str) -> set[str]:
    """A lenient token set for similarity scoring — for *overlap*.

    Deliberately more generous than :func:`terms`: the consumer
    (``divergence.text_overlap``) uses a low overlap score to *flag* a
    realization as divergent, so under-counting shared meaning raises a false
    alarm on the author's screen. Unspaced runs therefore contribute both
    n-grams and content characters, which keeps a reworded-but-equivalent
    Chinese sentence scoring high.
    """
    return {t.casefold() for t in _segment(text or "", unigrams=True)}


# ---------------------------------------------------------------------------
# Script-scaled magic numbers
# ---------------------------------------------------------------------------
# Both helpers below exist because a constant expressed in *characters* silently
# means something different in another script. They convert such a constant into
# what its author actually meant, which was always a count of words.

# ``apply._MIN_PRESERVED_RUN`` was 24 characters ≈ four Latin words: long enough
# to be a preserved clause rather than shared vocabulary.
PRESERVED_CLAUSE_WORDS = 4.0


def clause_chars(text: str, *, words: float = PRESERVED_CLAUSE_WORDS) -> int:
    """How many characters make up ``words`` words of ``text``'s script.

    For Latin prose this returns 24 — the constant it replaces, unchanged. For
    Chinese it returns ~8, which is the whole point: a 24-character contiguous
    run in Chinese is an entire sentence, so the old constant made
    ``preserved_ratio`` score every real amend at ~0 and pushed every one of them
    into the review queue.
    """
    return max(4, round(words * chars_per_word(text)))


def char_budget(base_chars: int, text: str) -> int:
    """A character cap written for Latin prose, rescaled for ``text``'s script.

    Prompt budgets are capped in characters but paid for in tokens, and the two
    diverge by roughly the same factor as word length: 400 characters of English
    is ~100 tokens, 400 characters of Chinese is several times that. Scaling the
    cap down keeps the *information* budget — and the bill — roughly constant
    instead of quietly multiplying prompt size on a CJK repo.
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
    embedder: str        # sentence-transformers model that understands it

    @property
    def is_default(self) -> bool:
        """English — the language every prompt was written in. The directive is
        empty for it, so an English repo's prompts stay byte-identical to before
        this feature existed (no cache invalidation, no behavior change)."""
        return self.code == "en"


# The English default embedder, kept as-is so nothing changes for English repos.
_EN_EMBEDDER = "all-MiniLM-L6-v2"
# Understands 50+ languages in ONE shared vector space, so it also scores a
# Chinese title against an English one — the state a repo is in while migrating.
_MULTILINGUAL_EMBEDDER = "paraphrase-multilingual-MiniLM-L12-v2"

_CJK_PROSE = (
    "Use this language's own punctuation (。，；：、「」《》) rather than ASCII "
    "punctuation. Put a space on each side of Latin text embedded in a sentence "
    "(so `使用 parse_tree 解析`), which is the convention readers expect and "
    "keeps identifiers selectable."
)

_PROFILES: dict[str, DocLanguage] = {
    "en": DocLanguage(
        code="en", name="English", script=SPACED,
        title_rule="a 3–6 word sentence-case noun phrase",
        prose_rule="", embedder=_EN_EMBEDDER,
    ),
    "zh-hans": DocLanguage(
        code="zh-Hans", name="Simplified Chinese / 简体中文", script=LOGOGRAPHIC,
        title_rule="a 4–12 character noun phrase, no trailing punctuation",
        prose_rule=_CJK_PROSE, embedder=_MULTILINGUAL_EMBEDDER,
    ),
    "zh-hant": DocLanguage(
        code="zh-Hant", name="Traditional Chinese / 繁體中文", script=LOGOGRAPHIC,
        title_rule="a 4–12 character noun phrase, no trailing punctuation",
        prose_rule=_CJK_PROSE, embedder=_MULTILINGUAL_EMBEDDER,
    ),
    "ja": DocLanguage(
        code="ja", name="Japanese / 日本語", script=LOGOGRAPHIC,
        title_rule="a 4–16 character noun phrase (体言止め), no trailing punctuation",
        prose_rule=_CJK_PROSE + " Write descriptions in である/だ体, not です・ます体.",
        embedder=_MULTILINGUAL_EMBEDDER,
    ),
    "ko": DocLanguage(
        code="ko", name="Korean / 한국어", script=HANGUL,
        title_rule="a 2–5 word noun phrase, no trailing punctuation",
        prose_rule="Write descriptions in the plain declarative (해라체/-다) form.",
        embedder=_MULTILINGUAL_EMBEDDER,
    ),
}

# Display names for tags with no bespoke profile. A generic profile is genuinely
# adequate for these — they are spaced scripts whose title/prose rules are the
# English ones — so the table only has to supply a name the model recognizes.
_GENERIC_NAMES: dict[str, str] = {
    "ar": "Arabic / العربية", "cs": "Czech / Čeština", "de": "German / Deutsch",
    "es": "Spanish / Español", "fi": "Finnish / Suomi", "fr": "French / Français",
    "he": "Hebrew / עברית", "hi": "Hindi / हिन्दी", "id": "Indonesian",
    "it": "Italian / Italiano", "nl": "Dutch / Nederlands", "pl": "Polish / Polski",
    "pt": "Portuguese / Português", "pt-br": "Brazilian Portuguese / Português do Brasil",
    "ru": "Russian / Русский", "sv": "Swedish / Svenska", "th": "Thai / ไทย",
    "tr": "Turkish / Türkçe", "uk": "Ukrainian / Українська", "vi": "Vietnamese / Tiếng Việt",
}

DEFAULT_CODE = "en"
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
        prose_rule="", embedder=_MULTILINGUAL_EMBEDDER,
    )


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


def known_codes() -> list[str]:
    """Tags with a bespoke profile — for CLI help and error messages. Any other
    BCP-47 tag is still accepted (see :func:`_generic`)."""
    return [p.code for p in _PROFILES.values()]


# ---------------------------------------------------------------------------
# Workspace configuration
# ---------------------------------------------------------------------------

def config_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / CONFIG_FILENAME


def read_config(codoc_dir: str | Path) -> dict:
    """The workspace config, or ``{}`` when absent/unreadable.

    Never raises: this is read on the daemon's hot path and from the CC hook,
    where a hand-mangled config must degrade to defaults rather than break the
    author's turn.
    """
    try:
        return json.loads(config_path(codoc_dir).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — missing, empty, or malformed: all defaults
        return {}


def write_config(codoc_dir: str | Path, **updates) -> dict:
    """Merge ``updates`` into the workspace config and persist it.

    Merge rather than replace so a future setting added by another writer is not
    dropped by an older one, and ``ensure_ascii=False`` so a Chinese value stays
    readable to the human who has to review this file in a diff.
    """
    from codoc.loop.fsio import atomic_write_text

    cfg = read_config(codoc_dir)
    cfg.update({k: v for k, v in updates.items() if v is not None})
    atomic_write_text(config_path(codoc_dir),
                      json.dumps(cfg, indent=2, ensure_ascii=False,
                                 sort_keys=True) + "\n")
    return cfg


def workspace_doc_language(codoc_dir: str | Path | None) -> DocLanguage:
    """The doc language in force: env var > workspace config > English.

    The env var wins so a one-off run can override, but it is checked *first*
    rather than used as a fallback on purpose — a committed workspace setting
    should not be silently defeated by a stale shell export, and if the operator
    exports the var they mean it for this run.
    """
    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        return resolve(env)
    if codoc_dir is None:
        return _PROFILES["en"]
    return resolve(read_config(codoc_dir).get("doc_language"))


# ---------------------------------------------------------------------------
# Per-node language — observed, never imposed
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
# what language a description is in — a well-cited Chinese description carries two
# long identifiers against a dozen Han characters, so measuring the raw string
# would call the correct answer wrong.
_CODEISH = re.compile(r"`[^`]*`|\[[^\]]*\]\([^)]*\)|\bcodoc:[^\s)]+|https?://\S+")

#: Share of a script's letters needed to call prose "in" that script.
SCRIPT_FLOOR = 0.30

#: The floor for a script that is not spaced — far lower, and the asymmetry is the
#: point. Borrowing runs one way: Chinese technical prose is full of English terms,
#: while English technical prose essentially never reaches for 使用 or 。. And the
#: two sides are not measured on the same scale — a Han content word is one or two
#: characters where the English term beside it is ten, so character share
#: systematically overweights the Latin. "使用 tree-sitter 解析 Python 与
#: TypeScript。" is 16% Han characters and unmistakably a Chinese sentence; a
#: symmetric floor called it English, which is exactly backwards for the writing
#: this feature exists to support.
UNSPACED_FLOOR = 0.10
#: …but not from a single stray character: an English description citing one
#: product name in Han is still English.
_MIN_UNSPACED_LETTERS = 2


def strip_code_spans(text: str) -> str:
    """``text`` with the spans that are always code blanked out."""
    return _CODEISH.sub(" ", text or "")


def prose_script(text: str) -> ScriptClass | None:
    """The script ``text``'s *prose* is written in, or None when there is no signal.

    None means "draw no conclusion" — an empty description, a run of digits.
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
    it is in — and a check that makes one anyway will be wrong loudly and often.
    """
    return sum(1 for ch in strip_code_spans(text) if ch.isalpha())


def detect_prose_language(text: str, default: DocLanguage) -> DocLanguage:
    """The language ``text`` appears to be written in, falling back to ``default``.

    Resolution is by *script*, so it can tell Chinese from English but not French
    from Spanish — and it returns ``default`` rather than guessing whenever the
    scripts agree, which is exactly the case where a model reading the surrounding
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


def language_tag_for(text: str, default: DocLanguage) -> str:
    """The BCP-47 tag to stamp on ``text`` when displaying it (a ``lang``
    attribute). Correct per-node tagging is what lets one view render a bilingual
    tree properly — the browser picks fonts, line-breaking, and quotation
    conventions per element, and cannot do any of that from a document-level tag
    that half the content contradicts."""
    return detect_prose_language(text, default).code


def embedder_model_for(codoc_dir: str | Path | None) -> str:
    """The sentence-transformers model that can actually compare this repo's
    titles. The English default is monolingual, so semantic title dedup is inert
    on a Chinese tree until this switches — a silent no-op, not an error."""
    return workspace_doc_language(codoc_dir).embedder


# ---------------------------------------------------------------------------
# The prompt directive
# ---------------------------------------------------------------------------

def prompt_directive(lang: DocLanguage, *, for_code_agent: bool = False) -> str:
    """The block that tells a model which language to author in.

    Empty for English by design (see :attr:`DocLanguage.is_default`).

    The identifier rule is the load-bearing line. Ask a model to write Chinese
    documentation and it will helpfully translate ``parse_tree`` to ``解析树``
    inside a ``codoc:`` link, and the binding it names stops resolving — a
    translated tree that has quietly lost its attachment to the code is worse
    than an English one. Prose is translated; anything that is an address is not.

    ``for_code_agent`` is for the realize prompt, whose reader writes source
    files rather than tree nodes. It needs the opposite default: the tree is in
    the doc language, but the code it is about to write is not automatically —
    identifier and comment language is a property of the repo it is editing.
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
