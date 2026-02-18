"""Sync state models: complement for incremental forward/inverse (quotient lens)."""

from typing import Optional, List, Dict, Literal
from pydantic import BaseModel, Field


class EntityFingerprint(BaseModel):
    """Fingerprint for one code entity (content + signature) for delta detection."""
    content_hash: str = Field(..., description="SHA-256 of normalized body/source")
    signature_hash: str = Field("", description="SHA-256 of normalized signature")
    content_sample: Optional[str] = Field(None, description="First N chars of normalized content for difflib similarity (optional)")


class FileFingerprint(BaseModel):
    """Fingerprint for one file (full content hash)."""
    content_hash: str = Field(..., description="SHA-256 of normalized file content")


class SemanticCacheEntry(BaseModel):
    """Cached SemanticFeature keyed by content_hash (so renames reuse cache)."""
    content_hash: str
    entity_name: str
    features: List[str] = Field(default_factory=list)


class NodeEntityMapping(BaseModel):
    """Bidirectional link: tree node_id or path → entity key (fpath, entity_name)."""
    node_id: str = ""
    node_path: str = ""
    fpath: str = ""
    entity_name: str = ""


class SyncState(BaseModel):
    """
    Complement C: everything get_c computed that we reuse for incrementality.
    Persisted at .codenav/sync_state.json.
    """
    entity_fingerprints: Dict[str, EntityFingerprint] = Field(
        default_factory=dict,
        description="entity_key (fpath::name) -> fingerprint",
    )
    file_fingerprints: Dict[str, str] = Field(
        default_factory=dict,
        description="fpath -> content_hash",
    )
    semantic_cache: Dict[str, SemanticCacheEntry] = Field(
        default_factory=dict,
        description="content_hash -> cached SemanticFeature data",
    )
    domain_areas: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Cached FunctionalArea names from domain discovery",
    )
    hierarchy_cache: List[Dict] = Field(
        default_factory=list,
        description="Cached HierarchyMapping from hierarchical construction",
    )
    mappings: List[NodeEntityMapping] = Field(
        default_factory=list,
        description="Tree node ↔ code entity links",
    )
    tree_version: int = Field(0, description="Monotonic counter; bumped after inverse sync")
    code_version: int = Field(0, description="Monotonic counter; bumped after forward sync")
    last_sync_direction: Optional[Literal["forward", "inverse"]] = None
    last_tree_md: Optional[str] = Field(None, description="Serialized tree for delta / tree_edit base")
    root_dir: Optional[str] = Field(None, description="Path this state belongs to")
    index_path: Optional[str] = Field(None, description="Scope ID for Postgres code index")
