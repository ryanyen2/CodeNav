"""LLM escalation and proposal emission for the reflective pipeline.

When cheap heuristics cannot decide what to do with a changed chunk, this
module builds the attribution input (chunk + 1-hop neighbourhood) and calls
the attribution agent.  The agent's response is converted into a proposal
Transaction and written to the transaction log.
"""

from __future__ import annotations

from codoc.pipelines.reflective.fingerprint_compare import ChunkChange
from codoc.agents.attribution import AttributionInput, propose_attribution, AttributionProposal
from codoc.core.log import TransactionLog
from codoc.storage.sqlite_store import SQLiteStore
from codoc.model.transaction import Transaction, TransactionKind
from codoc.model.hlc import HLC


# ---------------------------------------------------------------------------
# Neighbourhood builder
# ---------------------------------------------------------------------------


def build_neighborhood(
    change: ChunkChange,
    store: SQLiteStore,
    max_neighbors: int = 20,
) -> list[dict]:
    """Build the 1-hop neighbourhood of features for LLM context.

    Neighbours are ranked by proximity to the changed chunk in three tiers:

    1. Features with at least one binding **in the same file** as the chunk.
    2. Features that are **binding-graph adjacent** — their bindings reference
       symbols also referenced by the changed chunk (shared symbol refs).
    3. Features that are **tree-structural neighbours** — parent or sibling
       chunks by symbol_path prefix.

    Each returned dict has the shape::

        {
            "uuid": str,
            "slug": str,
            "intent": str,
            "binding_count": int,
        }

    The list is deduplicated and capped at *max_neighbors* entries.
    """
    all_bindings = store.get_all_bindings()

    # --- Tier 1: same-file features ---
    same_file_features: dict[str, int] = {}  # feature_uuid → binding count
    for b in all_bindings:
        if b.anchor.file == change.file:
            same_file_features[b.feature_uuid] = same_file_features.get(b.feature_uuid, 0) + 1

    # --- Tier 2: binding-graph adjacent via shared symbol prefix ---
    # Heuristic: features whose bindings share the same top-level module/class
    # prefix as the changed chunk's symbol_path are likely adjacent.
    adjacent_features: dict[str, int] = {}
    changed_prefix = _symbol_prefix(change.symbol_path)
    if changed_prefix:
        for b in all_bindings:
            if b.feature_uuid in same_file_features:
                continue
            if b.anchor.symbol_path and _symbol_prefix(b.anchor.symbol_path) == changed_prefix:
                adjacent_features[b.feature_uuid] = adjacent_features.get(b.feature_uuid, 0) + 1

    # --- Tier 3: tree-structural neighbours (parent / sibling by symbol path) ---
    structural_features: dict[str, int] = {}
    changed_parts = change.symbol_path.rsplit(".", 1)
    if len(changed_parts) == 2:
        parent_prefix = changed_parts[0]
        for b in all_bindings:
            if b.feature_uuid in same_file_features or b.feature_uuid in adjacent_features:
                continue
            if b.anchor.symbol_path and b.anchor.symbol_path.startswith(parent_prefix):
                structural_features[b.feature_uuid] = (
                    structural_features.get(b.feature_uuid, 0) + 1
                )

    # Build ordered, deduplicated list respecting tier priority.
    ordered_uuids: list[str] = []
    seen: set[str] = set()

    for tier in (same_file_features, adjacent_features, structural_features):
        for uuid in sorted(tier, key=lambda u: -tier[u]):  # sort by binding count desc
            if uuid not in seen:
                ordered_uuids.append(uuid)
                seen.add(uuid)
            if len(ordered_uuids) >= max_neighbors:
                break
        if len(ordered_uuids) >= max_neighbors:
            break

    # Hydrate feature metadata.
    result: list[dict] = []
    bindings_count_by_feature: dict[str, int] = {}
    for b in all_bindings:
        bindings_count_by_feature[b.feature_uuid] = (
            bindings_count_by_feature.get(b.feature_uuid, 0) + 1
        )

    for uuid in ordered_uuids[:max_neighbors]:
        feature = store.get_feature(uuid)
        if feature is None:
            continue
        result.append(
            {
                "uuid": uuid,
                "slug": feature.slug,
                "intent": feature.intent,
                "binding_count": bindings_count_by_feature.get(uuid, 0),
            }
        )

    return result


def _symbol_prefix(symbol_path: str) -> str:
    """Return the top-level module/class prefix of a symbol path.

    ``"api/parser.py::RequestParser.parse"`` → ``"api/parser.py::RequestParser"``
    ``"api/parser.py::top_level_func"``       → ``"api/parser.py"``
    """
    if "::" in symbol_path:
        file_part, entity_part = symbol_path.split("::", 1)
        parts = entity_part.split(".")
        if len(parts) > 1:
            return f"{file_part}::{parts[0]}"
        return file_part
    return symbol_path.rsplit(".", 1)[0] if "." in symbol_path else symbol_path


# ---------------------------------------------------------------------------
# LLM escalation
# ---------------------------------------------------------------------------


def escalate_to_llm(
    change: ChunkChange,
    store: SQLiteStore,
    tx_log: TransactionLog,
    repo_name: str = "codebase",
    author: str = "reflective",
) -> Transaction | None:
    """Call the attribution agent and emit a proposal transaction.

    Parameters
    ----------
    change:
        The ChunkChange that could not be resolved by cheap heuristics.
    store:
        An open SQLiteStore for neighbourhood lookups.
    tx_log:
        TransactionLog used to persist the emitted proposal.
    repo_name:
        Human-readable repository name passed to the attribution prompt.
    author:
        Author string recorded on the transaction.

    Returns
    -------
    Transaction | None
        The stamped proposal transaction, or ``None`` if the attribution agent
        returns an unrecognised kind or raises an exception.
    """
    neighborhood = build_neighborhood(change, store)

    # Build the current_binding dict if this chunk is already attributed.
    current_binding_dict: dict | None = None
    if change.existing_binding_uuid is not None:
        binding = store.get_binding(change.existing_binding_uuid)
        if binding is not None:
            current_binding_dict = binding.model_dump(mode="json")

    source_snippet = ""
    if change.chunk is not None:
        source_snippet = change.chunk.source[:400]

    attribution_input = AttributionInput(
        file=change.file,
        symbol_path=change.symbol_path,
        source_snippet=source_snippet,
        change_kind=change.change_kind,
        current_binding=current_binding_dict,
        neighboring_features=neighborhood,
    )

    try:
        proposal: AttributionProposal = propose_attribution(
            attribution_input,
            repo_name=repo_name,
        )
    except Exception:
        # If the LLM call fails (network, parsing, etc.) do not crash the
        # pipeline — skip this chunk and return None.
        return None

    hlc = HLC.now()

    payload = dict(proposal.payload)
    payload.setdefault("symbol_path", change.symbol_path)
    payload.setdefault("file", change.file)
    payload.setdefault("rationale", proposal.rationale)
    payload.setdefault("change_kind", change.change_kind)
    if change.existing_binding_uuid is not None:
        payload.setdefault("binding_uuid", change.existing_binding_uuid)

    tx = Transaction(
        hlc=hlc,
        parent_hlcs=[],
        kind=proposal.kind,
        payload=payload,
        author=author,
        proposal=True,
    )

    try:
        stamped = tx_log.append_proposal(tx)
    except Exception:
        return None

    return stamped
