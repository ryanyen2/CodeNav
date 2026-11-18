"""Install the Claude Code integration into a repository, for this machine.

Called by ``codoc init`` and ``codoc install-hooks``; each person runs it once
after cloning. The hooks name this machine's interpreter, so they go in the
untracked ``<root>/.claude/settings.local.json``, with a permission rule that
allows every ``mcp__codoc__*`` tool (a headless ``claude -p`` is otherwise
denied each one). The MCP server is registered at Claude Code's local scope
(``claude mcp add --scope local``, which keeps it in ``~/.claude.json`` under
this project). The skill and the ``/codoc:*`` commands are copied into
``<root>/.claude/skills/`` and ``<root>/.claude/commands/``, which may be shared.

An older install wrote the hooks into the tracked ``.claude/settings.json`` and
the server into the tracked ``.mcp.json``; codoc's own entries there are
removed and everything else is left.

Merging: for each hook event an entry whose command runs the codoc hook is
replaced by the fresh one; other entries and other keys are untouched, so
installing twice changes nothing and an entry from an older install is upgraded.
The hook template says ``python``; installing replaces it with
``sys.executable``, the interpreter running the install.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

from codoc.claude.autorealize import find_claude
from codoc.workspace.layout import atomic_write

# The module the installed hook commands run, and the path it had before; an
# entry naming either is ours to replace.
_HOOK_MODULES = ("codoc.claude.hook", "codoc.agent.hook")
MCP_ALLOW = "mcp__codoc__*"
LOCAL_SETTINGS = ".claude/settings.local.json"


def _plugin_dir() -> Path:
    return Path(__file__).parent / "plugin"


def _load_plugin_hooks() -> dict:
    hooks_path = _plugin_dir() / "hooks" / "hooks.json"
    try:
        return json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_hooks(hooks_data: dict) -> dict:
    """The template with ``python`` in every hook command replaced by sys.executable."""
    resolved = json.loads(json.dumps(hooks_data))
    for entries in resolved.get("hooks", {}).values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                if "command" in hook:
                    hook["command"] = hook["command"].replace("python ", f"{sys.executable} ", 1)
    return resolved


def _is_codoc_hook(hook: dict) -> bool:
    return any(m in hook.get("command", "") for m in _HOOK_MODULES)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _strip_hooks(hooks: dict) -> dict:
    """``hooks`` without codoc's entries, dropping an event they left empty."""
    out = {}
    for event, entries in hooks.items():
        kept = [e for e in entries if not any(_is_codoc_hook(h) for h in e.get("hooks", []))]
        if kept or not entries:
            out[event] = kept
    return out


def _mcp_command() -> list[str]:
    """The ``codoc-mcp`` console script beside this interpreter (a venv or a
    ``uv`` install), else ``python -m codoc.mcp.server``."""
    script = Path(sys.executable).parent / "codoc-mcp"
    return [str(script)] if script.exists() else [sys.executable, "-m", "codoc.mcp.server"]


def install_mcp(root_dir: str) -> str | None:
    """Register the codoc MCP server at Claude Code's local scope for the
    project at ``root_dir``. Returns None when done, else why not and the
    command to run once ``claude`` is on PATH."""
    add = ["claude", "mcp", "add", "--scope", "local", "codoc", "--", *_mcp_command()]
    claude = find_claude()
    if claude is None:
        return f"`claude` is not on PATH, so the MCP server is not registered. Run in {root_dir}: {shlex.join(add)}"
    cwd = str(Path(root_dir).resolve())
    # `add` refuses a name that is already registered at that scope, and a
    # stale entry may name another interpreter: replace it.
    subprocess.run([claude, "mcp", "remove", "--scope", "local", "codoc"], cwd=cwd, capture_output=True, timeout=60)
    run = subprocess.run([claude, *add[1:]], cwd=cwd, capture_output=True, text=True, timeout=60)
    if run.returncode != 0:
        return f"`claude mcp add` failed ({(run.stderr or run.stdout).strip()}). Run in {root_dir}: {shlex.join(add)}"
    return None


