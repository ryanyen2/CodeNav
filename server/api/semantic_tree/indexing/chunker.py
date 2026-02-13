"""One chunk per CodeEntity (signature + docstring + body excerpt) for embedding."""

from typing import List, Tuple

from api.semantic_tree.models import CodeEntity, CodebaseSnapshot


def entity_chunks(snapshot: CodebaseSnapshot) -> List[Tuple[CodeEntity, str]]:
    """
    Produce one text chunk per entity for embedding. Returns list of (entity, chunk_text).
    Chunk = signature + docstring + truncated body so each embedding maps to one entity.
    """
    out: List[Tuple[CodeEntity, str]] = []
    for entity in snapshot.all_entities:
        parts = [entity.name]
        if entity.signature:
            parts.append(entity.signature)
        if entity.docstring:
            parts.append(entity.docstring)
        if entity.body_text:
            # Cap body to avoid huge chunks (e.g. 2000 chars)
            body = entity.body_text[:2000]
            if len(entity.body_text) > 2000:
                body += "\n..."
            parts.append(body)
        chunk_text = "\n".join(parts)
        out.append((entity, chunk_text))
    return out
