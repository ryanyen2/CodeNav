"""Render SQLite state to a single hierarchical `.codoc` document.

Layout:
    .codoc/tree/
        _index.codoc          — single document: full nested outline with descriptions
        tree.meta.json        — sidecar (see meta.py)

Format (mirrors test/altair/example_codoc.txt):
    - Section Title
        Description paragraph one.

        Description paragraph two.

      - Subsection Title
          Description …

Proposals use col-0 diff markers (no inline HLC):
    + - New Feature
    +     New description.
    ~ - Changed Feature
    ~     Old description.
    +     New description.
    - ~ Retired Feature

State info (severed/strained/stub) is sidecar-only and NOT written to the buffer,
so the buffer always looks like a clean human outline.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from codoc.core.log import TransactionLog
from codoc.model.feature import Feature
from codoc.model.transaction import Transaction, TransactionKind
from codoc.projection.meta import TreeMeta, _sha1
from codoc.projection.tree_align import _title_norm_hash
from codoc.storage.sqlite_store import SQLiteStore

_INDEX_FILENAME = "_index.codoc"


def _build_proposal_index(
    store: SQLiteStore,
) -> tuple[dict[str, list[Transaction]], list[Transaction]]:
    proposals = store.list_transactions(proposal=True, limit=0)
    per_feature: dict[str, list[Transaction]] = {}
    top_level: list[Transaction] = []
    existing_uuids = {f.uuid for f in store.list_features()}
    for tx in proposals:
        feat = tx.payload.get("feature_uuid") or tx.payload.get("affected_feature_uuid")
        if feat and feat in existing_uuids:
            per_feature.setdefault(feat, []).append(tx)
        else:
            # INTRODUCE proposals reference a provisional_uuid that isn't in
            # the features table yet — treat as top-level until accepted.
            top_level.append(tx)
    return per_feature, top_level


def _format_description(
    description: str,
    indent: str,
    col0: str = "",
) -> list[str]:
    """Render a multi-paragraph description block with consistent indent."""
    if not description.strip():
        return []
    lines: list[str] = []
    for para in description.split("\n"):
        stripped = para.strip()
        if stripped:
            lines.append(f"{col0}{indent}{stripped}")
        else:
            lines.append("")
    # Remove trailing blank lines.
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _first_sentence(prose: str, max_chars: int = 160) -> str:
    """Return the first sentence of *prose*, capped at *max_chars*.

    A sentence ends at the first ``.``, ``!``, or ``?`` followed by
    whitespace or end-of-string.
    """
    if not prose.strip():
        return ""
    text = " ".join(prose.split())  # normalise whitespace
    m = re.search(r"[.!?](?:\s|$)", text)
    if m:
        sentence = text[: m.start() + 1]
    else:
        sentence = text
    return sentence[:max_chars]


def _intent_hash(intent: str) -> str:
    """sha1 of intent with normalised whitespace."""
    return _sha1(" ".join(intent.split()))


def _render_proposal(
    tx: Transaction,
    depth: int,
    line_offset: int,
    location_tracker: dict,
    line_range_to_hlc: dict,
) -> list[str]:
    """Render a proposal as col-0 diff hunks. Populates line_range_to_hlc."""
    file_name = _INDEX_FILENAME
    indent = "  " * depth
    desc_indent = indent + "    "
    payload = tx.payload
    kind = tx.kind
    hlc_str = tx.hlc.to_str()
    start_line = line_offset
    lines: list[str] = []

    if kind == TransactionKind.INTRODUCE:
        title = payload.get("title") or payload.get("slug") or "(unnamed)"
        # Try new structured format first, fall back to intent
        purpose = payload.get("purpose", "")
        description = purpose or payload.get("description") or payload.get("intent", "")
        lines.append(f"+ {indent}- {title}")
        lines.extend(_format_description(description, desc_indent, col0="+"))

    elif kind in (TransactionKind.RETIRE, TransactionKind.RETIRE_REFLECTIVE):
        title = payload.get("slug") or "(unknown)"
        lines.append(f"- {indent}~ {title}")

    elif kind in (TransactionKind.AMEND, TransactionKind.RENAME, TransactionKind.RENAME_INFER):
        new_title = payload.get("new_title") or payload.get("new_slug") or ""
        old_desc = payload.get("old_description") or payload.get("old_intent", "")
        new_desc = payload.get("new_description") or payload.get("new_intent") or payload.get("intent", "")
        if new_title:
            lines.append(f"~ {indent}- {new_title}")
        if old_desc:
            lines.extend(_format_description(old_desc, desc_indent, col0="~"))
        if new_desc:
            lines.extend(_format_description(new_desc, desc_indent, col0="+"))
        if not lines:
            slug = payload.get("slug") or "(unknown)"
            lines.append(f"~ {indent}- {slug}")

    elif kind in (TransactionKind.ABSORB, TransactionKind.EVICT, TransactionKind.REATTRIBUTE):
        sym = payload.get("symbol_path") or payload.get("file") or ""
        rationale = payload.get("rationale", "")
        lines.append(f"~ {indent}~ {sym}")
        if rationale:
            lines.append(f"~{desc_indent}{rationale[:100]}")

    elif kind == TransactionKind.RETIRE_FILE:
        file = payload.get("file", "(unknown)")
        n_bindings = len(payload.get("affected_binding_uuids", []))
        n_features = len(payload.get("affected_feature_uuids", []))
        lines.append(f"- {indent}~ file:{file}")
        lines.append(f"-{desc_indent}({n_bindings} bindings, {n_features} features)")

    elif kind == TransactionKind.RENAME_FILE:
        old_f = payload.get("old_file", "?")
        new_f = payload.get("new_file", "?")
        sim = payload.get("similarity", 0.0)
        n = len(payload.get("affected_binding_uuids", []))
        lines.append(f"→ {indent}~ {old_f} → {new_f}")
        lines.append(f"→{desc_indent}({n} bindings remapped, {sim:.0%} similar)")

    elif kind in (TransactionKind.FRACTURE, TransactionKind.COALESCE):
        if kind == TransactionKind.FRACTURE:
            old_sp = payload.get("old_symbol_path", "?")
            n = len(payload.get("new_chunks", []))
            lines.append(f"> {indent}~ {old_sp} → {n} chunks")
        else:
            n = len(payload.get("source_binding_uuids", []))
            new_sp = payload.get("new_chunk", {}).get("symbol_path", "?")
            lines.append(f"< {indent}~ {n} chunks → {new_sp}")

    elif kind == TransactionKind.FEEDFORWARD_FILL:
        slug = payload.get("slug") or "(unknown)"
        lines.append(f"? {indent}feedforward: {slug}")
        for field_key, field_label in [
            ("new_purpose", "purpose"),
            ("new_rationale", "rationale"),
        ]:
            val = payload.get(field_key, "")
            if val:
                lines.append(f"+{desc_indent}{field_label}: {val}")
        scenario = payload.get("new_scenario", "")
        if scenario:
            lines.append(f"+{desc_indent}scenario:")
            for sline in scenario.split("\n"):
                sline = sline.strip()
                if sline:
                    lines.append(f"+{desc_indent}    {sline}")
        directive = payload.get("coding_directive", "")
        if directive:
            lines.append(f"+{desc_indent}plan: {directive[:200]}")

    elif kind == TransactionKind.FEEDBACK_RECONCILE:
        slug = payload.get("slug") or "(unknown)"
        note = payload.get("divergence_note", "")
        header = f"feedback: {slug}" + (f" ({note[:120]})" if note else "")
        lines.append(f"? {indent}{header}")
        old_val = payload.get("old_rationale", "")
        new_val = payload.get("new_rationale", "")
        if old_val:
            lines.append(f"~{desc_indent}rationale: {old_val[:200]}")
        if new_val:
            lines.append(f"+{desc_indent}rationale: {new_val[:200]}")
        for intro in payload.get("introduce_features", []):
            lines.append(f"+ {indent}  * {intro.get('title', '?')}")
            if intro.get("purpose"):
                lines.append(f"+  {desc_indent}purpose: {intro['purpose'][:200]}")

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


def _stale_paths_for_feature(feature_uuid: str, store: "SQLiteStore") -> frozenset[str]:
    """Return the set of stale target_path values for this feature's citations."""
    try:
        rows = store.list_citations(feature_uuid)
        return frozenset(r["target_path"] for r in rows if r.get("is_stale"))
    except Exception:
        return frozenset()


