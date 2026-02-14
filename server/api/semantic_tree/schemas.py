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
