"""Render SQLite state to multi-file `.codoc` text format.

Layout:
    .codoc/tree/
        _index.codoc          — top-level slug listing
        <top-slug>.codoc      — one file per root-level feature, contains its subtree
        tree.meta.json        — sidecar (see meta.py)

The rendered text is a projection of the DB. SQLite is source of truth.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from codoc.core.log import TransactionLog
from codoc.core.state_derivation import BindingResolution, compute_feature_state
from codoc.model.feature import Feature
from codoc.model.transaction import Transaction, TransactionKind
from codoc.projection.meta import TreeMeta
from codoc.storage.sqlite_store import SQLiteStore

_LEGACY_UUID_LINES = os.environ.get("CODOC_LEGACY_UUID_LINES") == "1"

_LEGEND = "# - active   ~ retired   ? proposal (delete=accept, !=reject)   [State] computed"


def _display_hlc(hlc_str: str) -> str:
    """Strip the trailing node_id segment (always '-default' in single-user mode)."""
    parts = hlc_str.rsplit("-", 1)
    return parts[0] if len(parts) > 1 else hlc_str

# State badge → display string. Keep short to be unobtrusive in the buffer.
_STATE_BADGES = {
    "stable": "Stable",
    "drafting": "Drafting",
    "stub": "Stub",
    "strained": "Strained",
    "deprecated": "Deprecated",
    "severed": "Severed",
}


def _state_badge(feature: Feature, store: SQLiteStore) -> str:
    """Compute the state badge for a feature using empty resolutions
    (state_derivation treats unknowns conservatively). The render does not
    perform anchor resolution — that would be expensive and unnecessary for
    the projection buffer."""
    bindings = store.list_bindings(feature.uuid)
    resolutions: list[BindingResolution] = []
    obligations = store.list_obligations(feature_uuid=feature.uuid, status="pending")
    state = compute_feature_state(feature, bindings, resolutions, obligations)
    return _STATE_BADGES.get(state.value, state.value.capitalize())


def _binding_summary(feature_uuid: str, store: SQLiteStore) -> str:
    """Return ``"file:symbol, file2:symbol2"`` style summary, or empty string."""
    bindings = store.list_bindings(feature_uuid)
    if not bindings:
        return ""
    parts: list[str] = []
    for b in bindings:
        sym = b.anchor.symbol_path or b.anchor.ts_query or ""
        # Strip "file::" prefix from the symbol_path (it duplicates `file`).
        if sym.startswith(b.anchor.file + "::"):
            sym = sym[len(b.anchor.file) + 2 :]
        parts.append(f"{b.anchor.file}:{sym}" if sym else b.anchor.file)
    return ", ".join(parts)


def _render_bindings_block(feature_uuid: str, store: SQLiteStore, indent: str) -> list[str]:
    """Render bindings as one-per-line with short ids: [b1] file :: symbol"""
    bindings = store.list_bindings(feature_uuid)
    if not bindings:
        return []
    lines = [f"{indent}bindings:"]
    entry_indent = indent + "  "
    for i, b in enumerate(bindings, 1):
        sym = b.anchor.symbol_path or b.anchor.ts_query or ""
        if sym.startswith(b.anchor.file + "::"):
            sym = sym[len(b.anchor.file) + 2:]
        entry = f"{b.anchor.file} :: {sym}" if sym else b.anchor.file
        lines.append(f"{entry_indent}[b{i}] {entry}")
    return lines


def _format_intent(intent: str, indent: str) -> list[str]:
    """Format an intent string as one or more indented lines (single paragraph
    is preferred; long intents are kept as-is, joined by spaces if multi-line)."""
    if not intent.strip():
        return []
    text = " ".join(intent.split())  # collapse whitespace into single paragraph
    return [f"{indent}{text}"]


def _proposal_kind_to_label(kind: TransactionKind) -> str:
    """Format kind for the ``? <kind>: ...`` line."""
    return kind.value.replace("_", "-")


def _build_proposal_index(store: SQLiteStore) -> tuple[dict[str, list[Transaction]], list[Transaction]]:
    """Return (per_feature, top_level) proposal lists.

    per_feature: {feature_uuid: [Transaction]}  — proposals targeting an existing feature
    top_level:   [Transaction]  — INTRODUCE proposals (no target feature yet)
    """
    proposals = store.list_transactions(proposal=True, limit=0)
    per_feature: dict[str, list[Transaction]] = {}
    top_level: list[Transaction] = []
    for tx in proposals:
        feat = tx.payload.get("feature_uuid") or tx.payload.get("affected_feature_uuid")
        if feat:
            per_feature.setdefault(feat, []).append(tx)
        else:
            top_level.append(tx)
    return per_feature, top_level


def _render_feature_block(
    feature: Feature,
    depth: int,
    slug_path: str,
    store: SQLiteStore,
    proposals_for_feature: dict[str, list[Transaction]],
    location_tracker: dict[str, dict],
    binding_index: dict,
    slug_path_to_uuid: dict,
    file_name: str,
    line_offset: int,
) -> tuple[list[str], int]:
    """Render a feature and recursively its children.

    Returns (lines, lines_emitted).  ``line_offset`` is the absolute line number
    in the output file where this block begins.
    """
    indent = "  " * depth
    intent_indent = "  " * (depth + 1)
    lines: list[str] = []

    # --- Feature line ---
    if feature.retired:
        prefix = f"{indent}~ {feature.slug}"
    else:
        prefix = f"{indent}- {feature.slug}"
    badge = _state_badge(feature, store)
    if _LEGACY_UUID_LINES:
        feature_line = f"{prefix}  [{badge}]  # @{feature.uuid}"
    else:
        feature_line = f"{prefix}  [{badge}]"
    feature_line_no = line_offset + len(lines)
    slug_path_to_uuid[slug_path] = feature.uuid
    lines.append(feature_line)
    location_tracker[feature.uuid] = {
        "file": file_name,
        "line": feature_line_no,
        "kind": "feature",
    }

    # --- Intent prose ---
    if feature.intent.strip():
        lines.extend(_format_intent(feature.intent, intent_indent))

    # --- Bindings echo (read-only) ---
    bindings = store.list_bindings(feature.uuid)
    if bindings:
        if _LEGACY_UUID_LINES:
            summary = _binding_summary(feature.uuid, store)
            if summary:
                lines.append(f"{intent_indent}bindings: {summary}")
        else:
            binding_lines = _render_bindings_block(feature.uuid, store, intent_indent)
            if binding_lines:
                lines.extend(binding_lines)
        binding_index[feature.uuid] = [
            {
                "file": b.anchor.file,
                "symbol_path": b.anchor.symbol_path,
                "ts_query": b.anchor.ts_query,
                "binding_uuid": b.uuid,
            }
            for b in bindings
        ]

    # --- Children ---
    children = store.list_features(parent_uuid=feature.uuid)
    for child in children:
        lines.append("")  # blank separator
        child_slug_path = f"{slug_path}/{child.slug}"
        child_lines, _ = _render_feature_block(
            child,
            depth + 1,
            child_slug_path,
            store,
            proposals_for_feature,
            location_tracker,
            binding_index,
            slug_path_to_uuid=slug_path_to_uuid,
            file_name=file_name,
            line_offset=line_offset + len(lines),
        )
        lines.extend(child_lines)

    # --- Proposals targeting this feature ---
    for tx in proposals_for_feature.get(feature.uuid, []):
        lines.append("")
        proposal_line_no = line_offset + len(lines)
        prop_lines = _render_proposal(tx, depth, intent_indent)
        location_tracker[tx.hlc.to_str()] = {
            "file": file_name,
            "line": proposal_line_no,
            "kind": "proposal",
            "feature_uuid": feature.uuid,
        }
        lines.extend(prop_lines)

    return lines, len(lines)


def _render_proposal(tx: Transaction, depth: int, body_indent: str) -> list[str]:
    """Format a proposal as ``? <kind>: <slug>  [proposal]  # ?<hlc>`` plus body."""
    indent = "  " * depth
    kind = _proposal_kind_to_label(tx.kind)
    payload = tx.payload
    slug = payload.get("slug") or payload.get("new_slug") or payload.get("symbol_path") or ""
    if not slug:
        binding_uuid = payload.get("binding_uuid", "")
        slug = binding_uuid[:8] if binding_uuid else "(unnamed)"
    head = f"{indent}? {kind}: {slug}  [proposal]  # ?{_display_hlc(tx.hlc.to_str())}"
    lines = [head]

    # Proposal-specific body.
    intent = payload.get("intent")
    if intent:
        lines.extend(_format_intent(intent, body_indent))

    rationale = payload.get("rationale")
    if rationale and not intent:
        lines.append(f"{body_indent}{rationale}")

    candidates = payload.get("candidate_bindings") or []
    if candidates:
        cand_strs: list[str] = []
        for c in candidates:
            anc = c.get("anchor", {}) if isinstance(c, dict) else {}
            sym = anc.get("symbol_path") or anc.get("ts_query") or ""
            file_ = anc.get("file", "")
            cand_strs.append(f"{file_}:{sym}" if sym else file_)
        if cand_strs:
            lines.append(f"{body_indent}candidate-bindings: {', '.join(cand_strs)}")
    elif payload.get("symbol_path") or payload.get("file"):
        sym = payload.get("symbol_path", "")
        file_ = payload.get("file", "")
        lines.append(f"{body_indent}candidate-bindings: {file_}:{sym}")

    return lines


