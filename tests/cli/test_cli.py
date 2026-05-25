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
    for cmd in ("init", "watch", "status", "sync"):
        r = runner.invoke(app, [cmd, "--help"])
        assert r.exit_code == 0, f"{cmd} --help failed: {r.output}"
