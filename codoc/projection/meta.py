"""TreeMeta sidecar: state needed to make sense of edits to .codoc/tree/.

Stored at ``.codoc/tree/tree.meta.json``. Captures:
- ``base_hlc``: the head HLC at last render time, used to detect stale buffers
- ``rendered_at``: ISO datetime
- ``uuid_to_location``: where each feature was rendered (file + line) so we can
  detect proposal lines that were deleted (= accepted) vs still present
- ``binding_index``: secondary index from feature_uuid → list of bindings,
  used by the VSCode extension for cross-file highlighting
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class TreeMeta:
    base_hlc: str
    rendered_at: str
    uuid_to_location: dict = field(default_factory=dict)
    # uuid_to_location: {uuid: {"file": "auth-flow.codoc", "line": 4, "kind": "feature"|"proposal"}}
    binding_index: dict = field(default_factory=dict)
    # binding_index: {feature_uuid: [{"file": ..., "symbol_path": ..., "binding_uuid": ..., "ts_query": ...}]}
    slug_path_to_uuid: dict = field(default_factory=dict)
    # slug_path_to_uuid: {"root-slug/child-slug": "<uuid>"}
    title_path_to_uuid: dict = field(default_factory=dict)
    # title_path_to_uuid: {"Core API > Schema Generation": "<uuid>"}
    line_range_to_hlc: dict = field(default_factory=dict)
    # line_range_to_hlc: {"auth-flow.codoc:12-15": "<hlc>"}  — diff hunk lines → proposal HLC

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TreeMeta":
        return cls(
            base_hlc=data.get("base_hlc", ""),
            rendered_at=data.get("rendered_at", ""),
            uuid_to_location=data.get("uuid_to_location", {}),
            binding_index=data.get("binding_index", {}),
            slug_path_to_uuid=data.get("slug_path_to_uuid", {}),
            title_path_to_uuid=data.get("title_path_to_uuid", {}),
            line_range_to_hlc=data.get("line_range_to_hlc", {}),
        )


def _meta_path(codoc_dir: str) -> Path:
    return Path(codoc_dir) / "tree" / "tree.meta.json"


def read_meta(codoc_dir: str) -> TreeMeta | None:
    """Read the TreeMeta sidecar; return None if missing or unreadable."""
    path = _meta_path(codoc_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return TreeMeta.from_dict(data)


def write_meta(codoc_dir: str, meta: TreeMeta) -> None:
    """Write the TreeMeta sidecar atomically."""
    path = _meta_path(codoc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(path)