def _slug_to_filename(slug: str) -> str:
    return f"{slug}.codoc"


def _render_all(store: SQLiteStore, tx_log: TransactionLog) -> tuple[dict[str, str], dict, dict, dict, str]:
    """Build all rendered files plus tracking data. Returns (files, location_tracker, binding_index, slug_path_to_uuid, head_str)."""
    proposals_per_feature, top_level_proposals = _build_proposal_index(store)
    head_hlc = _current_head_hlc(tx_log)
    head_str = head_hlc.to_str() if head_hlc is not None else ""

    files: dict[str, str] = {}
    location_tracker: dict[str, dict] = {}
    binding_index: dict = {}
    slug_path_to_uuid: dict = {}

    root_features = store.list_features(parent_uuid="")

    # _index.codoc — top-level slug listing.
    index_lines = ["# codoc index", "", _LEGEND, ""]
    if not root_features and not top_level_proposals:
        index_lines.append("# (empty tree — run `codoc bootstrap` to seed)")
    for f in root_features:
        marker = "~" if f.retired else "-"
        if _LEGACY_UUID_LINES:
            index_lines.append(f"{marker} {f.slug}  # @{f.uuid}")
        else:
            index_lines.append(f"{marker} {f.slug}")
    if top_level_proposals:
        index_lines.append("")
        index_lines.append("# Pending top-level proposals (no parent feature yet)")
        for tx in top_level_proposals:
            payload = tx.payload
            slug = payload.get("slug") or "(unnamed)"
            line_no = len(index_lines)
            index_lines.append(
                f"? {_proposal_kind_to_label(tx.kind)}: {slug}  [proposal]  # ?{_display_hlc(tx.hlc.to_str())}"
            )
            location_tracker[tx.hlc.to_str()] = {
                "file": "_index.codoc",
                "line": line_no,
                "kind": "proposal",
                "feature_uuid": None,
            }
            intent = payload.get("intent")
            if intent:
                index_lines.append(f"  {intent.strip()}")
    files["_index.codoc"] = "\n".join(index_lines) + "\n"

    for f in root_features:
        filename = _slug_to_filename(f.slug)
        header = f"# codoc subtree: {f.slug}"
        body_lines: list[str] = [header, "", _LEGEND, ""]
        block_lines, _ = _render_feature_block(
            f, depth=0, slug_path=f.slug, store=store,
            proposals_for_feature=proposals_per_feature,
            location_tracker=location_tracker,
            binding_index=binding_index,
            slug_path_to_uuid=slug_path_to_uuid,
            file_name=filename,
            line_offset=len(body_lines),
        )
        body_lines.extend(block_lines)
        files[filename] = "\n".join(body_lines) + "\n"

    return files, location_tracker, binding_index, slug_path_to_uuid, head_str


