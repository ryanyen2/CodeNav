"""Voice — harvest lessons from the author's rewrites, retrieve them, inject them.

The policy half of the style memory; :mod:`codoc.agent.voice` is the one LLM call
and :mod:`codoc.model.voice` is the shape. Four jobs, in the order the data moves:

1. :func:`harvest` — read the two ways an author corrects our writing, ask the
   model what each one teaches, and fold the answers into the lesson set. The
   ledger's human rewrites are PRELUDE's own signal, inferred from the draft →
   revision gap; a comment thread on our prose is the author STATING the preference
   instead of demonstrating it, which is the language-feedback channel the same
   notes place upstream of that whole line. Both are read in one call, so a note and
   a rewrite that agree corroborate each other.
2. :func:`retrieve` — for the node about to be written, rank the lessons by how
   close their context is to it. CIPHER's k-nearest-context step; the adaptation is
   that "context" here is structural (where in the tree, which files) rather than
   lexical, because two features that read alike are much less likely to want the
   same register than two features in the same subsystem.
3. :func:`voice_context` — the payload the prose prompts read, lessons and raw
   samples together. The samples stay because they carry things no instruction
   does; the lessons are added because
   the imitation result in ``papers/02-continual-learning-from-user-edits.md``
   says samples alone are the weak form.
4. :func:`edit_cost_trend` — PRELUDE's metric, computed from real edits: is the
   author having to change less of what we write than they used to?

Everything here degrades to nothing rather than failing. A tree with no human
edits yields no lessons, an unreachable model yields no lessons, and a corrupt row
yields no lesson — in every case the prompts get what they got before this module
existed, which is the raw samples.
"""
from __future__ import annotations

import difflib
import json
import logging
from pathlib import Path

from codoc.doclang import DocLanguage, tokens
from codoc.model.annotation import CommentThread
from codoc.model.event import ACTOR_HUMAN, Event, NodeOpKind
from codoc.model.hlc import HLC
from codoc.model.voice import (
    ACTIVE_AT,
    EXAMPLE_CHARS,
    MAX_INJECTED,
    LessonAxis,
    LessonStatus,
    StyleLesson,
)
from codoc.store.db import Store

_log = logging.getLogger(__name__)

#: Where the harvest keeps its position in the ledger. A watermark rather than a
#: per-event "seen" flag because the ledger is append-only and read in time order,
#: so one string answers the whole question — and it must advance even when a batch
#: produced no lesson, or a history of pure content edits is re-read and re-billed
#: on every pass forever.
WATERMARK_KEY = "voice.harvest_watermark"

#: How many rewrites one harvest sends to the model. Small: a harvest runs inside a
#: loop pass that the author is waiting on, and the signal is cumulative anyway —
#: what this batch does not read, the next one does, because the watermark only
#: advances over what was actually sent.
BATCH = 8

#: Where the note harvest keeps its position. A SECOND watermark rather than one
#: shared with the rewrites, because the two streams are read from different tables
#: at different rates: a single cursor would have to be the minimum of the two and
#: would re-read one side forever.
NOTE_WATERMARK_KEY = "voice.note_watermark"

#: A note below this many characters says nothing a writer could act on ("no", "?",
#: "fix"). Lower than the rewrite floor on purpose: a rewrite has to DEMONSTRATE the
#: preference and needs room to do it, while a note STATES it, and "too jargony" is
#: already a complete instruction.
MIN_NOTE_CHARS = 12

#: A rewrite below this many characters of change is noise (a typo, a link fix) and
#: is dropped before the model sees it, so the batch spends its slots on rewrites
#: that could carry a preference. Deliberately generous — the model classifies
#: `noise` too, and this is only the cheap pre-filter.
MIN_CHANGED_CHARS = 24

#: Two instructions this similar are the same lesson, so the second corroborates the
#: first instead of crowding the memory with paraphrases. Measured on term overlap
#: (Jaccard), which is blunt but transparent; the alternative — asking the model
#: whether two lessons match — spends a call to make a judgment a reader can check
#: by eye in the ``codoc voice`` listing.
SAME_LESSON_OVERLAP = 0.55


