"""Tests for the why-evidence channel (codoc/loop/why.py).

The property under test throughout is *restraint*: this channel exists to let a
description state a reason, so anything it surfaces that is not a reason costs
more than it saves. Most of these tests pin what gets thrown away.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from codoc.loop import why as why_mod
from codoc.loop.why import (
    _body_gist,
    _directive_gist,
    _is_noise,
    _parse_log,
    clear_cache,
    commit_rationales,
    directive_rationales,
    gather_why_evidence,
    prior_rationales,
)
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import open_store


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A real git repo — the parsing is against git's actual output format."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Tester")
    return tmp_path


def _commit(repo, file, body, message):
    (repo / file).write_text(body)
    _git(repo, "add", file)
    _git(repo, "commit", "-q", "-m", message)


class TestCommitRationales:
    def test_reads_subject_and_body_for_touched_files(self, repo):
        _commit(repo, "client.py", "x = 1\n",
                "Retry Ollama calls on timeout\n\n"
                "The local server drops a connection under load and the whole "
                "run died with it.\n\nImplementation note nobody needs.")
        (entry,) = commit_rationales(repo, {"client.py"})
        assert entry["subject"] == "Retry Ollama calls on timeout"
        assert "drops a connection under load" in entry["why"]
        # Only the opening paragraph — the rest is implementation chatter.
        assert "Implementation note" not in entry["why"]

    def test_ignores_files_outside_the_request(self, repo):
        _commit(repo, "other.py", "y = 2\n", "Rework the scheduler queueing")
        assert commit_rationales(repo, {"client.py"}) == []

    def test_drops_commits_that_state_no_reason(self, repo):
        _commit(repo, "client.py", "a\n", "Add retry so a dropped call is survivable")
        _commit(repo, "client.py", "b\n", "fix typo")
        _commit(repo, "client.py", "c\n", "wip")
        subjects = [e["subject"] for e in commit_rationales(repo, {"client.py"})]
        assert subjects == ["Add retry so a dropped call is survivable"]

    def test_one_commit_covering_several_files_appears_once(self, repo):
        (repo / "a.py").write_text("1\n")
        (repo / "b.py").write_text("2\n")
        _git(repo, "add", "a.py", "b.py")
        _git(repo, "commit", "-q", "-m", "Split parsing from rendering for reuse")
        entries = commit_rationales(repo, {"a.py", "b.py"})
        assert len(entries) == 1
        assert entries[0]["files"] == ["a.py", "b.py"]

    def test_per_file_budget_caps_history_depth(self, repo):
        for i in range(6):
            _commit(repo, "client.py", f"v{i}\n",
                    f"Change number {i} for a stated and sufficient reason")
        assert len(commit_rationales(repo, {"client.py"}, per_file=2)) == 2

    def test_noisy_commit_spends_its_budget(self, repo):
        """Otherwise one file's ancient history crowds out every other file's
        recent history — a free skip makes the depth cap meaningless."""
        _commit(repo, "client.py", "old\n", "Cache the handle to avoid a reconnect")
        _commit(repo, "client.py", "new\n", "fix typo")
        assert commit_rationales(repo, {"client.py"}, per_file=1) == []

    def test_no_git_repository_is_not_an_error(self, tmp_path):
        assert commit_rationales(tmp_path, {"client.py"}) == []

    def test_missing_git_binary_is_not_an_error(self, repo, monkeypatch):
        def _boom(*_a, **_k):
            raise FileNotFoundError("git")

        monkeypatch.setattr(subprocess, "run", _boom)
        assert commit_rationales(repo, {"client.py"}) == []

    def test_log_is_read_once_per_repo(self, repo, monkeypatch):
        _commit(repo, "a.py", "1\n", "Give the parser its own error type")
        calls = []
        real = why_mod._run_git_log

        def _counting(root):
            calls.append(root)
            return real(root)

        monkeypatch.setattr(why_mod, "_run_git_log", _counting)
        for _ in range(5):
            commit_rationales(repo, {"a.py"})
        assert len(calls) == 1


class TestParsing:
    def test_body_gist_stops_at_trailers(self):
        body = ("Because the queue could be read mid-write.\n"
                "Co-Authored-By: Someone <s@example.com>\n"
                "More prose after the trailer.")
        assert _body_gist(body) == "Because the queue could be read mid-write."

    def test_body_gist_of_empty_body(self):
        assert _body_gist("") == ""

    def test_subject_with_newline_body_does_not_leak_into_file_list(self):
        rec = "\x1eabc\x1fSubject here\x1fline one\nline two\x1fsrc/a.py\nsrc/b.py\n"
        (subject, body, files) = _parse_log(rec)[0]
        assert subject == "Subject here"
        assert body.splitlines() == ["line one", "line two"]
        assert files == ["src/a.py", "src/b.py"]

    @pytest.mark.parametrize("subject", [
        "wip", "fix typo", "Merge branch 'main'", "bump version",
        "lint", "cleanup", "update dependencies", "short",
    ])
    def test_noise_subjects(self, subject):
        assert _is_noise(subject) is True

    @pytest.mark.parametrize("subject", [
        "Retry on timeout so a dropped call is survivable",
        "Serialize writes because two daemons raced the queue",
    ])
    def test_real_subjects_survive(self, subject):
        assert _is_noise(subject) is False


class TestDirectiveRationales:
    def test_extracts_the_stated_ask(self, tmp_path):
        (tmp_path / "realized.jsonl").write_text(json.dumps({
            "id": "d-1", "feature_id": "f-1", "kind": "amend",
            "text": ('UPDATE FEATURE: "Ollama backend"\n'
                     '  New intent: Retry transient failures.\n'
                     '  Bound code: mini.py::OllamaClient.complete\n'
                     '  Edit only: mini.py\n'
                     '  Author asked: "make it survive the server dropping out"'),
        }) + "\n")
        (entry,) = directive_rationales(tmp_path, {"f-1"})
        assert entry["feature_id"] == "f-1"
        assert "Retry transient failures." in entry["asked"]
        assert "survive the server dropping out" in entry["asked"]
        # The scaffolding is not rationale and must not ride along.
        assert "Edit only" not in entry["asked"]
        assert "Bound code" not in entry["asked"]

    def test_ignores_other_features(self, tmp_path):
        (tmp_path / "realized.jsonl").write_text(json.dumps({
            "id": "d-1", "feature_id": "f-9", "text": "  Author asked: \"x\"",
        }) + "\n")
        assert directive_rationales(tmp_path, {"f-1"}) == []

    def test_missing_log_is_not_an_error(self, tmp_path):
        assert directive_rationales(tmp_path, {"f-1"}) == []

    def test_placeholder_intent_is_not_evidence(self):
        assert _directive_gist("  Intent: (none)") == ""


class TestPriorRationales:
    def test_returns_recorded_reasoning_newest_first(self, tmp_path):
        with open_store(tmp_path) as store:
            from codoc.loop.apply import apply_op

            add = NodeOp(kind=NodeOpKind.ADD_NODE, title="Queue", description="A queue.",
                         rationale="first reason")
            apply_op(add, store, source="loop_a", applied=True)
            fid = store.list_features()[0].id
            apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description="A queue!",
                            rationale="second reason"),
                     store, source="loop_a", applied=True)
            (entry,) = prior_rationales(store, [fid])
        assert entry["recorded"][0] == "second reason"
        assert "first reason" in entry["recorded"]

    def test_features_with_no_rationale_are_omitted(self, tmp_path):
        with open_store(tmp_path) as store:
            from codoc.loop.apply import apply_op

            apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Q", description="d."),
                     store, source="loop_a", applied=True)
            fid = store.list_features()[0].id
            assert prior_rationales(store, [fid]) == []

    def test_no_store_is_not_an_error(self):
        assert prior_rationales(None, ["f-1"]) == []


class TestGather:
    def test_empty_when_nothing_was_recorded(self, tmp_path):
        assert gather_why_evidence(root_dir=tmp_path, files={"a.py"}) == {}

    def test_block_stays_within_budget(self, repo):
        long_reason = "Because " + ("the server misbehaves in a specific way " * 40)
        for i in range(12):
            _commit(repo, f"f{i}.py", "x\n",
                    f"Rework subsystem {i} for a stated reason\n\n{long_reason}")
        block = gather_why_evidence(root_dir=repo, files={f"f{i}.py" for i in range(12)})
        assert len(json.dumps(block)) <= why_mod._TOTAL_CHARS

    def test_evidence_reaches_the_tree_update_call(self, repo):
        """The end the whole module exists for: a reason someone wrote down in
        a commit is in front of the model when it describes that code."""
        from codoc.loop.diff import ChangeSet, ChunkRef
        from codoc.loop.loop_a import apply_changeset

        _commit(repo, "a.py", "def guard(): ...\n",
                "Guard fan-out with a retry\n\nThe upstream drops long polls.")
        codoc_dir = repo / ".codoc"
        codoc_dir.mkdir()
        seen: dict = {}

        def capture(changes, subtree, all_titles, *, repo_name="codebase", config=None):
            seen.update(changes)
            return []

        with open_store(repo) as store:
            cs = ChangeSet(added=[ChunkRef("a.py", "a.py::guard", "fp", "def guard(): ...")])
            apply_changeset(cs, store, propose=capture,
                            codoc_dir=str(codoc_dir), root_dir=str(repo))

        commits = seen["why_evidence"]["commits"]
        assert commits[0]["subject"] == "Guard fan-out with a retry"
        assert "upstream drops long polls" in commits[0]["why"]

    def test_no_evidence_key_when_the_repo_records_nothing(self, tmp_path):
        from codoc.loop.diff import ChangeSet, ChunkRef
        from codoc.loop.loop_a import apply_changeset

        codoc_dir = tmp_path / ".codoc"
        codoc_dir.mkdir()
        seen: dict = {}

        def capture(changes, subtree, all_titles, *, repo_name="codebase", config=None):
            seen.update(changes)
            return []

        with open_store(tmp_path) as store:
            cs = ChangeSet(added=[ChunkRef("a.py", "a.py::guard", "fp", "def guard(): ...")])
            apply_changeset(cs, store, propose=capture,
                            codoc_dir=str(codoc_dir), root_dir=str(tmp_path))

        assert "why_evidence" not in seen
        assert "author_voice" not in seen

    def test_a_failing_source_does_not_sink_the_others(self, repo, monkeypatch):
        _commit(repo, "a.py", "1\n", "Give the parser its own error type")

        class _Exploding:
            def events_for_feature(self, *_a, **_k):
                raise RuntimeError("db gone")

        block = gather_why_evidence(root_dir=repo, store=_Exploding(),
                                   files={"a.py"}, feature_ids={"f-1"})
        assert block["commits"][0]["subject"] == "Give the parser its own error type"
