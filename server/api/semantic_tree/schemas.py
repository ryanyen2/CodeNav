"""Request/response schemas for semantic tree API."""

from typing import Optional, List
from pydantic import BaseModel, Field


class InterventionResponse(BaseModel):
    """Returned when the pipeline must stop for user intervention (invalid LLM output, parse error, etc.)."""
    status: str = Field("intervention_required", description="Always 'intervention_required'")
    step: str = Field(..., description="Pipeline step that failed (e.g. domain_discovery, semantic_parsing)")
    message: str = Field(..., description="Human-readable reason; fix and retry.")


class AnalyzeRequest(BaseModel):
    """Request for full pipeline: extract → index (RAG) → domain → semantic (RAG) → hierarchy → tree."""
    path: str = Field(..., description="Local directory path to the codebase")
    repo_name: str = Field("", description="Repository name for prompts")
    provider: str = Field("openai", description="LLM provider (openai, google, ollama, etc.)")
    model: Optional[str] = Field(None, description="Model name; uses provider default if omitted")
    format: str = Field("md", description="Output format: 'md' (markdown) or 'json'")
    excluded_dirs: Optional[List[str]] = None
    excluded_files: Optional[List[str]] = None
    index_path: Optional[str] = Field(None, description="Where to save/load FAISS index (default: path/.codenav/index)")


class AnalyzeResponse(BaseModel):
    """Analyze result: tree in requested format."""
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
