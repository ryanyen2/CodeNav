"""`codoc install-hooks` sets up this machine and writes nothing tracked."""
import json
import subprocess
import sys

from codoc.claude import install_hooks as I

LOCAL = ".claude/settings.local.json"


def _repo(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))       # no global git excludes
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def test_install_writes_local_settings_registers_locally_and_clears_the_tracked_files(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    lint = {"hooks": [{"type": "command", "command": "lint"}]}
    old = {"hooks": [{"type": "command", "command": "/old/python -m codoc.claude.hook stop"}]}
    (root / ".claude" / "settings.json").write_text(json.dumps({"model": "x", "hooks": {
        "Stop": [lint, old], "SessionStart": [old]}}))
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"codoc": {"command": "/old/codoc-mcp"},
                                                                "other": {"command": "o"}}}))
    calls, run = [], subprocess.run

    def fake_run(argv, **k):
        if argv[0] != "claude":
            return run(argv, **k)
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(I, "find_claude", lambda: "claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    for _ in range(2):
        assert "/codoc:sync" in I.install_hooks(str(root))
    local = json.loads((root / LOCAL).read_text())
    assert local["permissions"]["allow"] == ["mcp__codoc__*"]
    commands = [h["command"] for es in local["hooks"].values() for e in es for h in e["hooks"]]
    assert len(commands) == 6 and all(c.startswith(f"{sys.executable} -m codoc.claude.hook ") for c in commands)
    assert json.loads((root / ".claude" / "settings.json").read_text()) == {"model": "x", "hooks": {"Stop": [lint]}}
    assert json.loads((root / ".mcp.json").read_text()) == {"mcpServers": {"other": {"command": "o"}}}
    assert run(["git", "-C", str(root), "check-ignore", "-q", LOCAL]).returncode == 0
    assert (root / ".gitignore").read_text().count(LOCAL) == 1
    assert calls[-1][:7] == ["claude", "mcp", "add", "--scope", "local", "codoc", "--"]


def test_without_claude_on_path_install_says_what_to_run(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"codoc": {"command": "/old/codoc-mcp"}}}))
    monkeypatch.setattr(I, "find_claude", lambda: None)
    said = []
    I.install_hooks(str(root), say=said.append)
    assert len(said) == 1 and "claude mcp add --scope local codoc --" in said[0]
    assert not (root / ".mcp.json").exists()
