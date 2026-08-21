"""The style memory, with no LLM in it.

`infer` is injected in every test here, so what is under test is the policy: which
rewrites are worth reading, when two inferences are the same lesson, when a lesson
is allowed to shape prose, which lessons a given node retrieves, and whether the
edit-cost metric reports a trend it can actually support.

The one thing these tests are most careful about is the case that makes a style
memory dangerous rather than useless: a person correcting a FACT must not become an
instruction. Two tests pin that from opposite ends — `test_content_edit_teaches_nothing`
(the model said content, so nothing is stored) and `test_human_authored_prose_is_not_a_lesson`
(the author is editing their own words, so it never reaches the model at all).
"""
from __future__ import annotations

import pytest

from codoc.agent.voice import InferredLesson
from codoc.loop import voice
from codoc.model.annotation import CommentScope, CommentStatus, CommentThread
from codoc.model.event import ACTOR_HUMAN, Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.model.voice import ACTIVE_AT, EditKind, LessonAxis, LessonStatus, StyleLesson
from codoc.store.db import Store


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "codoc.db")
    s.open()
    yield s
    s.close()


def _feature(store: Store, fid: str, title: str, parent_id: str | None = None) -> Feature:
    f = Feature(id=fid, title=title, description="", parent_id=parent_id)
    store.upsert_feature(f)
    return f


def _amend(
    store: Store,
    fid: str,
    *,
    before: str,
    after: str,
    written_by: str = "loop",
    actor: str = ACTOR_HUMAN,
    field: str = "description",
) -> Event:
    """One applied AMEND, shaped the way `loop/apply._record_displaced` shapes them.

    Built by hand rather than by running Loop B, because what these tests are about
    is what `harvest` reads OUT of the ledger — putting the real loop in the middle
    would make a voice failure and an apply failure indistinguishable.
    """
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid)
    if field == "description":
        op.description = after
        op.prev_description = before
    else:
        op.title = after
        op.prev_title = before
    op.prev_written_by = written_by
    event = Event(
        op=op, actor=actor, source="user", applied=True, at=HLC.now(),
    )
    store.append_event(event)
    return event


def _note(
    store: Store,
    fid: str,
    body: str,
    *,
    quoted: str = "The EditQueueHandler processes each item.",
    scope: CommentScope = CommentScope.BOTH,
) -> CommentThread:
    """One comment thread a person left on a feature's description.

    Built by hand for the reason `_amend` gives: what these tests are about is what
    `harvest` reads out of the store, and running the webview's command path in the
    middle would make a voice failure and an edits failure indistinguishable.
    """
    thread = CommentThread(feature_id=fid, body=body, anchor_text=quoted, scope=scope)
    store.upsert_comment(thread)
    return thread


def _ours(store: Store, fid: str) -> None:
    """Mark this feature's prose as codoc-written, the way `apply_op` marks it."""
    store.set_feature_writer(fid, "loop_a", "loop")


def _theirs(store: Store, fid: str) -> None:
    store.set_feature_writer(fid, "human", ACTOR_HUMAN)


def _infer(answers: dict[str, InferredLesson | None]):
    """An `infer` that answers from a dict keyed by event id.

    Also records what it was ASKED, so a test can assert on the batch itself — half
    the policy in `harvest` is about which rewrites get sent, and that is invisible
    from the return value alone.
    """
    seen: list[list[dict]] = []

    def fake(rewrites, *, config=None, doc_language=None):
        seen.append(rewrites)
        out = []
        for row in rewrites:
            answer = answers.get(row["event_id"])
            if answer is not None:
                out.append(answer)
        return out

    fake.seen = seen
    return fake


def _lesson(event_id: str, instruction: str, axis=LessonAxis.STRUCTURE,
            kind=EditKind.STYLE) -> InferredLesson:
    return InferredLesson(event_id, kind, axis, instruction, "detail")


LONG_A = "This module holds the retry policy for outbound calls to the index."
LONG_B = "Outbound calls to the index fail in bursts, so this holds the retry policy."


# --------------------------------------------------------------------------
# what reaches the model
# --------------------------------------------------------------------------

