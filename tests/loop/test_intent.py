"""Tests for captured author intent (codoc/loop/intent.py)."""
from __future__ import annotations

import json
import time

import pytest

from codoc.loop.activity import write_activity
from codoc.loop.filenames import INTENT_FILENAME
from codoc.loop.intent import (
    _MAX_ENTRIES, record_intent, recent_intent, relevant_intent,
)


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    return d


def _entries(codoc_dir):
    path = codoc_dir / INTENT_FILENAME
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines()]


class TestRecordIntent:
    def test_appends_entry(self, codoc_dir):
        record_intent(codoc_dir, "sess-1", "make retries idempotent")
        (e,) = _entries(codoc_dir)
        assert e["session_id"] == "sess-1"
        assert e["prompt"] == "make retries idempotent"
        assert e["ts"] > 0

    def test_skips_blank_and_slash_commands(self, codoc_dir):
        record_intent(codoc_dir, "s", "")
        record_intent(codoc_dir, "s", "   ")
        record_intent(codoc_dir, "s", "/codoc:sync")
        assert _entries(codoc_dir) == []

    def test_truncates_long_prompts(self, codoc_dir):
        record_intent(codoc_dir, "s", "x" * 5000)
        (e,) = _entries(codoc_dir)
        assert len(e["prompt"]) == 2000

    def test_trims_to_bounded_tail(self, codoc_dir):
        for i in range(_MAX_ENTRIES + 10):
            record_intent(codoc_dir, "s", f"prompt {i}")
        entries = _entries(codoc_dir)
        assert len(entries) == _MAX_ENTRIES
        assert entries[-1]["prompt"] == f"prompt {_MAX_ENTRIES + 9}"

    def test_tolerates_missing_dir(self, tmp_path):
        # Never raises even when .codoc does not exist.
        record_intent(tmp_path / "nope" / ".codoc", "s", "hello")


class TestRecentIntent:
    def test_empty_when_no_file(self, codoc_dir):
        assert recent_intent(codoc_dir) == []

    def test_returns_recent_tail_oldest_first(self, codoc_dir):
        for p in ("first", "second", "third", "fourth"):
            record_intent(codoc_dir, "s", p)
        assert recent_intent(codoc_dir) == ["second", "third", "fourth"]

    def test_filters_stale_entries(self, codoc_dir):
        record_intent(codoc_dir, "s", "old")
        path = codoc_dir / INTENT_FILENAME
        entry = json.loads(path.read_text())
        entry["ts"] = time.time() - 10 * 60 * 60
        path.write_text(json.dumps(entry) + "\n")
        record_intent(codoc_dir, "s", "fresh")
        assert recent_intent(codoc_dir) == ["fresh"]

    def test_prefers_epoch_owning_session(self, codoc_dir):
        record_intent(codoc_dir, "other", "someone else's ask")
        record_intent(codoc_dir, "mine", "my ask")
        write_activity(codoc_dir, {
            "version": 1,
            "epoch": {"id": "ep-mine", "origin": "interactive", "open": True,
                      "started_at": "", "ended_at": None},
            "touched": {}, "recent": [],
        })
        assert recent_intent(codoc_dir) == ["my ask"]

    def test_falls_back_when_epoch_session_captured_nothing(self, codoc_dir):
        record_intent(codoc_dir, "other", "the only ask")
        write_activity(codoc_dir, {
            "version": 1,
            "epoch": {"id": "ep-mine", "origin": "interactive", "open": True,
                      "started_at": "", "ended_at": None},
            "touched": {}, "recent": [],
        })
        assert recent_intent(codoc_dir) == ["the only ask"]

    def test_collapses_consecutive_duplicates(self, codoc_dir):
        record_intent(codoc_dir, "s", "same ask")
        record_intent(codoc_dir, "s", "same ask")
        assert recent_intent(codoc_dir) == ["same ask"]

    def test_tolerates_corrupt_lines(self, codoc_dir):
        path = codoc_dir / INTENT_FILENAME
        path.write_text("{not json\n")
        record_intent(codoc_dir, "s", "good")
        assert recent_intent(codoc_dir) == ["good"]


class TestRelevantIntent:
    """Recency answers "what was the user just doing"; a description needs to
    know which ask was about *this* code."""

    def test_picks_the_prompt_about_the_changed_symbols(self, codoc_dir):
        record_intent(codoc_dir, "s", "rework the billing invoice totals")
        record_intent(codoc_dir, "s", "make the ollama client retry on timeout")
        record_intent(codoc_dir, "s", "update the readme")
        got = relevant_intent(codoc_dir, {"mini.py::OllamaClient.complete"})
        assert "make the ollama client retry on timeout" in got
        assert "rework the billing invoice totals" not in got

    def test_keeps_the_newest_prompt_even_with_no_overlap(self, codoc_dir):
        """A follow-up turn ("now do the same for the other one") shares no
        words with the diff, and recency is the only signal it leaves."""
        record_intent(codoc_dir, "s", "make the ollama client retry on timeout")
        record_intent(codoc_dir, "s", "now do the same over there")
        got = relevant_intent(codoc_dir, {"mini.py::OllamaClient.complete"})
        assert got[-1] == "now do the same over there"

    def test_splits_camel_case_to_find_the_match(self, codoc_dir):
        record_intent(codoc_dir, "s", "the retry logic in the ollama client is wrong")
        record_intent(codoc_dir, "s", "unrelated later ask")
        got = relevant_intent(codoc_dir, {"m.py::OllamaClient.complete"})
        assert "the retry logic in the ollama client is wrong" in got

    def test_stop_words_do_not_create_a_match(self, codoc_dir):
        record_intent(codoc_dir, "s", "can you make this file work for the tests")
        record_intent(codoc_dir, "s", "the newest ask")
        got = relevant_intent(codoc_dir, {"billing.py::Invoice.total"})
        assert got == ["the newest ask"]

    def test_returns_chronological_order(self, codoc_dir):
        record_intent(codoc_dir, "s", "add retry to the ollama client")
        record_intent(codoc_dir, "s", "give the ollama client a timeout")
        got = relevant_intent(codoc_dir, {"m.py::OllamaClient"})
        assert got == ["add retry to the ollama client",
                       "give the ollama client a timeout"]

    def test_falls_back_to_recency_without_terms(self, codoc_dir):
        record_intent(codoc_dir, "s", "first")
        record_intent(codoc_dir, "s", "second")
        assert relevant_intent(codoc_dir, set()) == ["first", "second"]

    def test_empty_when_no_file(self, codoc_dir):
        assert relevant_intent(codoc_dir, {"a.py::b"}) == []


