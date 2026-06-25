"""The codoc CLI — core commands (init/watch/status/sync/realize) + plumbing."""
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
    for cmd in ("init", "watch", "status", "sync", "realize", "propose", "install-hooks"):
        r = runner.invoke(app, [cmd, "--help"])
        assert r.exit_code == 0, f"{cmd} --help failed: {r.output}"


def test_realize_rejects_unknown_engine(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    (cd / "realize.md").write_text('### 1. STEER FEATURE: "x"\n  do it\n')  # past the queue check
    r = runner.invoke(app, ["realize", "--root", str(tmp_path), "--engine", "bogus"])
    assert r.exit_code == 2


def test_realize_with_no_queue_exits_clean(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    r = runner.invoke(app, ["realize", "--root", str(tmp_path)])
    assert r.exit_code == 0
    assert "Nothing queued" in r.output


def test_realize_flushes_held_drafts(tmp_path):
    """Held-draft model: `codoc realize` IS the CLI hand-off gesture. With a held draft
    in the manifest and no realize.md, it appends the hand-off signal and runs a Loop B
    pass that (re)builds realize.md — so the flush produces the agent trigger even though
    the (absent) claude CLI then exits non-zero."""
    from codoc.codoc_file.render import write_tree
    from codoc.loop import edits as edits_channel
    from codoc.loop.loop_b import realize_path
    from codoc.model.binding import Binding

    cd = tmp_path / ".codoc"; cd.mkdir()
    s = open_store(str(cd))
    f = Feature(title="Cache", description="Caches values.")
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file="c.py", symbol_path="c.py::C", fingerprint="h"))
    write_tree(s, str(cd))
    s.close()
    # A held draft (handed_off=False) sitting in the manifest, no realize.md.
    edits_channel.write_manifest(str(cd), [edits_channel.Directive(
        id="d-held1", feature_id=f.id, kind="amend",
        text='UPDATE FEATURE: "Cache"\n  New intent: …', handed_off=False)])
    assert not realize_path(str(cd)).exists()

    r = runner.invoke(app, ["realize", "--root", str(tmp_path), "--engine", "cli"])
    # The flush ran before the engine: realize.md now exists (the held draft was handed off).
    assert realize_path(str(cd)).exists()
    assert "d-held1" in realize_path(str(cd)).read_text()
    # (exit code reflects the absent claude CLI / sdk — not our concern here.)


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


def test_accept_applies_proposal_from_cli(tmp_path):
    """``codoc accept`` drains the verdict and applies a code-drift proposal
    (no IDE, no agent spawn for an already-bound/descriptive ADD)."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    from codoc.codoc_file.render import write_tree
    s = open_store(cd)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Widget",
                        description="A small UI widget.",
                        bindings=[("ui.py", "ui.py::Widget")]))
    s.append_event(e)
    write_tree(s, str(cd))
    s.close()

    r = runner.invoke(app, ["accept", e.id, "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "accepted" in r.output

    s2 = open_store(cd)
    try:
        assert s2.pending_events() == []
        assert any(f.title == "Widget" for f in s2.list_features())
    finally:
        s2.close()


def test_reject_drops_proposal_from_cli(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    from codoc.codoc_file.render import write_tree
    s = open_store(cd)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Doomed", description="x"))
    s.append_event(e)
    write_tree(s, str(cd))
    s.close()

    r = runner.invoke(app, ["reject", e.id, "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "rejected" in r.output

    s2 = open_store(cd)
    try:
        assert s2.pending_events() == []
        assert not any(f.title == "Doomed" for f in s2.list_features())
    finally:
        s2.close()


def test_accept_unknown_event_exits_nonzero(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    from codoc.codoc_file.render import write_tree
    s = open_store(cd)
    write_tree(s, str(cd))
    s.close()
    r = runner.invoke(app, ["accept", "e-deadbeef", "--root", str(tmp_path)])
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