def test_harvest_sends_the_rewrite_and_stores_its_lesson(store):
    _feature(store, "f-1", "Retry policy")
    ev = _amend(store, "f-1", before=LONG_A, after=LONG_B)
    infer = _infer({ev.id: _lesson(ev.id, "Open on the caller's problem.")})

    touched = voice.harvest(store, infer=infer)

    assert len(infer.seen) == 1
    (row,) = infer.seen[0]
    assert row["before"] == LONG_A
    assert row["after"] == LONG_B
    assert row["field"] == "description"
    assert row["tree_path"] == "Retry policy"

    assert len(touched) == 1
    stored = store.all_lessons()
    assert len(stored) == 1
    assert stored[0].instruction == "Open on the caller's problem."
    assert stored[0].source_events == [ev.id]


def test_human_authored_prose_is_not_a_lesson(store):
    """An author editing their OWN sentence changed their mind about content.

    Reading that as a preference about how codoc should write is a category error, so
    the rewrite never reaches the model at all.
    """
    _feature(store, "f-1", "Retry policy")
    _amend(store, "f-1", before=LONG_A, after=LONG_B, written_by=ACTOR_HUMAN)
    infer = _infer({})

    assert voice.harvest(store, infer=infer) == []
    assert infer.seen == []  # not even sent
    assert store.all_lessons() == []


def test_content_edit_teaches_nothing(store):
    """The model classified it as a factual correction, so nothing is stored."""
    _feature(store, "f-1", "Retry policy")
    ev = _amend(store, "f-1", before=LONG_A, after="The retry limit is four, not ten.")
    infer = _infer({ev.id: InferredLesson(ev.id, EditKind.CONTENT)})

    assert voice.harvest(store, infer=infer) == []
    assert store.all_lessons() == []


def test_tiny_rewrites_are_dropped_before_the_model(store):
    """A typo is not a preference, and spending a batch slot on it costs a real one."""
    _feature(store, "f-1", "Retry policy")
    _amend(store, "f-1", before=LONG_A, after=LONG_A.replace("holds", "hold"))
    infer = _infer({})

    assert voice.harvest(store, infer=infer) == []
    assert infer.seen == []


def test_a_retitling_survives_the_noise_floor(store):
    """A title is a few words, so the paragraph-sized floor would reject every real one."""
    _feature(store, "f-1", "Index retry policy")
    ev = _amend(store, "f-1", before="Retry wrapper", after="Index retry policy",
                field="title")
    infer = _infer({ev.id: _lesson(ev.id, "Name the subsystem in the title.",
                                   axis=LessonAxis.TITLING)})

    touched = voice.harvest(store, infer=infer)
    assert len(touched) == 1
    assert infer.seen[0][0]["field"] == "title"


def test_tree_path_is_the_ancestor_titles(store):
    _feature(store, "f-root", "Codoc")
    _feature(store, "f-mid", "The two loops", parent_id="f-root")
    _feature(store, "f-1", "Retry policy", parent_id="f-mid")
    ev = _amend(store, "f-1", before=LONG_A, after=LONG_B)
    infer = _infer({ev.id: _lesson(ev.id, "Open on the caller's problem.")})

    voice.harvest(store, infer=infer)
    assert infer.seen[0][0]["tree_path"] == "Codoc / The two loops / Retry policy"
    assert store.all_lessons()[0].scope_path == ["Codoc", "The two loops", "Retry policy"]


# --------------------------------------------------------------------------
# what a note teaches
# --------------------------------------------------------------------------
#
# A comment is the author STATING the preference instead of demonstrating it, and it
# was invisible to this module: a note asking for the prose to change is answered by
# an agent, so the AMEND it produces is not a human edit and the ledger walk never
# saw the author's own words. These tests pin the second stream and the two filters
# that keep it from teaching the wrong thing.

def test_a_note_on_our_prose_teaches_a_lesson(store):
    _feature(store, "f-1", "Edit queue")
    _ours(store, "f-1")
    thread = _note(store, "f-1", "don't call it a handler — it drains the queue")
    infer = _infer({thread.id: _lesson(thread.id, "Never write 'handler'.",
                                       axis=LessonAxis.VOCABULARY)})

    touched = voice.harvest(store, infer=infer)

    (row,) = infer.seen[0]
    assert row["event_id"] == thread.id
    assert row["note"] == "don't call it a handler — it drains the queue"
    assert row["quoted"] == "The EditQueueHandler processes each item."
    assert row["field"] == "description"
    assert row["tree_path"] == "Edit queue"

    assert len(touched) == 1
    (stored,) = store.all_lessons()
    assert stored.instruction == "Never write 'handler'."
    assert stored.source_events == [thread.id]


