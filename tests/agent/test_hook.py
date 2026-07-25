"""Tests for the codoc CC hook handler (codoc/agent/hook.py)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from codoc.agent.hook import (
    _find_codoc_dir,
    _rel,
    _resolve_features,
    handle_pre_tool,
    handle_session_end,
    handle_session_start,
    handle_stop,
    main,
)
from codoc.loop.activity import ACTIVITY_FILENAME, read_activity


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path):
    """Minimal repo with a .codoc dir and a pre-written sidecar."""
    root = tmp_path / "repo"
    root.mkdir()
    codoc_dir = root / ".codoc"
    codoc_dir.mkdir()

    # Write a sidecar with one file→feature mapping.
    sidecar = {
        "version": 1,
        "by_feature": {"f-abc": [{"file": "src/app.py", "symbol": "app.run"}]},
        "by_file": {"src/app.py": [{"symbol": "app.run", "feature_id": "f-abc", "feature_title": "App runner"}]},
        "features": {"f-abc": {"title": "App runner", "parent_id": None}},
    }
    (codoc_dir / "tree.bindings.json").write_text(json.dumps(sidecar))
    return root, codoc_dir


def _payload(cwd: str, **extra) -> dict:
    return {"session_id": "sess-1", "cwd": cwd, **extra}


@pytest.fixture(autouse=True)
def no_real_spawn(monkeypatch):
    """Capture (and never actually launch) the Stop hook's detached reflect."""
    import subprocess
    calls: list[list[str]] = []

    def fake_popen(cmd, *a, **k):
        calls.append(cmd)
        return None

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


# ── Unit helpers ──────────────────────────────────────────────────────────────

def test_find_codoc_dir_finds_parent(tmp_path):
    (tmp_path / ".codoc").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert _find_codoc_dir(str(sub)) == str(tmp_path / ".codoc")


def test_find_codoc_dir_returns_none_when_absent(tmp_path):
    assert _find_codoc_dir(str(tmp_path)) is None


def test_rel_inside_root(tmp_path):
    f = tmp_path / "src" / "main.py"
    assert _rel(str(f), str(tmp_path)) == "src/main.py"


def test_rel_outside_root_returns_none(tmp_path):
    assert _rel("/etc/passwd", str(tmp_path)) is None


def test_resolve_features_from_sidecar(repo):
    root, codoc_dir = repo
    fids = _resolve_features("src/app.py", str(codoc_dir))
    assert fids == ["f-abc"]


def test_resolve_features_missing_file(repo):
    root, codoc_dir = repo
    assert _resolve_features("nonexistent.py", str(codoc_dir)) == []