def _render_structured_fields(feature: "Feature", desc_indent: str, store: "SQLiteStore") -> list[str]:
    """Render a feature's structured fields (purpose/rationale/scenario/needs/binds)."""
    from codoc.core.citations import apply_stale_markers

    lines: list[str] = []
    field_indent = desc_indent  # 4 spaces past the feature marker indent

    stale = _stale_paths_for_feature(feature.uuid, store)

    if feature.purpose:
        text = apply_stale_markers(feature.purpose, stale)
        lines.append(f"{field_indent}purpose: {text}")

    if feature.rationale:
        text = apply_stale_markers(feature.rationale, stale)
        lines.append(f"{field_indent}rationale: {text}")

    if feature.scenario:
        lines.append(f"{field_indent}scenario:")
        scenario_indent = field_indent + "    "
        for scenario_line in feature.scenario.split("\n"):
            stripped = scenario_line.strip()
            if stripped:
                marked = apply_stale_markers(stripped, stale)
                lines.append(f"{scenario_indent}{marked}")

    # needs: from feature_edges — CSV when ≤3 deps, arrow-list when more
    try:
        edges = store.list_feature_edges(feature.uuid)
        if edges:
            slugs = []
            for edge in edges:
                target = store.get_feature(edge["target_uuid"])
                if target:
                    slugs.append(target.slug)
            if slugs:
                if len(slugs) <= 3:
                    lines.append(f"{field_indent}needs: {', '.join(slugs)}")
                else:
                    lines.append(f"{field_indent}needs:")
                    edge_indent = field_indent + "    "
                    for slug in slugs:
                        lines.append(f"{edge_indent}-> feature://{slug}")
    except Exception:
        pass

    # Bindings are NOT rendered in the human file — they live in the DB and in
    # .codoc/tree/_index.bindings.json (written by write_bindings_sidecar).
    # VSCode shows them via CodeLens above each feature title.

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
    line_offset: int,
    sibling_index: int = 0,
    feature_hashes: dict | None = None,
) -> list[str]:
    if feature_hashes is None:
        feature_hashes = {}

    file_name = _INDEX_FILENAME
    indent = "  " * depth
    desc_indent = indent + "    "
    lines: list[str] = []

    display = feature.title or feature.slug
    current_title_path = f"{title_path} > {display}" if title_path else display

    # Feature title line.  * = placeholder, ~ = retired, - = live
    if feature.retired:
        marker = "~ "
    elif feature.status in ("placeholder", "feedforward_pending"):
        marker = "* "
    else:
        marker = "- "
    feature_line = f"{indent}{marker}{display}"
    feature_line_no = line_offset

    slug_path_to_uuid[slug_path] = feature.uuid
    title_path_to_uuid[current_title_path] = feature.uuid
    lines.append(feature_line)

    # Full intent: render all paragraphs so the round-trip is lossless.
    description_lines: list[str] = []
    if not feature.retired:
        if feature.purpose:
            description_lines.extend(_render_structured_fields(feature, desc_indent, store))
        elif feature.intent:
            # Legacy fallback: features created before Phase 1 have only intent, not purpose.
            # Render intent as prose so the round-trip is lossless for these features.
            for para in feature.intent.split("\n"):
                para_stripped = para.strip()
                if para_stripped:
                    description_lines.append(f"{desc_indent}{para_stripped}")

    lines.extend(description_lines)
    line_end = feature_line_no + len(description_lines)  # last line of this block (before children)

    # Populate sidecar location entry with structural fields.
    location_tracker[feature.uuid] = {
        "file": file_name,
        "kind": "feature",
        "line": feature_line_no,
        "line_end": line_end,
        "depth": depth,
        "sibling_index": sibling_index,
        "parent_uuid": feature.parent_uuid,
        "title": display,
        "slug": feature.slug,
        "title_norm_hash": _title_norm_hash(display),
        "intent_hash": _intent_hash(feature.intent),
    }

    # Populate feature_hashes for conflict detection.
    feature_hashes[feature.uuid] = _sha1(
        display + "|" + feature.intent + "|" + (feature.parent_uuid or "") + "|" + str(feature.retired)
    )

    # Bindings index (sidecar only, not rendered inline).
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

    # Children.
    children = store.list_features(parent_uuid=feature.uuid)
    for child_idx, child in enumerate(children):
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
            line_offset=line_offset + len(lines),
            sibling_index=child_idx,
            feature_hashes=feature_hashes,
        )
        lines.extend(child_lines)

    # Proposals targeting this feature.
    for tx in proposals_for_feature.get(feature.uuid, []):
        lines.append("")
        prop_lines = _render_proposal(
            tx, depth,
            line_offset=line_offset + len(lines),
            location_tracker=location_tracker,
            line_range_to_hlc=line_range_to_hlc,
        )
        lines.extend(prop_lines)

    return lines


