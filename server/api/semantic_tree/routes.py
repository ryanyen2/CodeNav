"""API routes for semantic tree pipeline (extract, index, analyze, search)."""

import os
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.semantic_tree.schemas import (
    ExtractRequest,
    ExtractResponse,
    IndexRequest,
    IndexResponse,
    AnalyzeRequest,
    AnalyzeResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    StatusResponse,
)
from api.semantic_tree.extraction.discovery import discover_files
from api.semantic_tree.extraction.python_extractor import extract_python_file
from api.semantic_tree.models import CodebaseSnapshot, FileInfo
from api.semantic_tree.indexing.chunker import entity_chunks
from api.semantic_tree.indexing.vector_store import SemanticVectorStore
from api.semantic_tree.pipeline.domain_discovery import run_domain_discovery
from api.semantic_tree.pipeline.semantic_parsing import run_semantic_parsing
from api.semantic_tree.pipeline.hierarchical_construction import run_hierarchical_construction
from api.semantic_tree.pipeline.tree_assembly import assemble_tree
from api.semantic_tree.output.tree_serializer import tree_to_markdown, tree_to_json
from api.tools.embedder import get_embedder
from api.config import get_embedder_type

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/semantic_tree", tags=["semantic_tree"])


def _build_snapshot(path: str, excluded_dirs=None, excluded_files=None) -> CodebaseSnapshot:
    """Extract codebase snapshot from local path (Python only)."""
    path_resolved = Path(path).resolve()
    if not path_resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    root_dir = str(path_resolved)
    discovered = discover_files(root_dir, excluded_dirs=excluded_dirs, excluded_files=excluded_files)
    files: list[FileInfo] = []

    for rel_path, lang in discovered:
        if lang != "python":
            continue
        full_path = path_resolved / rel_path
        if not full_path.is_file():
            continue
        try:
            source = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("Skip file %s: %s", rel_path, e)
            continue
        file_info = extract_python_file(rel_path, source, root_dir, include_imports=True)
        files.append(file_info)

    return CodebaseSnapshot(root_dir=root_dir, files=files)


@router.post("/extract", response_model=ExtractResponse)
async def extract(request: ExtractRequest):
    """Extract codebase snapshot (entities + imports) from a local directory."""
    try:
        snapshot = _build_snapshot(
            request.path,
            excluded_dirs=request.excluded_dirs,
            excluded_files=request.excluded_files,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Extract failed")
        raise HTTPException(status_code=500, detail=str(e))

    return ExtractResponse(
        root_dir=snapshot.root_dir,
        file_count=len(snapshot.files),
        entity_count=len(snapshot.all_entities),
        import_count=len(snapshot.all_imports),
    )


@router.post("/index", response_model=IndexResponse)
async def index(request: IndexRequest):
    """Extract, embed, and build FAISS index. Requires embedder configured (e.g. OPENAI_API_KEY or Ollama)."""
    try:
        snapshot = _build_snapshot(
            request.path,
            excluded_dirs=request.excluded_dirs,
            excluded_files=request.excluded_files,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Extract failed")
        raise HTTPException(status_code=500, detail=str(e))

    embedder_type = get_embedder_type()
    embedder = get_embedder(embedder_type=embedder_type)

    store = SemanticVectorStore()
    chunks = entity_chunks(snapshot)
    store.add_entities(chunks, embedder)

    index_path = request.index_path or os.path.join(request.path, ".codenav", "index")
    store.save(index_path)

    return IndexResponse(entity_count=store.size, index_path=index_path)


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """
    Full pipeline: extract → domain discovery → semantic parsing → hierarchy → tree assembly.
    Requires LLM provider/model configured (e.g. OPENAI_API_KEY and provider openai).
    """
    try:
        snapshot = _build_snapshot(
            request.path,
            excluded_dirs=request.excluded_dirs,
            excluded_files=request.excluded_files,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Extract failed")
        raise HTTPException(status_code=500, detail=str(e))

    if not snapshot.all_entities:
        raise HTTPException(status_code=400, detail="No entities extracted; check path and filters.")

    try:
        areas = run_domain_discovery(
            snapshot,
            repo_name=request.repo_name,
            repo_info=None,
            provider=request.provider,
            model=request.model,
        )
    except ValueError as e:
        if "not configured" in str(e).lower() or "environment" in str(e).lower():
            raise HTTPException(status_code=503, detail="LLM not configured or keys missing.")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Domain discovery failed")
        raise HTTPException(status_code=500, detail=str(e))

    try:
        features = run_semantic_parsing(
            snapshot,
            repo_name=request.repo_name,
            repo_info="",
            provider=request.provider,
            model=request.model,
        )
    except Exception as e:
        logger.exception("Semantic parsing failed")
        raise HTTPException(status_code=500, detail=str(e))

    group_to_entities: dict[str, list[str]] = {}
    for f in snapshot.files:
        group_to_entities[f.fpath] = [e.name for e in f.entities]

    try:
        hierarchy = run_hierarchical_construction(
            areas,
            group_to_entities,
            provider=request.provider,
            model=request.model,
        )
    except Exception as e:
        logger.exception("Hierarchy construction failed")
        raise HTTPException(status_code=500, detail=str(e))

    tree = assemble_tree(snapshot, features, hierarchy, include_deps=True)

    if request.format == "json":
        return AnalyzeResponse(
            tree_json=tree_to_json(tree),
            root_dir=snapshot.root_dir,
            file_count=len(snapshot.files),
            entity_count=len(snapshot.all_entities),
        )
    return AnalyzeResponse(
        tree_md=tree_to_markdown(tree),
        root_dir=snapshot.root_dir,
        file_count=len(snapshot.files),
        entity_count=len(snapshot.all_entities),
    )


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """Semantic search over an existing index. Requires embedder configured."""
    if not Path(request.index_path).is_dir():
        raise HTTPException(status_code=404, detail="Index path not found")

    embedder_type = get_embedder_type()
    embedder = get_embedder(embedder_type=embedder_type)

    store = SemanticVectorStore()
    try:
        store.load(request.index_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load index: {e}")

    hits = store.search(request.query, embedder, top_k=request.top_k)
    results = [
        SearchResultItem(
            entity_name=e.name,
            fpath=e.fpath,
            entity_type=e.entity_type,
            distance=dist,
        )
        for e, dist in hits
    ]
    return SearchResponse(results=results)


@router.get("/status", response_model=StatusResponse)
async def status(index_path: str | None = None):
    """Return index status if index_path given."""
    if not index_path:
        return StatusResponse()
    p = Path(index_path)
    if not p.is_dir() or not (p / "entities.pkl").exists():
        return StatusResponse(index_path=index_path, index_size=0)
    try:
        store = SemanticVectorStore()
        store.load(index_path)
        return StatusResponse(index_path=index_path, index_size=store.size)
    except Exception:
        return StatusResponse(index_path=index_path, index_size=0)