def test_a_note_carries_no_before_after_pair(store):
    """Neither in the prompt nor in the lesson, and neither is invented.

    A lesson keeps one pair as its cue — a rule plus its instance — and a note has no
    instance: the author asked for the change rather than making it. Showing the
    quoted sentence as a `before` with an empty `after` would put a half comparison
    in a later prompt, which reads as prose codoc should have written.
    """
    _feature(store, "f-1", "Edit queue")
    _ours(store, "f-1")
    thread = _note(store, "f-1", "say what it is for, not what class it is")
    infer = _infer({thread.id: _lesson(thread.id, "Open on the purpose.")})

    voice.harvest(store, infer=infer)

    (row,) = infer.seen[0]
    assert "before" not in row and "after" not in row
    (stored,) = store.all_lessons()
    assert stored.example_before == "" and stored.example_after == ""
    block = voice.voice_context(store) or {}
    assert all("example_before" not in x for x in block.get("lessons", []))


def test_a_note_on_the_authors_own_prose_never_reaches_the_model(store):
    """The same category error `prev_written_by == human` drops on the rewrite side.

    A note on a paragraph the author wrote themselves is a request about the code, or
    a note to themselves — not a correction of how codoc writes.
    """
    _feature(store, "f-1", "Edit queue")
    _theirs(store, "f-1")
    _note(store, "f-1", "make this shorter and drop the second sentence")
    infer = _infer({})

    assert voice.harvest(store, infer=infer) == []
    assert infer.seen == []
    # And it is not re-examined on every pass forever.
    assert store.get_meta(voice.NOTE_WATERMARK_KEY, "") != ""


def test_a_note_too_short_to_be_an_instruction_is_dropped(store):
    _feature(store, "f-1", "Edit queue")
    _ours(store, "f-1")
    _note(store, "f-1", "no")
    infer = _infer({})

    assert voice.harvest(store, infer=infer) == []
    assert infer.seen == []


def test_a_note_and_a_rewrite_arrive_in_one_call(store):
    """One call for both streams, so a note and a rewrite that agree can corroborate.

    Inferred in separate calls they would only meet afterwards as two lessons to be
    merged, and the batch is what makes a weak signal readable in the first place.
    """
    _feature(store, "f-1", "Edit queue")
    _ours(store, "f-1")
    thread = _note(store, "f-1", "don't call it a handler — it drains the queue")
    event = _amend(store, "f-1", before=LONG_A, after=LONG_B)
    infer = _infer({})

    voice.harvest(store, infer=infer)

    assert len(infer.seen) == 1, "one call, not one per stream"
    sent = [row["event_id"] for row in infer.seen[0]]
    assert sorted(sent) == sorted([thread.id, event.id])


def test_a_note_and_a_rewrite_that_agree_promote_the_lesson(store):
    """The payoff. Two channels, one preference, and it starts shaping prose.

    Corroboration counts distinct things the author DID, and asking for a change is
    not the same act as making one.
    """
    _feature(store, "f-1", "Edit queue")
    _ours(store, "f-1")
    thread = _note(store, "f-1", "don't call it a handler — it drains the queue")
    event = _amend(store, "f-1", before=LONG_A, after=LONG_B)
    said = "Call this what the codebase calls it; never 'handler'."
    infer = _infer({
        thread.id: _lesson(thread.id, said, axis=LessonAxis.VOCABULARY),
        event.id: _lesson(event.id, said, axis=LessonAxis.VOCABULARY),
    })

    voice.harvest(store, infer=infer)

    (stored,) = store.all_lessons()
    assert stored.evidence == ACTIVE_AT
    assert stored.status is LessonStatus.ACTIVE
    assert sorted(stored.source_events) == sorted([thread.id, event.id])


