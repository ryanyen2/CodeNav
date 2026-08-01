"""The server-owned realize worker step (codoc/serve/realize_hub.py).

process_ready is the pure orchestration step (run/agent/readers injected): it fires
only on handed-off + undone directives, derives the edit scope from the feature's
bindings, opens a PR via realize_directive, and records done so it never re-fires.
"""
from __future__ import annotations

import json

from codoc.serve.realize_hub import process_ready, scope_for
from codoc.serve.realize_trigger import read_done


def _write_sidecar(cd, by_feature):
    (cd / "tree.bindings.json").write_text(json.dumps({"by_feature": by_feature, "features": {}}))


class _Run:
    def __init__(self):
        self.argvs = []

    def __call__(self, argv, cwd=None):
        self.argvs.append(argv)
        return 0


def test_scope_from_feature_bindings(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    _write_sidecar(cd, {"f-1": [{"file": "codoc/a.py"}, {"file": "codoc/b.py"},
                                {"file": "codoc/a.py"}]})
    assert scope_for(str(cd), "f-1") == ["codoc/a.py", "codoc/b.py"]
    assert scope_for(str(cd), "f-none") is None  # no bindings → unscoped
    assert scope_for(str(cd), "") is None


def test_process_ready_realizes_handed_off_and_marks_done(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    _write_sidecar(cd, {"f-1": [{"file": "codoc/a.py"}]})

    status = {"state": "awaiting_impl"}
    manifest = [
        {"id": "d-1", "feature_id": "f-1", "handed_off": True, "text": "do it"},
        {"id": "d-2", "feature_id": "f-1", "handed_off": False, "text": "held"},  # draft → skipped
    ]
    run = _Run()

    seen = {}

    def agent(directive, worktree_path, scope=None):
        seen["scope"] = scope
        seen["id"] = directive["id"]
        return ["codoc/a.py"]  # an in-scope change

    done = process_ready(
        str(tmp_path), str(cd), base="main", run=run, agent=agent,
        read_status=lambda _cd: status, read_manifest=lambda _cd: manifest,
        printer=lambda *_: None,
    )
    assert done == ["d-1"]                       # only the handed-off directive
    assert seen["scope"] == ["codoc/a.py"]        # scoped to the feature's bindings
    assert "d-1" in read_done(str(cd))            # recorded so it never re-fires
    # a PR was opened on a feature branch, never a push to main
    assert any(a[:3] == ["gh", "pr", "create"] for a in run.argvs)
    assert not any(a[:2] == ["git", "push"] for a in run.argvs)


def test_process_ready_skips_already_done(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    _write_sidecar(cd, {})
    from codoc.serve.realize_trigger import mark_done
    mark_done(str(cd), "d-1")

    manifest = [{"id": "d-1", "feature_id": "", "handed_off": True, "text": "x"}]
    calls = {"agent": 0}

    def agent(*a, **k):
        calls["agent"] += 1
        return []

    done = process_ready(str(tmp_path), str(cd), run=_Run(), agent=agent,
                         read_status=lambda _c: {"state": "awaiting_impl"},
                         read_manifest=lambda _c: manifest, printer=lambda *_: None)
    assert done == []
    assert calls["agent"] == 0  # already-done directive is never re-run


def test_process_ready_out_of_scope_write_opens_no_pr_but_marks_done(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    _write_sidecar(cd, {"f-1": [{"file": "codoc/a.py"}]})
    manifest = [{"id": "d-9", "feature_id": "f-1", "handed_off": True, "text": "x"}]
    run = _Run()

    def rogue_agent(directive, worktree_path, scope=None):
        return ["codoc/a.py", ".github/workflows/ci.yml"]  # escapes scope

    done = process_ready(str(tmp_path), str(cd), run=run, agent=rogue_agent,
                         read_status=lambda _c: {"state": "awaiting_impl"},
                         read_manifest=lambda _c: manifest, printer=lambda *_: None)
    assert done == []  # not realized (no PR)
    assert not any(a[:3] == ["gh", "pr", "create"] for a in run.argvs)
    assert "d-9" in read_done(str(cd))  # but recorded so the unsafe directive won't retry


def test_process_ready_noop_when_not_awaiting_impl(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    _write_sidecar(cd, {})
    manifest = [{"id": "d-1", "feature_id": "", "handed_off": True, "text": "x"}]
    done = process_ready(str(tmp_path), str(cd), run=_Run(), agent=lambda *a, **k: ["x"],
                         read_status=lambda _c: {"state": "in_sync"},
                         read_manifest=lambda _c: manifest, printer=lambda *_: None)
    assert done == []
