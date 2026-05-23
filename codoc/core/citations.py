"""Citation parsing, extraction, and freshness maintenance for [ref:] tokens.

[ref:] tokens appear in purpose/rationale/scenario fields and link descriptions
to the code or features they describe. This module:

  1. Parses raw ref token bodies into (target_kind, target_path, line_range).
  2. Extracts all citations from a feature's structured text fields.
  3. Applies stale markers at render time when a citation's target has moved.

Citation grammar (same as markup DSL):
  [ref: file::Symbol]               → code citation, no line range
  [ref: file::Symbol#L42-58]        → code citation, line range 42-58
  [ref: feature://slug]             → feature cross-reference
  [ref: file://path/to/file.py]     → whole-file citation
"""

from __future__ import annotations

import re

_REF_RE = re.compile(r'\[ref:\s*([^\]]+?)\]')
# @symbol or @file.py::Symbol inline form — not preceded by a word char (avoids email addresses)
_AT_REF_RE = re.compile(r'(?<!\w)@([\w.]+(?:::[\w.]+)*)')
_LINE_RE = re.compile(r'#L(\d+)(?:-(\d+))?$')


def parse_ref_target(raw: str) -> tuple[str, str, int | None, int | None]:
    """Parse a ref token body into (target_kind, target_path, line_start, line_end)."""
    raw = raw.strip()

    line_start = line_end = None
    m = _LINE_RE.search(raw)
    if m:
        line_start = int(m.group(1))
        line_end = int(m.group(2)) if m.group(2) else line_start
        raw = raw[:m.start()].rstrip()

    if raw.startswith("feature://"):
        return "feature", raw[len("feature://"):], None, None
    if raw.startswith("file://"):
        return "file", raw[len("file://"):], line_start, line_end
    return "code", raw, line_start, line_end


def extract_citations(feature_uuid: str, fields: dict[str, str]) -> list[dict]:
    """Extract all [ref:] and @symbol citations from a {field_name: text} mapping.

    Returns a list of dicts ready for ``store.upsert_citation(**c)``.
    """
    results: list[dict] = []
    for field_name, text in fields.items():
        if not text:
            continue
        seen: set[str] = set()
        i = 0
        # [ref: ...] form
        for m in _REF_RE.finditer(text):
            kind, path, ls, le = parse_ref_target(m.group(1))
            key = f"ref:{path}"
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "id": f"{feature_uuid}:{field_name}:{i}",
                "feature_uuid": feature_uuid,
                "field": field_name,
                "bullet_index": i,
                "target_kind": kind,
                "target_path": path,
                "line_start": ls,
                "line_end": le,
                "is_stale": False,
            })
            i += 1
        # @symbol form — skip if same path already recorded via [ref:]
        for m in _AT_REF_RE.finditer(text):
            raw = m.group(1)
            kind, path, ls, le = parse_ref_target(raw)
            key = f"ref:{path}"
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "id": f"{feature_uuid}:{field_name}:at:{i}",
                "feature_uuid": feature_uuid,
                "field": field_name,
                "bullet_index": i,
                "target_kind": kind,
                "target_path": path,
                "line_start": ls,
                "line_end": le,
                "is_stale": False,
            })
            i += 1
    return results


def populate_citations(feature_uuid: str, feature, store) -> None:
    """Re-extract and upsert all citations for a feature's structured text fields.

    Clears all existing citations for the feature first so stale entries from
    renamed/removed refs don't accumulate.
    """
    fields = {
        "purpose": getattr(feature, "purpose", "") or "",
        "rationale": getattr(feature, "rationale", "") or "",
        "scenario": getattr(feature, "scenario", "") or "",
    }
    try:
        store.delete_citations_for_feature(feature_uuid)
    except Exception:
        pass
    for c in extract_citations(feature_uuid, fields):
        try:
            store.upsert_citation(**c)
        except Exception:
            pass


def apply_stale_markers(text: str, stale_paths: frozenset[str]) -> str:
    """Mark stale [ref:] and @symbol citations inline.

    [ref: stale_path] → [ref: ⚠ stale: stale_path]
    @staleSym → [⚠ @staleSym]

    *stale_paths* contains canonical target_path values (no line ranges or kind prefixes).
    """
    if not stale_paths or not text:
        return text

    def _sub_ref(m: re.Match) -> str:
        raw = m.group(1).strip()
        path = _LINE_RE.sub("", raw).rstrip()
        for prefix in ("feature://", "file://"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        if path in stale_paths:
            return f"[ref: ⚠ stale: {raw}]"
        return m.group(0)

    def _sub_at(m: re.Match) -> str:
        path = m.group(1)
        if path in stale_paths:
            return f"[⚠ @{path}]"
        return m.group(0)

    text = _REF_RE.sub(_sub_ref, text)
    text = _AT_REF_RE.sub(_sub_at, text)
    return text
