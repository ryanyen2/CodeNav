"""LLM escalation and proposal emission for the reflective pipeline.

When cheap heuristics cannot decide what to do with a changed chunk, this
module builds the attribution input (chunk + 1-hop neighbourhood) and calls
the attribution agent.  The agent's response is converted into a proposal
Transaction and written to the transaction log.

Also provides ``propose_for_new_file`` — a batched entrypoint that treats an
entirely new file as a mini-bootstrap, grouping all its chunks in one LLM call
instead of N separate per-chunk escalations.
"""

from __future__ import annotations

from codoc.pipelines.reflective.types import ChunkChange
from codoc.agents.attribution import AttributionInput, propose_attribution, AttributionProposal
from codoc.core.log import TransactionLog
from codoc.core.logging import get_logger
from codoc.storage.sqlite_store import SQLiteStore
from codoc.model.transaction import Transaction, TransactionKind
from codoc.model.hlc import HLC

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Neighbourhood builder
# ---------------------------------------------------------------------------


def build_neighborhood(
    change: ChunkChange,
    store: SQLiteStore,
    max_neighbors: int = 20,
) -> list[dict]:
    # Delegate to the shared module (kept here for backward compatibility).
    from codoc.pipelines._shared.prompt_context import build_neighborhood_features
    return build_neighborhood_features(change.file, change.symbol_path, store, max_neighbors)



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

    from codoc.pipelines._shared.prompt_context import build_tree_context
    tree_context = build_tree_context(store)

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
        tree_context=tree_context,
    )

    try:
        proposal: AttributionProposal = propose_attribution(
            attribution_input,
            repo_name=repo_name,
        )
    except Exception as exc:
        _log.warning("reflect.propose.llm_failed %s@%s: %s", change.symbol_path, change.file, exc)
        return None

    hlc = HLC.now()

    payload = dict(proposal.payload)
    payload.setdefault("symbol_path", change.symbol_path)
    payload.setdefault("file", change.file)
    payload.setdefault("rationale", proposal.rationale)
    payload.setdefault("change_kind", change.change_kind)
    payload.setdefault("description", "")  # ensure description field is always present
    if change.existing_binding_uuid is not None:
        payload.setdefault("binding_uuid", change.existing_binding_uuid)
    if change.current_fingerprint is not None:
        payload.setdefault("current_fingerprint", change.current_fingerprint)

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


# ---------------------------------------------------------------------------
# New-file batched proposal
# ---------------------------------------------------------------------------


