"""Content hashing for entities and files (robust normalization for accurate delta)."""

import hashlib
import re
from typing import Tuple

from api.semantic_tree.models import CodeEntity


def _normalize_whitespace(text: str) -> str:
    """Collapse whitespace and strip for stable hashing."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


def _strip_line_comments(line: str) -> str:
    """Remove Python line comment (# ...) from a single line; keeps string literals intact (simple heuristic)."""
    stripped = line.strip()
    if "#" not in stripped:
        return line.rstrip()
    # Find first # not inside a string (simple: not after " or ')
    in_double = False
    in_single = False
    i = 0
    for i, c in enumerate(line):
        if c == '"' and not in_single:
            in_double = not in_double
        elif c == "'" and not in_double:
            in_single = not in_single
        elif c == "#" and not in_double and not in_single:
            return line[:i].rstrip()
    return line.rstrip()


def _normalize_for_fingerprint(text: str) -> str:
    """
    Robust normalization so trivial edits (comments, blank lines, whitespace) don't change the hash.
    Uses line-level strip of comments and collapse of blank lines so delta is more accurate.
    """
    if not text:
        return ""
    lines = text.splitlines()
    meaningful = []
    for line in lines:
        cleaned = _strip_line_comments(line)
        cleaned = _normalize_whitespace(cleaned)
        if cleaned:
            meaningful.append(cleaned)
    return "\n".join(meaningful) if meaningful else ""


_CONTENT_SAMPLE_MAX = 2000


def get_entity_content_for_fingerprint(entity: CodeEntity) -> str:
    """Return normalized content string used for hashing (and optional difflib sample)."""
    parts = []
    if entity.signature:
        parts.append(_normalize_whitespace(entity.signature))
    if entity.docstring:
        parts.append(_normalize_for_fingerprint(entity.docstring))
    if entity.body_text:
        parts.append(_normalize_for_fingerprint(entity.body_text))
    return "\n".join(parts) or entity.name


def compute_entity_fingerprint(entity: CodeEntity) -> Tuple[str, str]:
    """
    Return (content_hash, signature_hash) for an entity.
    content_hash = body + docstring (robustly normalized); signature_hash = signature (normalized).
    Normalization strips line comments and collapses whitespace so minor edits don't flip the hash.
    """
    content = get_entity_content_for_fingerprint(entity)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    sig_text = _normalize_whitespace(entity.signature or "")
    signature_hash = hashlib.sha256(sig_text.encode("utf-8")).hexdigest()
    return content_hash, signature_hash


def get_entity_content_sample(entity: CodeEntity, max_chars: int = _CONTENT_SAMPLE_MAX) -> str:
    """Return truncated normalized content for difflib similarity in delta (optional)."""
    content = get_entity_content_for_fingerprint(entity)
    return content[:max_chars] if len(content) > max_chars else content


def compute_file_fingerprint(content: str) -> str:
    """SHA-256 of normalized file content (robust normalization for accurate delta)."""
    normalized = _normalize_for_fingerprint(content)
    if not normalized:
        normalized = _normalize_whitespace(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
