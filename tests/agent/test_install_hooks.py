"""install_mcp: register the codoc MCP server in <root>/.mcp.json."""
from __future__ import annotations

import json

from codoc.agent.install_hooks import install_mcp


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
