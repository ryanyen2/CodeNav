"""Install the codoc CC integration into a target repo's ``.claude`` + ``.mcp.json``.

Called by ``codoc init`` (the default).  Deep-merges the hook block from
``codoc/plugin/hooks/hooks.json`` into ``<root>/.claude/settings.json``, copies
the skill into ``<root>/.claude/skills/codoc-intent/SKILL.md`` and the plugin
commands into ``<root>/.claude/commands/`` (e.g. ``/codoc:plan``), and registers
the codoc MCP server in ``<root>/.mcp.json`` — so Claude Code loads all of it
automatically for any session in that repo.

**Merge semantics** (append-not-clobber):

* For each hook event (``PreToolUse``, ``Stop``, etc.), any existing entry whose
  commands contain ``codoc.agent.hook`` is replaced with the freshly-resolved
  entry.  Commands from other tools are untouched.  This makes ``codoc init``
  idempotent and also upgrades stale entries (e.g. a wrong Python path from a
  previous install).
* Other ``settings.json`` keys (e.g. ``permissions``, ``model``) are untouched.

The write is atomic (tmp → ``os.replace``) to avoid corrupting the file if the
process is killed mid-write.

**Python path resolution:**
The hook template uses the literal string ``python`` as a placeholder.
At install time we replace it with ``sys.executable`` — the absolute path of
whichever interpreter is running ``codoc init`` — so the hooks work whether the
user typed ``python3``, used a venv, or ran via ``uv run``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _plugin_dir() -> Path:
    return Path(__file__).parent.parent / "plugin"


def _load_plugin_hooks() -> dict:
    hooks_path = _plugin_dir() / "hooks" / "hooks.json"
    try:
        return json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_hooks(hooks_data: dict) -> dict:
    """Replace the ``python`` placeholder in every hook command with sys.executable.

    The template file uses ``python`` for readability; the installed commands
    use the real interpreter path so hooks work regardless of PATH.
    """
    python = sys.executable
    # Deep-copy by round-tripping through JSON to avoid mutating the template.
    resolved = json.loads(json.dumps(hooks_data))
    for entries in resolved.get("hooks", {}).values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                if "command" in hook:
                    hook["command"] = hook["command"].replace("python ", f"{python} ", 1)
    return resolved


def _is_codoc_hook(hook: dict) -> bool:
    return "codoc.agent.hook" in hook.get("command", "")


def _read_settings(settings_path: Path) -> dict:
    if not settings_path.exists():
        return {}
    try:
        return json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _merge_hooks(existing: dict, new_hooks: dict) -> dict:
    """Merge codoc hook entries into each event's array.

    Strategy per event:
    - Remove any existing entries that contain a codoc.agent.hook command
      (handles upgrades from a stale python path or an older install).
    - Append the fresh codoc entry.
    - Leave all non-codoc entries untouched.
    """
    merged = dict(existing)
    for event_name, new_entries in new_hooks.items():
        current: list = merged.get(event_name, [])
        # Strip stale codoc entries (identity: any hook command contains the marker).
        kept = [
            entry for entry in current
            if not any(_is_codoc_hook(h) for h in entry.get("hooks", []))
        ]
        kept.extend(new_entries)
        merged[event_name] = kept
    return merged


def _write_settings(settings_path: Path, data: dict) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, settings_path)


def _resolve_mcp_command() -> dict:
    """Resolve the codoc MCP server launch command for this interpreter.

    Prefer the ``codoc-mcp`` console script alongside the running interpreter (so
    a venv / uv install works); fall back to ``python -m codoc.mcp.server``.
    """
    script = Path(sys.executable).parent / "codoc-mcp"
    if script.exists():
        return {"type": "stdio", "command": str(script), "args": []}
    return {"type": "stdio", "command": sys.executable, "args": ["-m", "codoc.mcp.server"]}


def install_mcp(root_dir: str) -> None:
    """Register the codoc MCP server in ``<root_dir>/.mcp.json`` (idempotent).

    Deep-merges a ``mcpServers.codoc`` entry; any other servers are left intact,
    and a stale ``codoc`` entry is replaced (handles a moved interpreter path).
    """
    mcp_path = Path(root_dir) / ".mcp.json"
    data: dict = {}
    if mcp_path.exists():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers["codoc"] = _resolve_mcp_command()
    data["mcpServers"] = servers

    tmp = mcp_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, mcp_path)


def install_hooks(root_dir: str) -> list[str]:
    """Install codoc CC hooks into ``<root_dir>/.claude/settings.json``.

    Also copies the SKILL.md into ``<root_dir>/.claude/skills/codoc-intent/``.
    Safe to call multiple times — idempotent and upgrades stale entries.

    Returns the slash commands it installed (``["/codoc:ask", …]``) so the caller
    reports what is actually there. The CLI used to print a hardcoded list, which
    said ``/codoc:plan, /codoc:sync`` for months after ``/codoc:ask`` shipped —
    the one line anybody reads to check the install, naming the wrong thing.
    """
    settings_path = Path(root_dir) / ".claude" / "settings.json"

    # 1. Resolve the hook template to use the real Python executable.
    plugin_hooks_data = _load_plugin_hooks()
    resolved = _resolve_hooks(plugin_hooks_data)
    plugin_hooks: dict = resolved.get("hooks", {})

    if plugin_hooks:
        settings = _read_settings(settings_path)
        existing_hooks: dict = settings.get("hooks", {})
        settings["hooks"] = _merge_hooks(existing_hooks, plugin_hooks)
        _write_settings(settings_path, settings)

    # 2. Copy the SKILL.md into the local skills directory.
    skill_src = _plugin_dir() / "skills" / "codoc-intent" / "SKILL.md"
    skill_dest = Path(root_dir) / ".claude" / "skills" / "codoc-intent" / "SKILL.md"
    if skill_src.exists():
        skill_dest.parent.mkdir(parents=True, exist_ok=True)
        skill_dest.write_text(skill_src.read_text(encoding="utf-8"), encoding="utf-8")

    # 3. Copy plugin commands into the local commands dir, preserving subdirs so
    #    `.claude/commands/codoc/plan.md` becomes the namespaced `/codoc:plan`.
    cmd_src_dir = _plugin_dir() / "commands"
    installed_cmds: list[str] = []
    if cmd_src_dir.is_dir():
        cmd_dest_dir = Path(root_dir) / ".claude" / "commands"
        for cmd in cmd_src_dir.rglob("*.md"):
            rel = cmd.relative_to(cmd_src_dir)
            dest = cmd_dest_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(cmd.read_text(encoding="utf-8"), encoding="utf-8")
            # `codoc/plan.md` is the namespaced `/codoc:plan`; a command at the
            # top level would just be `/plan`.
            parts = list(rel.parts)
            installed_cmds.append("/" + ":".join(parts[:-1] + [rel.stem]))
        # Drop previously-installed codoc commands the plugin no longer ships
        # (e.g. the old /codoc:realize, folded into /codoc:sync).
        dest_ns = cmd_dest_dir / "codoc"
        if dest_ns.is_dir():
            shipped = {p.name for p in (cmd_src_dir / "codoc").glob("*.md")}
            for installed in dest_ns.glob("*.md"):
                if installed.name not in shipped:
                    installed.unlink()

    # 4. Register the codoc MCP server in <root>/.mcp.json.
    install_mcp(root_dir)
    return sorted(installed_cmds)
