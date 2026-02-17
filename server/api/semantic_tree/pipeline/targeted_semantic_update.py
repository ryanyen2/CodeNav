"""Feature generation for new entities only (patch path). Single LLM call with file context; no embeddings/RAG."""

import logging
from typing import Dict, List, Optional

from api.semantic_tree.models import CodebaseSnapshot, CodeEntity, FileInfo, SemanticNode, SemanticTree
from api.semantic_tree.state.models import SemanticCacheEntry
from api.semantic_tree.state.fingerprint import compute_entity_fingerprint
from api.semantic_tree.llm.prompt_loader import load_prompt, format_prompt, parse_solution_json
from api.semantic_tree.llm.completion import complete

logger = logging.getLogger(__name__)


def _entity_key(e: CodeEntity) -> str:
    return f"{e.fpath}::{e.name}"


def _format_file_context(file_info: FileInfo) -> str:
    """Format one file's entities for the prompt (same pattern as semantic_parsing_incremental)."""
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
    by_fpath: Dict[str, List[CodeEntity]] = {}
    for e in entities:
        by_fpath.setdefault(e.fpath, []).append(e)
    return [
        FileInfo(fpath=fpath, language="python", entities=ents, imports=[])
        for fpath, ents in by_fpath.items()
    ]


def update_features_for_new_entities(
    new_entity_keys: List[str],
    snapshot: CodebaseSnapshot,
    tree: SemanticTree,
    node_index: Dict[str, SemanticNode],
    semantic_cache: Dict[str, SemanticCacheEntry],
    repo_name: str = "",
    provider: str = "openai",
    model: Optional[str] = None,
) -> Dict[str, SemanticCacheEntry]:
    """
    Single LLM call for truly new entities (no semantic_cache hit). Uses file context only (no RAG/embedder).
    Updates SemanticNode.feature in the tree and returns updated cache.
    """
    entity_by_key = {_entity_key(e): e for e in snapshot.all_entities}
    entities = [entity_by_key[k] for k in new_entity_keys if k in entity_by_key]
    if not entities:
        return semantic_cache

    updated_cache = dict(semantic_cache)
    template = load_prompt("semantic_parsing")
    file_slices = _entities_to_file_slices(entities)
    context = "\n\n".join(_format_file_context(fs) for fs in file_slices)
    prompt = format_prompt(template, repo_name=repo_name, repo_info="")
    prompt += "\n\n### New entities (describe purpose in one short phrase)\n" + context

    try:
        response = complete(prompt=prompt, provider=provider, model=model)
        data = parse_solution_json(response)
    except Exception as e:
        logger.warning("Targeted semantic update LLM failed: %s; using entity names as features", e)
        for e in entities:
            key = _entity_key(e)
            node = node_index.get(key)
            if node:
                node.feature = e.name.replace("_", " ")
        return updated_cache

    if not isinstance(data, dict):
        for e in entities:
            key = _entity_key(e)
            node = node_index.get(key)
            if node:
                node.feature = e.name.replace("_", " ")
        return updated_cache

    for entity in entities:
        key = _entity_key(entity)
        name = entity.name.strip()
        feat_list = []
        if name in data:
            raw = data[name]
            feat_list = [str(f) for f in raw] if isinstance(raw, list) else [str(raw)] if raw else []
        if not feat_list:
            feat_list = [entity.name.replace("_", " ")]
        node = node_index.get(key)
        if node:
            node.feature = feat_list[0] if feat_list else entity.name.replace("_", " ")
        content_hash, _ = compute_entity_fingerprint(entity)
        updated_cache[content_hash] = SemanticCacheEntry(
            content_hash=content_hash,
            entity_name=entity.name,
            features=feat_list,
        )

    return updated_cache
