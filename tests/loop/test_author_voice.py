"""Authorship signals in the describing model's context.

Two of them. `written_by` on a subtree entry says whose words are at stake in
an amend; `author_voice` shows the model how this codebase's author writes, so
a brand-new node does not arrive in house style in a tree that reads nothing
like it. Both are cues, not instructions — which is why the tests here check
that they are present and correctly scoped rather than what the model does with
them.
"""
from __future__ import annotations

import pytest

from codoc.loop.apply import apply_op
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.loop_a import apply_changeset
from codoc.loop.subtree import select_context
from codoc.model.event import (
    ACTOR_HUMAN, ACTOR_LOOP, Event, NodeOp, NodeOpKind,
)
from codoc.model.hlc import HLC
from codoc.model.voice import LessonAxis, LessonStatus, StyleLesson
from codoc.store.db import open_store

HUMAN_PROSE = (
    "Keeps the outline honest about what the code does, so a reader can trust "
    "it without opening the files."
)


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _add(store, title, description, *, file="a.py", symbol="a.py::thing"):
    apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title=title, description=description,
                    bindings=[(file, symbol)]),
             store, source="loop_a", applied=True)
    return next(f.id for f in store.list_features() if f.title == title)


class TestWrittenBy:
    def test_subtree_entry_carries_the_last_writer_role(self, store):
        fid = _add(store, "Outline freshness", HUMAN_PROSE)
        store.set_feature_writer(fid, "someone", ACTOR_HUMAN)
        subtree, _titles, _ctx = select_context(store, {"a.py"})
        assert [e["written_by"] for e in subtree] == [ACTOR_HUMAN]

    def test_loop_written_features_are_labelled_too(self, store):
        fid = _add(store, "Outline freshness", HUMAN_PROSE)
        store.set_feature_writer(fid, "loop_a", ACTOR_LOOP)
        subtree, _titles, _ctx = select_context(store, {"a.py"})
        assert [e["written_by"] for e in subtree] == [ACTOR_LOOP]

    def test_key_is_absent_when_no_writer_was_recorded(self, store):
        """A feature written before provenance was tracked must not be reported
        as anyone's writing — unknown authorship is not the same as the loop's,
        and guessing here would put a person's prose under the loose gate."""
        fid = _add(store, "Outline freshness", HUMAN_PROSE)
        store.conn.execute("DELETE FROM feature_writers WHERE feature_id=?", (fid,))
        subtree, _titles, _ctx = select_context(store, {"a.py"})
        assert "written_by" not in subtree[0]


class TestAuthorVoice:
    def test_returns_human_written_descriptions_newest_first(self, store):
        first = _add(store, "One", HUMAN_PROSE, symbol="a.py::one")
        second = _add(store, "Two", "Reads the queue before anyone writes to it, "
                                    "so a half-written entry is never acted on.",
                      symbol="a.py::two")
        store.set_feature_writer(first, "me", ACTOR_HUMAN)
        store.set_feature_writer(second, "me", ACTOR_HUMAN)
        voice = store.human_written_descriptions()
        assert voice[0].startswith("Reads the queue")

    def test_excludes_machine_written_prose(self, store):
        fid = _add(store, "One", HUMAN_PROSE)
        store.set_feature_writer(fid, "loop_a", ACTOR_LOOP)
        assert store.human_written_descriptions() == []

    def test_excludes_retired_features(self, store):
        fid = _add(store, "One", HUMAN_PROSE)
        store.set_feature_writer(fid, "me", ACTOR_HUMAN)
        apply_op(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=fid),
                 store, source="test", applied=True)
        assert store.human_written_descriptions() == []

    def test_excludes_prose_too_short_to_have_a_register(self, store):
        fid = _add(store, "One", "A queue.")
        store.set_feature_writer(fid, "me", ACTOR_HUMAN)
        assert store.human_written_descriptions() == []

    def test_is_capped(self, store):
        for i in range(5):
            fid = _add(store, f"Feature {i}", HUMAN_PROSE + f" ({i})",
                       symbol=f"a.py::s{i}")
            store.set_feature_writer(fid, "me", ACTOR_HUMAN)
        assert len(store.human_written_descriptions(limit=2)) == 2

    def test_reaches_the_tree_update_call(self, tmp_path):
        codoc_dir = tmp_path / ".codoc"
        codoc_dir.mkdir()
        seen: dict = {}

        def capture(changes, subtree, all_titles, *, repo_name="codebase", config=None, **_kw):
            seen.update(changes)
            return []

        with open_store(tmp_path) as store:
            fid = _add(store, "Outline freshness", HUMAN_PROSE)
            store.set_feature_writer(fid, "me", ACTOR_HUMAN)
            cs = ChangeSet(added=[ChunkRef("b.py", "b.py::new", "fp", "def new(): ...")])
            apply_changeset(cs, store, propose=capture, codoc_dir=str(codoc_dir))

        assert seen["author_voice"] == [HUMAN_PROSE]

