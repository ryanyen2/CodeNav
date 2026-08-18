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


def test_install_hooks_installs_every_shipped_command_and_removes_stale(tmp_path):
    """Every command the plugin ships is installed, a previously-installed one it
    no longer ships (the old /codoc:realize) is removed, and a command of the
    user's own is left alone.

    Asserted against the plugin directory rather than a list written here. The
    list version said "exactly plan + sync" and went on passing for months after
    /codoc:ask shipped, so the one test covering this agreed with the CLI's
    hardcoded summary that the new command did not exist.
    """
    from codoc.agent.install_hooks import _plugin_dir

    shipped = {p.name for p in (_plugin_dir() / "commands" / "codoc").glob("*.md")}
    assert "ask.md" in shipped, "the walkthrough command is part of the plugin"

    cmd_dir = tmp_path / ".claude" / "commands" / "codoc"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "realize.md").write_text("stale")
    other = tmp_path / ".claude" / "commands" / "mine.md"
    other.write_text("user command")

    installed = install_hooks(str(tmp_path))

    assert {p.name for p in cmd_dir.glob("*.md")} == shipped
    assert other.read_text() == "user command"

    # What it reports is what it wrote: the CLI prints this list, and it is the
    # one line anybody reads to check the install.
    assert installed == sorted("/codoc:" + n[:-3] for n in shipped)
