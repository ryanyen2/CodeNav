"""install_mcp: register the codoc MCP server in <root>/.mcp.json."""
from __future__ import annotations

import json

from codoc.agent.install_hooks import install_hooks, install_mcp


def test_install_mcp_writes_codoc_server(tmp_path):
    install_mcp(str(tmp_path))
    data = json.loads((tmp_path / ".mcp.json").read_text())
    codoc = data["mcpServers"]["codoc"]
    assert codoc["type"] == "stdio"
    assert codoc["command"]  # resolved to codoc-mcp or python -m
    assert "args" in codoc


def test_install_mcp_is_idempotent_and_preserves_others(tmp_path):
    mcp_path = tmp_path / ".mcp.json"
    mcp_path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    install_mcp(str(tmp_path))
    install_mcp(str(tmp_path))  # twice — still one codoc entry
    data = json.loads(mcp_path.read_text())
    assert set(data["mcpServers"]) == {"other", "codoc"}
    assert data["mcpServers"]["other"] == {"command": "x"}


def test_install_hooks_ships_two_commands_and_removes_stale(tmp_path):
    """The plugin ships exactly /codoc:plan + /codoc:sync; a previously-installed
    command the plugin no longer ships (the old /codoc:realize) is removed, while
    non-codoc commands are left alone."""
    cmd_dir = tmp_path / ".claude" / "commands" / "codoc"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "realize.md").write_text("stale")
    other = tmp_path / ".claude" / "commands" / "mine.md"
    other.write_text("user command")

    install_hooks(str(tmp_path))

    assert {p.name for p in cmd_dir.glob("*.md")} == {"plan.md", "sync.md"}
    assert other.read_text() == "user command"