class TestVoiceLessons:
    """The learned half of the channel, at the seam where it reaches the model.

    `tests/loop/test_voice.py` covers what gets learned and how; these cover only
    that a lesson which HAS been learned arrives in the prompt, that one which has
    not been corroborated does not, and that the harvest's LLM call stays off unless
    a caller asked for it. The last one is the failure that would be least visible:
    a unit test quietly making a network call looks like a slow test, not a bug.
    """

    @staticmethod
    def _capture(seen: dict):
        def capture(changes, subtree, all_titles, *, repo_name="codebase", config=None, **_kw):
            seen.update(changes)
            return []
        return capture

    @staticmethod
    def _cs():
        return ChangeSet(added=[ChunkRef("b.py", "b.py::new", "fp", "def new(): ...")])

    def test_an_active_lesson_reaches_the_tree_update_call(self, tmp_path):
        codoc_dir = tmp_path / ".codoc"
        codoc_dir.mkdir()
        seen: dict = {}
        with open_store(tmp_path) as store:
            _add(store, "Outline freshness", HUMAN_PROSE)
            store.upsert_lesson(StyleLesson(
                axis=LessonAxis.STRUCTURE,
                instruction="Open on the problem the caller has, not the module name.",
                status=LessonStatus.ACTIVE, evidence=2,
            ))
            apply_changeset(self._cs(), store, propose=self._capture(seen),
                            codoc_dir=str(codoc_dir))

        assert seen["voice_lessons"][0]["instruction"].startswith("Open on the problem")
        assert seen["voice_lessons"][0]["learned_from"] == 2

    def test_a_provisional_lesson_does_not(self, tmp_path):
        """One rewrite is a hypothesis. It is recorded so a second can confirm it, and
        it must not shape the whole tree in the meantime."""
        codoc_dir = tmp_path / ".codoc"
        codoc_dir.mkdir()
        seen: dict = {}
        with open_store(tmp_path) as store:
            _add(store, "Outline freshness", HUMAN_PROSE)
            store.upsert_lesson(StyleLesson(
                axis=LessonAxis.STRUCTURE, instruction="Do it this way.",
                status=LessonStatus.PROVISIONAL, evidence=1,
            ))
            apply_changeset(self._cs(), store, propose=self._capture(seen),
                            codoc_dir=str(codoc_dir))

        assert "voice_lessons" not in seen

    def test_the_samples_channel_survives_having_lessons(self, tmp_path):
        """Two keys, not one: a sample says sound like this, a lesson says do this."""
        codoc_dir = tmp_path / ".codoc"
        codoc_dir.mkdir()
        seen: dict = {}
        with open_store(tmp_path) as store:
            fid = _add(store, "Outline freshness", HUMAN_PROSE)
            store.set_feature_writer(fid, "me", ACTOR_HUMAN)
            store.upsert_lesson(StyleLesson(
                axis=LessonAxis.LENGTH, instruction="Stop after the rule.",
                status=LessonStatus.ACTIVE, evidence=2,
            ))
            apply_changeset(self._cs(), store, propose=self._capture(seen),
                            codoc_dir=str(codoc_dir))

        assert seen["author_voice"] == [HUMAN_PROSE]
        assert seen["voice_lessons"][0]["instruction"] == "Stop after the rule."

    @staticmethod
    def _human_rewrote(store, fid: str):
        """Put one human rewrite of machine prose in the ledger for the harvest to find."""
        op = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid)
        op.description = ("Readers cannot trust an outline they have to verify, so this "
                          "keeps it honest about what the code does.")
        op.prev_description = HUMAN_PROSE
        op.prev_written_by = ACTOR_LOOP
        store.append_event(
            Event(op=op, actor=ACTOR_HUMAN, source="user", applied=True, at=HLC.now()))

    def test_a_bare_caller_never_triggers_the_harvest(self, tmp_path, monkeypatch):
        """The harvest makes its own model call, so it is off on the `embed_fn`
        precedent: a unit test that did not ask for one must not get one.

        Two things here are deliberate. It patches the name in `loop_a`, not in
        `loop.voice` — loop_a imported the function directly, so patching the defining
        module would leave its reference untouched and the test would pass without
        testing anything. And it RECORDS the call rather than raising on it: loop_a
        wraps the harvest in a tolerant `except Exception` so that learning can never
        sink a pass, and an `AssertionError` raised in there would be swallowed,
        leaving this green with the gate broken.
        """
        codoc_dir = tmp_path / ".codoc"
        codoc_dir.mkdir()
        calls: list = []
        monkeypatch.setattr("codoc.loop.loop_a.harvest",
                            lambda *a, **k: calls.append(k) or [])
        with open_store(tmp_path) as store:
            fid = _add(store, "Outline freshness", HUMAN_PROSE)
            self._human_rewrote(store, fid)  # there IS something to learn; still off
            apply_changeset(self._cs(), store, propose=self._capture({}),
                            codoc_dir=str(codoc_dir))
        assert calls == []

    def test_injecting_infer_voice_runs_the_harvest(self, tmp_path):
        """The seam tests use: turn learning on without turning a network call on."""
        codoc_dir = tmp_path / ".codoc"
        codoc_dir.mkdir()
        asked: list = []

        def infer(rewrites, *, config=None, doc_language=None):
            asked.append(rewrites)
            return []

        with open_store(tmp_path) as store:
            fid = _add(store, "Outline freshness", HUMAN_PROSE)
            self._human_rewrote(store, fid)
            apply_changeset(self._cs(), store, propose=self._capture({}),
                            codoc_dir=str(codoc_dir), infer_voice=infer)

        assert len(asked) == 1
        assert asked[0][0]["before"] == HUMAN_PROSE

    def test_a_pass_with_nothing_new_to_read_spends_no_call(self, tmp_path):
        """What makes harvesting on every Loop A pass affordable."""
        codoc_dir = tmp_path / ".codoc"
        codoc_dir.mkdir()
        asked: list = []

        def infer(rewrites, *, config=None, doc_language=None):
            asked.append(rewrites)
            return []

        with open_store(tmp_path) as store:
            _add(store, "Outline freshness", HUMAN_PROSE)
            apply_changeset(self._cs(), store, propose=self._capture({}),
                            codoc_dir=str(codoc_dir), infer_voice=infer)
        assert asked == []