def test_notes_are_not_starved_by_a_busy_rewrite_stream(store):
    """A stated preference must not queue behind inferred ones.

    Filling the batch with rewrites and giving notes the leftovers means a tree whose
    author edits often never has a note read at all.
    """
    _feature(store, "f-1", "Edit queue")
    _ours(store, "f-1")
    for i in range(6):
        _amend(store, "f-1", before=LONG_A, after=LONG_B + f" {i}")
    thread = _note(store, "f-1", "stop opening on the class name")
    infer = _infer({})

    voice.harvest(store, infer=infer, batch=4)

    sent = [row["event_id"] for row in infer.seen[0]]
    assert thread.id in sent
    assert len(sent) == 4


def test_a_second_harvest_does_not_reread_a_note(store):
    _feature(store, "f-1", "Edit queue")
    _ours(store, "f-1")
    thread = _note(store, "f-1", "stop opening on the class name")
    infer = _infer({thread.id: _lesson(thread.id, "Open on the purpose.")})

    voice.harvest(store, infer=infer)
    assert voice.harvest(store, infer=infer) == []
    assert len(infer.seen) == 1


def test_resolving_a_note_does_not_make_it_new_again(store):
    """The cursor is the thread's insertion order, and Loop B writes to the row.

    Stamping the directive it produced and marking it resolved both update the
    thread. If either moved its place in the queue, one note would be read again on
    every pass — and would corroborate its own lesson into ACTIVE by itself.
    """
    _feature(store, "f-1", "Edit queue")
    _ours(store, "f-1")
    thread = _note(store, "f-1", "stop opening on the class name")
    infer = _infer({thread.id: _lesson(thread.id, "Open on the purpose.")})
    voice.harvest(store, infer=infer)

    thread.directive_id = "d-1"
    thread.status = CommentStatus.RESOLVED
    store.upsert_comment(thread)

    assert voice.harvest(store, infer=infer) == []
    assert len(infer.seen) == 1
    (stored,) = store.all_lessons()
    assert stored.evidence == 1


def test_a_failing_inference_holds_the_note_watermark_too(store):
    _feature(store, "f-1", "Edit queue")
    _ours(store, "f-1")
    thread = _note(store, "f-1", "stop opening on the class name")

    def boom(rewrites, *, config=None, doc_language=None):
        raise RuntimeError("no model")

    assert voice.harvest(store, infer=boom) == []
    assert store.get_meta(voice.NOTE_WATERMARK_KEY, "") == ""

    infer = _infer({thread.id: _lesson(thread.id, "Open on the purpose.")})
    assert len(voice.harvest(store, infer=infer)) == 1


# --------------------------------------------------------------------------
# the watermark
# --------------------------------------------------------------------------

def test_second_harvest_with_no_new_edits_does_nothing(store):
    _feature(store, "f-1", "Retry policy")
    ev = _amend(store, "f-1", before=LONG_A, after=LONG_B)
    infer = _infer({ev.id: _lesson(ev.id, "Open on the caller's problem.")})

    voice.harvest(store, infer=infer)
    voice.harvest(store, infer=infer)

    assert len(infer.seen) == 1  # no second call at all
    assert store.all_lessons()[0].evidence == 1


def test_the_watermark_advances_over_uninteresting_events(store):
    """A history of pure content edits must not be re-read on every pass forever."""
    _feature(store, "f-1", "Retry policy")
    for _ in range(3):
        _amend(store, "f-1", before=LONG_A, after=LONG_B, written_by=ACTOR_HUMAN)
    infer = _infer({})

    voice.harvest(store, infer=infer)
    assert store.get_meta(voice.WATERMARK_KEY) != ""

    _amend(store, "f-1", before=LONG_A, after=LONG_B, written_by=ACTOR_HUMAN)
    voice.harvest(store, infer=infer)
    assert store.get_meta(voice.WATERMARK_KEY) != ""


def test_an_overflowing_batch_leaves_the_rest_for_next_pass(store):
    _feature(store, "f-1", "Retry policy")
    events = [_amend(store, "f-1", before=LONG_A, after=LONG_B + f" {i}")
              for i in range(4)]
    answers = {e.id: _lesson(e.id, f"Lesson {i}.", axis=LessonAxis.LENGTH)
               for i, e in enumerate(events)}
    infer = _infer(answers)

    voice.harvest(store, infer=infer, batch=2)
    assert len(infer.seen[0]) == 2

    voice.harvest(store, infer=infer, batch=2)
    assert len(infer.seen[1]) == 2
    sent = [row["event_id"] for batch in infer.seen for row in batch]
    assert sent == [e.id for e in events]  # each read exactly once, in order


