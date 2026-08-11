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
from codoc.model.event import ACTOR_HUMAN, ACTOR_LOOP, NodeOp, NodeOpKind
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