def test_resolve_features_corrupt_sidecar(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    (codoc_dir / "tree.bindings.json").write_text("{corrupt")
    assert _resolve_features("src/app.py", str(codoc_dir)) == []


@pytest.fixture
def shared_file_repo(tmp_path):
    """A single file bound to two features (a shared module) — the multi-feature
    fan-out scenario: editing it should attribute to whichever feature is
    actually being realized, not to both."""
    root = tmp_path / "repo"
    root.mkdir()
    codoc_dir = root / ".codoc"
    codoc_dir.mkdir()
    sidecar = {
        "version": 1,
        "by_feature": {
            "f-one": [{"file": "src/shared.py", "symbol": "One.run"}],
            "f-two": [{"file": "src/shared.py", "symbol": "Two.run"}],
        },
        "by_file": {"src/shared.py": [
            {"symbol": "One.run", "feature_id": "f-one", "feature_title": "One"},
            {"symbol": "Two.run", "feature_id": "f-two", "feature_title": "Two"},
        ]},
        "features": {"f-one": {"title": "One", "parent_id": None},
                     "f-two": {"title": "Two", "parent_id": None}},
    }
    (codoc_dir / "tree.bindings.json").write_text(json.dumps(sidecar))
    return root, codoc_dir


def test_resolve_features_narrows_to_in_flight_directive(shared_file_repo):
    """Only the feature with a handed-off directive lights up — not its sibling
    that merely shares the same file."""
    root, codoc_dir = shared_file_repo
    from codoc.loop.edits import Directive, write_manifest

    write_manifest(str(codoc_dir), [
        Directive(id="d-1", feature_id="f-one", kind="amend", handed_off=True),
    ])
    # A handed-off directive without realize.md reads back as stale (read_manifest's
    # contract) — mirror the real state: handed-off directives live in realize.md.
    (codoc_dir / "realize.md").write_text("### d-1\n")

    assert _resolve_features("src/shared.py", str(codoc_dir)) == ["f-one"]


def test_resolve_features_falls_back_with_no_directive(shared_file_repo):
    """No realize directive at all (ad hoc editing, no active realize session) →
    the full fan-out, since there's no signal to narrow with."""
    root, codoc_dir = shared_file_repo
    assert set(_resolve_features("src/shared.py", str(codoc_dir))) == {"f-one", "f-two"}


def test_resolve_features_ignores_draft_directive(shared_file_repo):
    """A held draft (handed_off=False) hasn't been sent to the agent yet — it must
    not narrow the attribution, or an unrelated ad hoc edit would misattribute."""
    root, codoc_dir = shared_file_repo
    from codoc.loop.edits import Directive, write_manifest

    write_manifest(str(codoc_dir), [
        Directive(id="d-1", feature_id="f-one", kind="amend", handed_off=False),
    ])

    assert set(_resolve_features("src/shared.py", str(codoc_dir))) == {"f-one", "f-two"}


# ── symbol-level narrowing (Edit old_string → enclosing symbol → feature) ──────

_MULTI_SYMBOL_SRC = '''\
class Alpha:
    def run(self):
        return "alpha-body"


class Beta:
    def run(self):
        return "beta-body"
'''


@pytest.fixture
def multi_symbol_repo(tmp_path):
    """One real Python file with two classes, each (and Beta's method) bound to a
    DIFFERENT feature — so a symbol-scoped edit must pick exactly one."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    codoc_dir = root / ".codoc"
    codoc_dir.mkdir()
    (root / "src" / "shared.py").write_text(_MULTI_SYMBOL_SRC)
    sidecar = {
        "version": 1,
        "by_feature": {},
        "by_file": {"src/shared.py": [
            {"symbol": "src/shared.py::Alpha", "feature_id": "f-alpha", "feature_title": "Alpha"},
            {"symbol": "src/shared.py::Beta", "feature_id": "f-beta", "feature_title": "Beta"},
            {"symbol": "src/shared.py::Beta.run", "feature_id": "f-beta-run", "feature_title": "Beta.run"},
        ]},
        "features": {"f-alpha": {"title": "Alpha"}, "f-beta": {"title": "Beta"},
                     "f-beta-run": {"title": "Beta.run"}},
    }
    (codoc_dir / "tree.bindings.json").write_text(json.dumps(sidecar))
    return root, codoc_dir


def _edit(root, old, new="x"):
    fp = str(root / "src" / "shared.py")
    return {"tool_name": "Edit", "tool_input": {"file_path": fp, "old_string": old, "new_string": new}, "abs_path": fp}


def test_symbol_scope_attributes_edit_to_the_edited_symbol(multi_symbol_repo):
    """An Edit whose old_string lives inside Alpha attributes to Alpha's feature
    only — not Beta's, even though both are bound to the same file."""
    root, codoc_dir = multi_symbol_repo
    kw = _edit(root, 'return "alpha-body"')
    assert _resolve_features("src/shared.py", str(codoc_dir), **kw) == ["f-alpha"]


def test_symbol_scope_picks_innermost_symbol(multi_symbol_repo):
    """An edit inside Beta.run resolves to the innermost bound symbol (the method
    feature f-beta-run), not the enclosing class feature f-beta."""
    root, codoc_dir = multi_symbol_repo
    kw = _edit(root, 'return "beta-body"')
    assert _resolve_features("src/shared.py", str(codoc_dir), **kw) == ["f-beta-run"]


def test_symbol_scope_finds_anchor_after_apply(multi_symbol_repo):
    """At the PostToolUse phase old_string is gone from the file; the new_string
    fallback still locates the edited symbol."""
    root, codoc_dir = multi_symbol_repo
    fp = str(root / "src" / "shared.py")
    # Simulate the applied state: the file now contains the new text.
    (root / "src" / "shared.py").write_text(_MULTI_SYMBOL_SRC.replace('"alpha-body"', '"alpha-new"'))
    kw = {"tool_name": "Edit",
          "tool_input": {"file_path": fp, "old_string": 'return "alpha-body"', "new_string": 'return "alpha-new"'},
          "abs_path": fp}
    assert _resolve_features("src/shared.py", str(codoc_dir), **kw) == ["f-alpha"]


def test_symbol_scope_falls_back_when_anchor_missing(multi_symbol_repo):
    """When old_string can't be located (neither old nor new present), symbol
    scoping yields nothing and the resolver falls back to the file-level set."""
    root, codoc_dir = multi_symbol_repo
    kw = _edit(root, "this text is not in the file at all")
    assert set(_resolve_features("src/shared.py", str(codoc_dir), **kw)) == {"f-alpha", "f-beta", "f-beta-run"}


def test_symbol_scope_write_falls_back_to_file_level(multi_symbol_repo):
    """A whole-file Write carries no symbol anchor → file-level fallback (all
    bound features), never a wrong single-symbol guess."""
    root, codoc_dir = multi_symbol_repo
    fp = str(root / "src" / "shared.py")
    kw = {"tool_name": "Write", "tool_input": {"file_path": fp, "content": _MULTI_SYMBOL_SRC}, "abs_path": fp}
    assert set(_resolve_features("src/shared.py", str(codoc_dir), **kw)) == {"f-alpha", "f-beta", "f-beta-run"}


# ── session-start ─────────────────────────────────────────────────────────────

def test_session_start_writes_open_epoch(repo):
    root, codoc_dir = repo
    payload = _payload(str(root))
    handle_session_start(payload, str(codoc_dir))

    data = read_activity(str(codoc_dir))
    assert data["epoch"]["open"] is True
    assert data["epoch"]["origin"] == "interactive"
    assert data["epoch"]["started_at"] is not None
    assert data["touched"] == {}


def test_session_start_records_loop_b_origin(repo, monkeypatch):
    root, codoc_dir = repo
    monkeypatch.setenv("CODOC_EPOCH_ORIGIN", "loop_b")
    handle_session_start(_payload(str(root)), str(codoc_dir))
    data = read_activity(str(codoc_dir))
    assert data["epoch"]["origin"] == "loop_b"


def test_session_start_resets_touched(repo):
    root, codoc_dir = repo
    # Write stale touched data from a previous epoch.
    stale = {"version": 1, "epoch": {"id": "old", "origin": "interactive", "open": False,
                                      "started_at": None, "ended_at": None},
             "touched": {"old_file.py": {}}, "recent": [{"old": True}]}
    (codoc_dir / ACTIVITY_FILENAME).write_text(json.dumps(stale))

    handle_session_start(_payload(str(root)), str(codoc_dir))
    data = read_activity(str(codoc_dir))
    assert data["touched"] == {}
    assert data["recent"] == []


# ── stop ──────────────────────────────────────────────────────────────────────

def test_stop_closes_epoch(repo):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_stop(_payload(str(root)), str(codoc_dir))

    data = read_activity(str(codoc_dir))
    assert data["epoch"]["open"] is False
    assert data["epoch"]["ended_at"] is not None


def test_stop_preserves_touched(repo):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))

    # Simulate a pre-tool having been recorded.
    handle_pre_tool(
        _payload(str(root), tool_name="Edit",
                 tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_stop(_payload(str(root)), str(codoc_dir))

    data = read_activity(str(codoc_dir))
    assert "src/app.py" in data["touched"]  # kept after close


def test_stop_spawns_reflect_when_no_daemon(repo, no_real_spawn):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Edit", tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_stop(_payload(str(root)), str(codoc_dir))

    assert len(no_real_spawn) == 1
    cmd = no_real_spawn[0]
    assert "reflect" in cmd and "--scope" in cmd
    assert "src/app.py" in cmd[cmd.index("--scope") + 1]


def test_stop_skips_reflect_when_daemon_running(repo, no_real_spawn):
    import os
    root, codoc_dir = repo
    (codoc_dir / "watch.pid").write_text(str(os.getpid()))  # a "live" daemon
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Edit", tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_stop(_payload(str(root)), str(codoc_dir))

    assert no_real_spawn == []  # daemon owns the epoch-close reconcile


def test_stop_skips_reflect_for_loop_b_origin(repo, no_real_spawn, monkeypatch):
    root, codoc_dir = repo
    monkeypatch.setenv("CODOC_EPOCH_ORIGIN", "loop_b")
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Edit", tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_stop(_payload(str(root)), str(codoc_dir))

    assert no_real_spawn == []  # Loop B reflects its own epoch


def test_stop_skips_reflect_with_no_writes(repo, no_real_spawn):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(  # a Read, not a write
        _payload(str(root), tool_name="Read", tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_stop(_payload(str(root)), str(codoc_dir))

    assert no_real_spawn == []


def test_stop_reflects_every_turn_even_when_epoch_already_closed(repo, no_real_spawn):
    """`Stop` fires at the end of EVERY turn, not only the last: turn 1's Stop
    closes the epoch, but turn 2's writes must still reflect daemonless — the
    already_closed skip applies only to the SessionEnd duplicate."""
    root, codoc_dir = repo
    edit = _payload(str(root), tool_name="Edit",
                    tool_input={"file_path": str(root / "src/app.py")})
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(edit, str(codoc_dir))
    handle_stop(_payload(str(root)), str(codoc_dir))   # turn 1 — closes the epoch
    handle_pre_tool(edit, str(codoc_dir))              # turn 2 writes more
    handle_stop(_payload(str(root)), str(codoc_dir))   # turn 2 — epoch already closed

    assert len(no_real_spawn) == 2


def test_session_end_after_stop_does_not_double_spawn(repo, no_real_spawn):
    """Clean exit fires Stop then SessionEnd — exactly ONE reflect must launch
    (the SessionEnd duplicate sees the epoch already closed and skips)."""
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Edit", tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_stop(_payload(str(root)), str(codoc_dir))
    handle_session_end(_payload(str(root)), str(codoc_dir))

    assert len(no_real_spawn) == 1


def test_session_end_reflects_when_stop_was_skipped(repo, no_real_spawn):
    """Esc/kill path: no Stop fired, so SessionEnd finds the epoch still open —
    it must close it AND reflect (the backstop WS1.3 exists for)."""
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Edit", tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_session_end(_payload(str(root)), str(codoc_dir))

    assert len(no_real_spawn) == 1
    data = read_activity(str(codoc_dir))
    assert data["epoch"]["open"] is False


def test_stale_session_close_leaves_newer_epoch_alone(repo, no_real_spawn):
    """A delayed Stop/SessionEnd from an OLD session must not close a NEWER
    session's live epoch (ownership guard: epoch id encodes the session id).
    The stale close is a no-op; the epoch lease expires it if truly dead."""
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))            # sess-1
    handle_session_start({"session_id": "sess-2", "cwd": str(root)}, str(codoc_dir))
    handle_pre_tool(
        {"session_id": "sess-2", "cwd": str(root), "tool_name": "Edit",
         "tool_input": {"file_path": str(root / "src/app.py")}},
        str(codoc_dir),
    )
    handle_session_end(_payload(str(root)), str(codoc_dir))              # sess-1, delayed

    data = read_activity(str(codoc_dir))
    assert data["epoch"]["open"] is True
    assert data["epoch"]["id"] == "ep-sess-2"
    assert no_real_spawn == []


# ── pre-tool / post-tool ──────────────────────────────────────────────────────

def test_pre_tool_records_read(repo):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Read",
                 tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )

    data = read_activity(str(codoc_dir))
    assert "src/app.py" in data["touched"]
    entry = data["touched"]["src/app.py"]
    assert "f-abc" in entry["feature_ids"]
    assert entry["mode"] == "read"
    assert len(data["recent"]) == 1
    assert data["recent"][0]["phase"] == "pre"


def test_pre_tool_write_upgrades_mode(repo):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    # First a read…
    handle_pre_tool(
        _payload(str(root), tool_name="Read",
                 tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    # …then a write — mode should become "write".
    handle_pre_tool(
        _payload(str(root), tool_name="Edit",
                 tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    data = read_activity(str(codoc_dir))
    assert data["touched"]["src/app.py"]["mode"] == "write"


def test_pre_tool_outside_repo_is_ignored(repo):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Edit",
                 tool_input={"file_path": "/etc/passwd"}),
        str(codoc_dir),
    )
    data = read_activity(str(codoc_dir))
    assert data["touched"] == {}


# ── main dispatch + safety ────────────────────────────────────────────────────

def test_main_no_codoc_dir_exits_zero(tmp_path, monkeypatch):
    """No .codoc → hook exits 0 without writing anything."""
    payload = json.dumps({"session_id": "x", "cwd": str(tmp_path)})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    assert main(["session-start"]) == 0


def test_main_corrupt_stdin_exits_zero(repo):
    root, codoc_dir = repo
    with patch("sys.stdin", __import__("io").StringIO("{bad json")):
        assert main(["session-start"]) == 0


def test_main_unknown_event_exits_zero(repo):
    root, codoc_dir = repo
    payload = json.dumps({"cwd": str(root)})
    with patch("sys.stdin", __import__("io").StringIO(payload)):
        assert main(["unknown-event"]) == 0


def test_atomic_write_no_tmp_left(repo):
    root, codoc_dir = repo
    payload = _payload(str(root))
    handle_session_start(payload, str(codoc_dir))

    tmp = codoc_dir / (ACTIVITY_FILENAME + ".tmp")
    assert not tmp.exists(), "tmp file should have been renamed to final dest"


# ── Layer-1 fallback: user-prompt hook drains the inbox with no daemon ─────────

def test_user_prompt_drains_inbox_when_no_daemon(tmp_path, capsys):
    """Accepting a code-implying plan with no `codoc watch` running should still
    queue realize.md on the next prompt (the daemon-free fallback)."""
    from codoc.agent.hook import handle_user_prompt
    from codoc.loop import inbox
    from codoc.mcp import tools

    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    cd = str(root / ".codoc")

    # A plan placeholder (realized=False ⇒ code-implying) accepted in the IDE.
    eid = tools.plan_add(cd, title="New thing", description="do the thing")["event_id"]
    inbox.append_verdict(cd, eid, accept=True)

    with patch("codoc.loop.watch.daemon_running", return_value=False):
        handle_user_prompt({}, cd)

    # Verdict drained, placeholder applied, and realize.md queued + nudged.
    assert inbox.read_verdicts(cd) == []
    assert (root / ".codoc" / "realize.md").exists()
    assert "realize" in capsys.readouterr().out


def test_user_prompt_defers_to_running_daemon(tmp_path):
    """When a daemon owns the repo, the hook must NOT drain the inbox itself."""
    from codoc.agent.hook import handle_user_prompt
    from codoc.loop import inbox
    from codoc.mcp import tools

    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    cd = str(root / ".codoc")
    eid = tools.plan_add(cd, title="New thing", description="do the thing")["event_id"]
    inbox.append_verdict(cd, eid, accept=True)

    with patch("codoc.loop.watch.daemon_running", return_value=True):
        handle_user_prompt({}, cd)

    assert [v.event_id for v in inbox.read_verdicts(cd)] == [eid]  # left for the daemon
