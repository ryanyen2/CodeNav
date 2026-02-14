"""Content hashing for entities and files (whitespace-normalized)."""

import hashlib
import re
from typing import Tuple

from api.semantic_tree.models import CodeEntity


def _normalize_whitespace(text: str) -> str:
    """Collapse whitespace and strip for stable hashing."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


def compute_entity_fingerprint(entity: CodeEntity) -> Tuple[str, str]:
    """
    Return (content_hash, signature_hash) for an entity.
    content_hash = body + docstring (normalized); signature_hash = signature (normalized).
    """
    parts = []
    if entity.signature:
        parts.append(_normalize_whitespace(entity.signature))
    if entity.docstring:
        parts.append(_normalize_whitespace(entity.docstring))
    if entity.body_text:
        parts.append(_normalize_whitespace(entity.body_text))
    content = "\n".join(parts) or entity.name
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    sig_text = _normalize_whitespace(entity.signature or "")
    signature_hash = hashlib.sha256(sig_text.encode("utf-8")).hexdigest()
    return content_hash, signature_hash


def compute_file_fingerprint(content: str) -> str:
    """SHA-256 of normalized file content."""
    return hashlib.sha256(_normalize_whitespace(content).encode("utf-8")).hexdigest()
