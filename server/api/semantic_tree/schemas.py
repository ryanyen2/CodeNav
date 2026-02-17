"""Request/response schemas for semantic tree API."""

from typing import Optional, List, Literal
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
    index_path: Optional[str] = Field(None, description="Scope ID for Postgres index (default: path/.codenav/index or temp dir when using files)")


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
    index_path: str = Field(..., description="Scope ID of the index (e.g. root_dir or path/.codenav/index)")
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
    force_full: bool = Field(
        False,
        description="If True, run full pipeline (slow, re-index all). Use False (default) after first sync for fast incremental (index only changed entities).",
    )
    excluded_dirs: Optional[List[str]] = None
    excluded_files: Optional[List[str]] = None
    index_path: Optional[str] = Field(None, description="Scope ID for Postgres index (default: path/.codenav/index)")


class MergeSummary(BaseModel):
    """Summary of forward merge (code wins grounded, user wins underspec)."""
    preserved_user_nodes: int = 0
    overwritten_grounded_nodes: int = 0
    surfaced_added: int = 0
    drifted_nodes: int = 0


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
    merge_summary: Optional[MergeSummary] = Field(None, description="Present when forward merge was applied (prior tree + re-encode)")
    is_patch_based: bool = Field(False, description="Legacy; always False (patch path removed)")
    patch_summary: Optional[dict] = Field(None, description="When is_patch_based: modified/added/removed/needs_feature_update counts")


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
    underspecified: Optional[bool] = Field(None, description="True when operation involves an underspecified node (best-effort completion)")
    underspec_reason: Optional[Literal["status", "missing_anchor", "both"]] = Field(None, description="Reason: status (#planned/#unresolved), missing_anchor, or both")


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


# --- Apply with code generation (Phase 4 inverse pipeline) ---


class ApplyRequest(BaseModel):
    """Request to apply tree edit with LLM code generation and file modification."""
    path: str = Field(..., description="Codebase path; base tree and state loaded from here")
    edited_tree_md: str = Field(..., description="User-edited tree markdown")
    base_tree_md: Optional[str] = Field(
        None,
        description="Tree markdown *before* this edit. When provided, diff is base_tree_md vs edited_tree_md so only actual user edits produce operations. When omitted, server uses state.last_tree_md (backend tree); if that differs in phrasing from the user's doc, every node can be reported as changed.",
    )
    dry_run: bool = Field(False, description="If true, return planned changes without writing files or updating state")
    provider: str = Field("openai", description="LLM provider for code generation")
    model: Optional[str] = Field(None, description="LLM model (default from provider)")
    diff_format: Literal["line_replace", "unified_diff", "search_replace"] = Field(
        "line_replace",
        description="Include unified_diff and/or search_replace_blocks in response when set",
    )


class PlannedChangeItem(BaseModel):
    """One planned or applied code edit (for dry_run or response)."""
    fpath: str = ""
    line_start: int = 0
    line_end: Optional[int] = None
    new_content: str = ""


class ApplyResponse(BaseModel):
    """Result of apply: operations, modified files, planned/applied changes, drift report."""
    operations: List[TreeEditOperationItem] = Field(default_factory=list)
    applied: bool = Field(False, description="True when code was written and state updated")
    modified_fpaths: List[str] = Field(default_factory=list)
    planned_changes: List[PlannedChangeItem] = Field(default_factory=list, description="Code changes (dry_run or applied)")
    drift_report: List[dict] = Field(default_factory=list, description="Re-extracted entities per modified file (observational)")
    tree_version: int = Field(0, description="New tree_version after apply (0 if dry_run)")
    error: Optional[str] = None
    completion_mode: Optional[Literal["best_effort"]] = Field(None, description="Present when underspecified operations were completed best-effort")
    generated_artifact_count: Optional[int] = Field(None, description="Count of generated/surfaced artifacts from best-effort completion")
    unified_diff: Optional[str] = Field(None, description="Unified diff of all planned/applied changes (for diff view)")
    search_replace_blocks: Optional[List[dict]] = Field(None, description="Search/replace blocks per file when diff_format requested")