def render_tree(store: SQLiteStore, tx_log: TransactionLog) -> dict[str, str]:
    """Render the entire DB to a {filename: content} dict.

    Always returns at least ``_index.codoc``. One file per root-level feature.
    """
    files, _, _, _, _ = _render_all(store, tx_log)
    return files


def render_tree_with_meta(
    store: SQLiteStore, tx_log: TransactionLog
) -> tuple[dict[str, str], TreeMeta]:
    """Like render_tree but also returns the meta sidecar."""
    files, loc, bindings, slug_path_to_uuid, head_str = _render_all(store, tx_log)
    meta = TreeMeta(
        base_hlc=head_str,
        rendered_at=datetime.now(timezone.utc).isoformat(),
        uuid_to_location=loc,
        binding_index=bindings,
        slug_path_to_uuid=slug_path_to_uuid,
    )
    return files, meta


def _current_head_hlc(tx_log: TransactionLog):
    """Return the most recent accepted HLC, or None.

    ``TransactionLog.head_hlc`` is documented as "most recent" but the
    underlying query orders ASC, so we re-implement by scanning all
    accepted transactions and picking the max.
    """
    txs = tx_log._storage.list_transactions(proposal=False, limit=0)
    if not txs:
        return None
    return max(txs, key=lambda t: t.hlc).hlc


def write_tree(codoc_dir: str, store: SQLiteStore, tx_log: TransactionLog) -> TreeMeta:
    """Render the DB and write the result to ``.codoc/tree/`` atomically.

    Existing ``.codoc`` files in ``.codoc/tree/`` that are not part of the new
    render are removed (e.g. when a top-level feature is retired and renamed).
    """
    files, meta = render_tree_with_meta(store, tx_log)
    tree_dir = Path(codoc_dir) / "tree"
    tree_dir.mkdir(parents=True, exist_ok=True)

    # Atomic write via temp + rename.
    keep: set[str] = set()
    for filename, content in files.items():
        target = tree_dir / filename
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
        keep.add(filename)

    # Remove stale .codoc files (not the meta sidecar).
    for existing in tree_dir.glob("*.codoc"):
        if existing.name not in keep:
            try:
                existing.unlink()
            except OSError:
                pass

    # Write meta last so it always reflects what's on disk.
    from codoc.projection.meta import write_meta

    write_meta(codoc_dir, meta)
    return meta
