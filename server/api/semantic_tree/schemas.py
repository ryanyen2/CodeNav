"""Request/response schemas for semantic tree API."""

from typing import Optional, List
from pydantic import BaseModel, Field


class ExtractRequest(BaseModel):
    """Request for extraction only."""
    path: str = Field(..., description="Local directory path to the codebase")
    excluded_dirs: Optional[List[str]] = None
    excluded_files: Optional[List[str]] = None


class ExtractResponse(BaseModel):
    """Extraction result: snapshot summary."""
    root_dir: str
    file_count: int
    entity_count: int
    import_count: int


class IndexRequest(BaseModel):
    """Request for extract + embed + index."""
    path: str = Field(..., description="Local directory path to the codebase")
    index_path: Optional[str] = Field(None, description="Directory to save/load FAISS index")
    excluded_dirs: Optional[List[str]] = None
    excluded_files: Optional[List[str]] = None


class IndexResponse(BaseModel):
    """Index build result."""
    entity_count: int
    index_path: Optional[str] = None


class AnalyzeRequest(BaseModel):
    """Request for full pipeline: extract → domain → semantic → hierarchy → tree."""
    path: str = Field(..., description="Local directory path to the codebase")
    repo_name: str = Field("", description="Repository name for prompts")
    provider: str = Field("openai", description="LLM provider (openai, google, ollama, etc.)")
    model: Optional[str] = Field(None, description="Model name; uses provider default if omitted")
    format: str = Field("md", description="Output format: 'md' (markdown) or 'json'")
    excluded_dirs: Optional[List[str]] = None
    excluded_files: Optional[List[str]] = None


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
