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
removed (the server only once the local registration succeeds) and everything
else is left. A file codoc edits that does not parse, or holds the wrong type
where codoc writes, stops the install before anything is written; null counts
as absent.

Merging: for each hook event an entry whose command runs the codoc hook is
replaced by the fresh one; other entries and other keys are untouched, so
installing twice changes nothing and an entry from an older install is upgraded.
The hook template says ``python``; installing replaces it with
``sys.executable``, the interpreter running the install.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

from codoc.claude.autorealize import find_claude
from codoc.llm.prompt import load_prompt
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


def _is_codoc_entry(entry) -> bool:
    """An entry whose command runs the codoc hook. Anything else is the
    person's and is kept as it is, whatever its shape."""
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    return isinstance(hooks, list) and any(
        isinstance(h, dict) and isinstance(h.get("command"), str) and any(m in h["command"] for m in _HOOK_MODULES)
        for h in hooks)


def _stop(path: Path, why: str):
    raise SystemExit(f"{path} {why}. codoc changed nothing: fix the file and run `codoc install-hooks` again.")


def _read_json(path: Path) -> dict:
    """The object in ``path``, or {} when there is no file. A file that does
    not parse stops the install: rewriting it would lose what it holds, and
    Claude Code keeps the person's permission approvals in settings.local.json."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        _stop(path, f"does not parse ({e})")
    if not isinstance(data, dict):
        _stop(path, f"holds a {type(data).__name__} where codoc expects an object")
    return data


def _field(data: dict, key: str, kind: type, path: Path):
    """``data[key]``, or an empty ``kind`` when it is absent or null. Another
    type stops the install."""
    value = data.get(key)
    if value is None:
        return kind()
    if not isinstance(value, kind):
        _stop(path, f"holds a {type(value).__name__} at `{key}` where codoc expects a {kind.__name__}")
    return value


def _write_json(path: Path, data: dict) -> None:
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _strip_hooks(hooks: dict, path: Path) -> dict:
    """``hooks`` without codoc's entries, dropping an event they left empty."""
    out = {}
    for event in hooks:
        entries = _field(hooks, event, list, path)
        kept = [e for e in entries if not _is_codoc_entry(e)]
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


def _save_or_remove(path: Path, data: dict, key: str, value: dict) -> None:
    """``data`` with ``key`` set to ``value``, or without it when ``value`` is
    empty; a file left with nothing in it is removed."""
    data = {**data, key: value} if value else {k: v for k, v in data.items() if k != key}
    if data:
        _write_json(path, data)
    else:
        path.unlink()


def install_hooks(root_dir: str, *, say=lambda _msg: None) -> list[str]:
    """Install the hooks, the MCP permission and registration, the skill and
    the commands into ``<root_dir>``. Safe to call again. ``say`` is told what
    could not be done. Returns the slash commands installed (``["/codoc:ask", …]``)."""
    root = Path(root_dir)
    local, shared, mcp = root / LOCAL_SETTINGS, root / ".claude" / "settings.json", root / ".mcp.json"

    # Every file codoc edits is read before any is written, so one that does
    # not fit stops the install with nothing changed.
    settings, tracked, mcp_json = _read_json(local), _read_json(shared), _read_json(mcp)
    hooks = _strip_hooks(_field(settings, "hooks", dict, local), local)
    perms = _field(settings, "permissions", dict, local)
    allow = _field(perms, "allow", list, local)
    tracked_hooks = _field(tracked, "hooks", dict, shared)
    tracked_kept = _strip_hooks(tracked_hooks, shared)
    servers = _field(mcp_json, "mcpServers", dict, mcp)

    # 1. Hooks and the MCP permission, in the untracked local settings.
    for event, entries in _resolve_hooks(_load_plugin_hooks()).get("hooks", {}).items():
        hooks[event] = hooks.get(event, []) + entries
    settings["hooks"] = hooks
    settings["permissions"] = {**perms, "allow": allow if MCP_ALLOW in allow else [*allow, MCP_ALLOW]}
    _write_json(local, settings)
    _ignore_local_settings(root)
    # An older install's hooks in the tracked settings.json.
    if tracked_kept != tracked_hooks:
        _save_or_remove(shared, tracked, "hooks", tracked_kept)

    # 2. The skill, with its `{{include:style}}` expanded as the prompts expand it.
    skill_src = _plugin_dir() / "skills" / "codoc-intent" / "SKILL.md"
    skill_dest = root / ".claude" / "skills" / "codoc-intent" / "SKILL.md"
    if skill_src.exists():
        skill_dest.parent.mkdir(parents=True, exist_ok=True)
        skill = re.sub(r"\{\{include:(\w+)\}\}", lambda m: load_prompt(m.group(1)).strip(),
                       skill_src.read_text(encoding="utf-8"))
        skill_dest.write_text(skill, encoding="utf-8")

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
        # Drop installed codoc commands that the plugin no longer ships, such
        # as `sync.md`, which became `realize.md`.
        dest_ns = cmd_dest_dir / "codoc"
        if dest_ns.is_dir():
            shipped = {p.name for p in (cmd_src_dir / "codoc").glob("*.md")}
            for installed in dest_ns.glob("*.md"):
                if installed.name not in shipped:
                    installed.unlink()

    # 4. The MCP server, at the local scope. An older install's entry in the
    #    tracked .mcp.json goes only once this one is registered.
    if (why := install_mcp(root_dir)) is None:
        if "codoc" in servers:
            _save_or_remove(mcp, mcp_json, "mcpServers", {k: v for k, v in servers.items() if k != "codoc"})
    else:
        say(why)
        if "codoc" in servers:
            say(f"Until then the codoc entry in the tracked {mcp} stays, holding this machine's path; "
                "run `codoc install-hooks` again afterwards to remove it.")
    return sorted(installed_cmds)
