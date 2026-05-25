"""Install the codoc CC hooks into a target repo's ``.claude/settings.json``.

Called by ``codoc init --hooks`` (the default).  Deep-merges the hook block from
``codoc/plugin/hooks/hooks.json`` into ``<root>/.claude/settings.json`` and
copies the skill file into ``<root>/.claude/skills/codoc-intent/SKILL.md`` so
Claude Code loads it automatically for any session in that repo.

**Merge semantics** (append-not-clobber):

* For each hook event (``PreToolUse``, ``Stop``, etc.), the codoc hook entry is
  appended to the existing array only if a hook with the same command is not
  already present.  This makes ``codoc init`` idempotent.
* Other ``settings.json`` keys (e.g. ``permissions``, ``model``) are untouched.

The write is atomic (tmp → ``os.replace``) to avoid corrupting the file if the
process is killed mid-write.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _plugin_dir() -> Path:
    return Path(__file__).parent.parent / "plugin"


def _load_plugin_hooks() -> dict:
    hooks_path = _plugin_dir() / "hooks" / "hooks.json"
    try:
        return json.loads(hooks_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _read_settings(settings_path: Path) -> dict:
    if not settings_path.exists():
        return {}
    try:
        return json.loads(settings_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _merge_hooks(existing: dict, new_hooks: dict) -> dict:
    """Append codoc hook entries to each event's array — never clobber."""
    merged = dict(existing)
    for event_name, entries in new_hooks.items():
        current: list = merged.get(event_name, [])
        # Collect the set of commands already registered to avoid duplicates.
        existing_commands: set[str] = set()
        for entry in current:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                if cmd:
                    existing_commands.add(cmd)
        for entry in entries:
            # Only add if none of this entry's hook commands are already registered.
            new_cmds = {h.get("command", "") for h in entry.get("hooks", [])}
            if not new_cmds.intersection(existing_commands):
                current.append(entry)
                existing_commands |= new_cmds
        merged[event_name] = current
    return merged


def _write_settings(settings_path: Path, data: dict) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, settings_path)


def install_hooks(root_dir: str) -> None:
    """Install codoc CC hooks into ``<root_dir>/.claude/settings.json``.

    Also copies the SKILL.md into ``<root_dir>/.claude/skills/codoc-intent/``.
    Safe to call multiple times — idempotent.
    """
    settings_path = Path(root_dir) / ".claude" / "settings.json"

    # 1. Merge the hook block into settings.json.
    plugin_hooks_data = _load_plugin_hooks()
    plugin_hooks: dict = plugin_hooks_data.get("hooks", {})
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
        skill_dest.write_text(skill_src.read_text())