def propose_for_new_file(
    file: str,
    changes: list[ChunkChange],
    store: SQLiteStore,
    tx_log: TransactionLog,
    root_dir: str = "",
    repo_name: str = "codebase",
    author: str = "reflective",
    language_adapters: dict | None = None,
) -> list[Transaction]:
    """Propose features for a brand-new file in a single batched LLM call.

    Instead of calling the LLM N times (once per chunk in the new file), this
    bundles all of the file's chunks into one ``ClusterInput`` and calls
    ``propose_subtree`` — the same agent used by the bootstrap pipeline.  This
    ensures:

    - At most 1-3 proposals per new file (matching the existing tree's grain).
    - Consistent noun-phrase slug style with the bootstrap tree.
    - The LLM sees the existing tree for context and can choose a fitting parent.

    Parameters
    ----------
    file:
        Repo-relative path of the new file.
    changes:
        All ``ChunkChange`` objects for this file (all should be change_kind="added").
    store:
        Open SQLiteStore for existing tree context.
    tx_log:
        TransactionLog for proposal emission.
    root_dir:
        Repository root (used to read source for module docstring/imports).
    repo_name:
        Human-readable repo name for the LLM prompt.
    author:
        Author string for emitted transactions.
    language_adapters:
        Mapping of language → adapter for fingerprinting candidate bindings.

    Returns
    -------
    list[Transaction]
        Emitted INTRODUCE proposal transactions (may be empty on LLM failure).
    """
    from codoc.lang import Chunk as LangChunk
    from codoc.agents.bootstrap_clustering import propose_subtree, ClusterInput
    from codoc.pipelines._shared.prompt_context import build_tree_context
    from codoc.pipelines.bootstrap.propose import emit_introduce_proposal
    from codoc.pipelines.bootstrap.semantic_cluster import (
        extract_module_docstring,
        extract_imports,
    )
    from pathlib import Path as _Path

    if language_adapters is None:
        language_adapters = {}

    # Read source once for module docstring + imports
    source = ""
    abs_path = _Path(root_dir) / file if root_dir else _Path(file)
    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass

    module_docstring = extract_module_docstring(source)
    imports = sorted(extract_imports(source))

    # Build cluster input from all chunks in this file
    chunk_dicts: list[dict] = []
    for change in changes:
        if change.chunk is None:
            continue
        chunk_dicts.append({
            "symbol_path": change.symbol_path,
            "file": file,
            "source_snippet": change.chunk.source[:600],
            "module_docstring": module_docstring,
            "imports": imports,
        })

    if not chunk_dicts:
        return []

    cluster = ClusterInput(chunks=chunk_dicts, cluster_id=0)

    # Build existing-tree context for parent/sibling hints
    tree_ctx = build_tree_context(store)
    existing_summaries = [
        {"slug": f["slug"], "intent": f["intent"]}
        for f in tree_ctx.get("root_features", [])
    ]

    # Find the best-matching existing parent feature by cosine similarity of
    # the file's module docstring/imports to existing feature intents.
    parent_uuid, parent_title, parent_intent = _find_best_parent(
        module_docstring=module_docstring,
        imports=imports,
        store=store,
    )

    try:
        feature_proposals = propose_subtree(
            cluster=cluster,
            parent_feature_title=parent_title,
            parent_feature_intent=parent_intent,
            sibling_titles=[],
            existing_feature_summaries=existing_summaries,
            depth=1 if parent_uuid else 0,
            repo_name=repo_name,
        )
    except Exception as exc:
        _log.warning("reflect.propose_for_new_file: LLM failed for %s: %s", file, exc)
        return []

    # Collect all chunk objects for fingerprinting
    chunks: list = [
        _change_to_lang_chunk(ch)
        for ch in changes
        if ch.chunk is not None
    ]

    emitted: list[Transaction] = []
    for fp in feature_proposals:
        # Restrict candidate_chunk_keys to symbols actually in this file
        file_symbols = {ch.symbol_path for ch in changes if ch.chunk is not None}
        fp.candidate_chunk_keys = [k for k in fp.candidate_chunk_keys if k in file_symbols]

        tx = emit_introduce_proposal(
            proposal=fp,
            chunks=chunks,
            tx_log=tx_log,
            language_adapters=language_adapters,
            author=author,
            parent_uuid=parent_uuid,
        )
        emitted.append(tx)

    return emitted


def _find_best_parent(
    module_docstring: str,
    imports: list[str],
    store: SQLiteStore,
    threshold: float = 0.40,
) -> tuple[str | None, str, str]:
    """Find the best existing feature to nest a new file under.

    Uses cosine similarity of the file's module docstring embedding vs each
    feature's intent embedding.  Returns ``(uuid, title, intent)`` of the
    best match, or ``(None, "<repo-root>", "")`` when no match exceeds
    *threshold* or embeddings are unavailable.
    """
    from codoc.pipelines._shared.prompt_context import build_tree_context
    from codoc.config import embed as _embed

    try:
        file_text = module_docstring + " " + " ".join(imports)
        file_vec = _embed(file_text)
    except Exception:
        return None, "<repo-root>", ""

    try:
        all_features = store.list_features()
    except Exception:
        return None, "<repo-root>", ""

    active = [f for f in all_features if not f.retired and f.intent]
    if not active:
        return None, "<repo-root>", ""

    best_sim, best_feature = 0.0, None
    for f in active:
        try:
            fv = _embed(f.intent)
            import math
            dot = sum(x * y for x, y in zip(file_vec, fv))
            na = math.sqrt(sum(x * x for x in file_vec))
            nb = math.sqrt(sum(x * x for x in fv))
            sim = dot / (na * nb) if na > 1e-9 and nb > 1e-9 else 0.0
            if sim > best_sim:
                best_sim, best_feature = sim, f
        except Exception:
            continue

    if best_feature is not None and best_sim >= threshold:
        return best_feature.uuid, best_feature.title or best_feature.slug, best_feature.intent

    return None, "<repo-root>", ""


def _change_to_lang_chunk(change: ChunkChange):
    """Wrap a ChunkChange's Chunk in the Chunk interface expected by emit_introduce_proposal."""
    return change.chunk
