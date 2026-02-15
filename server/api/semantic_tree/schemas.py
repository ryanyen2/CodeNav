"""Request/response schemas for semantic tree API."""

from typing import Optional, List
from pydantic import BaseModel, Field


class FileInput(BaseModel):
    """One file passed inline for analyze (path = relative path, e.g. 'requests/api.py')."""
    path: str = Field(..., description="Relative path of the file (e.g. requests/api.py)")
    content: str = Field(..., description="Full file content (UTF-8)")


class InterventionResponse(BaseModel):
    """Returned when the pipeline must stop for user intervention (invalid LLM output, parse error, etc.)."""
    status: str = Field("intervention_required", description="Always 'intervention_required'")
    step: str = Field(..., description="Pipeline step that failed (e.g. domain_discovery, semantic_parsing)")
    message: str = Field(..., description="Human-readable reason; fix and retry.")


class AnalyzeRequest(BaseModel):
    """Request for full pipeline: extract → index (RAG) → domain → semantic (RAG) → hierarchy → tree.
    Either provide path (local directory) or files + root_dir (in-memory codebase). When files is set, path is ignored.
    """
    path: Optional[str] = Field(None, description="Local directory path to the codebase (ignored if files is set)")
    files: Optional[List[FileInput]] = Field(None, description="Inline file list; when set, root_dir is used as virtual root")
    root_dir: Optional[str] = Field(None, description="Virtual root for relative paths; required when using files (e.g. 'requests')")
    repo_name: str = Field("", description="Repository name for prompts")
    provider: str = Field("openai", description="LLM provider (openai, google, ollama, etc.)")
    model: Optional[str] = Field(None, description="Model name; uses provider default if omitted")
    format: str = Field("md", description="Output format: 'md' (markdown) or 'json'")
    excluded_dirs: Optional[List[str]] = None
    excluded_files: Optional[List[str]] = None
    index_path: Optional[str] = Field(None, description="Where to save FAISS index (default: path/.codenav/index; when using files, a temp dir is used)")


class AnalyzeResponse(BaseModel):
    """Analyze result: tree in requested format. tree_md is parseable by parseTreeBlock() and matches test fixture format (sigils, [path], (entity), deps:)."""
    tree_md: Optional[str] = None
    tree_json: Optional[dict] = None
    root_dir: str
    file_count: int
    entity_count: int


class SearchRequest(BaseModel):
    """Semantic search over indexed entities."""
    query: str = Field(..., description="Natural language or code query")
    index_path: str = Field(..., description="Path to existing FAISS index directory")
    top_k: int = Field(10, ge=1, le=100)


class SearchResultItem(BaseModel):
    """One search hit."""
    entity_name: str
    fpath: str
    entity_type: str
    distance: float


class SearchResponse(BaseModel):
    """Search results."""
    results: List[SearchResultItem]


class StatusResponse(BaseModel):
    """Index/cache status."""
    index_path: Optional[str] = None
    index_size: int = 0


# --- Sync (incremental forward) ---


class DeltaSummary(BaseModel):
    """Summary of entity delta for incremental sync."""
    added: int = 0
    removed: int = 0
    modified: int = 0
    renamed: int = 0
    unchanged: int = 0


class SyncRequest(BaseModel):
    """Request for sync (forward: code → tree). Same as AnalyzeRequest plus force_full.
    When force_full=False and state exists, runs incremental forward."""
    path: Optional[str] = Field(None, description="Local directory path (ignored if files is set)")
    files: Optional[List[FileInput]] = Field(None, description="Inline file list; root_dir used as virtual root")
    root_dir: Optional[str] = Field(None, description="Virtual root when using files")
    repo_name: str = Field("", description="Repository name for prompts")
    provider: str = Field("openai", description="LLM provider")
    model: Optional[str] = Field(None, description="Model name")
    format: str = Field("md", description="Output format: 'md' or 'json'")
    force_full: bool = Field(False, description="If True, run full pipeline and ignore existing state")
    excluded_dirs: Optional[List[str]] = None
    excluded_files: Optional[List[str]] = None
    index_path: Optional[str] = Field(None, description="Where to save/load FAISS index")


class SyncResponse(BaseModel):
    """Sync result: tree + incremental metadata."""
    tree_md: Optional[str] = None
    tree_json: Optional[dict] = None
    root_dir: str
    file_count: int
    entity_count: int
    is_incremental: bool = Field(False, description="True when incremental forward was used")
    delta_summary: Optional[DeltaSummary] = None
    timing: Optional[dict] = None


# --- Tree edit → target identification ---


class TreeEditRequest(BaseModel):
    """Request for tree edit target identification (no code generation)."""
    path: Optional[str] = Field(None, description="Codebase path; if set, base_tree_md is loaded from state")
    base_tree_md: Optional[str] = Field(None, description="Base tree markdown (required if path not provided)")
    edited_tree_md: str = Field(..., description="User-edited tree markdown")


class TargetModificationArea(BaseModel):
    """One codebase region affected by an operation."""
    fpath: Optional[str] = None
    entity_name: Optional[str] = None
    line_range: Optional[tuple[int, int]] = None
    node_path: str = ""


class TreeEditOperationItem(BaseModel):
    """One inferred operation with its code targets."""
    op: str = Field(..., description="Operation type: AddNode, DeleteNode, MoveNode, EditFeature, EditContract, ReorderChildren")
    target: str = Field("", description="Target node path(s)")
    params: dict = Field(default_factory=dict)
    targets: List[TargetModificationArea] = Field(default_factory=list, description="Affected code locations")


class TreeEditResponse(BaseModel):
    """Operations and code targets from tree edit (for Phase 4 code generation)."""
    operations: List[TreeEditOperationItem] = Field(default_factory=list)
    error: Optional[str] = None


# --- Apply tree edit (inverse sync: close the loop) ---


class ApplyTreeEditRequest(BaseModel):
    """Request to apply a tree edit: persist edited tree as canonical and update state (no code generation yet)."""
    path: Optional[str] = Field(None, description="Codebase path; base tree loaded from state")
    base_tree_md: Optional[str] = Field(None, description="Base tree markdown (required if path not provided)")
    edited_tree_md: str = Field(..., description="User-edited tree markdown to persist as canonical")


class ApplyTreeEditResponse(BaseModel):
    """Result of applying tree edit: operations/targets plus state-updated confirmation."""
    operations: List[TreeEditOperationItem] = Field(default_factory=list)
    applied: bool = Field(True, description="True when edited tree was persisted to state")
    tree_version: int = Field(0, description="New tree_version after apply")
    error: Optional[str] = None
