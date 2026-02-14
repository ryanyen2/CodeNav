"""Step 4: Semantic parsing — LLM extracts per-entity features. RAG version: one call per area."""

import logging
from typing import List, Optional, Dict, Any, Set, Tuple

from api.semantic_tree.models import (
    CodebaseSnapshot,
    CodeEntity,
    SemanticFeature,
    FileInfo,
    FunctionalArea,
)
from api.semantic_tree.llm.prompt_loader import load_prompt, format_prompt, parse_solution_json
from api.semantic_tree.llm.completion import complete
from api.semantic_tree.indexing.vector_store import SemanticVectorStore

logger = logging.getLogger(__name__)

RAG_TOP_K = 40
REMAINING_BATCH = 50


def _entity_key(e: CodeEntity) -> Tuple[str, str]:
    return (e.fpath, e.name)


def _format_file_context(file_info: FileInfo) -> str:
    """Format one file's entities for the prompt."""
    lines = [f"### File: {file_info.fpath}", ""]
    for e in file_info.entities:
        lines.append(f"#### {e.name}")
        if e.signature:
            lines.append(f"Signature: {e.signature}")
        if e.docstring:
            lines.append(f"Docstring: {e.docstring}")
        if e.body_text:
            body = e.body_text[:1500] + ("..." if len(e.body_text) > 1500 else "")
            lines.append(f"```\n{body}\n```")
        lines.append("")
    return "\n".join(lines)


def _entities_to_file_slices(entities: List[CodeEntity]) -> List[FileInfo]:
    """Group entities by fpath into FileInfo slices."""
    by_fpath: Dict[str, List[CodeEntity]] = {}
    for e in entities:
        by_fpath.setdefault(e.fpath, []).append(e)
    return [
        FileInfo(fpath=fpath, language="python", entities=ents, imports=[])
        for fpath, ents in by_fpath.items()
    ]


def run_semantic_parsing_rag(
    snapshot: CodebaseSnapshot,
    store: SemanticVectorStore,
    embedder: Any,
    areas: List[FunctionalArea],
    repo_name: str = "",
    repo_info: str = "",
    provider: str = "openai",
    model: Optional[str] = None,
) -> List[SemanticFeature]:
    """
    RAG-based semantic parsing: one LLM call per functional area (retrieve entities by area name),
    then one call for remaining entities. Uses prompts/semantic_parsing.txt.
    """
    template = load_prompt("semantic_parsing")
    all_features: List[SemanticFeature] = []
    covered: Set[Tuple[str, str]] = set()
    entity_by_key = {_entity_key(e): e for e in snapshot.all_entities}

    for area in areas:
        if not area.name or not area.name.strip():
            continue
        hits = store.search(area.name.strip(), embedder, top_k=RAG_TOP_K)
        if not hits:
            continue
        entities = [e for e, _ in hits]
        for e in entities:
            covered.add(_entity_key(e))
        file_slices = _entities_to_file_slices(entities)
        context_parts = [_format_file_context(fs) for fs in file_slices]
        context = "\n\n".join(context_parts)
        prompt = format_prompt(template, repo_name=repo_name, repo_info=repo_info)
        prompt += "\n\n" + context
        response = complete(prompt=prompt, provider=provider, model=model)
        data = parse_solution_json(response)
        if not isinstance(data, dict):
            logger.warning("semantic_parsing_rag response not a dict for area %s: %s", area.name, type(data))
            continue
        for entity_name, features in data.items():
            feat_list = [str(f) for f in features] if isinstance(features, list) else []
            all_features.append(SemanticFeature(entity_name=entity_name.strip(), features=feat_list))

    remaining = [e for e in snapshot.all_entities if _entity_key(e) not in covered]
    if remaining:
        batch = remaining[:REMAINING_BATCH]
        file_slices = _entities_to_file_slices(batch)
        context = "\n\n".join(_format_file_context(fs) for fs in file_slices)
        prompt = format_prompt(template, repo_name=repo_name, repo_info=repo_info)
        prompt += "\n\n### Remaining entities\n" + context
        response = complete(prompt=prompt, provider=provider, model=model)
        data = parse_solution_json(response)
        if isinstance(data, dict):
            for entity_name, features in data.items():
                feat_list = [str(f) for f in features] if isinstance(features, list) else []
                all_features.append(SemanticFeature(entity_name=entity_name.strip(), features=feat_list))
        else:
            for e in batch:
                all_features.append(SemanticFeature(entity_name=e.name, features=[e.name.replace("_", " ")]))

    return all_features
