"""Central comparison engine for the binding-health loop.

**codoc is a binding-health loop.**  For every ``Binding``, the system answers:
*does the stored ``fingerprint`` still match the current source at the anchor?*

This module provides the one comparison function used by all three production
pipelines:

- ``pipelines/reflective/runner.py`` — post-commit and on-save reflect.
- ``pipelines/health/runner.py``     — scheduled health sweep.

``compare()`` returns a ``Comparison`` whose ``verdict`` is one of:

    still_aligned  — anchor resolves; fingerprint unchanged.
    moved          — anchor no longer resolves; a matching chunk was found elsewhere.
    drifted        — anchor resolves; fingerprint changed.
    severed        — anchor does not resolve; no matching chunk found.
    novel          — new chunk with no stored fingerprint (unattributed add).

``evidence`` carries optional ``{similarity, target_anchor?, rationale?}``.

The GumTree/RefDiff machinery (chunk matcher) is invoked *only* in the
``moved`` branch; the LLM escalation path is invoked *only* in the ``drifted``
branch.  All other branches are decided by pure hashing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from codoc.core.fingerprint import fingerprint_chunk

if TYPE_CHECKING:
    from codoc.model.binding import Binding


VERDICT_STILL_ALIGNED = "still_aligned"
VERDICT_MOVED = "moved"
VERDICT_DRIFTED = "drifted"
VERDICT_SEVERED = "severed"
VERDICT_NOVEL = "novel"


@dataclass
class Comparison:
    verdict: str   # one of the VERDICT_* constants above
    binding_uuid: str
    evidence: dict = field(default_factory=dict)
    """Optional evidence dict:
    - similarity (float 0-1): combined skeleton+minhash score when a move candidate was found.
    - target_file (str): file of the match candidate for 'moved' verdicts.
    - target_symbol_path (str): symbol_path of the match candidate.
    - rationale (str): human-readable explanation.
    - score (float): matcher score for moved/similar matches.
    """


def compare(
    binding: "Binding",
    current_chunks_index: dict[tuple[str, str], str],
    *,
    adapter=None,
    root_dir: str | None = None,
    moved_candidates: list[dict] | None = None,
    matcher_thresholds=None,
) -> Comparison:
    """Compare a stored binding against the current codebase state.

    Parameters
    ----------
    binding:
        The ``Binding`` record from the store.
    current_chunks_index:
        Mapping of ``(file, symbol_path) → current_fingerprint`` for all chunks
        visible in the current working tree.  Built once per pipeline run.
    adapter:
        Optional language adapter used to fingerprint the current source text.
        If ``None``, fingerprints are compared using the pre-computed values in
        ``current_chunks_index`` only.
    root_dir:
        Absolute path to the repo root.  Required for reading source files when
        ``adapter`` is provided and anchor-level fingerprinting is needed.
    moved_candidates:
        Optional list of chunk dicts ``{file, symbol_path, source}`` to search
        for a move target when the anchor no longer resolves.  If absent or
        empty, a severed anchor is classified as ``severed`` rather than
        ``moved``.
    matcher_thresholds:
        Optional ``MatchingThresholds`` instance.  Defaults to
        ``MatchingThresholds()`` (0.85 moved / 0.55 similar floor).

    Returns
    -------
    Comparison
    """
    anchor = binding.anchor
    key = (anchor.file, anchor.symbol_path or "")

    current_fp = current_chunks_index.get(key)

    # -----------------------------------------------------------------------
    # Branch 1: anchor key found in the current index (still in the tree).
    # -----------------------------------------------------------------------
    if current_fp is not None:
        if current_fp == binding.fingerprint:
            return Comparison(
                verdict=VERDICT_STILL_ALIGNED,
                binding_uuid=binding.uuid,
                evidence={"rationale": "Fingerprint unchanged."},
            )
        else:
            return Comparison(
                verdict=VERDICT_DRIFTED,
                binding_uuid=binding.uuid,
                evidence={"rationale": "Fingerprint changed."},
            )

    # -----------------------------------------------------------------------
    # Branch 2: anchor key not in the current index.
    # Try to find a move candidate.
    # -----------------------------------------------------------------------
    if moved_candidates:
        from codoc.core.chunk_matching.matcher import match_chunks, MatchingThresholds
        thresholds = matcher_thresholds or MatchingThresholds()

        # Build a synthetic "removed" chunk for the binding's anchor.
        old_source = _read_binding_source(binding, root_dir) if root_dir else ""
        removed = [{"file": anchor.file, "symbol_path": anchor.symbol_path or "", "source": old_source}]

        matches = match_chunks(removed, moved_candidates, thresholds=thresholds, adapter=adapter)
        if matches:
            best = matches[0]
            if best.verdict in ("moved", "similar"):
                return Comparison(
                    verdict=VERDICT_MOVED,
                    binding_uuid=binding.uuid,
                    evidence={
                        "target_file": best.new_file,
                        "target_symbol_path": best.new_symbol_path,
                        "score": round(best.score, 4),
                        "rationale": f"Chunk moved to {best.new_file}::{best.new_symbol_path}.",
                    },
                )

    # -----------------------------------------------------------------------
    # Branch 3: anchor gone, no move candidate found.
    # -----------------------------------------------------------------------
    return Comparison(
        verdict=VERDICT_SEVERED,
        binding_uuid=binding.uuid,
        evidence={"rationale": "Anchor symbol not found in current source."},
    )


def build_chunks_index(
    chunks_by_file: dict,
    language_adapters: dict,
) -> dict[tuple[str, str], str]:
    """Build ``(file, symbol_path) → fingerprint`` index from chunk lists.

    Parameters
    ----------
    chunks_by_file:
        Mapping ``{file_path: [Chunk, ...]}`` as returned by
        ``extract_chunks_for_files``.
    language_adapters:
        Mapping ``{language_name: adapter}`` used to compute fingerprints.
    """
    from codoc.lang import detect_language, get_adapter

    index: dict[tuple[str, str], str] = {}
    for file, chunks in chunks_by_file.items():
        lang = detect_language(file)
        if lang is None:
            continue
        adapter = language_adapters.get(lang)
        if adapter is None:
            try:
                adapter = get_adapter(lang)
            except ValueError:
                continue
        for chunk in chunks:
            try:
                fp = fingerprint_chunk(chunk.source, adapter)
            except Exception:
                fp = fingerprint_chunk(chunk.source)
            index[(file, chunk.symbol_path)] = fp
    return index


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_binding_source(binding: "Binding", root_dir: str) -> str:
    """Read the current source for a binding's anchor file (best-effort)."""
    try:
        file_abs = Path(root_dir) / binding.anchor.file
        return file_abs.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