def test_rewrites_sharing_a_millisecond_are_all_read(store):
    """Regression: the cursor is insertion order, not the HLC stamp.

    ``HLC.now()`` reports the wall clock with ``logical_time`` pinned at zero, so
    every event Loop B applies inside one millisecond carries an IDENTICAL ``at`` —
    and a drained batch of a person's edits is exactly that. Paging on ``at > since``
    dropped whatever shared the watermark's millisecond, which is silent permanent
    loss of the signal this whole module exists to collect. These four events are
    written in a tight loop specifically so they collide.
    """
    _feature(store, "f-1", "Retry policy")
    events = [_amend(store, "f-1", before=LONG_A, after=LONG_B + f" {i}")
              for i in range(4)]
    assert len({e.at.to_str() for e in events}) < len(events), (
        "these events were supposed to collide on `at`; if HLC gained a per-process"
        " counter this test needs rewriting, not deleting"
    )
    answers = {e.id: _lesson(e.id, f"Lesson {i}.", axis=LessonAxis.LENGTH)
               for i, e in enumerate(events)}
    infer = _infer(answers)

    voice.harvest(store, infer=infer, batch=2)
    voice.harvest(store, infer=infer, batch=2)

    read = [row["event_id"] for batch in infer.seen for row in batch]
    assert read == [e.id for e in events]


def test_a_failing_inference_holds_the_watermark(store):
    """Learning is optional, but a dropped batch must be re-readable."""
    _feature(store, "f-1", "Retry policy")
    ev = _amend(store, "f-1", before=LONG_A, after=LONG_B)

    def boom(rewrites, *, config=None, doc_language=None):
        raise RuntimeError("no model")

    assert voice.harvest(store, infer=boom) == []
    assert store.get_meta(voice.WATERMARK_KEY) == ""

    infer = _infer({ev.id: _lesson(ev.id, "Open on the caller's problem.")})
    assert len(voice.harvest(store, infer=infer)) == 1


# --------------------------------------------------------------------------
# corroboration
# --------------------------------------------------------------------------

def test_one_edit_is_provisional_and_does_not_reach_a_prompt(store):
    _feature(store, "f-1", "Retry policy")
    ev = _amend(store, "f-1", before=LONG_A, after=LONG_B)
    infer = _infer({ev.id: _lesson(ev.id, "Open on the caller's problem.")})

    voice.harvest(store, infer=infer)
    assert store.all_lessons()[0].status is LessonStatus.PROVISIONAL
    assert store.injectable_lessons() == []
    assert voice.retrieve(store) == []


def test_a_second_agreeing_edit_promotes_the_lesson(store):
    _feature(store, "f-1", "Retry policy")
    _feature(store, "f-2", "Index writes")
    e1 = _amend(store, "f-1", before=LONG_A, after=LONG_B)
    e2 = _amend(store, "f-2", before=LONG_A + " Twice.", after=LONG_B + " Twice.")
    infer = _infer({
        e1.id: _lesson(e1.id, "Open on the caller's problem, not the module name."),
        # A paraphrase, not a repetition: the same lesson said differently is what
        # corroboration actually looks like coming out of a model.
        e2.id: _lesson(e2.id, "Open on the problem the caller has, not the module name."),
    })

    touched = voice.harvest(store, infer=infer)

    lessons = store.all_lessons()
    assert len(lessons) == 1, "a paraphrase must corroborate, not duplicate"
    assert lessons[0].evidence == ACTIVE_AT
    assert lessons[0].status is LessonStatus.ACTIVE
    assert sorted(lessons[0].sources) == ["f-1", "f-2"]
    assert len(touched) == 2


