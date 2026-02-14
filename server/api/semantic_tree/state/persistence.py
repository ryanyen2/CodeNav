"""Load/save SyncState to .codenav/sync_state.json."""

import json
from pathlib import Path
from typing import Optional

from api.semantic_tree.state.models import SyncState


def _state_path_for_root(root_dir: str) -> Path:
    return Path(root_dir) / ".codenav" / "sync_state.json"


def load_sync_state(root_dir: str) -> Optional[SyncState]:
    """Load SyncState from root_dir/.codenav/sync_state.json if present."""
    path = _state_path_for_root(root_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SyncState.model_validate(data)
    except Exception:
        return None


def save_sync_state(state: SyncState, root_dir: Optional[str] = None) -> Path:
    """Persist SyncState to root_dir/.codenav/sync_state.json (root_dir from state if not given)."""
    root = root_dir or state.root_dir
    if not root:
        raise ValueError("root_dir required to save sync state")
    path = _state_path_for_root(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return path
