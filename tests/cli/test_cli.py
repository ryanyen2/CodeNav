"""Phase 7 — the 4-command CLI."""
from __future__ import annotations

from typer.testing import CliRunner

from codoc.cli.main import app
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store

runner = CliRunner()


def test_help_lists_four_commands():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in ("init", "watch", "status", "sync"):
        assert cmd in r.output


def test_status_reports_features_and_pending(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    s = open_store(cd)
    s.upsert_feature(Feature(title="Thing one"))
    s.append_event(Event(source="loop_a", applied=False,
                         op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Proposed two")))
    s.close()

    r = runner.invoke(app, ["status", "--root", str(tmp_path)])
    assert r.exit_code == 0
    assert "1 features" in r.output
    assert "1 pending" in r.output
    assert "Proposed two" in r.output


def test_each_command_has_help():
    for cmd in ("init", "watch", "status", "sync", "propose", "install-hooks"):
        r = runner.invoke(app, [cmd, "--help"])
        assert r.exit_code == 0, f"{cmd} --help failed: {r.output}"


def test_help_lists_new_commands():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "propose" in r.output
    assert "install-hooks" in r.output


def test_propose_creates_pending_event(tmp_path):
    """``codoc propose add_node`` should create a plan proposal."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    # Seed an empty store + rendered tree.
    from codoc.codoc_file.render import write_tree
    s = open_store(cd)
    write_tree(s, str(cd))
    s.close()

    r = runner.invoke(app, [
        "propose", "add_node",
        "--root", str(tmp_path),
        "--title", "Date formatting",
        "--description", "ISO-8601 helpers.",
    ])
    assert r.exit_code == 0, r.output
    assert "Proposal created" in r.output

    s2 = open_store(cd)
    pending = s2.pending_events()
    s2.close()
    assert len(pending) == 1
    assert pending[0].op.title == "Date formatting"


def test_propose_invalid_kind_exits_nonzero(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    from codoc.codoc_file.render import write_tree
    s = open_store(cd)
    write_tree(s, str(cd))
    s.close()

    r = runner.invoke(app, [
        "propose", "bad_kind",
        "--root", str(tmp_path),
        "--title", "X",
    ])
    assert r.exit_code != 0


def test_install_hooks_writes_settings_json(tmp_path):
    """install-hooks command should write .claude/settings.json."""
    r = runner.invoke(app, ["install-hooks", "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output

    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists(), "settings.json not created"

    import json
    settings = json.loads(settings_path.read_text())
    hooks = settings.get("hooks", {})
    assert "SessionStart" in hooks
    assert "Stop" in hooks
    assert "PreToolUse" in hooks
    assert "PostToolUse" in hooks


def test_install_hooks_is_idempotent(tmp_path):
    """Running install-hooks twice should not duplicate hook entries."""
    runner.invoke(app, ["install-hooks", "--root", str(tmp_path)])
    runner.invoke(app, ["install-hooks", "--root", str(tmp_path)])

    import json
    settings_path = tmp_path / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    hooks = settings.get("hooks", {})
    # Each event should have exactly ONE entry.
    for event_name in ("SessionStart", "Stop"):
        assert len(hooks[event_name]) == 1, \
            f"{event_name} has {len(hooks[event_name])} entries (expected 1)"
