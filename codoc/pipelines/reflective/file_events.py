"""File-level change event detector.

Pre-pass that runs before per-chunk processing in the reflective pipeline.
Classifies whole-file mutations and emits one typed proposal per event so the
user sees semantically cohesive actions rather than a swarm of per-binding
EVICTs.

Taxonomy (implemented here):
  RETIRE_FILE  — file deleted with no similar replacement; all bindings evicted
                 and orphaned features retired in one atomic accept.
  RENAME_FILE  — file moved/renamed; all binding anchors remapped to the new
                 path on accept, preserving every binding UUID.

(SPLIT_FILE and MERGE_FILE are reserved in TransactionKind but their detection
heuristics require multi-file overlap analysis not implemented yet.)

Detection algorithm
-------------------
1. For each deleted file, read old source from git at *from_ref*.
2. For each candidate new file (surviving file with no stored bindings),
   read source from disk.
3. Compute per-file MinHash sketches and estimate Jaccard similarity.
4. Greedily assign highest-similarity (deleted, new) pairs above RENAME_THRESHOLD
   as RENAME_FILE candidates.
5. Remaining unmatched deleted files with existing bindings → RETIRE_FILE.
"""

from __future__ import annotations

from pathlib import Path

from codoc.model.transaction import Transaction, TransactionKind
from codoc.model.hlc import HLC
from codoc.core.logging import get_logger

_log = get_logger(__name__)

_RENAME_THRESHOLD = 0.68   # Jaccard floor to call a deletion a rename
_RETIRE_MIN_BINDINGS = 1   # only emit RETIRE_FILE if the file had at least this many bindings


def detect_file_events(
    deleted_files: list[str],
    surviving_files: list[str],
    root_dir: str,
    from_ref: str | None,
    store,
    tx_log,
    author: str = "reflective",
    *,
    old_file_sources: dict[str, str] | None = None,
) -> tuple[list[Transaction], set[str], set[str]]:
    """Classify file-level events and emit typed proposals.

    Parameters
    ----------
    deleted_files:
        Files that no longer exist on disk (identified by the caller).
    surviving_files:
        Files still on disk from the changed set.
    root_dir:
        Absolute path to the repository root.
    from_ref:
        Git ref of the previous state (used to read old file content).
    store:
        Open SQLiteStore for binding lookups.
    tx_log:
        TransactionLog for proposal emission.
    author:
        Author string for emitted transactions.

    Returns
    -------
    tuple of (proposals, claimed_deleted, claimed_surviving)
        proposals: emitted transactions (already appended to tx_log).
        claimed_deleted: deleted file paths consumed by file events.
        claimed_surviving: surviving file paths consumed as rename targets.
    """
    if not deleted_files:
        return [], set(), set()

    from codoc.core.chunk_matching.minhash import minhash_sketch, minhash_jaccard

    # --- Build per-file binding index ---
    all_bindings = store.get_all_bindings()
    bindings_by_file: dict[str, list] = {}
    for b in all_bindings:
        bindings_by_file.setdefault(b.anchor.file, []).append(b)

    # --- Identify candidate new files: surviving files with no stored bindings ---
    files_with_bindings: set[str] = set(bindings_by_file.keys())
    candidate_new: list[str] = [f for f in surviving_files if f not in files_with_bindings]

    # --- Resolve old file sources: prefer caller-supplied (e.g. LanceDB snapshot) ---
    def _get_old_source(f: str) -> str | None:
        if old_file_sources is not None:
            return old_file_sources.get(f)
        if from_ref is None:
            return None
        from codoc.pipelines.reflective.commit_diff import get_file_source_at_ref
        return get_file_source_at_ref(root_dir, f, from_ref)

    # --- Compute MinHash sketches for deleted files ---
    deleted_sketches: dict[str, bytes] = {}
    for f in deleted_files:
        src = _get_old_source(f)
        if src:
            deleted_sketches[f] = minhash_sketch(src.split())

    # --- Compute MinHash sketches for candidate new files (from disk) ---
    new_sketches: dict[str, bytes] = {}
    for f in candidate_new:
        try:
            content = (Path(root_dir) / f).read_text(encoding="utf-8", errors="replace")
            new_sketches[f] = minhash_sketch(content.split())
        except Exception:
            pass

    # --- Build similarity pairs and greedily assign renames ---
    pairs: list[tuple[str, str, float]] = []
    for d in deleted_files:
        if d not in deleted_sketches:
            continue
        for n in candidate_new:
            if n not in new_sketches:
                continue
            sim = minhash_jaccard(deleted_sketches[d], new_sketches[n])
            if sim >= _RENAME_THRESHOLD:
                pairs.append((d, n, sim))

    pairs.sort(key=lambda x: -x[2])

    proposals: list[Transaction] = []
    claimed_deleted: set[str] = set()
    claimed_surviving: set[str] = set()

    for old_f, new_f, sim in pairs:
        if old_f in claimed_deleted or new_f in claimed_surviving:
            continue

        bindings = bindings_by_file.get(old_f, [])
        affected_binding_uuids = [b.uuid for b in bindings]

        payload: dict = {
            "old_file": old_f,
            "new_file": new_f,
            "affected_binding_uuids": affected_binding_uuids,
            "similarity": round(sim, 3),
        }
        tx = Transaction(
            hlc=HLC.now(),
            parent_hlcs=[],
            kind=TransactionKind.RENAME_FILE,
            payload=payload,
            author=author,
            proposal=True,
            label=f"{old_f} → {new_f} ({sim:.0%} similar)",
        )
        try:
            stamped = tx_log.append_proposal(tx)
        except Exception:
            continue

        proposals.append(stamped)
        claimed_deleted.add(old_f)
        claimed_surviving.add(new_f)
        _log.info(
            "file_events: RENAME_FILE %s → %s (jaccard=%.3f, %d bindings)",
            old_f, new_f, sim, len(affected_binding_uuids),
        )

    # --- Emit RETIRE_FILE for remaining unmatched deleted files ---
    for f in deleted_files:
        if f in claimed_deleted:
            continue

        bindings = bindings_by_file.get(f, [])
        if len(bindings) < _RETIRE_MIN_BINDINGS:
            continue  # no bindings → nothing to retire; silent no-op

        affected_feature_uuids = list({b.feature_uuid for b in bindings})
        affected_binding_uuids = [b.uuid for b in bindings]

        payload = {
            "file": f,
            "affected_feature_uuids": affected_feature_uuids,
            "affected_binding_uuids": affected_binding_uuids,
        }
        tx = Transaction(
            hlc=HLC.now(),
            parent_hlcs=[],
            kind=TransactionKind.RETIRE_FILE,
            payload=payload,
            author=author,
            proposal=True,
            label=f"delete {f} ({len(bindings)} bindings, {len(affected_feature_uuids)} features)",
        )
        try:
            stamped = tx_log.append_proposal(tx)
        except Exception:
            continue

        proposals.append(stamped)
        claimed_deleted.add(f)
        _log.info(
            "file_events: RETIRE_FILE %s (%d bindings across %d features)",
            f, len(bindings), len(affected_feature_uuids),
        )

    return proposals, claimed_deleted, claimed_surviving