def _ignore_local_settings(root: Path) -> None:
    """Claude Code keeps settings.local.json out of git only when it created
    the file itself, so add it to .gitignore unless git already ignores it.
    ``check-ignore`` exits 1 for a path not ignored (128 outside a repo)."""
    if subprocess.run(["git", "-C", str(root), "check-ignore", "-q", LOCAL_SETTINGS],
                      capture_output=True, timeout=30).returncode != 1:
        return
    gi = root / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    gi.write_text(text + ("\n" if text and not text.endswith("\n") else "") + LOCAL_SETTINGS + "\n", encoding="utf-8")


def _remove_tracked_entries(root: Path) -> None:
    """Take codoc's hooks out of ``.claude/settings.json`` and its server out
    of ``.mcp.json``. A file left with nothing in it is removed."""
    def save(path: Path, data: dict) -> None:
        if data:
            _write_json(path, data)
        else:
            path.unlink()

    shared = root / ".claude" / "settings.json"
    s = _read_json(shared)
    if isinstance(s.get("hooks"), dict) and (hooks := _strip_hooks(s["hooks"])) != s["hooks"]:
        save(shared, {**s, "hooks": hooks} if hooks else {k: v for k, v in s.items() if k != "hooks"})
    mcp = root / ".mcp.json"
    m = _read_json(mcp)
    servers = m.get("mcpServers")
    if isinstance(servers, dict) and "codoc" in servers:
        servers = {k: v for k, v in servers.items() if k != "codoc"}
        save(mcp, {**m, "mcpServers": servers} if servers else {k: v for k, v in m.items() if k != "mcpServers"})


def install_hooks(root_dir: str, *, say=lambda _msg: None) -> list[str]:
    """Install the hooks, the MCP permission and registration, the skill and
    the commands into ``<root_dir>``. Safe to call again. ``say`` is told what
    could not be done. Returns the slash commands installed (``["/codoc:ask", …]``)."""
    root = Path(root_dir)

    # 1. Hooks and the MCP permission, in the untracked local settings.
    local = root / LOCAL_SETTINGS
    settings = _read_json(local)
    hooks = _strip_hooks(settings.get("hooks", {}))
    for event, entries in _resolve_hooks(_load_plugin_hooks()).get("hooks", {}).items():
        hooks[event] = hooks.get(event, []) + entries
    settings["hooks"] = hooks
    perms = settings.setdefault("permissions", {})
    if MCP_ALLOW not in perms.setdefault("allow", []):
        perms["allow"].append(MCP_ALLOW)
    _write_json(local, settings)
    _ignore_local_settings(root)
    _remove_tracked_entries(root)

    # 2. The skill.
    skill_src = _plugin_dir() / "skills" / "codoc-intent" / "SKILL.md"
    skill_dest = root / ".claude" / "skills" / "codoc-intent" / "SKILL.md"
    if skill_src.exists():
        skill_dest.parent.mkdir(parents=True, exist_ok=True)
        skill_dest.write_text(skill_src.read_text(encoding="utf-8"), encoding="utf-8")

    # 3. The commands, keeping subdirectories so `.claude/commands/codoc/plan.md`
    #    is the namespaced `/codoc:plan`.
    cmd_src_dir = _plugin_dir() / "commands"
    installed_cmds: list[str] = []
    if cmd_src_dir.is_dir():
        cmd_dest_dir = root / ".claude" / "commands"
        for cmd in cmd_src_dir.rglob("*.md"):
            rel = cmd.relative_to(cmd_src_dir)
            dest = cmd_dest_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(cmd.read_text(encoding="utf-8"), encoding="utf-8")
            installed_cmds.append("/" + ":".join(list(rel.parts[:-1]) + [rel.stem]))
        # Drop installed codoc commands that the plugin does not ship.
        dest_ns = cmd_dest_dir / "codoc"
        if dest_ns.is_dir():
            shipped = {p.name for p in (cmd_src_dir / "codoc").glob("*.md")}
            for installed in dest_ns.glob("*.md"):
                if installed.name not in shipped:
                    installed.unlink()

    # 4. The MCP server, at the local scope.
    if (why := install_mcp(root_dir)) is not None:
        say(why)
    return sorted(installed_cmds)