def _render_all(
    store: SQLiteStore, tx_log: TransactionLog
) -> tuple[dict[str, str], dict, dict, dict, dict, dict, str, dict]:
    """Build the single _index.codoc document plus tracking dicts."""
    proposals_per_feature, top_level_proposals = _build_proposal_index(store)
    head_hlc = _current_head_hlc(tx_log)
    head_str = head_hlc.to_str() if head_hlc is not None else ""

    location_tracker: dict[str, dict] = {}
    binding_index: dict = {}
    slug_path_to_uuid: dict = {}
    title_path_to_uuid: dict = {}
    line_range_to_hlc: dict = {}
    feature_hashes: dict = {}

    root_features = store.list_features(parent_uuid="")

    body_lines: list[str] = []

    if not root_features and not top_level_proposals:
        body_lines.append("# (empty tree — run `codoc bootstrap` to seed)")
    else:
        for root_idx, f in enumerate(root_features):
            block_lines = _render_feature_block(
                f, depth=0, slug_path=f.slug, title_path="",
                store=store,
                proposals_for_feature=proposals_per_feature,
                location_tracker=location_tracker,
                binding_index=binding_index,
                slug_path_to_uuid=slug_path_to_uuid,
                title_path_to_uuid=title_path_to_uuid,
                line_range_to_hlc=line_range_to_hlc,
                line_offset=len(body_lines),
                sibling_index=root_idx,
                feature_hashes=feature_hashes,
            )
            body_lines.extend(block_lines)
            body_lines.append("")

        for tx in top_level_proposals:
            prop_lines = _render_proposal(
                tx, 0,
                line_offset=len(body_lines),
                location_tracker=location_tracker,
                line_range_to_hlc=line_range_to_hlc,
            )
            body_lines.extend(prop_lines)

    content = "\n".join(body_lines) + "\n"
    files: dict[str, str] = {_INDEX_FILENAME: content}
    return files, location_tracker, binding_index, slug_path_to_uuid, title_path_to_uuid, line_range_to_hlc, head_str, feature_hashes


