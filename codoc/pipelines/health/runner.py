"""Heuristic binding-health sweep: re-resolves anchors and compares current
fingerprints to stored ones. No LLM calls. Fast; safe to run on every commit.

Routes through ``codoc.core.reconciler.compare`` — the same comparison engine
used by the reflective pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from codoc.core.logging import get_logger
from codoc.model.hlc import HLC

if TYPE_CHECKING:
    from codoc.storage.sqlite_store import SQLiteStore

_log = get_logger(__name__)


def reconcile_feature(
    feature_uuid: str,
    store: "SQLiteStore",
    root_dir: str,
) -> list[dict]:
    """Check all bindings for *feature_uuid* and write BindingResolution rows.

    Returns a list of resolution dicts (one per binding).
    """
    from codoc.lang import detect_language, get_adapter
    from codoc.core.reconciler import compare, build_chunks_index
    from codoc.pipelines.reflective.commit_diff import extract_chunks_for_files

    bindings = store.list_bindings(feature_uuid)
    if not bindings:
        return []

    # Collect the set of files touched by this feature's bindings.
    files_needed: set[str] = {b.anchor.file for b in bindings}

    # Build the chunks index for those files (only files that exist on disk).
    existing_files = [f for f in files_needed if (Path(root_dir) / f).exists()]
    chunks_by_file = extract_chunks_for_files(root_dir, existing_files) if existing_files else {}

    # Build language adapters for the files we found.
    language_adapters: dict = {}
    for file in chunks_by_file:
        lang = detect_language(file)
        if lang and lang not in language_adapters:
            try:
                language_adapters[lang] = get_adapter(lang)
            except ValueError:
                pass

    chunks_index = build_chunks_index(chunks_by_file, language_adapters)

    resolutions: list[dict] = []

    for binding in bindings:
        # Resolve adapter for this binding's file.
        lang = detect_language(binding.anchor.file)
        adapter = language_adapters.get(lang) if lang else None

        comparison = compare(
            binding,
            chunks_index,
            adapter=adapter,
            root_dir=root_dir,
        )

        # Map reconciler verdict to health-sweep fields.
        verdict = comparison.verdict
        rationale = comparison.evidence.get("rationale", "")
        resolved = verdict not in ("severed",)
        fingerprint_matches = verdict == "still_aligned"

        hlc = HLC.now().to_str()
        store.upsert_binding_resolution(
            binding_uuid=binding.uuid,
            checked_at_hlc=hlc,
            resolved=resolved,
            fingerprint_matches=fingerprint_matches,
            similarity=None,
            verdict=verdict,
            confidence=None,
            rationale=rationale,
        )
        resolutions.append({
            "binding_uuid": binding.uuid,
            "resolved": resolved,
            "fingerprint_matches": fingerprint_matches,
            "similarity": None,
            "verdict": verdict,
            "confidence": None,
        })
        if verdict != "still_aligned":
            _log.warning("health.binding_checked %s: %s", binding.uuid[:8], verdict)

    return resolutions


def reconcile_all(
    store: "SQLiteStore",
    root_dir: str,
) -> dict:
    """Reconcile all bindings across all features."""
    features = store.list_features()
    total = 0
    drifted = 0
    severed = 0

    for feature in features:
        resolutions = reconcile_feature(feature.uuid, store, root_dir)
        for r in resolutions:
            total += 1
            v = r.get("verdict", "still_aligned")
            if v == "drifted":
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
            if v == "drifted":
                drifted += 1
            elif v == "severed":
                severed += 1

    return {"total_checked": total, "drifted": drifted, "severed": severed}
