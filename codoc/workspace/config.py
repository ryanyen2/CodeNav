"""``.codoc/config.json``: the workspace settings every clone must agree on.

Tracked in git, unlike the rest of ``.codoc/``. It holds ``doc_language`` (the
language the tree is authored in, see ``codoc.doclang``), ``roles`` (actor →
role, see ``codoc.workspace.roles``) and ``index_exclude`` (files the index
skips, read by ``codoc.index.app``).

A file that is there but does not parse (a merge conflict, a trailing comma)
is left exactly as it is: readers take the defaults and writers refuse
(``ConfigUnreadable``). Writing over it would replace every setting it holds,
and moving it aside, as ``layout.read_json`` does with a control file, would
delete a tracked file and keep only one side of the conflict.
"""
from __future__ import annotations

import json
from pathlib import Path

from codoc.workspace.layout import CONFIG, atomic_write


class ConfigUnreadable(ValueError):
    """``config.json`` is there but is not a JSON object, so no setting can be
    written into it until a person fixes it."""


def config_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / CONFIG


def _load(codoc_dir: str | Path) -> dict | None:
    """The config, ``{}`` when absent or empty, None when it does not parse."""
    try:
        text = config_path(codoc_dir).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    if not text.strip():
        return {}
    try:
        cfg = json.loads(text)
    except ValueError:
        return None
    return cfg if isinstance(cfg, dict) else None


def read_config(codoc_dir: str | Path) -> dict:
    """The workspace config, or ``{}`` when absent or unreadable. Never raises:
    the daemon and the Claude Code hook read this on every pass, and a
    hand-mangled file must degrade to defaults."""
    return _load(codoc_dir) or {}


def write_config(codoc_dir: str | Path, **updates) -> dict:
    """Merge ``updates`` into the workspace config and write it. A merge, so a
    setting another writer added is kept. Raises ``ConfigUnreadable`` when the
    file is there but does not parse, and writes nothing."""
    cfg = _load(codoc_dir)
    if cfg is None:
        raise ConfigUnreadable(f"{config_path(codoc_dir)} is not a JSON object (a merge conflict?); "
                               "fix it before codoc writes a setting into it")
    cfg.update({k: v for k, v in updates.items() if v is not None})
    atomic_write(config_path(codoc_dir), json.dumps(cfg, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return cfg
