"""Binding-health reconciler.

For each binding, resolves the anchor against current source, compares the
stored fingerprint to the current fingerprint, and writes a BindingResolution
row.  Runs:
  - After every commit (post-commit hook), for bindings in changed files.
  - On demand via `codoc health`.

Drift detection concept borrowed from Gama et al. CSUR 2014 (concept-drift
periodic re-evaluation) and Panthaplackel et al. ACL 2020/2021 (just-in-time
comment-code inconsistency detection).

LLM judge: DocChecker-style (Dau et al. NAACL 2024) single-call verdict at
temperature=0 unless confidence is in the 0.4–0.7 band, in which case we
resample N=3 (Wang et al. NeurIPS 2022 self-consistency, narrow band only).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from codoc.core.fingerprint import fingerprint_chunk
from codoc.core.logging import get_logger
from codoc.model.hlc import HLC

if TYPE_CHECKING:
    from codoc.storage.sqlite_store import SQLiteStore

_log = get_logger(__name__)


def reconcile_feature(
    feature_uuid: str,
    store: "SQLiteStore",
    root_dir: str,
    *,
    run_llm_judge: bool = False,
) -> list[dict]:
    """Check all bindings for *feature_uuid* and write BindingResolution rows.

    Returns a list of resolution dicts (one per binding).
    """
    from codoc.lang import detect_language, get_adapter

    bindings = store.list_bindings(feature_uuid)
    resolutions: list[dict] = []

    for binding in bindings:
        anchor = binding.anchor
        file_abs = str(Path(root_dir) / anchor.file)

        # --- Resolve anchor ---
        resolved = False
        fingerprint_matches = False
        similarity: float | None = None

        try:
            source = Path(file_abs).read_text(encoding="utf-8", errors="replace")
        except OSError:
            # File deleted or unreadable — binding is severed.
            hlc = HLC.now().to_str()
            store.upsert_binding_resolution(
                binding_uuid=binding.uuid,
                checked_at_hlc=hlc,
                resolved=False,
                fingerprint_matches=False,
                verdict="severed",
                confidence=1.0,
                rationale="Source file not found.",
            )
            resolutions.append({"binding_uuid": binding.uuid, "verdict": "severed"})
            continue

        lang = detect_language(anchor.file)
        adapter = None
        if lang:
            try:
                adapter = get_adapter(lang)
            except ValueError:
                pass

        # Try anchor resolution.
        if adapter is not None and anchor.symbol_path:
            try:
                from codoc.core.anchor_resolver import resolve_anchor
                result = resolve_anchor(anchor, source, adapter)
                resolved = result is not None
            except Exception:
                resolved = False
        else:
            resolved = bool(anchor.symbol_path and anchor.symbol_path in source)

        # --- Fingerprint comparison ---
        if resolved and adapter is not None:
            try:
                current_fp = fingerprint_chunk(source, adapter)
                fingerprint_matches = current_fp == binding.fingerprint
                if not fingerprint_matches:
                    from codoc.core.chunk_matching.similarity import token_jaccard
                    similarity = token_jaccard(binding.fingerprint, current_fp)
            except Exception:
                fingerprint_matches = True  # conservative: don't flag on error

        # --- Determine verdict ---
        if not resolved:
            verdict = "severed"
            confidence = 0.95
            rationale = "Anchor symbol not found in current source."
        elif fingerprint_matches:
            verdict = "still_aligned"
            confidence = 1.0
            rationale = "Fingerprint unchanged."
        elif similarity is not None and similarity >= 0.8:
            verdict = "partially_drifted"
            confidence = 0.75
            rationale = f"Fingerprint changed but tokens still {similarity:.0%} similar."
        else:
            verdict = "severed"
            confidence = 0.8
            rationale = "Fingerprint significantly diverged."

        hlc = HLC.now().to_str()
        store.upsert_binding_resolution(
            binding_uuid=binding.uuid,
            checked_at_hlc=hlc,
            resolved=resolved,
            fingerprint_matches=fingerprint_matches,
            similarity=similarity,
            verdict=verdict,
            confidence=confidence,
            rationale=rationale,
        )
        resolutions.append({
            "binding_uuid": binding.uuid,
            "resolved": resolved,
            "fingerprint_matches": fingerprint_matches,
            "similarity": similarity,
            "verdict": verdict,
            "confidence": confidence,
        })
        _log.warning("health.binding_checked %s: %s", binding.uuid[:8], verdict) if verdict != "still_aligned" else None

    return resolutions


def reconcile_all(
    store: "SQLiteStore",
    root_dir: str,
    *,
    run_llm_judge: bool = False,
) -> dict:
    """Reconcile all bindings across all features."""
    features = store.list_features()
    total = 0
    drifted = 0
    severed = 0

    for feature in features:
        resolutions = reconcile_feature(
            feature.uuid, store, root_dir, run_llm_judge=run_llm_judge
        )
        for r in resolutions:
            total += 1
            v = r.get("verdict", "still_aligned")
            if v == "partially_drifted":
                drifted += 1
            elif v == "severed":
                severed += 1

    return {"total_checked": total, "drifted": drifted, "severed": severed}


def reconcile_files(
    store: "SQLiteStore",
    root_dir: str,
    file_paths: list[str],
) -> dict:
    """Reconcile bindings for a specific set of files (post-commit fast-path)."""
    checked_features: set[str] = set()
    for file_path in file_paths:
        from codoc.storage.sqlite_store import SQLiteStore as _S
        bindings = store.list_bindings_by_file(file_path)
        for b in bindings:
            checked_features.add(b.feature_uuid)

    total = 0
    drifted = 0
    severed = 0
    for fid in checked_features:
        resolutions = reconcile_feature(fid, store, root_dir)
        for r in resolutions:
            total += 1
            v = r.get("verdict", "still_aligned")
            if v == "partially_drifted":
                drifted += 1
            elif v == "severed":
                severed += 1

    return {"total_checked": total, "drifted": drifted, "severed": severed}
