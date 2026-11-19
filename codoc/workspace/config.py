"""``.codoc/config.json``: the workspace settings every clone must agree on.

Tracked in git, unlike the rest of ``.codoc/``. It holds ``doc_language`` (the
language the tree is authored in, see ``codoc.doclang``), ``roles`` (actor →
role, see ``codoc.workspace.roles``) and ``index_exclude`` (files the index
skips, read by ``codoc.index.app``).
"""
from __future__ import annotations

import json
from pathlib import Path

from codoc.workspace.layout import CONFIG, atomic_write


def config_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / CONFIG


def read_config(codoc_dir: str | Path) -> dict:
    """The workspace config, or ``{}`` when absent or unreadable. Never raises:
    the daemon and the Claude Code hook read this on every pass, and a
    hand-mangled file must degrade to defaults."""
    try:
        return json.loads(config_path(codoc_dir).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 (missing, empty, or malformed: all defaults)
        return {}


def write_config(codoc_dir: str | Path, **updates) -> dict:
    """Merge ``updates`` into the workspace config and write it. A merge, so a
    setting another writer added is kept."""
    cfg = read_config(codoc_dir)
    cfg.update({k: v for k, v in updates.items() if v is not None})
    atomic_write(config_path(codoc_dir), json.dumps(cfg, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return cfg