class TestLoopAThreading:
    def test_author_intent_reaches_the_llm_changes(self, tmp_path):
        """Captured intent must ride into the tree-update change set so the
        model can state the author's why instead of guessing it."""
        from codoc.loop.diff import ChangeSet, ChunkRef
        from codoc.loop.loop_a import apply_changeset
        from codoc.store.db import open_store

        codoc_dir = tmp_path / ".codoc"
        codoc_dir.mkdir()
        record_intent(codoc_dir, "s", "add a retry guard to fan-out")

        seen: dict = {}

        def capture(changes, subtree, all_titles, *, repo_name="codebase", config=None, **_kw):
            seen.update(changes)
            return []

        store = open_store(tmp_path)
        try:
            cs = ChangeSet(added=[ChunkRef("a.py", "a.py::guard", "fp", "def guard(): ...")])
            res = apply_changeset(cs, store, propose=capture, codoc_dir=str(codoc_dir))
        finally:
            store.close()

        assert res.llm_called
        # Wrapped with a citation id, so a description that rests on what the
        # author asked for can name it (see codoc.loop.warrant).
        assert seen["author_intent"] == [
            {"id": "a1", "asked": "add a retry guard to fan-out"}]

    def test_no_intent_key_when_nothing_captured(self, tmp_path):
        from codoc.loop.diff import ChangeSet, ChunkRef
        from codoc.loop.loop_a import apply_changeset
        from codoc.store.db import open_store

        codoc_dir = tmp_path / ".codoc"
        codoc_dir.mkdir()
        seen: dict = {}

        def capture(changes, subtree, all_titles, *, repo_name="codebase", config=None, **_kw):
            seen.update(changes)
            return []

        store = open_store(tmp_path)
        try:
            cs = ChangeSet(added=[ChunkRef("a.py", "a.py::guard", "fp", "def guard(): ...")])
            apply_changeset(cs, store, propose=capture, codoc_dir=str(codoc_dir))
        finally:
            store.close()

        assert "author_intent" not in seen


class TestDirectiveIntentCitation:
    """W6: build_directive embeds the author's captured prompt so the realizing
    agent implements the stated goal rather than a reconstruction."""

    def test_update_directive_cites_freshest_intent(self):
        from codoc.loop.loop_b import build_directive
        from codoc.model.event import NodeOp, NodeOpKind
        from codoc.model.feature import Feature
        from codoc.store.db import open_store
        import tempfile

        d = tempfile.mkdtemp()
        store = open_store(d)
        try:
            f = Feature(title="Fan-out", description="Old.")
            store.upsert_feature(f)
            op = NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id,
                        description="Retries must be idempotent.")
            text = build_directive(op, store,
                                   author_intent=["first ask", "make fan-out retries idempotent"])
        finally:
            store.close()
        assert 'Author asked: "make fan-out retries idempotent"' in text
        assert "first ask" not in text  # only the freshest

    def test_new_and_retire_directives_cite_intent(self):
        from codoc.loop.loop_b import build_directive
        from codoc.model.event import NodeOp, NodeOpKind
        from codoc.store.db import open_store
        import tempfile

        d = tempfile.mkdtemp()
        store = open_store(d)
        try:
            add = NodeOp(kind=NodeOpKind.ADD_NODE, title="New thing",
                         description="do it")
            add_text = build_directive(add, store, author_intent=["build the thing"])
        finally:
            store.close()
        assert 'Author asked: "build the thing"' in add_text

    def test_no_intent_leaves_directive_unchanged(self):
        from codoc.loop.loop_b import build_directive
        from codoc.model.event import NodeOp, NodeOpKind
        from codoc.store.db import open_store
        import tempfile

        d = tempfile.mkdtemp()
        store = open_store(d)
        try:
            op = NodeOp(kind=NodeOpKind.ADD_NODE, title="X", description="y")
            assert "Author asked" not in build_directive(op, store)
            assert "Author asked" not in build_directive(op, store, author_intent=[])
            assert "Author asked" not in build_directive(op, store, author_intent=["   "])
        finally:
            store.close()

    def test_long_intent_is_truncated(self):
        from codoc.loop.loop_b import build_directive
        from codoc.model.event import NodeOp, NodeOpKind
        from codoc.store.db import open_store
        import tempfile

        d = tempfile.mkdtemp()
        store = open_store(d)
        try:
            op = NodeOp(kind=NodeOpKind.ADD_NODE, title="X", description="y")
            text = build_directive(op, store, author_intent=["z" * 500])
        finally:
            store.close()
        assert "…" in text
        assert "z" * 300 not in text
