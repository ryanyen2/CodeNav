"""The prose gate: check a generated title or description before it lands.

``prompts/style.txt`` states the rules every prose pass is held to, and until now
nothing checked whether a sample followed them. That gap costs twice. Once
directly, because a description that opens on a mechanism reaches the tree and the
reader gets mechanism where they came for purpose. And once indirectly, because
the author then fixes it by hand, which the style memory (:mod:`codoc.loop.voice`)
reads as a preference, so an unenforced rule quietly turns into a learned lesson
that the rule already covered. Enforcing upstream is what keeps that memory about
the author's taste rather than about our sloppiness.

Everything here is deterministic. That is a deliberate limit, not a stage on the
way to an LLM critic: a check that spends a model call per sample cannot run at
every write, and a check whose verdict varies between runs cannot be a gate. So
this module only reports defects it can point at, the words that tripped it in the
field they are in, and every one of them is a rule from ``style.txt`` that
survives being written down mechanically. The rules that do not survive that
(is the first sentence TRUE, is this the right feature to name) are left to the
prompt and to the reader.

Three properties keep it usable:

**A defect is a sentence you could say to a writer.** :attr:`Defect.message` is
imperative and specific, because it is not only shown to a person, it IS the
repair prompt. A code like ``opens-on-a-mechanism`` tells a model nothing; "open
on what this is for, not on ``_near_edge``" tells it what to do.

**It never blocks a write.** :func:`gate` gives one repair attempt and then keeps
the better of the two samples, defect and all. A tree missing a description is
worse than a tree with an awkward one, and a gate that can refuse is a gate that
can lose a pass's whole output to a rule about dashes.

**A check that would fire on good prose is not in here.** Every rule below was run
against the worked example in ``style.txt`` and against 412 paragraphs of this
repo's own docstrings, which are the closest corpus available to prose the author
wrote and is happy with. The ones with false positives were narrowed or dropped,
and where narrowing was impossible the comment says what was given up. A critic
the author learns to ignore is worse than none, because it also trains them to
ignore the ones that were right.

What that sweep left, as a share of the 412 (a share, not a rate: several rules
fire more than once in a paragraph):

===========================  =====  ====================================================
rule                         share  reading
===========================  =====  ====================================================
``decorated``                 109%  dashes and clause colons, and NOT a false positive
                                    rate: ``style.txt`` exempts exactly this register
                                    ("notes to you rather than prose for a reader") and
                                    a docstring uses both freely, so the corpus is the
                                    wrong one for this rule and the right one for the
                                    rest.
``overlong-sentence``          10%  real, and the sentences are long.
``demonstrative-opening``       8%  real; a docstring opens on "This is" constantly and
                                    a description may not.
``machine-register``            2%  all of them the word "simply".
``opens-on-a-mechanism``        1%
``clipped-sentences``           1%  a run of short attribute sentences, which is a
                                    docstring shape rather than a description's.
``rhetorical-question``         0%  two, both genuinely asking one.
===========================  =====  ====================================================

The number to watch is not any single share but whether they move: a rule that
starts firing on a fifth of everything has stopped describing a defect. That is
why :func:`record` keeps the running counts per code in the store, and why
``altitude-too-high`` was removed when it reached 19% here.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from codoc.doclang import (
    SPACED,
    DocLanguage,
    clause_chars,
    prose_script,
    strip_code_spans,
    terms,
)

# ---------------------------------------------------------------------------
# What a finding is
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Defect:
    """One rule a sample broke, in the words you would use to ask for a fix.

    ``message`` is written imperative and specific because it has two readers and
    the same sentence has to serve both: a person reading the defect rate, and the
    model being asked to rewrite. ``quote`` is the words that tripped the rule, so
    neither reader has to hunt for them.
    """

    code: str
    field: str
    message: str
    quote: str = ""

    def line(self) -> str:
        """One line for a prompt or a terminal."""
        where = f" ({self.quote!r})" if self.quote else ""
        return f"{self.field}: {self.message}{where}"


# ---------------------------------------------------------------------------
# Vocabulary the style guide bans
# ---------------------------------------------------------------------------

# From "Use the words two colleagues would use out loud". Each entry is a word
# whose presence is the defect, with no context in which it reads well in a
# description of code.
#
# Two of the guide's words are NOT here, and the omissions are the interesting
# part. `key` is a noun this domain uses constantly (a cache key, an API key, a
# dict key) and there is no cheap way to tell it from the adjective, so flagging
# it would fire on correct prose several times per tree. `clean` is handled below
# by pattern instead, because "clean up the queue" is fine and "the code is clean"
# is what the guide forbids. A rule that cannot be made precise is better left to
# the prompt than enforced wrongly.
_BANNED_WORDS = (
    "leverage", "leverages", "leveraged", "leveraging",
    "robust", "robustly", "robustness",
    "seamless", "seamlessly",
    "ensure", "ensures", "ensured", "ensuring",
    "utilize", "utilizes", "utilized", "utilizing",
    "comprehensive", "comprehensively",
    "crucial", "crucially",
    "powerful", "elegant", "elegantly",
    "simply", "careful", "carefully",
    "delve", "delves", "myriad", "plethora",
)

# Phrases that carry no fact, from "Cut anything that carries no fact" and
# "Do not decorate". Matched case-insensitively as whole phrases.
_BANNED_PHRASES = (
    "it is worth noting", "it's worth noting", "worth noting that",
    "at its core", "under the hood", "in order to", "in terms of",
    "a variety of", "plays a key role", "plays a crucial role",
    "when it comes to", "that being said", "needless to say",
)

# "Do not call the code clean or careful" -- the adjectival use only. "clean up
# the buffer" and "a clean checkout" are ordinary and must not fire.
_SELF_PRAISE = re.compile(
    r"\b(?:is|are|was|were|very|really|nice and)\s+clean\b"
    r"|\bclean(?:er|est)?\s+(?:code|design|implementation|abstraction|api)\b",
    re.I,
)

# "Not just X but Y is banned, and so are rhetorical questions."
_NOT_JUST = re.compile(r"\bnot (?:just|only)\b[^.?!]{0,80}?\bbut\b", re.I)

# "Do not start a sentence with This, That, These, or Those; name the thing."
_DEMONSTRATIVE = re.compile(r"^(This|That|These|Those)\b(?!\s+(?:file|module)\b)")

# Words a description can be built entirely out of while saying nothing. Wider
# than doclang's stopword set, which drops grammar; these are content-SHAPED
# words that carry no content about this particular codebase.
_EMPTY_WORDS = frozenset("""
handles handle handling manages manage managing processes process processing
provides provide providing performs perform performing implements implement
implementing supports support supporting contains contain containing holds hold
stores store storing represents represent representing encapsulates wraps
various related relevant necessary appropriate proper properly correctly
correct important needed required additional specific certain multiple several
logic functionality utility utilities helper helpers wrapper module modules
class classes function functions method methods data value values object objects
information details operations operation behaviour behavior features feature
system systems component components structure structures thing things part parts
main core basic simple general common standard default internal external
""".split())

# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------

# Code spans are masked before splitting, because `codoc:loop/apply.py#apply_op`
# and `f.py` both carry full stops that are not sentence ends. Masking to a word
# of similar shape (rather than deleting) keeps the sentence's word count honest.
_ABBREV = re.compile(r"\b(?:e\.g|i\.e|etc|vs|cf|no|fig)\.", re.I)
# ``x`` is in the lookahead because a masked code span is a lowercase run, and a
# sentence that begins with a citation ("`SessionStart` fires once per turn") would
# otherwise not be seen to begin at all -- it joined the sentence before it and the
# pair came back reported as one overlong sentence.
_SENT_SPLIT = re.compile(
    "(?<=[.!?])[\\s\u3000]+(?=[\"\u201c'(\\[A-Z0-9x])|(?<=[\u3002\uff01\uff1f])")
# Double backticks come FIRST: a single-backtick alternative tried first matches
# the empty span between the two opening backticks of ``[.!?]`` and leaves the
# contents exposed as prose, which reported the sample as asking a question.
_CODE_SPAN = re.compile(
    r"``[^`]*``|`[^`]*`|\[[^\]]*\]\([^)]*\)|\bcodoc:[^\s)]+|https?://\S+")


_BULLET = re.compile(r"\s*(?:[-*+]|\d+[.)])\s+")


def _mask(text: str) -> str:
    """``text`` with code spans replaced by a run of x, CHARACTER FOR CHARACTER.

    Sentence splitting, word counting, and every punctuation rule below run on
    this rather than the original: a citation is not prose, and a rule that reads
    it as prose reports the full stop inside a symbol path as a sentence end and
    the colon in ``codoc:`` as decoration.

    The length has to be preserved exactly, because the rules search the masked
    text and then quote the ORIGINAL at the offset they found. An earlier version
    capped the replacement at twelve characters, which shifted every offset after
    a long citation and quoted the author two words from the wrong place -- one
    report came back as ``'e: e'``, sliced out of the middle of a word.
    """
    return _CODE_SPAN.sub(lambda m: "x" * len(m.group(0)), text or "")


def _units(text: str) -> list[tuple[str, str, bool]]:
    """Sentences as (masked, original, listed), so a rule can test one and quote the other.

    ``listed`` says the sentence is a markdown list item, which the sentence-shape
    rules need: a list of steps is SUPPOSED to be a stack of short statements, and
    reporting it as clipped prose would ask an author to write the list back into a
    paragraph.

    A line break ends a sentence even without a full stop, because a description
    may hold a markdown list and a list item is a sentence for every purpose this
    module has. Without that, four bullets read as one 60-word sentence and get
    reported as overlong.
    """
    raw = text or ""
    masked = _ABBREV.sub(lambda m: m.group(0).replace(".", "\u2024"), _mask(raw))
    out: list[tuple[str, str, bool]] = []
    for line in re.finditer(r"[^\n]+", masked):
        base, body = line.start(), line.group(0)
        bullet = _BULLET.match(body)
        cut = bullet.end() if bullet else 0
        bounds, prev = [], cut
        for m in _SENT_SPLIT.finditer(body, cut):
            bounds.append((prev, m.start()))
            prev = m.end()
        bounds.append((prev, len(body)))
        for start, end in bounds:
            one = masked[base + start:base + end].strip()
            if one:
                out.append((one.replace("\u2024", "."),
                            raw[base + start:base + end].strip(),
                            bullet is not None))
    return out


def sentences(text: str) -> list[str]:
    """``text`` split into sentences, with abbreviations and code spans masked."""
    return [masked for masked, _, _ in _units(text)]


def _around(text: str, start: int, end: int, width: int = 26) -> str:
    """The author's own words around a match found in the masked copy.

    Every punctuation rule searches the mask and has to report the original, and
    a match like ``[a-z]: [a-z]`` is four characters wide -- quoting it verbatim
    told one author their defect was ``'e: e'``, which names nothing they wrote.
    The mask is length-preserving, so the offsets carry straight over.
    """
    return (text or "")[max(0, start - width):end + width].strip()


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'-]*", text or "")


def _is_latin_prose(text: str) -> bool:
    """Whether the lexical rules apply to ``text`` at all.

    Every rule about banned words, dashes and demonstratives was written for
    English, and running an English wordlist over Chinese prose finds nothing
    while a Latin-punctuation rule over it fires on the library names it quotes.
    So the lexical half of the gate is scoped to prose whose script is spaced,
    and the structural half (opens on an identifier, adds nothing beyond the
    names) runs on everything, because it is about symbols rather than words.
    """
    script = prose_script(text or "")
    return script is None or script is SPACED


# ---------------------------------------------------------------------------
# Does the sample open on a mechanism?
# ---------------------------------------------------------------------------

# An identifier as prose would write it: snake_case, camelCase, a dotted path, or
# a call. Deliberately NOT a bare capitalized word -- `Store` is an identifier but
# so is every sentence's first word, and telling them apart needs the symbol table
# rather than a regex.
_IDENTIFIER = re.compile(
    r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)+[A-Za-z_][A-Za-z0-9_]*\b"     # a.b.c
    r"|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"                          # snake_case
    r"|\b[a-z]+[A-Z][A-Za-z0-9]*\b"                                # camelCase
    r"|\b[A-Za-z_][A-Za-z0-9_]*\(\)"                               # a call
)
_CITATION = re.compile(r"`[^`]+`|\[[^\]]*\]\(codoc:[^)]*\)")
# One dot and a known extension is a FILE, and naming a file a reader can open is
# orienting them rather than showing them a mechanism. "Apply tree.codoc edits,
# then reflect the code" is the sentence this repo would want; reporting it as an
# identifier opening teaches the model to write around the artifact's own name.
# Two dots is a symbol path again (``store.db.Store``), so only the single-dot
# form is excused.
_FILENAME = re.compile(
    r"^[A-Za-z_][\w-]*\.(?:py|pyi|ts|tsx|js|jsx|json|jsonl|md|txt|toml|ya?ml"
    r"|codoc|db|sqlite|lock|cfg|ini|sh|html|css|svg)$", re.I)

# How far into the first sentence counts as "opening on". Three words is the
# subject and its verb: past that, an identifier is being used as evidence for a
# claim already made, which is what the guide asks for.
_OPENING_WORDS = 3


def _opens_on_mechanism(description: str) -> Defect | None:
    """"Never open on a mechanism or an identifier."

    Checked on the first sentence only, and only in its first few words, because
    the rule is about what the reader meets first. A description whose second
    clause names the function is doing exactly what the guide wants.
    """
    masked, raw, _ = next(iter(_units(description)), ("", "", False))
    if not masked:
        return None
    lead = _CITATION.match((description or "").strip())
    if lead:
        return Defect(
            "opens-on-a-mechanism", "description",
            "open on what this is for in a reader's words, not on a symbol; move "
            "the citation into the sentence that makes a claim about it",
            lead.group(0)[:60])
    # Searched on the MASKED head and quoted from the raw one at the same offset:
    # a citation is already reported by the branch above, and a run of mask
    # characters must never be read back as a symbol.
    cut = len(" ".join(masked.split()[:_OPENING_WORDS]))
    hit = _IDENTIFIER.search(masked[:cut])
    if hit and not _FILENAME.match(raw[hit.start():hit.end()] or hit.group(0)):
        return Defect(
            "opens-on-a-mechanism", "description",
            "the first sentence names an implementation detail before saying what "
            "the feature is for; say the purpose first and keep the detail for the "
            "sentence that shows the rule",
            (raw[hit.start():hit.end()] or hit.group(0))[:60])
    return None


# ---------------------------------------------------------------------------
# Does it just say the title again?
# ---------------------------------------------------------------------------

# How much of the first sentence has to be title vocabulary before it is a
# restatement. Not 1.0, because "Renders the tree to tree.codoc" under the title
# "Tree rendering" is a restatement with one word of padding, and padding is
# exactly what a restatement is made of.
_RESTATEMENT_SHARE = 0.75
# Under this many words there is no signal either way: a four-word first sentence
# made of title words may be the honest opening of a longer paragraph. Counted in
# WORDS rather than in distinct terms, because the most complete restatement is
# also the most repetitive one ("Tree rendering renders the tree, rendering each
# feature in the tree") and a distinct-term floor let exactly that case through.
_RESTATEMENT_MIN_WORDS = 7
_RESTATEMENT_MIN_TERMS = 3


# Enough of a stem to see that "renders" and "rendering" are the same word, and
# no more. A real stemmer is not worth the dependency here: this comparison exists
# to catch a title turned into a clause, and turning a noun into its verb is the
# commonest way that is done ("Tree rendering" becoming "Renders the tree").
# No ``-er``/``-ers`` rule: it turns "renders" into "rend" and "users" into "us",
# and the pair it would unify ("render"/"renderer") is not the pair that matters.
_SUFFIXES = ("ations", "ation", "ings", "ing", "ies", "ied", "ed", "es", "s")


def _stem(word: str) -> str:
    """Enough of a stem that two forms of one word compare equal.

    Only internal consistency matters here, never linguistic accuracy: both sides
    of every comparison go through this function, so "featur" is as good a key as
    "feature" provided "features" also lands on it. The trailing ``e`` goes for
    exactly that reason -- "features" loses ``es`` and "feature" loses nothing, and
    without the extra step the two forms of the same word would not match.
    """
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    return word[:-1] if len(word) > 3 and word.endswith("e") else word


def _stems(words) -> set[str]:
    return {_stem(w) for w in words}


def _restates_title(title: str, description: str) -> Defect | None:
    """The first sentence has to tell the reader something the heading did not.

    The failure this catches is specific and common: the model treats the
    description's first sentence as a place to expand the title into a clause, so
    a reader who has already read the heading gets nothing for the second line
    and learns to skip descriptions.
    """
    first = next(iter(sentences(description)), "")
    if not first or not (title or "").strip():
        return None
    have = _stems(terms(first))
    if (len(_words(first)) < _RESTATEMENT_MIN_WORDS
            or len(have) < _RESTATEMENT_MIN_TERMS):
        return None
    novel = have - (_stems(terms(title)) | _stems(_EMPTY_WORDS))
    if len(novel) / max(1, len(have)) <= 1.0 - _RESTATEMENT_SHARE:
        return Defect(
            "restates-the-title", "description",
            "the first sentence is the title again in a clause; the reader has "
            "already read the heading, so spend the first sentence on what the "
            "feature is FOR",
            first[:80])
    return None


# ---------------------------------------------------------------------------
# Register and decoration
# ---------------------------------------------------------------------------


def _register_defects(field: str, text: str) -> list[Defect]:
    """Words and phrases the guide bans outright, plus the self-praise pattern."""
    out: list[Defect] = []
    low = f" {text.lower()} "
    hits = [w for w in _BANNED_WORDS if re.search(rf"\b{re.escape(w)}\b", low)]
    if hits:
        out.append(Defect(
            "machine-register", field,
            "these are the words that make writing sound machine-made; say what "
            "the code does and let the reader judge it",
            ", ".join(sorted(set(hits))[:6])))
    phrases = [p for p in _BANNED_PHRASES if p in low]
    if phrases:
        out.append(Defect(
            "carries-no-fact", field,
            "cut these; each one announces a fact instead of stating one",
            ", ".join(sorted(set(phrases))[:4])))
    praise = _SELF_PRAISE.search(text)
    if praise:
        out.append(Defect(
            "praises-the-code", field,
            "do not call the code clean or careful; describe what it does",
            praise.group(0)))
    return out


# A colon holding two clauses together. Code spans and URLs are masked before this
# runs, so `codoc:` and `https://` cannot reach it. A colon introducing a list is
# not matched (it is followed by a newline or a fragment), because that use is not
# what the guide forbids and is the one a rewrite cannot mechanically improve.
_CLAUSE_COLON = re.compile(r"[a-z0-9)\]]\s*:\s+[a-z]", re.I)
_DASHES = re.compile("\\s[\u2014\u2013]\\s|\u2014\u2014")


def _decoration_defects(field: str, text: str) -> list[Defect]:
    """"Do not decorate" -- dashes, clause colons, demonstratives, questions."""
    out: list[Defect] = []
    masked = _mask(text)
    dash = _DASHES.search(masked)
    if dash:
        out.append(Defect(
            "decorated", field,
            "no em dash or en dash; a full stop or the word and does the same work",
            _around(text, dash.start(), dash.end())))
    colon = _CLAUSE_COLON.search(masked)
    if colon:
        out.append(Defect(
            "decorated", field,
            "no colon holding two clauses together; make them two sentences or "
            "join them with the word that says the relation",
            _around(text, colon.start(), colon.end())))
    for one, raw, _ in _units(text):
        if _DEMONSTRATIVE.match(one):
            out.append(Defect(
                "demonstrative-opening", field,
                "do not start a sentence with This, That, These or Those; name "
                "the thing you mean",
                raw[:60]))
            break
    not_just = _NOT_JUST.search(masked)
    if not_just:
        out.append(Defect(
            "decorated", field,
            "the not just X but Y construction is banned; state Y",
            _around(text, not_just.start(), not_just.end())))
    # A sentence that ENDS in a question mark, not merely one containing it: a
    # quoted question inside a claim ("the question it answers is \"did this keep
    # what was there?\"") is a statement about a question, which is exactly how a
    # rationale gets written and must not be flagged.
    asked = next((raw for m, raw, _ in _units(text) if m.rstrip().endswith("?")), "")
    if asked:
        out.append(Defect(
            "rhetorical-question", field,
            "no rhetorical questions; answer it in the sentence instead",
            asked[:70]))
    return out


# ---------------------------------------------------------------------------
# Sentence shape
# ---------------------------------------------------------------------------

# "Prefer one explanatory sentence to two clipped ones." A clipped sentence is one
# whose clauses were never joined; below this many words there is no room for a
# connector, so a run of them is the stacked-statement failure the guide names.
_CLIPPED_WORDS = 9
_CLIPPED_RUN = 3
# "Two or three clauses is the right size for a sentence, and four is too many."
# Counted in words rather than clauses because clause detection needs a parser,
# and a sentence past this length has four clauses in it whatever they are.
_OVERLONG_WORDS = 48


def _shape_defects(field: str, text: str) -> list[Defect]:
    out: list[Defect] = []
    parts = _units(text)
    run: list[str] = []
    for sentence, raw, listed in parts + [("", "", False)]:
        if sentence and not listed and len(_words(sentence)) <= _CLIPPED_WORDS:
            run.append(raw)
            continue
        if len(run) >= _CLIPPED_RUN:
            out.append(Defect(
                "clipped-sentences", field,
                "short statements stacked together read as precise and leave the "
                "connection for the reader to guess; join them with because, so, "
                "which means, unless, or rather than",
                " ".join(run)[:110]))
            break
        run = []
    long_one = next(
        (raw for m, raw, _ in parts if len(_words(m)) > _OVERLONG_WORDS), None)
    if long_one:
        out.append(Defect(
            "overlong-sentence", field,
            "this sentence carries more than three clauses; split it where the "
            "subject changes",
            long_one[:110]))
    return out


# ---------------------------------------------------------------------------
# Does it say anything the names do not?
# ---------------------------------------------------------------------------

# The negative rule with teeth: a description a reader could have written from the
# symbol names alone has failed, whatever else is true of it. Novel terms are
# counted after removing the title's vocabulary, the bound symbols' vocabulary and
# the empty words, so what is left is what this paragraph contributed.
_MIN_NOVEL_TERMS = 3
# Below this many prose words the check has no basis: a one-line description of a
# one-line function may legitimately be built out of its name.
_MIN_PROSE_WORDS = 12


def _adds_nothing(
    title: str, description: str, names: tuple[str, ...],
) -> Defect | None:
    """Nothing here that the identifier names did not already say.

    Deliberately silent for non-Latin prose: ``terms`` segments unspaced script
    into n-grams that cannot match a Latin symbol path, so every term would count
    as novel and the check would pass anything at all. Reporting nothing is the
    honest outcome, because the rule is about vocabulary overlap and there is none
    to measure across two scripts.
    """
    prose = strip_code_spans(description or "")
    if len(_words(prose)) < _MIN_PROSE_WORDS or not _is_latin_prose(description or ""):
        return None
    known = _EMPTY_WORDS | terms(title or "")
    for name in names:
        known |= terms(name.replace("/", " ").replace("::", " ").replace("#", " "))
    novel = terms(prose) - known
    if len(novel) < _MIN_NOVEL_TERMS:
        return Defect(
            "nothing-beyond-the-names", "description",
            "everything here is recoverable from the title and the symbol names, "
            "so a reader learns nothing by reading it; name what is checked, what "
            "happens when the check fails, and what the caller sees afterwards",
            ", ".join(sorted(novel)[:5]) or "(no novel terms)")
    return None


# "Go from the general to the particular, every time" asks for three sentences and
# makes only the third optional: what the feature is for, then the rule or the
# choice that shapes it. A single short sentence has stopped after the first, so
# the reader is told what something is and never why it is that way.
_MIN_EXPLAINED_WORDS = 14


def _no_rule_given(description: str, names: tuple[str, ...]) -> Defect | None:
    """One sentence naming the thing, with nothing that shapes it.

    Requires ``names``, so a bare organizing node with no code under it does not
    trip: there the honest description IS one sentence, because the rule that
    shapes it lives in the children.
    """
    if not names:
        return None
    parts = sentences(description)
    if len(parts) > 1:
        return None
    prose = strip_code_spans(description or "").strip()
    # A word count is a Latin measure. Chinese says in sixteen characters what
    # English needs fourteen words for, so counting Latin words in Chinese prose
    # returns zero and reports every description in the tree.
    if _is_latin_prose(description or ""):
        if len(_words(prose)) >= _MIN_EXPLAINED_WORDS:
            return None
    elif len(prose) >= 2 * clause_chars(description or ""):
        return None
    return Defect(
        "no-rule-given", "description",
        "one sentence says what this is and stops, so a reader learns nothing "
        "about why it works the way it does; add the rule or the choice that "
        "shapes it, and where there is one worth the room, the case that shows it",
        description.strip()[:80])


# ---------------------------------------------------------------------------
# Altitude: is the register the right height for what the node covers?
# ---------------------------------------------------------------------------

# Altitude is measured by SPAN -- how many files the node's code sits in, and
# whether it has children -- rather than by depth alone. Depth is a fact about
# where an author filed something; span is a fact about how much a reader has to
# hold in their head, which is what decides the register. It is also available at
# every site that generates prose, where depth often is not: a bootstrap file pass
# does not yet know where the organization pass will put its node.
_BROAD_FILES = 3
# Something a reader can check against the code: a number, a threshold, a name.
_CONCRETE = re.compile(r"`[^`]+`|\[[^\]]*\]\(codoc:[^)]*\)|\b\d+(?:\.\d+)?%?\b")


def _altitude_defects(
    description: str, *, files: int, has_children: bool | None, depth: int | None,
) -> list[Defect]:
    """A top node feeds hypothesis forming, so it cannot be written at leaf level.

    Only the broad failure is enforced. A broad node written at symbol level gives
    a reader who is starting out a detail they cannot place, and that is checkable:
    every sentence needs a citation to parse. The opposite direction -- a leaf
    written too abstractly -- was implemented and then removed, because the only
    mechanical test for it is "does the prose name a symbol or a number", and good
    leaf prose often names neither (the BINDINGS already tie the node to its code,
    so the sentence does not have to). It fired on one description in five of this
    repo's own, which is the rate at which a critic stops being read. What is left
    of that direction lives in ``no-rule-given`` and
    ``nothing-beyond-the-names``, which catch the abstract leaf by what it fails
    to SAY rather than by which characters it contains, and in the register the
    prompt asks for per altitude.
    """
    out: list[Defect] = []
    if not (description or "").strip():
        return out
    broad = files >= _BROAD_FILES or bool(has_children) or depth == 0
    if broad:
        cites = len(_CITATION.findall(description))
        # Tested on the raw sentence: the mask replaces every citation with a run
        # of x, so searching it for a citation finds none and every sentence looks
        # plain -- which silenced this rule entirely.
        plain = [raw for _, raw, _ in _units(description) if not _CONCRETE.search(raw)]
        if cites >= 2 and not plain:
            out.append(Defect(
                "altitude-too-low", "description",
                "this node spans several files and a reader meets it before they "
                "know the codebase, so at least the opening sentence has to hold "
                "without a symbol in it",
                f"{cites} citations, no sentence free of one"))
    return out


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

_TITLE_MAX_WORDS = 8
# A title built only out of these names a container rather than an intent, and one
# word is the commonest form of it. The ``>= 2`` guard the general check needs (a
# single ordinary word is a perfectly good title) is what let "Helpers" through,
# so the vacuous set is checked without it.
_CONTAINER_TITLE = frozenset("""
helper helpers util utils utility utilities handler handlers manager management
core misc miscellaneous common shared base stuff thing things logic module modules
component components infrastructure plumbing support wrapper wrappers main other
others extras internals bits pieces glue
""".split())


def _title_defects(title: str) -> list[Defect]:
    """A title is a name, not a sentence and not a symbol.

    The identifier case is the one worth enforcing hardest: a tree whose nodes are
    named after their functions has stopped being a view of intent and become a
    second copy of the file listing, which is the failure the feature tree exists
    to avoid.
    """
    out: list[Defect] = []
    text = (title or "").strip()
    if not text:
        return out
    if _IDENTIFIER.fullmatch(text) or text.endswith("()"):
        out.append(Defect(
            "title-is-an-identifier", "title",
            "name the intent, not the symbol; a reader scanning the tree needs the "
            "words they would use for this, not the code's",
            text[:60]))
    if len(_words(text)) > _TITLE_MAX_WORDS or text.endswith("."):
        out.append(Defect(
            "title-is-a-sentence", "title",
            f"a title is a name, so keep it near {_TITLE_MAX_WORDS} words and drop "
            "the full stop; move the claim into the description",
            text[:70]))
    if _is_latin_prose(text):
        # Compared against the RAW terms, not against the terms left after the
        # empty words are removed: "Helpers" is itself an empty word, so removing
        # them first left nothing to test and the commonest form of this defect --
        # the one-word container title -- went unreported.
        bare = _stems(terms(text))
        if bare and bare <= _stems(_CONTAINER_TITLE):
            out.append(Defect(
                "title-says-nothing", "title",
                "this names a container, not an intent; say what the code under it "
                "is for, in the words a reader would use for it",
                text[:60]))
        elif not (terms(text) - _EMPTY_WORDS) and len(_words(text)) >= 2:
            out.append(Defect(
                "title-says-nothing", "title",
                "every word here would fit any codebase; name what this one does",
                text[:60]))
        out.extend(_register_defects("title", text))
    return out


# ---------------------------------------------------------------------------
# The public check
# ---------------------------------------------------------------------------


def check(
    title: str | None = None,
    description: str | None = None,
    *,
    names: tuple[str, ...] | list[str] = (),
    depth: int | None = None,
    files: int = 0,
    has_children: bool | None = None,
    doc_language: DocLanguage | None = None,
) -> list[Defect]:
    """Every rule ``title`` and ``description`` break, worst first.

    ``names`` are the symbol paths and files the node binds, which is the evidence
    for "does this say anything the names did not". ``files`` / ``has_children`` /
    ``depth`` set the altitude; each is optional and the altitude rules simply do
    not fire without enough signal, because guessing a node's height and then
    correcting its register on that guess is the one way this gate could make
    prose worse.

    A field that is None was not written by this op and is not checked. That
    matters at the write boundary, where an AMEND commonly carries a description
    and no title, and checking the stored title against the new prose would report
    a defect nobody in this pass could have caused.
    """
    out: list[Defect] = []
    if title is not None:
        out.extend(_title_defects(title))
    if description is None or not description.strip():
        return out
    hit = _opens_on_mechanism(description)
    if hit:
        out.append(hit)
    if title:
        hit = _restates_title(title, description)
        if hit:
            out.append(hit)
    hit = _adds_nothing(title or "", description, tuple(names))
    if hit:
        out.append(hit)
    hit = _no_rule_given(description, tuple(names))
    if hit:
        out.append(hit)
    out.extend(_altitude_defects(
        description, files=files, has_children=has_children, depth=depth))
    if _is_latin_prose(description):
        # The lexical half, scoped to spaced script for the reason in
        # `_is_latin_prose`.
        out.extend(_register_defects("description", description))
        out.extend(_decoration_defects("description", description))
        out.extend(_shape_defects("description", description))
    return out


# ---------------------------------------------------------------------------
# Reviewing a pass's ops, and asking once for a repair
# ---------------------------------------------------------------------------


def review_ops(
    ops: list,
    *,
    names_of=None,
    depth_of=None,
    children_of=None,
    doc_language: DocLanguage | None = None,
) -> dict[int, list[Defect]]:
    """Check every op in ``ops`` that writes prose, keyed by index in ``ops``.

    Indexed rather than keyed by feature id because a bootstrap op has no id yet,
    and because the caller's next move is to hand the same list back for a
    rewrite: position is the only identity both halves agree on.
    """
    findings: dict[int, list[Defect]] = {}
    for i, op in enumerate(ops):
        title = getattr(op, "title", None)
        description = getattr(op, "description", None)
        if title is None and description is None:
            continue
        pairs = [tuple(b) for b in (getattr(op, "bindings", None) or []) if len(b) == 2]
        names = [f"{b[0]} {b[1]}" for b in pairs]
        files = len({b[0] for b in pairs})
        if names_of is not None:
            extra = [str(x) for x in (names_of(op) or ())]
            names += extra
            # Only a name that carries its file counts toward the file span. A
            # caller with symbol paths and no files (the subtree rows the tree
            # pass is given) would otherwise report one file per symbol, and a
            # three-symbol leaf would be treated as a broad node.
            spanned = {x.split(" ")[0] for x in extra if " " in x}
            files = max(files, len(spanned))
        defects = check(
            title, description, names=tuple(names), files=files,
            depth=depth_of(op) if depth_of else None,
            has_children=children_of(op) if children_of else None,
            doc_language=doc_language,
        )
        if defects:
            findings[i] = defects
    return findings


def critique(ops: list, findings: dict[int, list[Defect]]) -> str:
    """The revise block to append to the prompt that produced ``ops``.

    Written as a request about specific nodes rather than a restatement of the
    style rules, which are already in the prompt's frozen prefix and were read
    once. Repeating them would spend cache-cold tokens saying what the model has
    seen; naming the node and the words that broke the rule is new information.
    """
    if not findings:
        return ""
    lines = [
        "",
        "## Revise before answering",
        "",
        "Your previous answer is below, with what is wrong with each description."
        " Return the SAME ops with the SAME ids, bindings and structure, changing"
        " only the prose that is called out. Do not drop an op, do not add one, and"
        " do not weaken a claim to make it easier to phrase: if a sentence is"
        " awkward only because the accurate version is long, keep it accurate.",
        "",
    ]
    for i, defects in sorted(findings.items()):
        op = ops[i]
        label = (getattr(op, "title", None) or getattr(op, "feature_id", None)
                 or f"op {i}")
        lines.append(f"### {label}")
        if getattr(op, "description", None):
            lines.append(f"you wrote: {op.description}")
        lines.extend(f"- {d.line()}" for d in defects)
        lines.append("")
    return "\n".join(lines)


# Not every defect weighs the same, and the difference decides which of two drafts
# to keep. A description recoverable from the names is a failure of the whole
# paragraph; an en dash is a failure of one character. Without this, a repair that
# "fixed" the dash by deleting the content would win on count.
_WEIGHTS = {
    "nothing-beyond-the-names": 5,
    "restates-the-title": 4,
    "no-rule-given": 4,
    "opens-on-a-mechanism": 3,
    "altitude-too-low": 3,
    "title-is-an-identifier": 3,
    "title-says-nothing": 3,
    "clipped-sentences": 2,
    "machine-register": 2,
    "carries-no-fact": 2,
}


def severity(defects: list[Defect]) -> int:
    """How badly a sample reads, as one comparable number."""
    return sum(_WEIGHTS.get(d.code, 1) for d in defects)


def gate(
    ops: list,
    *,
    rerun=None,
    names_of=None,
    depth_of=None,
    children_of=None,
    doc_language: DocLanguage | None = None,
) -> tuple[list, dict[int, list[Defect]]]:
    """Check ``ops``, ask once for a repair, and keep whichever reads better.

    ``rerun(critique_text)`` re-runs the call that produced ``ops`` with the
    critique appended, or is None when the caller cannot repeat itself. Exactly
    one attempt: a second costs another call on prose that is already close, and a
    model that did not take the note the first time rarely takes it the third.

    A repair is only ACCEPTED if it answers the same question, meaning the same
    ops with the same kinds and the same bindings. A rewrite that quietly dropped
    a node or re-attributed code would trade a dash for a hole in the tree, so a
    structurally different answer is discarded and the first draft stands with its
    defects recorded.
    """
    findings = review_ops(ops, names_of=names_of, depth_of=depth_of,
                          children_of=children_of, doc_language=doc_language)
    if not findings or rerun is None:
        return ops, findings
    import logging

    log = logging.getLogger(__name__)
    try:
        fixed = rerun(critique(ops, findings))
    except Exception as exc:  # noqa: BLE001 -- a failed repair keeps the first draft
        log.warning("codoc: prose repair failed (%s); keeping the first draft", exc)
        return ops, findings
    if not _same_shape(ops, fixed):
        log.warning(
            "codoc: prose repair changed the answer's shape (%d ops in, %d out); "
            "keeping the first draft", len(ops), len(fixed or []))
        return ops, findings
    after = review_ops(fixed, names_of=names_of, depth_of=depth_of,
                       children_of=children_of, doc_language=doc_language)
    before_cost = sum(severity(d) for d in findings.values())
    after_cost = sum(severity(d) for d in after.values())
    if after_cost < before_cost:
        return fixed, after
    return ops, findings


def _same_shape(first: list, second: list | None) -> bool:
    """Whether ``second`` answers the same question ``first`` did."""
    if not second or len(second) != len(first):
        return False
    for a, b in zip(first, second):
        if getattr(a, "kind", None) is not getattr(b, "kind", None):
            return False
        if getattr(a, "feature_id", None) != getattr(b, "feature_id", None):
            return False
        if (sorted(tuple(x) for x in (getattr(a, "bindings", None) or []))
                != sorted(tuple(x) for x in (getattr(b, "bindings", None) or []))):
            return False
    return True


# ---------------------------------------------------------------------------
# The defect rate, as an eval
# ---------------------------------------------------------------------------

STATS_KEY = "prose.gate"


def record(store, *, checked: int, defects=()) -> None:
    """Count what the gate saw, so the defect rate is a number and not a feeling.

    Kept as a rolling total in ``store_meta`` rather than a table, because the
    question it answers (how often does a fresh sample trip the critic) needs
    counts per code and nothing per sample. A table would also invite reading it
    as a to-do list of prose to fix, which it is not: the gate already kept the
    best sample it could get, and a defect that survived is recorded as the price
    of keeping the write rather than as an open ticket.
    """
    if checked <= 0:
        return
    try:
        stats = json.loads(store.get_meta(STATS_KEY, "") or "{}")
    except (ValueError, TypeError):
        stats = {}
    codes = dict(stats.get("codes") or {})
    seen = 0
    for d in defects or ():
        codes[d.code] = int(codes.get(d.code, 0)) + 1
        seen += 1
    stats["checked"] = int(stats.get("checked", 0)) + checked
    stats["defective"] = int(stats.get("defective", 0)) + (1 if seen else 0)
    stats["defects"] = int(stats.get("defects", 0)) + seen
    stats["codes"] = codes
    try:
        store.set_meta(STATS_KEY, json.dumps(stats, sort_keys=True))
    except Exception:  # noqa: BLE001 -- a statistic must never sink a write
        pass


def defect_rate(store) -> dict:
    """What the gate has seen: samples checked, share defective, worst codes."""
    try:
        stats = json.loads(store.get_meta(STATS_KEY, "") or "{}")
    except (ValueError, TypeError):
        stats = {}
    checked = int(stats.get("checked", 0))
    defective = int(stats.get("defective", 0))
    codes = stats.get("codes") or {}
    return {
        "checked": checked,
        "defective": defective,
        "rate": (defective / checked) if checked else None,
        "top": sorted(codes.items(), key=lambda kv: -int(kv[1]))[:5],
    }


def render_rate(stats: dict) -> str:
    """One line for ``codoc status``."""
    if not stats.get("checked"):
        return "prose gate: nothing checked yet"
    top = ", ".join(f"{code} x{n}" for code, n in stats.get("top") or ())
    tail = f", most often {top}" if top else ""
    return (f"prose gate: {stats['defective']}/{stats['checked']} generated samples "
            f"tripped a rule ({stats.get('rate') or 0.0:.0%}){tail}")
