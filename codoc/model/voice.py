"""Voice — what codoc has learned about how this codebase's author writes.

Codoc generates a description, a person rewrites it, and today that rewrite
teaches nothing: the ledger records it and the next pass writes in the same
register it just had corrected. This module is the memory that closes that gap.

The shape follows PRELUDE / CIPHER (Gao et al., NeurIPS 2024,
arXiv:2404.15269 — see ``papers/02-continual-learning-from-user-edits.md``): infer
a NAMED, natural-language preference from the gap between draft and revision,
keep it, retrieve the ones whose context resembles the node being written now,
and put them in the prompt. Deliberately no fine-tuning, for the reason that
paper gives and for one more of codoc's own: an author can read a sentence of
English and tell us it is wrong, which is the only correction channel that makes
a learned preference safe.

Three properties are load-bearing, and each answers a way this goes wrong:

* A lesson is **about the writing, never the content.** An author who fixed a
  false claim taught us nothing about voice, and generalizing from that edit is
  how a memory learns to assert one specific fact everywhere. :class:`EditKind`
  is what the inference pass sorts by, and only ``style`` survives into a lesson.
* A lesson is **provisional until a second edit corroborates it.** One rewrite is
  a hypothesis. :attr:`StyleLesson.evidence` counts corroborations and
  :attr:`StyleLesson.status` gates injection on it, so a single unusual edit
  cannot change how the whole tree reads.
* A lesson **remembers where it was learned.** Preferences vary by context, which
  is why CIPHER retrieves by nearest context rather than keeping one global
  string; :attr:`StyleLesson.scope_path` and :attr:`StyleLesson.scope_files` are
  that context, and :mod:`codoc.loop.voice` ranks on them.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from codoc.model.hlc import HLC
from codoc.model.ids import _short


def new_lesson_id() -> str:
    """A learned style lesson. Prefixed ``v-`` for voice, so a lesson id is never
    mistakable for a feature, event, or directive in a log line."""
    return _short("v", 8)


class EditKind(str, Enum):
    """What a human's rewrite was actually about.

    The distinction is the whole safety of the memory. A ``content`` edit says the
    prose was WRONG, so the correction is about this one feature and generalizes
    to nothing; a ``style`` edit says the prose was right and badly said, which is
    the only case that carries a preference. ``mixed`` exists because a real edit
    usually does both at once — a person fixing a wrong claim tidies the sentence
    while they are in there — and the honest handling is to take the style half
    and ignore the rest rather than to discard the edit or trust all of it.
    """

    STYLE = "style"      # same facts, said differently → a preference
    CONTENT = "content"  # the facts changed → teaches nothing about voice
    MIXED = "mixed"      # both; the style half is usable, the content half is not
    NOISE = "noise"      # a typo, a whitespace change, too small to mean anything


class LessonStatus(str, Enum):
    """Whether a lesson is allowed to influence what codoc writes.

    ``PROVISIONAL`` is the default and the point: a lesson inferred from one edit
    is recorded so a second edit can corroborate it, but it is NOT injected into a
    prompt, because acting on a single observation is how a memory acquires
    confident wrong style. ``ACTIVE`` is reached by corroboration
    (``evidence >= _ACTIVE_AT``) or by a person promoting it by hand.
    ``RETIRED`` is a lesson a person told us to forget; it is kept rather than
    deleted so the same inference does not walk back in on the next harvest.
    """

    PROVISIONAL = "provisional"
    ACTIVE = "active"
    RETIRED = "retired"


class LessonAxis(str, Enum):
    """Which property of the writing a lesson is about.

    Axes are kept apart rather than pooled into one style blob because they are
    independently satisfiable — an author can want shorter sentences and MORE
    concrete detail at once, and a single blended instruction ("write more
    tersely") loses the second half. Keeping them separate also means a
    contradiction between two lessons is visible instead of averaged, which is
    what lets :mod:`codoc.loop.voice` drop the older side of one.
    """

    ALTITUDE = "altitude"        # how far above the mechanism to write
    LENGTH = "length"            # sentence and paragraph size
    VOCABULARY = "vocabulary"    # the words this author uses and refuses
    STRUCTURE = "structure"      # what the first sentence does, what order things come in
    SPECIFICITY = "specificity"  # how much naming of symbols, values, thresholds
    TITLING = "titling"          # how a feature gets named


class StyleLesson(BaseModel):
    """One named preference, learned from one or more human rewrites.

    ``instruction`` is the whole payload and it is written as a directive to the
    writing model in the second person, because that is what goes in the prompt
    verbatim. ``evidence`` is the corroboration count, not a confidence score: it
    is the number of distinct human edits that produced this same lesson, which is
    a fact rather than an estimate.
    """

    id: str = Field(default_factory=new_lesson_id)
    axis: LessonAxis
    #: The preference, phrased as an instruction ("open on the caller's problem,
    #: not the module's name"). Injected verbatim, so it has to read as guidance
    #: to a writer and not as a note about an author.
    instruction: str
    #: One short before/after pair, kept because a rule plus its instance is
    #: followable where the rule alone is not. Truncated hard: this is a cue.
    example_before: str = ""
    example_after: str = ""
    axis_detail: str = ""
    #: Where it was learned: the ancestor titles of the feature whose prose was
    #: rewritten, root first. Retrieval prefers a lesson learned in the same
    #: region of the tree.
    scope_path: list[str] = Field(default_factory=list)
    #: The files that feature binds, so a lesson learned while writing about the
    #: loop can be preferred when writing about the loop again.
    scope_files: list[str] = Field(default_factory=list)
    status: LessonStatus = LessonStatus.PROVISIONAL
    evidence: int = 1
    #: Feature ids whose rewrites produced this lesson, so ``evidence`` can never
    #: be inflated by re-harvesting the same edit.
    sources: list[str] = Field(default_factory=list)
    #: The event ids the lesson was inferred from — the audit trail a reader
    #: follows back from ``codoc voice`` to the actual rewrite.
    source_events: list[str] = Field(default_factory=list)
    created_at: HLC = Field(default_factory=HLC.now)
    updated_at: HLC = Field(default_factory=HLC.now)

    @property
    def injectable(self) -> bool:
        return self.status is LessonStatus.ACTIVE


#: Corroborations needed before a provisional lesson starts shaping prose. Two,
#: not three: the cost of a wrong lesson is bounded (it is one sentence of
#: guidance, visible in ``codoc voice``, and the author can delete it), while the
#: cost of a high bar is that codoc never learns anything from a tree whose author
#: only edits a few nodes. Two edits agreeing is already much more than the raw
#: sample pair the old ``author_voice`` acted on immediately.
ACTIVE_AT = 2

#: Hard cap on how many lessons ride into one prompt. A memory that grows without
#: bound stops being guidance and becomes a second style guide competing with
#: ``style.txt``; past roughly this many the model follows the ones it read last.
MAX_INJECTED = 6

#: Character budget for an example, before and after. Long enough to show a
#: register, short enough that six lessons do not crowd out the change set.
EXAMPLE_CHARS = 220