def render_tree(store: SQLiteStore, tx_log: TransactionLog) -> dict[str, str]:
    """Render the entire DB to a {filename: content} dict."""
    files, *_ = _render_all(store, tx_log)
    return files


def render_tree_with_meta(
    store: SQLiteStore, tx_log: TransactionLog
) -> tuple[dict[str, str], TreeMeta]:
    """Like render_tree but also returns the meta sidecar."""
    files, loc, bindings, slug_path_to_uuid, title_path_to_uuid, line_range_to_hlc, head_str, feature_hashes = (
        _render_all(store, tx_log)
    )
    content = files.get(_INDEX_FILENAME, "")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    meta = TreeMeta(
        base_hlc=head_str,
        rendered_at=datetime.now(timezone.utc).isoformat(),
        uuid_to_location=loc,
        binding_index=bindings,
        slug_path_to_uuid=slug_path_to_uuid,
        title_path_to_uuid=title_path_to_uuid,
        line_range_to_hlc=line_range_to_hlc,
        content_hash=content_hash,
        feature_hashes=feature_hashes,
    )
    return files, meta


def _current_head_hlc(tx_log: TransactionLog):
    txs = tx_log._storage.list_transactions(proposal=False, limit=0)
    if not txs:
        return None
    return max(txs, key=lambda t: t.hlc).hlc


def write_tree(codoc_dir: str, store: SQLiteStore, tx_log: TransactionLog) -> TreeMeta:
    """Render the DB and write to .codoc/tree/ — only writes files that changed."""
    files, meta = render_tree_with_meta(store, tx_log)
    tree_dir = Path(codoc_dir) / "tree"
    tree_dir.mkdir(parents=True, exist_ok=True)

    kept: set[str] = set()
    for filename, content in files.items():
        target = tree_dir / filename
        # Idempotent write: skip if content is byte-identical.
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if existing == content:
                kept.add(filename)
                continue
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
        kept.add(filename)

    # Remove any stale .codoc files no longer in the render set.
    for existing in tree_dir.glob("*.codoc"):
        if existing.name not in kept:
            try:
                existing.unlink()
            except OSError:
                pass

    from codoc.projection.meta import write_meta
    write_meta(codoc_dir, meta)

    try:
        from codoc.projection.bindings_sidecar import write_bindings_sidecar
        write_bindings_sidecar(codoc_dir, store)
    except Exception:
        pass

    return meta
