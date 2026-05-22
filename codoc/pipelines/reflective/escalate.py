"""Cheap heuristics that decide whether a ChunkChange needs LLM escalation.

The gate rules are intentionally simple and fast so the LLM is only called
for genuinely ambiguous cases.  Every path that avoids LLM escalation either
emits a transaction directly (EVICT) or takes no action at all.
"""

from __future__ import annotations

from codoc.pipelines.reflective.types import ChunkChange
from codoc.model.transaction import Transaction, TransactionKind
from codoc.model.hlc import HLC


def should_escalate(change: ChunkChange, all_bindings: list) -> bool:
    """Return True if this change requires LLM attribution judgment.

    Decision matrix
    ---------------
    - ``"removed"`` + has existing binding → **False** (emit EVICT directly)
    - ``"removed"`` + no existing binding  → **False** (unattributed removal, no-op)
    - ``"added"``   + no existing binding  → **True**  (new chunk: INTRODUCE or ABSORB)
    - ``"modified"``+ has existing binding → **True**  (fingerprint drift: LLM judges)
    - ``"added"``   + existing binding     → **True**  (surprising state: let LLM decide)
    - ``"modified"``+ no existing binding  → **False** (unattributed change, ignore)

    Cheap heuristic shortcut (ABSORB without LLM)
    -----------------------------------------------
    If ``change_kind == "added"`` **and** there is exactly one feature whose
    other bindings all reside in the same file as the new chunk, we can emit
    ABSORB with high confidence and return **False** to skip the LLM.

    The caller is responsible for actually emitting the ABSORB transaction
    when this function returns False and change_kind == "added" — check
    ``_is_namespace_absorb(change, all_bindings)`` separately for that case.
    """
    kind = change.change_kind

    if kind == "removed":
        # Never escalate removals: we handle them with emit_evict_proposal or ignore.
        return False

    if kind == "modified" and change.existing_binding_uuid is None:
        # An unattributed chunk changed — no feature owns it, nothing to update.
        return False

    if kind == "added" and change.existing_binding_uuid is None:
        # Check namespace absorb heuristic before committing to LLM.
        if _is_namespace_absorb(change, all_bindings):
            return False  # Caller will emit ABSORB directly.
        return True  # Needs LLM to propose INTRODUCE or ABSORB.

    # All remaining cases (added with existing binding, modified with binding)
    # go to the LLM.
    return True


def is_namespace_absorb(change: ChunkChange, all_bindings: list) -> bool:
    """Return True if this added chunk can be absorbed into an existing feature
    without LLM judgment.

    Namespace-gate absorb: returns True only when the new chunk's dotted symbol
    prefix matches >= 50% of an existing feature's bindings in the same file.
    """
    return _is_namespace_absorb(change, all_bindings)


def _is_namespace_absorb(change: ChunkChange, all_bindings: list) -> bool:
    """Namespace-gate absorb: returns True only when the new chunk's dotted symbol
    prefix matches >= 50% of an existing feature's bindings in the same file.

    Requires *both*:
    1. Exactly one feature has all its bindings in the same file as the new chunk.
    2. The new chunk shares its symbol namespace (top-level dotted prefix) with
       the majority of that feature's bindings — prevents over-absorption into
       broad single-file features that contain unrelated top-level entities.
    """
    if not all_bindings:
        return False

    bindings_by_feature: dict[str, list] = {}
    for b in all_bindings:
        bindings_by_feature.setdefault(b.feature_uuid, []).append(b)

    candidates: list[list] = []
    for bindings in bindings_by_feature.values():
        if all(b.anchor.file == change.file for b in bindings):
            candidates.append(bindings)

    if len(candidates) != 1:
        return False

    return _shares_symbol_namespace(change.symbol_path, candidates[0])


# Keep the old name as an alias so any external callers are not broken.
is_cheap_absorb = is_namespace_absorb


def _shares_symbol_namespace(new_sp: str, bindings: list) -> bool:
    """Return True if *new_sp* shares its dotted-path prefix with ≥ 50% of bindings.

    Prevents absorbing a top-level function into a feature that only contains
    methods of a specific class (different namespace prefix).
    """
    if not new_sp or not bindings:
        return True

    new_parts = new_sp.split(".")
    if len(new_parts) < 2:
        # Top-level symbol: only absorb if the majority of bindings are also
        # top-level (no dotted path) — avoids pulling a module-level util into
        # a class-scoped feature.
        top_level_count = sum(
            1 for b in bindings
            if b.anchor.symbol_path and "." not in b.anchor.symbol_path
        )
        return top_level_count / len(bindings) >= 0.5

    new_prefix = new_parts[0]
    matches = sum(
        1 for b in bindings
        if b.anchor.symbol_path and b.anchor.symbol_path.split(".")[0] == new_prefix
    )
    return matches / len(bindings) >= 0.5


def emit_evict_proposal(
    change: ChunkChange,
    tx_log,
    author: str = "reflective",
) -> Transaction:
    """Emit an EVICT proposal for a chunk whose binding broke.

    This is called when:
    - ``change_kind == "removed"`` and ``existing_binding_uuid`` is not ``None``

    The transaction is written as a *proposal* (requires user acceptance).

    Parameters
    ----------
    change:
        The ChunkChange describing the removed chunk.
    tx_log:
        A :class:`~codoc.core.log.TransactionLog` instance used to persist
        the proposal.
    author:
        Author string recorded on the transaction (default ``"reflective"``).

    Returns
    -------
    Transaction
        The stamped proposal transaction as returned by ``tx_log.append_proposal``.
    """
    hlc = HLC.now()

    payload: dict = {
        "binding_uuid": change.existing_binding_uuid,
        "symbol_path": change.symbol_path,
        "file": change.file,
        "reason": "anchor_unresolvable_or_file_deleted",
        "stored_fingerprint": change.stored_fingerprint,
    }

    tx = Transaction(
        hlc=hlc,
        parent_hlcs=[],
        kind=TransactionKind.EVICT,
        payload=payload,
        author=author,
        proposal=True,
    )

    return tx_log.append_proposal(tx)
