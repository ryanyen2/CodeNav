"""Install the codoc CC hooks into a target repo's ``.claude/settings.json``.

Called by ``codoc init`` (the default).  Deep-merges the hook block from
``codoc/plugin/hooks/hooks.json`` into ``<root>/.claude/settings.json`` and
copies the skill file into ``<root>/.claude/skills/codoc-intent/SKILL.md`` so
Claude Code loads it automatically for any session in that repo.

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
        return json.loads(hooks_path.read_text())
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
        return json.loads(settings_path.read_text())
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
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, settings_path)


def install_hooks(root_dir: str) -> None:
    """Install codoc CC hooks into ``<root_dir>/.claude/settings.json``.

    Also copies the SKILL.md into ``<root_dir>/.claude/skills/codoc-intent/``.
    Safe to call multiple times — idempotent and upgrades stale entries.
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
        skill_dest.write_text(skill_src.read_text())
