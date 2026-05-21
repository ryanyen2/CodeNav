"""Render SQLite state to multi-file `.codoc` text format.

Layout:
    .codoc/tree/
        _index.codoc          — top-level title listing (no intent prose)
        <top-slug>.codoc      — one file per root-level feature, contains its subtree
        tree.meta.json        — sidecar (see meta.py)

Format:
    - Title [( strained|severed|stub|deprecated)]
        Intent prose, one paragraph.
      - Child Title
          Child intent.

    ~ - Retired Feature

    Proposals use col-0 diff markers (no inline HLC):
    + - New Feature
    +     New intent.
    ~ - Changed Feature
    ~     Old intent.
    +     New intent.
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

# States that show a suffix; Stable, Drafting, and Deprecated render clean.
# Deprecated is omitted because the ~ marker already signals retirement.
_WARN_SUFFIX_STATES = {"stub", "strained", "severed"}


def _state_suffix(feature: Feature, store: SQLiteStore) -> str:
    bindings = store.list_bindings(feature.uuid)
    obligations = store.list_obligations(feature_uuid=feature.uuid, status="pending")
    state = compute_feature_state(feature, bindings, [], obligations)
    if state.value in _WARN_SUFFIX_STATES:
        return f"  ({state.value})"
    return ""


def _format_intent(intent: str, intent_indent: str, col0: str = "") -> list[str]:
    if not intent.strip():
        return []
    text = " ".join(intent.split())
    return [f"{col0}{intent_indent}{text}"]


def _build_proposal_index(
    store: SQLiteStore,
) -> tuple[dict[str, list[Transaction]], list[Transaction]]:
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


def _render_proposal(
    tx: Transaction,
    depth: int,
    file_name: str,
    line_offset: int,
    location_tracker: dict,
    line_range_to_hlc: dict,
) -> list[str]:
    """Render a proposal as col-0 diff hunks (+/-/~). Populates line_range_to_hlc."""
    indent = "  " * depth
    intent_indent = indent + "    "
    payload = tx.payload
    kind = tx.kind
    hlc_str = tx.hlc.to_str()
    start_line = line_offset
    lines: list[str] = []

    if kind == TransactionKind.INTRODUCE:
        title = payload.get("title") or payload.get("slug") or "(unnamed)"
        intent = payload.get("intent", "")
        lines.append(f"+ {indent}- {title}")
        lines.extend(_format_intent(intent, intent_indent, col0="+"))

    elif kind in (TransactionKind.RETIRE, TransactionKind.RETIRE_REFLECTIVE):
        title = payload.get("slug") or "(unknown)"
        lines.append(f"- {indent}~ {title}")

    elif kind in (TransactionKind.AMEND, TransactionKind.RENAME, TransactionKind.RENAME_INFER):
        new_title = payload.get("new_title") or payload.get("new_slug") or ""
        old_intent = payload.get("old_intent", "")
        new_intent = payload.get("new_intent") or payload.get("intent", "")
        if new_title:
            lines.append(f"~ {indent}- {new_title}")
        if old_intent:
            lines.extend(_format_intent(old_intent, intent_indent, col0="~"))
        if new_intent:
            lines.extend(_format_intent(new_intent, intent_indent, col0="+"))
        if not lines:
            slug = payload.get("slug") or "(unknown)"
            lines.append(f"~ {indent}- {slug}")

    elif kind in (TransactionKind.ABSORB, TransactionKind.EVICT, TransactionKind.REATTRIBUTE):
        sym = payload.get("symbol_path") or payload.get("file") or ""
        rationale = payload.get("rationale", "")
        lines.append(f"~ {indent}~ {sym}")
        if rationale:
            lines.append(f"~{intent_indent}{rationale[:100]}")

    else:
        slug = payload.get("slug") or payload.get("symbol_path") or ""
        lines.append(f"~ {indent}~ {slug}")

    end_line = start_line + len(lines) - 1
    key = f"{file_name}:{start_line}-{end_line}"
    line_range_to_hlc[key] = hlc_str
    location_tracker[hlc_str] = {
        "file": file_name,
        "line": start_line,
        "kind": "proposal",
    }
    return lines


def _render_feature_block(
    feature: Feature,
    depth: int,
    slug_path: str,
    title_path: str,
    store: SQLiteStore,
    proposals_for_feature: dict[str, list[Transaction]],
    location_tracker: dict,
    binding_index: dict,
    slug_path_to_uuid: dict,
    title_path_to_uuid: dict,
    line_range_to_hlc: dict,
    file_name: str,
    line_offset: int,
) -> list[str]:
    indent = "  " * depth
    intent_indent = indent + "    "
    lines: list[str] = []

    display = feature.title or feature.slug
    current_title_path = f"{title_path} > {display}" if title_path else display

    # --- Feature title line ---
    marker = "~ " if feature.retired else "- "
    suffix = _state_suffix(feature, store)
    feature_line = f"{indent}{marker}{display}{suffix}"
    if os.environ.get("CODOC_LEGACY_UUID_LINES"):
        feature_line += f"  # @{feature.uuid}"
    feature_line_no = line_offset

    slug_path_to_uuid[slug_path] = feature.uuid
    title_path_to_uuid[current_title_path] = feature.uuid
    lines.append(feature_line)
    location_tracker[feature.uuid] = {
        "file": file_name,
        "line": feature_line_no,
        "kind": "feature",
    }

    # --- Intent prose (suppressed for retired features) ---
    if not feature.retired and feature.intent.strip():
        lines.extend(_format_intent(feature.intent, intent_indent))

    # --- Bindings index (sidecar only, not rendered inline) ---
    bindings = store.list_bindings(feature.uuid)
    if bindings:
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
        lines.append("")
        child_slug_path = f"{slug_path}/{child.slug}"
        child_lines = _render_feature_block(
            child,
            depth + 1,
            child_slug_path,
            current_title_path,
            store,
            proposals_for_feature,
            location_tracker,
            binding_index,
            slug_path_to_uuid,
            title_path_to_uuid,
            line_range_to_hlc,
            file_name=file_name,
            line_offset=line_offset + len(lines),
        )
        lines.extend(child_lines)

    # --- Proposals targeting this feature ---
    for tx in proposals_for_feature.get(feature.uuid, []):
        lines.append("")
        prop_lines = _render_proposal(
            tx, depth, file_name,
            line_offset=line_offset + len(lines),
            location_tracker=location_tracker,
            line_range_to_hlc=line_range_to_hlc,
        )
        lines.extend(prop_lines)

    return lines


def _slug_to_filename(slug: str) -> str:
    return f"{slug}.codoc"


def _render_all(
    store: SQLiteStore, tx_log: TransactionLog
) -> tuple[dict[str, str], dict, dict, dict, dict, dict, str]:
    """Build all rendered files plus tracking dicts."""
    proposals_per_feature, top_level_proposals = _build_proposal_index(store)
    head_hlc = _current_head_hlc(tx_log)
    head_str = head_hlc.to_str() if head_hlc is not None else ""

    files: dict[str, str] = {}
    location_tracker: dict[str, dict] = {}
    binding_index: dict = {}
    slug_path_to_uuid: dict = {}
    title_path_to_uuid: dict = {}
    line_range_to_hlc: dict = {}

    root_features = store.list_features(parent_uuid="")

    # _index.codoc — title listing only (no intent prose, no children).
    index_lines: list[str] = [
        "# codoc index — auto-generated. Edit subtree files, not this index.",
        "# col-0 markers: + introduce  ~ amend  - retire  |  (stub) (strained) (severed) = needs attention",
        "",
    ]
    if not root_features and not top_level_proposals:
        index_lines.append("# (empty tree — run `codoc bootstrap` to seed)")
    for f in root_features:
        display = f.title or f.slug
        marker = "~ " if f.retired else "- "
        index_lines.append(f"{marker}{display}")
    for tx in top_level_proposals:
        prop_lines = _render_proposal(
            tx, 0, "_index.codoc",
            line_offset=len(index_lines),
            location_tracker=location_tracker,
            line_range_to_hlc=line_range_to_hlc,
        )
        index_lines.extend(prop_lines)
    files["_index.codoc"] = "\n".join(index_lines) + "\n"

    # One .codoc file per root-level feature (no header, no legend).
    for f in root_features:
        filename = _slug_to_filename(f.slug)
        block_lines = _render_feature_block(
            f, depth=0, slug_path=f.slug, title_path="",
            store=store,
            proposals_for_feature=proposals_per_feature,
            location_tracker=location_tracker,
            binding_index=binding_index,
            slug_path_to_uuid=slug_path_to_uuid,
            title_path_to_uuid=title_path_to_uuid,
            line_range_to_hlc=line_range_to_hlc,
            file_name=filename,
            line_offset=0,
        )
        files[filename] = "\n".join(block_lines) + "\n"

    return files, location_tracker, binding_index, slug_path_to_uuid, title_path_to_uuid, line_range_to_hlc, head_str


def render_tree(store: SQLiteStore, tx_log: TransactionLog) -> dict[str, str]:
    """Render the entire DB to a {filename: content} dict."""
    files, *_ = _render_all(store, tx_log)
    return files


def render_tree_with_meta(
    store: SQLiteStore, tx_log: TransactionLog
) -> tuple[dict[str, str], TreeMeta]:
    """Like render_tree but also returns the meta sidecar."""
    files, loc, bindings, slug_path_to_uuid, title_path_to_uuid, line_range_to_hlc, head_str = (
        _render_all(store, tx_log)
    )
    meta = TreeMeta(
        base_hlc=head_str,
        rendered_at=datetime.now(timezone.utc).isoformat(),
        uuid_to_location=loc,
        binding_index=bindings,
        slug_path_to_uuid=slug_path_to_uuid,
        title_path_to_uuid=title_path_to_uuid,
        line_range_to_hlc=line_range_to_hlc,
    )
    return files, meta


def _current_head_hlc(tx_log: TransactionLog):
    txs = tx_log._storage.list_transactions(proposal=False, limit=0)
    if not txs:
        return None
    return max(txs, key=lambda t: t.hlc).hlc


def write_tree(codoc_dir: str, store: SQLiteStore, tx_log: TransactionLog) -> TreeMeta:
    """Render the DB and write the result to ``.codoc/tree/`` atomically."""
    files, meta = render_tree_with_meta(store, tx_log)
    tree_dir = Path(codoc_dir) / "tree"
    tree_dir.mkdir(parents=True, exist_ok=True)

    keep: set[str] = set()
    for filename, content in files.items():
        target = tree_dir / filename
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
        keep.add(filename)

    for existing in tree_dir.glob("*.codoc"):
        if existing.name not in keep:
            try:
                existing.unlink()
            except OSError:
                pass

    from codoc.projection.meta import write_meta
    write_meta(codoc_dir, meta)
    return meta
