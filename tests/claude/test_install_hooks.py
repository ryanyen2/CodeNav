"""`codoc install-hooks` sets up this machine and writes nothing tracked."""
import json
import re
import subprocess
import sys

import pytest

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
        if argv[:2] == [sys.executable, "-c"]:          # the interpreter imports this codoc
            return subprocess.CompletedProcess(argv, 0, I.codoc.__file__ + "\n", "")
        if argv[0] != "claude":
            return run(argv, **k)
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(I, "find_claude", lambda: "claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    old_command = root / ".claude" / "commands" / "codoc" / "sync.md"        # what /codoc:realize was called
    old_command.parent.mkdir(parents=True)
    old_command.write_text("old")
    for _ in range(2):
        assert I.install_hooks(str(root)) == ["/codoc:ask", "/codoc:plan", "/codoc:realize"]
    assert not old_command.exists()
    skill = (root / ".claude" / "skills" / "codoc-intent" / "SKILL.md").read_text()
    assert "{{include" not in skill and "## How to write a description" in skill
    local = json.loads((root / LOCAL).read_text())
    assert local["permissions"]["allow"] == ["mcp__codoc__*"]
    commands = [h["command"] for es in local["hooks"].values() for e in es for h in e["hooks"]]
    assert len(commands) == 6 and all(c.startswith(f"{sys.executable} -m codoc.claude.hook ") for c in commands)
    assert json.loads((root / ".claude" / "settings.json").read_text()) == {"model": "x", "hooks": {"Stop": [lint]}}
    assert json.loads((root / ".mcp.json").read_text()) == {"mcpServers": {"other": {"command": "o"}}}
    assert run(["git", "-C", str(root), "check-ignore", "-q", LOCAL]).returncode == 0
    assert (root / ".gitignore").read_text().count(LOCAL) == 1
    assert calls[-1] == ["claude", "mcp", "add", "--scope", "local", "codoc", "--", sys.executable, "-m", "codoc.mcp.server"]


def test_when_the_interpreter_would_import_another_codoc_the_hooks_and_server_are_pointed_at_this_one(tmp_path, monkeypatch):
    """codoc run from a checkout beside another codoc installed in the same
    environment: the installed hooks and server must not run the other one."""
    from pathlib import Path
    root = _repo(tmp_path, monkeypatch)
    here = str(Path(I.codoc.__file__).resolve().parent.parent)
    calls, run = [], subprocess.run

    def fake_run(argv, **k):
        if argv[:2] == [sys.executable, "-c"]:
            assert "PYTHONPATH" not in k["env"] and k["cwd"] == str(root)
            return subprocess.CompletedProcess(argv, 0, "/venv/site-packages/codoc/__init__.py\n", "")
        if argv[0] != "claude":
            return run(argv, **k)
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(I, "find_claude", lambda: "claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    I.install_hooks(str(root))
    local = json.loads((root / LOCAL).read_text())
    commands = [h["command"] for es in local["hooks"].values() for e in es for h in e["hooks"]]
    assert all(c.startswith(f"PYTHONPATH={here} {sys.executable} -m codoc.claude.hook ") for c in commands), commands
    assert calls[-1] == ["claude", "mcp", "add", "--scope", "local", "codoc", "-e", f"PYTHONPATH={here}", "--",
                         sys.executable, "-m", "codoc.mcp.server"]


def test_without_claude_on_path_install_says_what_to_run_and_keeps_the_tracked_server(tmp_path, monkeypatch):
    """Until the local registration succeeds, the tracked entry is the only one."""
    root = _repo(tmp_path, monkeypatch)
    mcp = json.dumps({"mcpServers": {"codoc": {"command": "/old/codoc-mcp"}}})
    (root / ".mcp.json").write_text(mcp)
    monkeypatch.setattr(I, "find_claude", lambda: None)
    said = []
    I.install_hooks(str(root), say=said.append)
    assert "claude mcp add --scope local codoc " in said[0] and "-m codoc.mcp.server" in said[0] and ".mcp.json" in said[1]
    assert (root / ".mcp.json").read_text() == mcp


@pytest.mark.parametrize("path,text", [
    (LOCAL, '{"permissions": {"allow": ["Bash(npm test)",]}}'),       # a trailing comma
    (LOCAL, '{"permissions": []}'),
    (LOCAL, '{"hooks": {"Stop": {}}}'),
    (".claude/settings.json", '{"hooks": '),
    (".mcp.json", '{"mcpServers": ["codoc"]}'),
])
def test_a_settings_file_that_does_not_fit_stops_the_install_and_is_left_alone(tmp_path, monkeypatch, path, text):
    root = _repo(tmp_path, monkeypatch)
    (root / path).write_text(text)
    monkeypatch.setattr(I, "find_claude", lambda: None)
    with pytest.raises(SystemExit, match=re.escape(str(root / path))):
        I.install_hooks(str(root))
    assert (root / path).read_text() == text
    assert path == LOCAL or not (root / LOCAL).exists()


def test_null_in_the_local_settings_is_absent(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    (root / LOCAL).write_text('{"hooks": null, "permissions": {"allow": null}, "env": {"X": "1"}}')
    monkeypatch.setattr(I, "find_claude", lambda: None)
    I.install_hooks(str(root))
    local = json.loads((root / LOCAL).read_text())
    assert local["env"] == {"X": "1"} and local["permissions"]["allow"] == ["mcp__codoc__*"] and local["hooks"]
