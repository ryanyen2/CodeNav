"""API routes for semantic tree pipeline: analyze (extract + index + RAG pipeline), search, status."""

import os
import logging
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from api.semantic_tree.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    InterventionResponse,
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
from api.semantic_tree.pipeline.semantic_parsing import run_semantic_parsing_rag
from api.semantic_tree.pipeline.hierarchical_construction import run_hierarchical_construction
from api.semantic_tree.pipeline.tree_assembly import assemble_tree
from api.semantic_tree.output.tree_serializer import tree_to_markdown, tree_to_json
from api.tools.embedder import get_embedder
from api.config import get_embedder_type

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/semantic_tree", tags=["semantic_tree"])


def _build_snapshot(
    path: str,
    excluded_dirs: Optional[List[str]] = None,
    excluded_files: Optional[List[str]] = None,
) -> CodebaseSnapshot:
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


def _intervention(step: str, message: str) -> JSONResponse:
    """Return 422 with intervention_required body; agent should stop for user fix."""
    body = InterventionResponse(status="intervention_required", step=step, message=message)
    return JSONResponse(status_code=422, content=body.model_dump())


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """
    Integrated pipeline: extract → index (RAG) → domain discovery → semantic parsing (RAG) → hierarchy → tree.
    Requires LLM and embedder configured. Stops with 422 intervention_required on parse/LLM issues.
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
        return _intervention("extract", "No entities extracted; check path and filters.")

    index_path = request.index_path or os.path.join(request.path, ".codenav", "index")
    embedder_type = get_embedder_type()
    try:
        embedder = get_embedder(embedder_type=embedder_type)
    except Exception as e:
        return _intervention("index", f"Embedder not available: {e}")

    store = SemanticVectorStore()
    chunks = entity_chunks(snapshot)
    try:
        store.add_entities(chunks, embedder)
    except Exception as e:
        return _intervention("index", f"Failed to build index: {e}")
    if store.size == 0:
        return _intervention("index", "No embeddings produced; check embedder config.")
    try:
        store.save(index_path)
    except Exception as e:
        logger.warning("Could not save index to %s: %s", index_path, e)

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
        return _intervention("domain_discovery", str(e))
    except Exception as e:
        logger.exception("Domain discovery failed")
        return _intervention("domain_discovery", str(e))

    if not areas:
        return _intervention("domain_discovery", "LLM returned no functional areas; check prompt/response.")

    try:
        features = run_semantic_parsing_rag(
            snapshot,
            store=store,
            embedder=embedder,
            areas=areas,
            repo_name=request.repo_name,
            repo_info="",
            provider=request.provider,
            model=request.model,
        )
    except ValueError as e:
        return _intervention("semantic_parsing", str(e))
    except Exception as e:
        logger.exception("Semantic parsing failed")
        return _intervention("semantic_parsing", str(e))

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
    except ValueError as e:
        return _intervention("hierarchical_construction", str(e))
    except Exception as e:
        logger.exception("Hierarchy construction failed")
        return _intervention("hierarchical_construction", str(e))

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
    """Semantic search over an existing index (e.g. after analyze). Requires embedder configured."""
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
    """Index status if index_path given (e.g. path/.codenav/index after analyze)."""
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