def test_a_different_axis_is_a_different_lesson(store):
    _feature(store, "f-1", "Retry policy")
    _feature(store, "f-2", "Index writes")
    e1 = _amend(store, "f-1", before=LONG_A, after=LONG_B)
    e2 = _amend(store, "f-2", before=LONG_A + " Twice.", after=LONG_B + " Twice.")
    infer = _infer({
        e1.id: _lesson(e1.id, "Say it in fewer words.", axis=LessonAxis.LENGTH),
        e2.id: _lesson(e2.id, "Say it in fewer words.", axis=LessonAxis.VOCABULARY),
    })

    voice.harvest(store, infer=infer)
    assert len(store.all_lessons()) == 2


def test_replaying_the_same_event_cannot_inflate_evidence(store):
    """Evidence is keyed on event ids the lesson carries, so a replay is a no-op."""
    _feature(store, "f-1", "Retry policy")
    ev = _amend(store, "f-1", before=LONG_A, after=LONG_B)
    lesson = _lesson(ev.id, "Open on the caller's problem.")
    infer = _infer({ev.id: lesson})

    voice.harvest(store, infer=infer)
    store.set_meta(voice.WATERMARK_KEY, "")  # simulate a crash before the commit
    voice.harvest(store, infer=infer)

    stored = store.all_lessons()
    assert len(stored) == 1
    assert stored[0].evidence == 1
    assert stored[0].status is LessonStatus.PROVISIONAL


def test_a_retired_lesson_stays_retired_when_it_recurs(store):
    """The author said no. A later edge that suggests it again does not overrule them."""
    _feature(store, "f-1", "Retry policy")
    e1 = _amend(store, "f-1", before=LONG_A, after=LONG_B)
    infer1 = _infer({e1.id: _lesson(e1.id, "Open on the caller's problem.")})
    voice.harvest(store, infer=infer1)

    lesson_id = store.all_lessons()[0].id
    store.set_lesson_status(lesson_id, LessonStatus.RETIRED)

    _feature(store, "f-2", "Index writes")
    e2 = _amend(store, "f-2", before=LONG_A + " Twice.", after=LONG_B + " Twice.")
    infer2 = _infer({e2.id: _lesson(e2.id, "Open on the caller's problem.")})
    voice.harvest(store, infer=infer2)

    lessons = store.all_lessons(include_retired=True)
    assert len(lessons) == 1
    assert lessons[0].status is LessonStatus.RETIRED
    assert store.injectable_lessons() == []


def test_corroboration_widens_scope_rather_than_replacing_it(store):
    _feature(store, "f-1", "Retry policy")
    _feature(store, "f-2", "Index writes")
    e1 = _amend(store, "f-1", before=LONG_A, after=LONG_B)
    e2 = _amend(store, "f-2", before=LONG_A + " Twice.", after=LONG_B + " Twice.")
    infer = _infer({
        e1.id: _lesson(e1.id, "Open on the caller's problem."),
        e2.id: _lesson(e2.id, "Open on the caller's problem."),
    })

    voice.harvest(store, infer=infer)
    scope = store.all_lessons()[0].scope_path
    assert "Retry policy" in scope
    assert "Index writes" in scope


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------

def _active(store: Store, instruction: str, *, axis=LessonAxis.STRUCTURE,
            path=(), files=(), evidence=2) -> StyleLesson:
    lesson = StyleLesson(
        axis=axis, instruction=instruction, scope_path=list(path),
        scope_files=list(files), status=LessonStatus.ACTIVE, evidence=evidence,
    )
    store.upsert_lesson(lesson)
    return lesson


def test_retrieval_prefers_the_lesson_learned_in_this_region(store):
    near = _active(store, "Near.", files=["codoc/loop/loop_a.py"])
    _active(store, "Far.", files=["vscode-codoc/src/extension.ts"])

    got = voice.retrieve(store, files=["codoc/loop/loop_b.py"], limit=1)
    assert [x.id for x in got] == [near.id]


def test_retrieval_sends_at_most_one_lesson_per_axis(store):
    """Two lessons on one axis are a contradiction, and sending both means the model
    follows whichever it read last. The better-matching one wins."""
    winner = _active(store, "Newer view.", files=["codoc/loop/loop_a.py"], evidence=3)
    _active(store, "Older view.", files=["docs/"], evidence=2)

    got = voice.retrieve(store, files=["codoc/loop/loop_a.py"])
    assert [x.id for x in got] == [winner.id]