# ---------------------------------------------------------------------------
# 1. Harvest
# ---------------------------------------------------------------------------

def _tree_path(store: Store, feature_id: str) -> list[str]:
    """Ancestor titles of ``feature_id``, root first, including its own.

    The structural half of a lesson's context. Titles rather than ids because the
    consumer is a model reading a prompt, and because a path of titles stays
    meaningful after the tree is reorganized, where a path of ids does not.
    """
    path: list[str] = []
    seen: set[str] = set()
    fid: str | None = feature_id
    while fid and fid not in seen:
        seen.add(fid)
        feature = store.get_feature(fid)
        if feature is None:
            break
        path.append(feature.title)
        fid = feature.parent_id
    return list(reversed(path))


def _rewrite_rows(store: Store, events: list[tuple[str, Event]]) -> list[dict]:
    """The events that are genuinely a person rewriting OUR prose, as prompt rows.

    Three filters, and each one drops a case that would teach the wrong thing:

    * The op must be an AMEND carrying the text it displaced. An ADD_NODE is the
      author writing from scratch, which is a sample of their voice (the raw
      ``author_voice`` channel already carries those) but not a *correction* of
      ours, so there is no gap to read a preference out of.
    * The displaced prose must NOT have been written by a person. When an author
      edits their own earlier sentence they are changing their mind about content,
      and reading that as a preference about how codoc should write is a category
      error.
    * The change must be big enough to mean something. See
      :data:`MIN_CHANGED_CHARS`.
    """
    rows: list[dict] = []
    for _cursor, event in events:
        op = event.op
        if op.kind is not NodeOpKind.AMEND or not op.feature_id:
            continue
        # `prev_written_by` is the ROLE of whoever wrote the displaced text
        # (loop/apply._record_displaced stamps it). A person correcting themselves
        # is not a lesson about us.
        if op.prev_written_by == ACTOR_HUMAN:
            continue
        for field, before, after in (
            ("description", op.prev_description, op.description),
            ("title", op.prev_title, op.title),
        ):
            if not before or not after or before.strip() == after.strip():
                continue
            if _changed_chars(before, after) < _min_changed_for(field):
                continue
            feature = store.get_feature(op.feature_id)
            rows.append({
                "event_id": event.id,
                "feature_id": op.feature_id,
                "feature_title": feature.title if feature else "",
                "field": field,
                "before": before.strip(),
                "after": after.strip(),
                "tree_path": " / ".join(_tree_path(store, op.feature_id)),
            })
    return rows


def _min_changed_for(field: str) -> int:
    """The noise floor for one field.

    A title is a handful of words, so the paragraph-sized floor would reject every
    real retitling. Four characters is about the smallest title change that is not a
    typo — a word swapped, a plural dropped.
    """
    return 4 if field == "title" else MIN_CHANGED_CHARS


def _changed_chars(before: str, after: str) -> int:
    """How many characters the rewrite actually touched.

    Length difference is the wrong measure: a rewrite that swaps one clause for
    another of the same size changed a great deal and would score zero. So this
    sums the opcodes that are not `equal`, which is what a reader would call the
    size of the edit.
    """
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    return sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )


def _note_rows(store: Store, threads: list[tuple[str, CommentThread]]) -> list[dict]:
    """The comment threads that are a person objecting to OUR prose, as prompt rows.

    A note is the more direct of the two signals and the one this module was missing.
    A rewrite makes codoc infer the preference from a gap; a note is the author
    saying it — "don't call these handlers", "this opens on the class name again" —
    which is the channel the language-feedback line treats as primary (see
    ``papers/02-continual-learning-from-user-edits.md``, section 2). It was also
    invisible here by construction: a note with prose scope is answered by an AGENT
    rewriting the description, so the resulting AMEND is not a human edit and
    :meth:`Store.human_amend_events` never sees it. The author's stated preference
    was acted on once and forgotten, and the next description repeated what they
    objected to.

    Two filters, mirroring the two that matter for a rewrite:

    * The prose must be OURS. A note on a description a PERSON wrote is a request
      about the code or a note to themselves, not a correction of how codoc writes —
      the same category error ``prev_written_by == ACTOR_HUMAN`` drops on the rewrite
      side, asked here of ``feature_writers``.
    * The note must be long enough to carry an instruction
      (:data:`MIN_NOTE_CHARS`).

    Nothing filters on ``scope`` or ``status``. A ``code``-scope note can still name
    what the author calls a thing, which is a titling preference; a note that turns
    out to be about the implementation is what the model's ``content`` class is for,
    and deciding it here would be this module guessing at prose it has not read.
    """
    rows: list[dict] = []
    for _cursor, thread in threads:
        if not thread.feature_id:
            continue
        body = thread.body.strip()
        if len(body) < MIN_NOTE_CHARS:
            continue
        _writer, role = store.feature_writer_info(thread.feature_id)
        if role == ACTOR_HUMAN:
            continue
        feature = store.get_feature(thread.feature_id)
        if feature is None:
            continue
        rows.append({
            "event_id": thread.id,
            "feature_id": thread.feature_id,
            "feature_title": feature.title,
            "field": "description",
            "note": body,
            # What they were pointing AT. A note reads as a correction and the claim
            # it corrects is half of it, which is why the thread stores the words and
            # not only the offsets.
            "quoted": (thread.anchor_text or "").strip(),
            # No `before`/`after` pair, and none invented. A lesson keeps one as its
            # cue — a rule plus its instance — and a note has no instance: the author
            # asked for the change instead of making it, so there is no prose of
            # theirs to show. The audit trail is the note itself, which `codoc voice
            # why` reads back out of the thread.
            "tree_path": " / ".join(_tree_path(store, thread.feature_id)),
        })
    return rows