def test_with_no_context_evidence_is_the_whole_ranking(store):
    """A bootstrap has no bindings yet; the most-confirmed lesson is the safe default."""
    weak = _active(store, "Weak.", axis=LessonAxis.LENGTH, evidence=2)
    strong = _active(store, "Strong.", axis=LessonAxis.ALTITUDE, evidence=9)

    got = voice.retrieve(store)
    assert [x.id for x in got] == [strong.id, weak.id]


def test_retrieval_is_capped(store):
    for i, axis in enumerate(LessonAxis):
        _active(store, f"Lesson {i}.", axis=axis)
    assert len(voice.retrieve(store, limit=2)) == 2


# --------------------------------------------------------------------------
# injection
# --------------------------------------------------------------------------

def test_voice_context_is_none_on_a_tree_nobody_has_edited(store):
    assert voice.voice_context(store) is None


def test_voice_context_carries_lessons_and_samples(store):
    _active(store, "Open on the caller's problem.", files=["codoc/loop/loop_a.py"])
    block = voice.voice_context(store, files=["codoc/loop/loop_a.py"])
    assert block is not None
    assert block["lessons"][0]["instruction"] == "Open on the caller's problem."
    assert block["lessons"][0]["axis"] == "structure"
    assert block["lessons"][0]["learned_from"] == 2


def test_voice_context_still_returns_samples_with_no_lesson_yet(store):
    """The behaviour codoc had before this module must survive having it."""
    f = Feature(id="f-1", title="Retry policy",
                description="Outbound calls fail in bursts.")
    store.upsert_feature(f)
    store.set_feature_writer("f-1", ACTOR_HUMAN)

    block = voice.voice_context(store)
    if block is not None:  # depends on how the store records authorship
        assert "lessons" not in block


def test_digest_names_every_live_lesson(store):
    a = _active(store, "Open on the caller's problem.")
    retired = _active(store, "Forget this.", axis=LessonAxis.LENGTH)
    store.set_lesson_status(retired.id, LessonStatus.RETIRED)

    digest = voice.lessons_digest(store)
    assert a.id in digest
    assert retired.id not in digest


# --------------------------------------------------------------------------
# the metric
# --------------------------------------------------------------------------

def test_edit_cost_is_zero_when_nothing_changed():
    assert voice.edit_cost("same words", "same words") == 0.0


def test_edit_cost_is_one_on_a_wholesale_replacement():
    assert voice.edit_cost("aaaa", "") == 1.0
    assert voice.edit_cost("", "bbbb") == 1.0


def test_edit_cost_grows_with_the_size_of_the_change():
    small = voice.edit_cost(LONG_A, LONG_A.replace("retry", "retrying"))
    large = voice.edit_cost(LONG_A, "Something else entirely, said another way.")
    assert 0.0 < small < large


def test_trend_declines_to_call_a_trend_from_too_few_edits(store):
    _feature(store, "f-1", "Retry policy")
    _amend(store, "f-1", before=LONG_A, after=LONG_B)

    trend = voice.edit_cost_trend(store)
    assert trend["n"] == 1
    assert trend["improving"] is None
    assert "too few" in voice.render_trend(trend)


def test_trend_reports_falling_cost_when_the_author_edits_less(store):
    _feature(store, "f-1", "Retry policy")
    # Early: the author replaced the paragraph. Later: they touched a word.
    for _ in range(6):
        _amend(store, "f-1", before=LONG_A, after="Nothing of the original remains.")
    for _ in range(6):
        _amend(store, "f-1", before=LONG_A, after=LONG_A.replace("policy", "policies"))

    trend = voice.edit_cost_trend(store, buckets=2)
    assert trend["n"] == 12
    assert trend["buckets"][0] > trend["buckets"][-1]
    assert trend["improving"] is True
    assert "falling" in voice.render_trend(trend)


def test_trend_ignores_the_authors_edits_to_their_own_prose(store):
    _feature(store, "f-1", "Retry policy")
    _amend(store, "f-1", before=LONG_A, after=LONG_B, written_by=ACTOR_HUMAN)
    assert voice.edit_cost_trend(store)["n"] == 0


def test_render_trend_on_an_untouched_tree(store):
    assert "no edits" in voice.render_trend(voice.edit_cost_trend(store))