def _note_slots(batch: int) -> int:
    """How much of one batch the notes may take.

    Half. Not all of it, because a burst of notes must not stall the rewrite stream
    that PRELUDE's method reads; not the leftovers either, because a stated
    preference queueing behind inferred ones is the wrong priority — on a busy tree
    the rewrites would fill every batch and the notes would never be read at all.
    """
    return max(1, max(1, batch) // 2)


def harvest(
    store: Store,
    *,
    doc_language: DocLanguage | None = None,
    batch: int = BATCH,
    infer=None,
) -> list[StyleLesson]:
    """Read what the author has told us since the watermarks, and fold it in.

    Two streams, because an author corrects our writing two ways and only one of
    them was ever read here. A **rewrite** shows the preference and codoc infers it
    from the gap (:func:`_rewrite_rows`, PRELUDE's method). A **note** states it in
    words, on the sentence it is about (:func:`_note_rows`) — the stronger signal,
    and until now the invisible one: a note asking for the prose to change is
    answered by an agent, so the AMEND it produces is not a human edit and the
    ledger walk never saw the author's own words.

    Returns the lessons that were created or corroborated by this pass (empty is the
    common and healthy outcome — most passes have neither a new rewrite nor a new
    note to read).

    Idempotent by watermark, one per stream: each position advances to the newest row
    CONSIDERED, including ones that yielded nothing, so a second call with no new
    feedback does no work and issues no LLM call. A crash mid-pass loses at most one
    batch's signal and never double-counts, because evidence is keyed on the row ids
    the lesson already carries.

    ``infer`` is the injection point for tests — it defaults to
    :func:`codoc.agent.voice.infer_lessons` and is imported lazily so that importing
    this module never pulls in the LLM config.
    """
    note_since = store.get_meta(NOTE_WATERMARK_KEY, "")
    notes, note_mark = _take_notes(store, note_since, slots=_note_slots(batch))
    since = store.get_meta(WATERMARK_KEY, "")
    rewrites, watermark = _take_rewrites(
        store, since, slots=max(1, batch) - len(notes))

    # ONE call for both streams, not one each. The batch is what makes a weak signal
    # readable (see :mod:`codoc.agent.voice`), and a note and a rewrite that say the
    # same thing corroborate each other — which they cannot do if they are inferred
    # in separate calls and only meet as two lessons to be merged afterwards.
    rows: list[dict] = notes + rewrites

    if not rows:
        _advance(store, (NOTE_WATERMARK_KEY, note_since, note_mark),
                 (WATERMARK_KEY, since, watermark))
        return []

    if infer is None:
        from codoc.agent.voice import infer_lessons as infer  # noqa: PLC0415

    try:
        inferred = infer(rows, doc_language=doc_language)
    except Exception as exc:  # noqa: BLE001 — learning is optional
        _log.warning("codoc voice: harvest inference failed (%s); watermark held", exc)
        return []

    by_event = {r["event_id"]: r for r in rows}
    touched: list[StyleLesson] = []
    for item in inferred:
        if not item.has_lesson:
            continue
        row = by_event.get(item.event_id)
        if row is None:
            continue  # an event id we never sent; nothing to scope it to
        lesson = _absorb(store, item, row)
        if lesson is not None:
            touched.append(lesson)

    _advance(store, (NOTE_WATERMARK_KEY, note_since, note_mark),
             (WATERMARK_KEY, since, watermark))
    return touched


def _take_notes(
    store: Store, since: str, *, slots: int,
) -> tuple[list[dict], str]:
    """The next notes worth sending, and the cursor they consumed.

    One thread is one row, so the batch arithmetic is the trivial case of the
    rewrite one below — a note cannot be half-read the way an AMEND carrying both a
    title and a description can.
    """
    threads = store.human_comment_threads(since=since, limit=max(1, slots) * 4)
    if not threads:
        return [], since
    keep = {row["event_id"]: row for row in _note_rows(store, threads)}
    rows: list[dict] = []
    watermark = since
    for cursor, thread in threads:
        row = keep.get(thread.id)
        if row is not None and len(rows) >= max(1, slots):
            break
        if row is not None:
            rows.append(row)
        # Advanced over the dropped ones too, for the reason the rewrite side gives:
        # a tree whose notes are all short or all on the author's own prose would
        # otherwise be re-examined on every pass forever.
        watermark = cursor
    return rows, watermark


def _take_rewrites(
    store: Store, since: str, *, slots: int,
) -> tuple[list[dict], str]:
    """The next rewrites worth sending, and the cursor they consumed.

    Walks the ledger forward taking WHOLE events until the slots are full. Whole
    events, because one AMEND can carry both a retitling and a rewritten
    description, and splitting it would send half a rewrite now and read the other
    half next pass as though it were unrelated to it.
    """
    if slots <= 0:
        return [], since
    events = store.human_amend_events(since=since, limit=max(1, slots) * 4)
    if not events:
        return [], since

    per_event: dict[str, list[dict]] = {}
    for row in _rewrite_rows(store, events):
        per_event.setdefault(row["event_id"], []).append(row)

    rows: list[dict] = []
    watermark = since
    for cursor, event in events:
        take = per_event.get(event.id, [])
        if rows and len(rows) + len(take) > slots:
            break
        rows.extend(take)
        # This event is accounted for either way — sent, or examined and found to
        # teach nothing (an ADD_NODE, a self-correction, a typo). Holding the
        # watermark for the boring ones is what would make a history of pure content
        # edits re-read on every pass forever.
        watermark = cursor
    return rows, watermark


def _advance(store: Store, *cursors: tuple[str, str, str]) -> None:
    """Move each watermark that actually moved.

    Both are written together at the END of a harvest, and neither is written when
    the inference call failed — so a failed batch is retried whole rather than half
    of it being lost. Skipping the unchanged ones keeps a harvest with nothing to
    read from touching the store at all, which is the common case on a quiet pass.
    """
    for key, before, after in cursors:
        if after != before:
            store.set_meta(key, after)


def _absorb(store: Store, item, row: dict) -> StyleLesson | None:
    """Fold one inferred lesson into the stored set, creating or corroborating.

    Corroboration is the whole point of the provisional stage, so matching has to be
    real: a new lesson joins an existing one when they are on the same axis and say
    close to the same thing (:data:`SAME_LESSON_OVERLAP`). On a match the evidence
    count rises and the lesson goes ACTIVE at :data:`ACTIVE_AT` — the moment it
    starts shaping prose.

    A RETIRED lesson still MATCHES, and absorbing into it leaves it retired. That is
    the point of keeping the row: the author said no to this instruction, and a
    second edit that happens to suggest it again is not grounds to overrule them.
    """
    existing = store.all_lessons(include_retired=True)
    match = _find_same(existing, item.axis, item.instruction)

    scope_path = [p for p in row.get("tree_path", "").split(" / ") if p]
    files = _feature_files(store, row.get("feature_id", ""))

    if match is None:
        lesson = StyleLesson(
            axis=item.axis,
            instruction=item.instruction,
            axis_detail=item.axis_detail,
            # Absent for a note (see `_note_rows`), and `voice_context` sends the
            # pair only when both halves are there — a cue with nothing to compare
            # against is worse than the instruction alone.
            example_before=row.get("before", "")[:EXAMPLE_CHARS],
            example_after=row.get("after", "")[:EXAMPLE_CHARS],
            scope_path=scope_path,
            scope_files=files,
            status=LessonStatus.PROVISIONAL if ACTIVE_AT > 1 else LessonStatus.ACTIVE,
            evidence=1,
            sources=[row["feature_id"]] if row.get("feature_id") else [],
            source_events=[item.event_id],
        )
        store.upsert_lesson(lesson)
        return lesson

    if item.event_id in match.source_events:
        return None  # already counted; a replayed harvest must not inflate evidence

    match.source_events = match.source_events + [item.event_id]
    if row.get("feature_id") and row["feature_id"] not in match.sources:
        match.sources = match.sources + [row["feature_id"]]
    match.evidence = len(match.source_events)
    # Scope WIDENS with corroboration rather than being replaced: a lesson seen in
    # two subsystems is about the author, not about one subsystem, and retrieval
    # scores it higher for both.
    match.scope_path = _merge_capped(match.scope_path, scope_path, 8)
    match.scope_files = _merge_capped(match.scope_files, files, 12)
    if match.status is LessonStatus.PROVISIONAL and match.evidence >= ACTIVE_AT:
        match.status = LessonStatus.ACTIVE
    match.updated_at = HLC.now()
    store.upsert_lesson(match)
    return match


def _merge_capped(current: list[str], incoming: list[str], cap: int) -> list[str]:
    """``current`` plus what is new in ``incoming``, order-stable, capped.

    Capped because scope is a retrieval hint and an unbounded one stops
    discriminating: a lesson that lists every file in the repo ranks equally for
    every node, which is the same as having no scope at all.
    """
    out = list(current)
    for item in incoming:
        if item and item not in out:
            out.append(item)
    return out[:cap]


def _find_same(
    lessons: list[StyleLesson], axis: LessonAxis | None, instruction: str,
) -> StyleLesson | None:
    """The stored lesson ``instruction`` is a restatement of, if any."""
    if axis is None:
        return None
    # tokens() rather than terms(): these are two SENTENCES being compared for
    # equivalence, so the lenient set is right — terms() drops CJK unigrams and would
    # score two equivalent Chinese instructions as unrelated.
    want = tokens(instruction)
    if not want:
        return None
    best: tuple[float, StyleLesson] | None = None
    for lesson in lessons:
        if lesson.axis is not axis:
            continue
        score = _overlap(want, tokens(lesson.instruction))
        if score >= SAME_LESSON_OVERLAP and (best is None or score > best[0]):
            best = (score, lesson)
    return best[1] if best else None


def _overlap(a: set[str], b: set[str]) -> float:
    """Jaccard overlap of two term sets, 0.0 when either is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _feature_files(store: Store, feature_id: str) -> list[str]:
    """The files a feature binds, deduplicated, in a stable order."""
    if not feature_id:
        return []
    try:
        bindings = store.bindings_for_feature(feature_id)
    except Exception:  # noqa: BLE001 — scope is advisory
        return []
    out: list[str] = []
    for b in bindings:
        if b.file not in out:
            out.append(b.file)
    return out


# ---------------------------------------------------------------------------
# 2. Retrieve
# ---------------------------------------------------------------------------

def retrieve(
    store: Store,
    *,
    tree_path: list[str] | None = None,
    files: list[str] | None = None,
    limit: int = MAX_INJECTED,
) -> list[StyleLesson]:
    """The lessons to apply when writing about this context, best match first.

    CIPHER's retrieval step. Scoring is deliberately simple and inspectable —
    directory overlap, then tree-path overlap, then evidence — because a learned
    instruction that a person cannot predict the appearance of is one they cannot
    audit, and auditability is the reason these are sentences and not weights.

    **At most one lesson per axis.** Two lessons on the same axis are either
    paraphrases (which :func:`_find_same` should have merged) or a contradiction —
    the author changed their mind, or two regions of the tree genuinely differ. In
    both cases sending both means the model follows whichever it read last, so the
    better-matching one wins and the other is left out. That is also what makes a
    changed mind take effect: the newer, better-corroborated lesson displaces the
    older one at the point of use instead of arguing with it in the prompt.
    """
    lessons = store.injectable_lessons()
    if not lessons:
        return []

    want_dirs = {str(Path(f).parent) for f in (files or []) if f}
    want_path = {p.casefold() for p in (tree_path or []) if p}

    scored: list[tuple[float, StyleLesson]] = []
    for lesson in lessons:
        have_dirs = {str(Path(f).parent) for f in lesson.scope_files if f}
        have_path = {p.casefold() for p in lesson.scope_path if p}
        score = (
            2.0 * _overlap(want_dirs, have_dirs)
            + 1.0 * _overlap(want_path, have_path)
            # Evidence breaks ties and, with no context to match on at all (a
            # bootstrap, a node with no bindings yet), is the whole ranking — which
            # is the right fallback: the lesson the author has confirmed most often
            # is the safest one to apply where we cannot tell.
            + 0.1 * min(lesson.evidence, 10)
        )
        scored.append((score, lesson))

    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    out: list[StyleLesson] = []
    seen_axes: set[LessonAxis] = set()
    for _score, lesson in scored:
        if lesson.axis in seen_axes:
            continue
        seen_axes.add(lesson.axis)
        out.append(lesson)
        if len(out) >= max(0, limit):
            break
    return out


# ---------------------------------------------------------------------------
# 3. Inject
# ---------------------------------------------------------------------------

def voice_context(
    store: Store,
    *,
    tree_path: list[str] | None = None,
    files: list[str] | None = None,
    samples: int = 2,
    limit: int = MAX_INJECTED,
) -> dict | None:
    """The ``author_voice`` block for a prose prompt, or None when there is nothing.

    Two channels, because they carry different things. ``lessons`` are named
    instructions the author's own edits produced, which is the form the imitation
    literature says actually transfers. ``samples`` are whole paragraphs they wrote,
    which carry rhythm and vocabulary that no instruction captures. Neither
    subsumes the other, and a tree with human edits but no corroborated lesson yet
    still gets the samples — the behaviour codoc had before this module.
    """
    lessons = retrieve(store, tree_path=tree_path, files=files, limit=limit)
    try:
        written = store.human_written_descriptions(limit=samples)
    except Exception:  # noqa: BLE001 — advisory context only
        written = []
    if not lessons and not written:
        return None
    block: dict = {}
    if lessons:
        block["lessons"] = [
            {
                "axis": lesson.axis.value,
                "instruction": lesson.instruction,
                "learned_from": lesson.evidence,
                **({"example_before": lesson.example_before,
                    "example_after": lesson.example_after}
                   if lesson.example_before and lesson.example_after else {}),
            }
            for lesson in lessons
        ]
    if written:
        block["samples"] = written
    return block


def lessons_digest(store: Store) -> str:
    """A one-line-per-lesson summary for a status surface or a log.

    Kept next to the injection code so the text a person is shown and the text the
    model is shown come from the same place — a memory whose displayed contents do
    not match its effective contents cannot be audited.
    """
    lessons = store.all_lessons(include_retired=False)
    if not lessons:
        return "(nothing learned yet)"
    return "\n".join(
        f"- [{lesson.id}] {lesson.axis.value}: {lesson.instruction}"
        f"  ({lesson.status.value}, {lesson.evidence}"
        f" edit{'s' if lesson.evidence != 1 else ''})"
        for lesson in lessons
    )


# ---------------------------------------------------------------------------
# 4. The metric
# ---------------------------------------------------------------------------

def edit_cost(before: str, after: str) -> float:
    """Normalized edit distance between generated prose and what the author left.

    PRELUDE's cost function, at character granularity: 0.0 when the author changed
    nothing, 1.0 when they replaced it wholesale. Character rather than token
    because the claim being tested is about wording, and a token-level distance
    reports zero for a reordering that a reader would call a rewrite.
    """
    if not before and not after:
        return 0.0
    if not before or not after:
        return 1.0
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    return 1.0 - matcher.ratio()


def edit_cost_trend(store: Store, *, buckets: int = 4, limit: int = 400) -> dict:
    """Whether the author is having to rewrite less of what codoc writes.

    Every human AMEND over machine-written prose is one observation of PRELUDE's
    cost. Split oldest-to-newest into equal buckets and report each bucket's mean:
    a falling series is the claim that the style memory works, and a flat one says
    it is doing nothing, which is exactly the finding worth having.

    Reported as data, not a verdict. With a handful of edits the buckets are noise
    and ``n`` says so, so the caller can decline to draw a conclusion rather than
    being handed one.
    """
    events = store.human_amend_events(since="", limit=limit)
    costs: list[float] = []
    for _cursor, event in events:
        op = event.op
        if op.kind is not NodeOpKind.AMEND or op.prev_written_by == ACTOR_HUMAN:
            continue
        if op.prev_description and op.description:
            costs.append(edit_cost(op.prev_description, op.description))
    if not costs:
        return {"n": 0, "buckets": [], "mean": None}

    n_buckets = max(1, min(buckets, len(costs)))
    size = len(costs) / n_buckets
    means: list[float] = []
    for i in range(n_buckets):
        chunk = costs[int(i * size):int((i + 1) * size)] or costs[-1:]
        means.append(round(sum(chunk) / len(chunk), 3))
    return {
        "n": len(costs),
        "buckets": means,
        "mean": round(sum(costs) / len(costs), 3),
        # The claim, stated only when there is enough to state it. Two observations
        # falling is not a trend, and reporting one would be the kind of confident
        # noise this whole module is trying to keep out of the tree.
        "improving": (means[-1] < means[0]) if len(costs) >= 8 else None,
    }


def render_trend(trend: dict) -> str:
    """The trend as a line a person reads in ``codoc voice``."""
    if not trend.get("n"):
        return "no edits over generated prose yet, so there is no cost to report"
    series = " → ".join(f"{m:.2f}" for m in trend["buckets"])
    line = (f"edit cost over {trend['n']} rewrites: {series}"
            f" (mean {trend['mean']:.2f})")
    if trend.get("improving") is True:
        return line + ", falling"
    if trend.get("improving") is False:
        return line + ", not falling"
    return line + ", too few to call a trend"


def as_json(store: Store) -> str:
    """The whole memory as JSON, for a test or an external eval."""
    return json.dumps(
        {
            "lessons": [x.model_dump(mode="json") for x in store.all_lessons()],
            "trend": edit_cost_trend(store),
        },
        indent=2, ensure_ascii=False,
    )
